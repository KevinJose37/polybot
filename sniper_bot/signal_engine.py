"""
sniper_bot/signal_engine.py — Event-driven imbalance signal detection.

Registered as callback on OrderbookManager.on_book_update().
Fires on every tick — checks ALL gates before emitting a signal.
Zero polling.
"""
import time
import logging
import asyncio
from dataclasses import dataclass, field
from collections import deque

from .config import SniperConfig
from .ws_manager import BookSnapshot, OrderbookManager
from .lifecycle import MarketLifecycleManager, Phase

logger = logging.getLogger("sniper_bot.signal")


@dataclass
class Signal:
    """Emitted when the engine detects a tradeable (or rejected) signal."""
    timestamp: float
    asset: str
    token_id: str
    direction: str          # UP / DOWN
    best_ask: float
    best_bid: float
    counterpart_ask: float
    spread: float
    imbalance: float
    ask_velocity: float
    bid_depth: float
    ask_depth: float
    score: float            # 0.0 to 1.0 confidence
    accepted: bool
    reject_reason: str = ""
    book_age_ms: float = 0.0
    phase: str = ""         # ENTRY / HOLD / etc.
    entry_window_remaining_s: float = 0.0
    
    # ── Telemetry ──
    engine: str = "heur"
    elapsed_at_entry_s: float = 0.0
    filters_passed: list[str] = field(default_factory=list)
    filters_failed: list[str] = field(default_factory=list)


class SignalEngine:
    """
    Event-driven signal detector.
    Called on every book tick via ws_manager callback.
    """

    def __init__(
        self,
        config: SniperConfig,
        ws_mgr: OrderbookManager,
        lifecycle: MarketLifecycleManager,
        xgb_scorer=None,
        xgb_features=None,
    ):
        self.config = config
        self._ws_mgr = ws_mgr
        self._lifecycle = lifecycle
        self._xgb_scorer = xgb_scorer      # Optional XGBScorer
        self._xgb_features = xgb_features  # Optional FeatureAccumulator

        # Token → asset/direction mapping
        self._token_map: dict[str, tuple[str, str]] = {}  # token_id → (asset, "UP"/"DOWN")

        self._pending_batches: dict = {}  # asset -> {"start": timestamp, "signals": list}

        # Signal history for dashboard
        self.signal_log: deque[Signal] = deque(maxlen=50)

        # Metrics
        self.signals_detected: int = 0
        self.signals_accepted: int = 0
        self.signals_rejected: int = 0
        self.rejection_reasons: dict[str, int] = {}
        self.last_signal_time: float = 0.0

        # Callbacks for downstream (executor)
        self._signal_callbacks: list = []
        self._rejected_callbacks: list = []

    def on_signal(self, callback) -> None:
        """Register callback fired on accepted signals."""
        self._signal_callbacks.append(callback)

    def on_rejected_signal(self, callback) -> None:
        """Register callback fired on rejected signals (for skipped trades logging)."""
        self._rejected_callbacks.append(callback)

    def register_tokens(self, asset: str, up_token: str, down_token: str) -> None:
        """Map token IDs to asset + direction."""
        # Clear old tokens for this asset
        to_remove = [k for k, v in self._token_map.items() if v[0] == asset]
        for k in to_remove:
            del self._token_map[k]
            
        if up_token:
            self._token_map[up_token] = (asset, "UP")
        if down_token:
            self._token_map[down_token] = (asset, "DOWN")

    def clear_tokens(self) -> None:
        """Clear all token mappings (for market rotation)."""
        self._token_map.clear()

    def _find_counterpart(self, asset: str, direction: str):
        """Find the counterpart token_id, direction, and book for an asset."""
        for tid, (a, d) in self._token_map.items():
            if a == asset and d != direction:
                c_book = self._ws_mgr.get_book(tid)
                return tid, d, c_book
        return None, None, None

    def on_book_tick(self, token_id: str, book: BookSnapshot) -> None:
        """
        Called on EVERY book tick from ws_manager.
        This is the hot path — must be fast.

        MOMENTUM-FLIP LOGIC:
        The token that passes the trigger range (cheap side) acts as a DETECTOR
        that the market is active. But the actual entry goes to whichever side
        has the HIGHER ask price (= more market conviction / momentum).

        Example: SOL DOWN ask=$0.40 passes trigger [0.40, 0.60].
        Counterpart SOL UP ask=$0.63 has MORE momentum.
        → FLIP: enter SOL UP at $0.63 instead.
        """
        mapping = self._token_map.get(token_id)
        if not mapping:
            return

        asset, direction = mapping
        cfg = self.config

        # ── Gate 1: Lifecycle phase ──────────────────────────────
        state = self._lifecycle.get(asset)
        if not state:
            return

        phase = state.phase()
        unfiltered = getattr(cfg, 'xgb_unfiltered', False)
        
        if not unfiltered:
            if phase != Phase.ENTRY:
                return  # Only signal during entry window
        else:
            # Unfiltered mode: allow signals in HOLD phase too, to let XGB warm up
            if phase not in (Phase.ENTRY, Phase.HOLD):
                return

        # Mark that we got data for this market
        self._lifecycle.mark_first_data(asset)

        # Arrived late?
        if state.arrived_late() and not unfiltered:
            return

        # ── Gate 2: Price range ──────────────────────────────────
        ask = book.best_ask
        if not unfiltered:
            if ask <= 0 or ask < cfg.min_ask:
                return
            if ask > cfg.max_ask:
                return
        else:
            if ask <= 0 or ask >= 1.0:
                return

        # ── Gate 3: Trigger range (DETECTOR — confirms market is in play) ─
        if not unfiltered:
            if not (cfg.trigger_low <= ask <= cfg.trigger_high):
                return  # Not in the sweet spot, skip silently

        # ── MOMENTUM FLIP: Check counterpart and enter the HIGHER side ──
        # This token ($0.40) detected the market. Now check which side to enter.
        c_tid, c_dir, c_book = self._find_counterpart(asset, direction)

        # Determine which side has more momentum
        entry_token_id = token_id
        entry_direction = direction
        entry_book = book
        entry_ask = ask
        c_ask = c_book.best_ask if c_book else 0.0
        flipped = False

        if c_book and c_ask > ask:
            # Counterpart has MORE momentum — ALWAYS flip to that side.
            # Gates 4-6 below will validate quality on the flipped side.
            # If quality fails → signal REJECTED (no entry at all).
            # NEVER fall back to the cheap side.
            entry_token_id = c_tid
            entry_direction = c_dir
            entry_book = c_book
            entry_ask = c_ask
            # c_ask for the signal context = the original (detector) side's ask
            c_ask = ask
            flipped = True

        # ── From here on, use entry_* variables for the actual entry ─

        self.signals_detected += 1
        reject_reason = ""
        filters_passed = []
        filters_failed = []
        velocity = 0.0

        # ── Gate 0: Hard Skips (Both Bots) ───────────────────────
        if entry_direction == "UP":
            reject_reason = "DIRECTION_UP_BANNED"
            filters_failed.append("DIRECTION")
        elif asset == "XRP":
            reject_reason = "ASSET_XRP_BANNED"
            filters_failed.append("ASSET")

        if not unfiltered and not reject_reason:
            # ── Gate 4: Spread (on entry side) ───────────────────────
            if entry_book.spread > cfg.max_spread:
                reject_reason = f"SPREAD_TOO_WIDE ({entry_book.spread:.3f} > {cfg.max_spread})"
                filters_failed.append("SPREAD")
            else:
                filters_passed.append("SPREAD")

            # ── Gate 5: Depth (on entry side) ────────────────────────
            if not reject_reason:
                if entry_book.ask_depth < cfg.min_depth:
                    reject_reason = f"DEPTH_TOO_THIN ({entry_book.ask_depth:.0f} < {cfg.min_depth})"
                    filters_failed.append("DEPTH")
                else:
                    filters_passed.append("DEPTH")

            # ── Gate 6: Velocity (on entry side) ─────────────────────
            if not reject_reason:
                velocity = self._ws_mgr.get_ask_velocity(entry_token_id, window_ms=500)
                if abs(velocity) > cfg.max_velocity:
                    reject_reason = f"VELOCITY_TOO_HIGH ({velocity:.3f})"
                    filters_failed.append("VELOCITY")
                else:
                    filters_passed.append("VELOCITY")
        else:
            velocity = 0.0

        if not reject_reason and not unfiltered:
            velocity = self._ws_mgr.get_ask_velocity(entry_token_id, window_ms=500)
        elif not reject_reason and unfiltered:
            velocity = self._ws_mgr.get_ask_velocity(entry_token_id, window_ms=500)

        # ── Anti-spam ────────────────────────────────────────────
        if not reject_reason:
            if time.time() - self.last_signal_time < cfg.min_signal_interval_s:
                reject_reason = "ANTI_SPAM"
                filters_failed.append("ANTI_SPAM")
            else:
                filters_passed.append("ANTI_SPAM")

        score = 0.0
        xgb_used = False
        engine_used = "heur"
        
        if not reject_reason:
            # ── XGBoost ML scoring (when enabled + warm) ─────────
            if (self._xgb_scorer and self._xgb_scorer.is_loaded
                    and self._xgb_features and self._xgb_features.is_warm(entry_token_id)):
                features = self._xgb_features.get_features(entry_token_id)
                if features:
                    score = self._xgb_scorer.predict(features, asset)
                    
                    if score is None:
                        reject_reason = f"NO_MODEL_FOR_ASSET ({asset})"
                        filters_failed.append("NO_MODEL_FOR_ASSET")
                    else:
                        xgb_used = True
                        engine_used = "xgb"
                        # Reject if below confidence threshold
                        if score < cfg.xgb_min_confidence:
                            reject_reason = f"LOW_XGB_CONFIDENCE ({score:.3f} < {cfg.xgb_min_confidence})"
                            filters_failed.append("XGB_CONFIDENCE")
                        else:
                            filters_passed.append("XGB_CONFIDENCE")

            # ── Fallback: heuristic scoring ──────────────────────
            if not xgb_used and not reject_reason:
                if unfiltered:
                    reject_reason = "XGB_WARMING_UP"
                    filters_failed.append("XGB_WARMUP")
                elif self._xgb_scorer and self._xgb_scorer.is_loaded:
                    # XGB bot should NEVER fallback to heuristics
                    reject_reason = "XGB_NOT_WARM_OR_LOW_CONFIDENCE"
                    filters_failed.append("XGB_FALLBACK_DISABLED")
                else:
                    score_range = 0.90 - cfg.trigger_low
                    price_score = min(1.0, max(0.0, (entry_ask - cfg.trigger_low) / score_range))
                    spread_score = 1.0 - (entry_book.spread / cfg.max_spread)
                    depth_score = min(1.0, entry_book.ask_depth / (cfg.min_depth * 3))
                    vel_score = 1.0 - abs(velocity) / cfg.max_velocity if cfg.max_velocity > 0 else 1.0
                    score = round(max(0.0, min(1.0,
                        (price_score * 0.50 + spread_score * 0.20 + depth_score * 0.20 + vel_score * 0.10)
                    )), 3)
                    filters_passed.append("HEURISTIC")

        accepted = not reject_reason
        elapsed_entry_s = state.seconds_elapsed() if hasattr(state, 'seconds_elapsed') else 0.0

        signal = Signal(
            timestamp=time.time(),
            asset=asset,
            token_id=entry_token_id,
            direction=entry_direction,
            best_ask=entry_ask,
            best_bid=entry_book.best_bid,
            counterpart_ask=c_ask,
            spread=entry_book.spread,
            imbalance=entry_book.imbalance,
            ask_velocity=velocity,
            bid_depth=entry_book.bid_depth,
            ask_depth=entry_book.ask_depth,
            score=score,
            accepted=accepted,
            reject_reason=reject_reason,
            book_age_ms=entry_book.age_ms,
            phase=phase.value,
            entry_window_remaining_s=state.entry_window_remaining(),
            engine=engine_used,
            elapsed_at_entry_s=elapsed_entry_s,
            filters_passed=filters_passed,
            filters_failed=filters_failed,
        )
        if not accepted:
            self.signal_log.append(signal)
            self.signals_rejected += 1
            self.rejection_reasons[reject_reason.split("(")[0].strip()] = \
                self.rejection_reasons.get(reject_reason.split("(")[0].strip(), 0) + 1
            
            for cb in self._rejected_callbacks:
                try:
                    cb(signal)
                except Exception as e:
                    logger.error("Rejected signal callback error: %s", e)
        else:
            # Buffer accepted signals for 50ms micro-batching
            if asset not in self._pending_batches:
                self._pending_batches[asset] = {"start": time.time(), "signals": []}
            self._pending_batches[asset]["signals"].append(signal)

    async def run_batcher(self) -> None:
        """Micro-batching loop: evaluates buffered signals every 10ms."""
        while True:
            await asyncio.sleep(0.01)
            now = time.time()
            for asset in list(self._pending_batches.keys()):
                batch = self._pending_batches.get(asset)
                if not batch:
                    continue
                
                if now - batch["start"] >= 0.050:
                    del self._pending_batches[asset]
                    
                    signals = batch["signals"]
                    if not signals:
                        continue
                        
                    # Best score wins
                    signals.sort(key=lambda s: s.score, reverse=True)
                    best_signal = signals[0]
                    
                    # Double check global anti-spam 
                    if now - self.last_signal_time < self.config.min_signal_interval_s:
                        best_signal.accepted = False
                        best_signal.reject_reason = "ANTI_SPAM"
                        self.signal_log.append(best_signal)
                        self.signals_rejected += 1
                        self.rejection_reasons["ANTI_SPAM"] = self.rejection_reasons.get("ANTI_SPAM", 0) + 1
                        continue
                        
                    # Winner accepted
                    self.signal_log.append(best_signal)
                    self.signals_accepted += 1
                    self.last_signal_time = now
                    
                    for cb in self._signal_callbacks:
                        try:
                            cb(best_signal)
                        except Exception as e:
                            logger.error("Signal callback error: %s", e)
                            
                    # Losers rejected
                    for loser in signals[1:]:
                        loser.accepted = False
                        loser.reject_reason = "BEATEN_BY_BETTER_SIGNAL"
                        loser.filters_failed.append("BEATEN_BY_BETTER_SIGNAL")
                        self.signal_log.append(loser)
                        self.signals_rejected += 1
                        self.rejection_reasons["BEATEN_BY_BETTER_SIGNAL"] = self.rejection_reasons.get("BEATEN_BY_BETTER_SIGNAL", 0) + 1
                        
                        for cb in self._rejected_callbacks:
                            try:
                                cb(loser)
                            except Exception as e:
                                pass

    def metrics(self) -> dict:
        """Metrics snapshot for dashboard."""
        return {
            "detected": self.signals_detected,
            "accepted": self.signals_accepted,
            "rejected": self.signals_rejected,
            "rejection_rate": round(self.signals_rejected / max(1, self.signals_detected), 3),
            "top_rejections": dict(
                sorted(self.rejection_reasons.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
        }
