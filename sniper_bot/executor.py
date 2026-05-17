"""
sniper_bot/executor.py — Paper + Live execution engine.

Entry: TAKER buy at best_ask (with slippage model for paper)
Exit:  MAKER GTC limit sell at dynamic TP (based on book depth)
Resolution: If maker doesn't fill, let market resolve to $1 or $0.
"""
import time
import logging
import uuid
from dataclasses import dataclass

from .config import SniperConfig
from .ws_manager import BookSnapshot, OrderbookManager
from .lifecycle import MarketLifecycleManager, Phase
from .positions import Position, PositionManager
from .circuit_breaker import CircuitBreaker, TradingState
from .signal_engine import Signal

logger = logging.getLogger("sniper_bot.executor")


@dataclass
class ExecutionMetrics:
    """Granular execution quality metrics."""
    entries_attempted: int = 0
    entries_filled: int = 0
    entries_rejected: int = 0

    maker_fills: int = 0
    resolution_wins: int = 0
    resolution_losses: int = 0

    total_slippage: float = 0.0
    total_book_age_at_entry_ms: float = 0.0
    total_signal_to_entry_ms: float = 0.0
    stale_book_entries: int = 0       # Entries with book > 500ms old

    def avg_slippage(self) -> float:
        return self.total_slippage / max(1, self.entries_filled)

    def avg_book_age_ms(self) -> float:
        return self.total_book_age_at_entry_ms / max(1, self.entries_filled)

    def avg_signal_to_entry_ms(self) -> float:
        return self.total_signal_to_entry_ms / max(1, self.entries_filled)

    def as_dict(self) -> dict:
        return {
            "entries_attempted": self.entries_attempted,
            "entries_filled": self.entries_filled,
            "entries_rejected": self.entries_rejected,
            "avg_slippage": round(self.avg_slippage(), 4),
            "avg_book_age_ms": round(self.avg_book_age_ms(), 1),
            "avg_signal_to_entry_ms": round(self.avg_signal_to_entry_ms(), 1),
            "stale_book_entries": self.stale_book_entries,
            "maker_fills": self.maker_fills,
            "resolution_wins": self.resolution_wins,
            "resolution_losses": self.resolution_losses,
        }


class Executor:
    """
    Handles trade execution in both paper and live modes.
    """

    def __init__(
        self,
        config: SniperConfig,
        ws_mgr: OrderbookManager,
        positions: PositionManager,
        lifecycle: MarketLifecycleManager,
        circuit_breaker: CircuitBreaker,
    ):
        self.config = config
        self._ws_mgr = ws_mgr
        self._positions = positions
        self._lifecycle = lifecycle
        self._cb = circuit_breaker
        self.metrics = ExecutionMetrics()

        # Event log for dashboard
        self.events: list[dict] = []
        
        # Anti-spam for skipped trades
        self._last_skipped_time: dict[str, float] = {}

    def on_rejected_signal(self, signal: Signal) -> None:
        """Handle signals rejected by the engine, logging them as skipped ghost trades."""
        # Anti-spam: max 1 skipped trade per token every 5 seconds
        now = time.time()
        if now - self._last_skipped_time.get(signal.token_id, 0) < 5.0:
            return
            
        self._last_skipped_time[signal.token_id] = now
        
        pos = Position(
            id=f"S-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}",
            asset=signal.asset,
            direction=signal.direction,
            token_id=signal.token_id,
            entry_price=signal.best_ask,
            entry_time=time.time(),
            shares=0,
            stake=self.config.stake,
            tp_price=0,
            status="SKIPPED",
            engine=signal.engine,
            elapsed_at_entry_s=signal.elapsed_at_entry_s,
            filters_passed=signal.filters_passed,
            filters_failed=signal.filters_failed,
            reject_reason=signal.reject_reason,
            market_context={
                "spread": signal.spread,
                "imbalance": signal.imbalance,
                "bid_depth": signal.bid_depth,
                "ask_depth": signal.ask_depth,
                "velocity": signal.ask_velocity
            }
        )
        self._positions.record_skipped(pos)

    def on_signal(self, signal: Signal) -> None:
        """
        Called by signal_engine when a signal passes all gates.
        This is the execution decision point.
        """
        cfg = self.config
        self.metrics.entries_attempted += 1

        # ── Circuit breaker check ────────────────────────────────
        state = TradingState(
            consecutive_losses=self._positions.consecutive_losses,
            total_pnl=self._positions.total_pnl,
            starting_capital=cfg.capital,
            open_positions=self._positions.open_count,
            last_signal_time=0,
        )
        halted, reason = self._cb.should_halt(state)
        if halted:
            self._log_event("CIRCUIT_BREAKER", signal.asset, reason)
            return

        if not self._cb.can_open(self._positions.open_count):
            self.metrics.entries_rejected += 1
            self._log_event("REJECTED", signal.asset, "MAX_POSITIONS")
            return

        # ── Already have position for this asset? ────────────────
        existing = self._positions.get_by_asset(signal.asset)
        if existing:
            self.metrics.entries_rejected += 1
            return

        # ── Capital check ────────────────────────────────────────
        available = cfg.capital + self._positions.total_pnl - \
            sum(p.stake for p in self._positions.get_open())
        if available < cfg.stake:
            self.metrics.entries_rejected += 1
            self._log_event("REJECTED", signal.asset, f"NO_CAPITAL (${available:.2f})")
            return

        # ── Get fresh book for TP calculation ────────────────────
        book = self._ws_mgr.get_book(signal.token_id)
        if not book:
            self.metrics.entries_rejected += 1
            self._log_event("REJECTED", signal.asset, "NO_BOOK")
            return

        # ── Execute entry ────────────────────────────────────────
        t_exec_start = time.time()

        if cfg.mode == "PAPER":
            result = self._paper_entry(signal, book)
        else:
            result = self._live_entry(signal, book)

        if not result:
            self.metrics.entries_rejected += 1
            return

        # ── Record execution metrics ─────────────────────────────
        signal_to_entry_ms = (time.time() - signal.timestamp) * 1000
        self.metrics.entries_filled += 1
        self.metrics.total_book_age_at_entry_ms += book.age_ms
        self.metrics.total_signal_to_entry_ms += signal_to_entry_ms
        if book.age_ms > 500:
            self.metrics.stale_book_entries += 1

        self._lifecycle.mark_entry(signal.asset)
        
        # Add new telemetry fields
        result.engine = signal.engine
        result.elapsed_at_entry_s = signal.elapsed_at_entry_s
        result.filters_passed = signal.filters_passed
        result.filters_failed = signal.filters_failed
        
        counter_str = f" (vs {signal.counterpart_ask:.4f})" if signal.counterpart_ask > 0 else ""
        self._log_event("ENTRY", signal.asset,
                        f"{signal.direction} @ ${result.entry_price:.4f}{counter_str} → TP ${result.tp_price:.4f}")

    def _update_price_paths(self, pos: Position, book: BookSnapshot) -> None:
        """Update time-based price paths and first adverse move."""
        time_alive = pos.time_alive_s
        unrealized = pos.unrealized_pnl(book.best_bid)
        
        if unrealized < 0 and pos.first_adverse_move_s is None:
            pos.first_adverse_move_s = round(time_alive, 1)
            
        if time_alive >= 10.0 and pos.price_at_10s is None:
            pos.price_at_10s = book.best_bid
        if time_alive >= 30.0 and pos.price_at_30s is None:
            pos.price_at_30s = book.best_bid
        if time_alive >= 60.0 and pos.price_at_60s is None:
            pos.price_at_60s = book.best_bid
        if time_alive >= 120.0 and pos.price_at_120s is None:
            pos.price_at_120s = book.best_bid

    def check_maker_fills(self) -> None:
        """
        Called periodically (or on every tick) to check if any
        maker exit orders have been filled based on book state.
        """
        for pos in self._positions.get_open():
            if pos.status == "CLOSED":
                continue

            book = self._ws_mgr.get_book(pos.token_id)
            
            if not book:
                book = self._ws_mgr._snapshots.get(pos.token_id)

            if book:
                self._update_price_paths(pos, book)

            # ── Check lifecycle for resolution ────────────────────
            state = self._lifecycle.get(pos.asset)
            
            # A position is resolved if lifecycle says so, OR if it's older than 310 seconds
            is_resolved = (state and state.phase() == Phase.RESOLVED) or pos.time_alive_s > 310

            if not book:
                if is_resolved:
                    self._positions.close_position(pos.id, 0.0, "RESOLUTION_LOSS")
                    self.metrics.resolution_losses += 1
                    self._log_event("RESOLUTION_LOSS", pos.asset, "Market ended, no final book data")
                elif pos.time_alive_s > 600:
                    self._positions.close_position(pos.id, 0.0, "FORCE_CLOSED_OFFLINE")
                    self.metrics.resolution_losses += 1
                    self._log_event("FORCE_CLOSED", pos.asset, "Market expired without any book data")
                continue
            
            # A position is resolved if lifecycle says so, OR if it's older than 310 seconds
            # (5 minutes + 10s grace period) because lifecycle is overwritten by new markets.
            is_resolved = (state and state.phase() == Phase.RESOLVED) or pos.time_alive_s > 310

            if is_resolved:
                # Market ended — check if it resolved in our favor
                if book.best_bid >= 0.95:
                    # Resolved YES — we win
                    self._positions.close_position(pos.id, 1.0, "RESOLUTION_WIN")
                    self.metrics.resolution_wins += 1
                    self._log_event("RESOLUTION_WIN", pos.asset, f"Resolved YES, PnL +${pos.shares * (1.0 - pos.entry_price):.2f}")
                elif book.best_ask <= 0.05 or book.best_bid == 0.0:
                    # Resolved NO — we lose
                    self._positions.close_position(pos.id, 0.0, "RESOLUTION_LOSS")
                    self.metrics.resolution_losses += 1
                    self._log_event("RESOLUTION_LOSS", pos.asset, f"Resolved NO, PnL -${pos.stake:.2f}")
                else:
                    # Ambiguous — check bid vs entry
                    if book.best_bid > pos.entry_price:
                        self._positions.close_position(pos.id, book.best_bid, "RESOLUTION_WIN")
                        self.metrics.resolution_wins += 1
                        self._log_event("RESOLUTION_WIN", pos.asset, 
                                        f"Resolved YES (Max seen: ${pos.max_favorable_price:.4f})")
                    else:
                        self._positions.close_position(pos.id, book.best_bid, "RESOLUTION_LOSS")
                        self.metrics.resolution_losses += 1
                        self._log_event("RESOLUTION_LOSS", pos.asset, 
                                        f"Resolved NO (Max seen: ${pos.max_favorable_price:.4f})")
                continue

            # ── Mark awaiting resolution if in exit window ────────
            if state and state.is_in_exit_window():
                self._positions.set_awaiting_resolution(pos.id)

            # ── Check maker fill (paper mode) ─────────────────────
            if self.config.mode == "PAPER":
                self._check_paper_maker_fill(pos, book)
            else:
                self._check_live_maker_fill(pos)

    def on_book_tick_for_fills(self, token_id: str, book: BookSnapshot) -> None:
        """Registered as WS callback to check maker fills on every tick."""
        pos = self._positions.get_by_token(token_id)
        if not pos:
            return

        # Track high water mark
        if book.best_bid > pos.max_favorable_price:
            pos.max_favorable_price = book.best_bid
            
        self._update_price_paths(pos, book)

        if self.config.mode == "PAPER":
            self._check_paper_maker_fill(pos, book)
        # Live mode checks via CLOB API, not on every tick

    # ── Paper Mode ────────────────────────────────────────────

    def _paper_entry(self, signal: Signal, book: BookSnapshot) -> Position | None:
        """Simulate taker entry with slippage model."""
        cfg = self.config

        # Slippage model: base + spread-proportional
        slippage = cfg.paper_slippage_base + (book.spread * cfg.paper_slippage_spread_mult)
        fill_price = round(signal.best_ask + slippage, 4)

        # Calculate shares
        shares = round(cfg.stake / fill_price, 4)

        # Dynamic TP from book
        tp_price = self._compute_dynamic_tp(fill_price, book)

        pos = Position(
            id=f"P-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}",
            asset=signal.asset,
            direction=signal.direction,
            token_id=signal.token_id,
            entry_price=fill_price,
            entry_time=time.time(),
            shares=shares,
            stake=cfg.stake,
            tp_price=tp_price,
            slippage=slippage,
            book_age_at_entry_ms=book.age_ms,
            signal_to_entry_ms=(time.time() - signal.timestamp) * 1000,
            market_context={
                "spread": signal.spread,
                "imbalance": signal.imbalance,
                "bid_depth": signal.bid_depth,
                "ask_depth": signal.ask_depth,
                "velocity": signal.ask_velocity
            }
        )
        self.metrics.total_slippage += slippage
        self._positions.open_position(pos)
        return pos

    def _check_paper_maker_fill(self, pos: Position, book: BookSnapshot) -> None:
        """
        Paper maker fill — CONSERVATIVE:
        Fill only counted when best_bid >= tp_price AND
        bid depth at TP level >= shares (if conservative mode on).
        """
        if self.config.hold_to_resolution:
            return

        if not pos.is_open or pos.status == "CLOSED":
            return

        if book.best_bid >= pos.tp_price:
            # Check depth if conservative mode
            if self.config.paper_maker_fill_conservative:
                # Sum bid depth at TP level or above
                depth_at_tp = sum(s for p, s in book.bids if p >= pos.tp_price)
                if depth_at_tp < pos.shares:
                    return  # Not enough depth — don't fill

            self._positions.close_position(pos.id, pos.tp_price, "MAKER")
            self.metrics.maker_fills += 1
            self._log_event("MAKER_FILL", pos.asset,
                           f"Filled @ ${pos.tp_price:.4f} ({pos.time_alive_s:.0f}s, Max: ${pos.max_favorable_price:.4f})")

    # ── Live Mode ─────────────────────────────────────────────

    def _live_entry(self, signal: Signal, book: BookSnapshot) -> Position | None:
        """Execute live CLOB buy via py-clob-client-v2."""
        try:
            from scalper.live_client import buy_outcome, init_live_client, is_live

            if not is_live():
                init_live_client()

            result = buy_outcome(
                token_id=signal.token_id,
                price=signal.best_ask,
                size=self.config.stake,
                asset=signal.asset,
                side=signal.direction,
            )
            if not result or not result.get("success"):
                self._log_event("LIVE_REJECT", signal.asset, str(result))
                return None

            shares = result.get("shares", self.config.stake / signal.best_ask)
            entry_price = result.get("actual_entry_price", signal.best_ask)
            tp_price = self._compute_dynamic_tp(entry_price, book)

            # Place maker exit
            order_id = None
            if not self.config.hold_to_resolution:
                from scalper.live_client import place_maker_limit_sell
                order_id = place_maker_limit_sell(signal.token_id, shares, tp_price)

            pos = Position(
                id=f"L-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}",
                asset=signal.asset,
                direction=signal.direction,
                token_id=signal.token_id,
                entry_price=entry_price,
                entry_time=time.time(),
                shares=shares,
                stake=self.config.stake,
                tp_price=tp_price,
                tp_order_id=order_id,
                book_age_at_entry_ms=book.age_ms,
                signal_to_entry_ms=(time.time() - signal.timestamp) * 1000,
                market_context={
                    "spread": signal.spread,
                    "imbalance": signal.imbalance,
                    "bid_depth": signal.bid_depth,
                    "ask_depth": signal.ask_depth,
                    "velocity": signal.ask_velocity
                }
            )
            self._positions.open_position(pos)
            return pos

        except Exception as e:
            logger.error("Live entry failed: %s", e)
            self._log_event("LIVE_ERROR", signal.asset, str(e))
            return None

    def _check_live_maker_fill(self, pos: Position) -> None:
        """Check CLOB order status for live maker fills."""
        if not pos.tp_order_id:
            return
        try:
            from scalper.live_client import get_maker_order_status
            pos.fill_attempts += 1
            status = get_maker_order_status(pos.tp_order_id)
            if status and status.get("status") == "matched":
                self._positions.close_position(pos.id, pos.tp_price, "MAKER")
                self.metrics.maker_fills += 1
                self._log_event("MAKER_FILL", pos.asset,
                               f"LIVE filled @ ${pos.tp_price:.4f}")
        except Exception as e:
            logger.error("Failed to check maker order: %s", e)

    # ── Dynamic TP ────────────────────────────────────────────

    def _compute_dynamic_tp(self, entry_price: float, book: BookSnapshot) -> float:
        """
        TP = nearest liquidity wall above entry on the ask side.
        Falls back to incremental TP if no wall found.
        """
        cfg = self.config
        if cfg.hold_to_resolution:
            return 1.0

        min_tp = entry_price + cfg.min_tp_increment

        # Walk the ask book looking for a wall
        cumulative = 0.0
        for price, size in book.asks:
            if price <= min_tp:
                continue
            if price > cfg.max_tp:
                break
            cumulative += size
            if size >= cfg.wall_threshold or cumulative >= cfg.wall_threshold * 1.5:
                # Park just below the wall
                tp = round(price - 0.01, 4)
                return max(tp, min_tp)

        # No wall found — use fallback
        return cfg.tp_for_entry(entry_price)

    # ── Helpers ───────────────────────────────────────────────

    def _log_event(self, event_type: str, asset: str, detail: str) -> None:
        self.events.append({
            "ts": time.time(),
            "type": event_type,
            "asset": asset,
            "detail": detail,
        })
        # Keep last 50 events
        if len(self.events) > 50:
            self.events = self.events[-50:]
