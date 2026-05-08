"""
scalper/runner.py — Loop principal del bot HFT de scalping.

Ciclo cada ~30 segundos:
  1. SCAN   → Descubrir mercados activos de 5m
  2. SIGNAL → Computar señales técnicas (Binance 1m klines)
  3. DECIDE → Entrar trades si |signal| > threshold
  4. MONITOR → Verificar posiciones abiertas (profit/reversal/resolution)
  5. DISPLAY → Actualizar dashboard en terminal
  6. SLEEP  → Esperar hasta el próximo ciclo
"""

import io
import logging
import time
import sys
from datetime import datetime, timezone

# ── Force UTF-8 on Windows for emoji and box-drawing ─────────
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import scalper.config as _cfg
from scalper.config import (
    HFT_ASSETS,
    HFT_MAX_SPREAD,
    HFT_SIGNAL_THRESHOLD,
    HFT_TRADEABLE_ASSETS,
)
from scalper.display import (
    print_cycle_separator,
    print_hindsight,
    print_hindsight_summary,
    print_market_status,
    print_no_signal_msg,
    print_open_positions,
    print_recent_trades,
    print_scalper_banner,
    print_session_header,
    print_session_stop,
    print_trade_action,
)
from scalper.market_scanner import scan_active_markets
from scalper.signals import compute_all_signals
from scalper.trader import (
    can_open_trade,
    check_gain_protection,
    check_open_positions,
    get_gain_protection_stop,
    get_hindsight_summary,
    get_open_positions,
    get_recent_resolved,
    get_session_stats,
    load_trades,
    open_trade,
    review_sold_trades,
    update_peak_capital,
)

logger = logging.getLogger("polybot.scalper.runner")


def _is_in_entry_window(market: dict, max_elapsed: int = 210, duration_minutes: int = 5) -> bool:
    """
    Check if a market is tradeable right now.

    Entry is allowed when:
    1. Market is UPCOMING and starts within `duration_minutes` (e.g. 300s or 900s)
    2. Market is IN PROGRESS and less than `max_elapsed` seconds have elapsed
    3. Market must be accepting orders
    """
    if not market.get("accepting_orders", False):
        return False

    time_to_start = market.get("time_to_start_sec", 99999)
    is_in_progress = market.get("is_in_progress", False)

    # If market is currently in progress, enforce profile entry window
    if is_in_progress:
        event_start = market.get("event_start")
        if event_start:
            now = datetime.now(timezone.utc)
            elapsed = (now - event_start).total_seconds()
            return elapsed < max_elapsed
        return False

    # Upcoming market: enter if it starts within duration
    duration_seconds = duration_minutes * 60
    return 0 < time_to_start <= duration_seconds


def _run_single_cycle(
    cycle_num: int,
    target_assets: dict | None = None,
    duration_minutes: int = 5,
) -> bool:
    """
    Execute a single polling cycle.

    Returns False if session stop-loss is hit (should stop).
    """
    assets = target_assets or HFT_ASSETS
    trades = load_trades()

    print_cycle_separator(cycle_num)

    # ── 1. SCAN: Find active markets ─────────────────────────
    try:
        markets = scan_active_markets(assets, duration_minutes=duration_minutes)
    except Exception as exc:
        logger.error("Market scan failed: %s", exc)
        markets = {}

    if not markets:
        print("  📡 No se encontraron mercados activos. Esperando...\n")

    # ── 2. SIGNAL: Compute technical signals ─────────────────
    try:
        signals = compute_all_signals(assets)
    except Exception as exc:
        logger.error("Signal computation failed: %s", exc)
        signals = {}

    # ── 3. Display market status ─────────────────────────────
    print_market_status(markets, signals)

    # ── 4. Check session stats ───────────────────────────────
    stats = get_session_stats(trades)
    print_session_header(stats)

    # Check stop-loss
    can_trade, reason = can_open_trade(trades)
    if not can_trade and "stop-loss" in reason.lower():
        print_session_stop()
        return False

    # ── 5. MONITOR: Check open positions ─────────────────────
    signal_scores = {}
    for asset_key, sig in signals.items():
        signal_scores[asset_key] = sig.score

    from scalper.trader import sync_trade_history
    sync_actions = sync_trade_history()
    for action in sync_actions:
        print_trade_action(action["type"], action["trade"])

    actions = check_open_positions(signal_scores)
    for action in actions:
        print_trade_action(action["type"], action["trade"])

    # ── 6. DECIDE: Open new trades ───────────────────────────
    entries_made = 0

    for asset_key in assets:
        if asset_key not in markets or asset_key not in signals:
            continue

        # Asset liquidity filter
        if asset_key not in HFT_TRADEABLE_ASSETS:
            continue

        market = markets[asset_key]
        signal = signals[asset_key]

        # Check if signal is strong enough
        if abs(signal.score) < HFT_SIGNAL_THRESHOLD:
            continue

        # Check if we're in the entry window
        if not _is_in_entry_window(market):
            continue

        # Check if we can open more trades
        ok, reason = can_open_trade()
        if not ok:
            logger.debug("Cannot trade %s: %s", asset_key, reason)
            continue

        # Determine side and entry price
        side = signal.direction  # "UP" or "DOWN"
        if side == "NEUTRAL":
            continue

        # Get the correct CLOB token ID for this side
        token_id = market.get(f"{side.lower()}_token_id", "")

        # REST pre-entry check: verify bilateral book + spread
        if token_id:
            from scalper.live_client import check_entry_conditions
            entry_check = check_entry_conditions(token_id, max_spread=HFT_MAX_SPREAD, asset=asset_key, side=side)
            if not entry_check["can_enter"]:
                print(f"  [REST CHECK] {asset_key} {side}: {entry_check['reason']} -> SKIP")
                continue
            # Use REST best_ask as real entry price (replaces stale Gamma)
            entry_price = entry_check["best_ask"]
            print(f"  [REST CHECK] {asset_key} {side}: {entry_check['reason']}")
        else:
            # Fallback: Gamma price (no token_id for REST)
            if side == "UP":
                entry_price = market.get("up_best_ask", market.get("up_price", 0.5))
                if entry_price <= 0:
                    entry_price = market.get("up_price", 0.5) + 0.01
            else:
                entry_price = market.get("down_best_ask", market.get("down_price", 0.5))
                if entry_price <= 0:
                    entry_price = market.get("down_price", 0.5) + 0.01

        # Sanity check: don't buy at extreme prices
        if entry_price >= 0.95 or entry_price <= 0.05:
            logger.debug("Skipping %s: entry price %.4f too extreme", asset_key, entry_price)
            continue

        # Open the trade
        trade = open_trade(
            asset=asset_key,
            side=side,
            entry_price=entry_price,
            signal_score=signal.score,
            market_slug=market["slug"],
            gamma_id=market["gamma_id"],
            event_start=market["event_start"],
            event_end=market["event_end"],
            token_id=token_id,
        )

        if trade:
            print_trade_action("entry", trade)
            entries_made += 1

    if entries_made == 0 and not actions:
        print_no_signal_msg()

    # ── 7. Display positions and history ─────────────────────
    open_pos = get_open_positions()
    print_open_positions(open_pos, markets)

    recent = get_recent_resolved(limit=5)
    print_recent_trades(recent)

    # ── 8. HINDSIGHT: Review sold trades after market closes ─
    hindsight_results = review_sold_trades()
    print_hindsight(hindsight_results)

    # ── 9. HINDSIGHT SUMMARY: Aggregate sell vs hold ────────
    hs_summary = get_hindsight_summary()
    print_hindsight_summary(hs_summary)

    # ── 10. LATENCY: Show pipeline latency diagnostics ──────
    try:
        from scalper.latency import format_latency_display
        print(f"\n{format_latency_display()}")
    except ImportError:
        pass

    return True


def _run_single_cycle_profiled(
    cycle_num: int,
    target_assets: dict,
    profile,
    chainlink_monitor=None,
    tick_manager=None,
    duration_minutes: int = 5,
) -> bool:
    """
    Execute a single polling cycle with strategy profile (V2/V3/V4).

    Routes signal computation, entry window, sizing, and exit logic
    based on the active StrategyProfile.
    """
    from scalper.chainlink_delta import compute_all_signals_chainlink
    from scalper.signals_v2 import compute_all_signals_v2
    from scalper.trader import (
        calculate_kelly_stake,
        check_open_positions_profiled,
        set_active_trades_file,
    )

    assets = target_assets or HFT_ASSETS
    trades = load_trades()

    print_cycle_separator(cycle_num)

    # ── 1. SCAN ──────────────────────────────────────────────
    try:
        markets = scan_active_markets(assets, duration_minutes=duration_minutes)
    except Exception as exc:
        logger.error("Market scan failed: %s", exc)
        markets = {}

    if not markets:
        print("  📡 No se encontraron mercados activos. Esperando...\n")

    # Subscribe ONLY current market + open position + NEXT market tokens to WS.
    # Uses replace_subscriptions to CLEAR stale tokens from resolved markets.
    # Includes next-market tokens for proactive pre-warming (velocity from second 0).
    all_needed_tokens = []

    # Current market tokens
    if markets:
        for m in markets.values():
            up_tid = m.get("up_token_id", "")
            dn_tid = m.get("down_token_id", "")
            if up_tid:
                all_needed_tokens.append(up_tid)
            if dn_tid:
                all_needed_tokens.append(dn_tid)

    # Open position tokens (may differ from current market if position spans cycles)
    open_trades = [t for t in trades if t.get("status") == "open"]
    for t in open_trades:
        tid = t.get("token_id", "")
        if tid and tid not in all_needed_tokens:
            all_needed_tokens.append(tid)

    # Proactive: prefetch NEXT market's tokens when next slot is <90s away.
    # Only runs in the last ~90s of each 5-min window to avoid wasting API calls.
    # Pre-warms the WS buffer so velocity is non-zero from cycle start.
    import time as _time
    _now_ts = int(_time.time())
    _slot_sec = duration_minutes * 60
    _next_slot = ((_now_ts // _slot_sec) + 1) * _slot_sec
    _secs_to_next = _next_slot - _now_ts

    if _secs_to_next <= 90:  # Only prefetch in the last 90s
        try:
            from scalper.market_scanner import prefetch_next_market_tokens
            next_tokens = prefetch_next_market_tokens(
                assets=assets, duration_minutes=duration_minutes,
            )
            for tid in next_tokens:
                if tid and tid not in all_needed_tokens:
                    all_needed_tokens.append(tid)
            if next_tokens:
                print(f"  [WS] Pre-warming {len(next_tokens)} tokens for next cycle ({_secs_to_next}s away)")
        except Exception:
            pass

    if all_needed_tokens:
        try:
            from scalper.orderbook_ws import replace_subscriptions
            replace_subscriptions(all_needed_tokens)
        except Exception:
            pass

    # ── 2. SIGNAL: Route by profile ──────────────────────────
    try:
        if profile.signal_source == "technical_v2":
            signals = compute_all_signals_v2(assets)
        elif profile.signal_source == "chainlink_delta" and chainlink_monitor:
            # V3: single update per cycle — buffer accumulates across cycles
            chainlink_monitor.update_all(list(assets.keys()))

            # Get technical signals for confirmation (if enabled)
            tech_signals = None
            if profile.use_technical_confirmation:
                tech_signals = compute_all_signals(assets)

            signals = compute_all_signals_chainlink(
                monitor=chainlink_monitor,
                assets=assets,
                threshold=profile.chainlink_delta_threshold,
                technical_signals=tech_signals,
                require_confirmation=profile.use_technical_confirmation,
            )

            # ── V3 Diagnostics ──────────────────────────────────
            for asset_key in assets:
                delta_info = chainlink_monitor.get_delta(asset_key)
                if delta_info:
                    sig = signals.get(asset_key)
                    sig_score = f"{sig.score:+.3f}" if sig else "none"
                    sig_dir = sig.direction if sig else "N/A"
                    passed = "PASS" if abs(delta_info["avg_delta_pct"]) >= profile.chainlink_delta_threshold else "BELOW"
                    print(
                        f"  [V3-DIAG] {asset_key} | delta={delta_info['avg_delta_pct']:+.4f}% "
                        f"| threshold={profile.chainlink_delta_threshold}% | {passed} "
                        f"| score={sig_score} dir={sig_dir} "
                        f"| sustained={delta_info['sustained']} readings={delta_info['readings_count']}"
                    )
        elif profile.signal_source == "poly_velocity":
            from scalper.signals_v4 import compute_all_signals_poly_velocity

            signals = compute_all_signals_poly_velocity(
                assets=assets,
                markets=markets,
                tick_manager=tick_manager,  # Optional Binance confirmation
            )
        elif profile.signal_source == "ticks_v4" and tick_manager:
            from scalper.signals_v4 import compute_all_signals_v4

            # Log warmup status
            warmup = tick_manager.get_warmup_status()
            any_cold = False
            for a, st in warmup.items():
                if not st["warm"]:
                    any_cold = True
                    print(f"  [V4-WARMUP] {a}: {st['ticks']}/{st['needed']} ticks (fallback to klines)")
            if not any_cold and cycle_num <= 3:
                print("  [V4] All WebSocket streams warm - OK")

            signals = compute_all_signals_v4(
                tick_manager=tick_manager,
                assets=assets,
                markets=markets,
            )
        elif profile.signal_source == "mean_reversion_v8":
            from scalper.signals_v8 import compute_all_signals_v8
            signals = compute_all_signals_v8(
                assets=assets,
                markets=markets,
                regime_window=getattr(profile, 'regime_window', 5),
                regime_threshold=getattr(profile, 'regime_trending_threshold', 4),
                reversion_threshold=getattr(profile, 'reversion_threshold', 0.008),
                ma_window_sec=getattr(profile, 'ma_window_sec', 60),
            )
        else:
            signals = compute_all_signals(assets)
    except Exception as exc:
        logger.error("Signal computation failed: %s", exc)
        signals = {}

    # ── 3. Display ───────────────────────────────────────────
    print_market_status(markets, signals)

    stats = get_session_stats(trades)
    print_session_header(stats)

    can_trade_ok, reason = can_open_trade(trades)
    if not can_trade_ok and "stop-loss" in reason.lower():
        print_session_stop()
        return False

    # ── 4. MONITOR with profile ──────────────────────────────
    from scalper.trader import sync_trade_history
    sync_actions = sync_trade_history()
    for action in sync_actions:
        print_trade_action(action["type"], action["trade"])


    signal_scores = {k: s.score for k, s in signals.items()}
    actions = check_open_positions_profiled(signal_scores, profile=profile, markets_data=markets)
    for action in actions:
        print_trade_action(action["type"], action["trade"])

    # ── 5. ENTRY with profile rules ──────────────────────────
    entries_made = 0

    # Position limit: how many more can we open?
    current_open = len([t for t in trades if t["status"] == "open"])
    slots_available = profile.max_open_positions - current_open

    if slots_available <= 0:
        print(f"  📊 Max positions reached ({profile.max_open_positions}). No new entries.")

    # Build candidate list (assets that pass threshold)
    candidates = []
    for asset_key in assets:
        if asset_key not in markets or asset_key not in signals:
            continue
        # Asset liquidity filter
        if asset_key not in HFT_TRADEABLE_ASSETS:
            continue
        signal = signals[asset_key]
        if abs(signal.score) >= profile.signal_threshold:
            candidates.append((asset_key, signal))
        else:
            # Add debug for why signal failed (only print if score is notable or we are V7 to avoid spam, or just print at trace level. Actually, if we print every cycle it's spammy. We will print if score > 0.05)
            if abs(signal.score) > 0.05:
                print(f"  [SKIP] {asset_key}: Señal débil (score={signal.score:+.3f} < threshold={profile.signal_threshold})")

    # Best signal only: sort by strength and limit to available slots
    if profile.best_signal_only and len(candidates) > 1:
        candidates.sort(key=lambda x: abs(x[1].score), reverse=True)
        if len(candidates) > slots_available:
            skipped = [c[0] for c in candidates[slots_available:]]
            candidates = candidates[:max(slots_available, 0)]
            if skipped:
                print(f"  [SKIP] {', '.join(skipped)}: Eliminados por filtro best_signal_only")

    for asset_key, signal in candidates:
        if entries_made >= slots_available:
            break

        market = markets[asset_key]
        time_multiplier = duration_minutes / 5.0

        # Entry window check
        if profile.entry_mode == "late":
            scaled_start = profile.entry_window_start * time_multiplier
            scaled_end = profile.entry_window_end * time_multiplier

            event_start = market.get("event_start")
            if event_start:
                now = datetime.now(timezone.utc)
                elapsed = (now - event_start).total_seconds()
            else:
                elapsed = scaled_start + 1

            if elapsed < scaled_start or elapsed > scaled_end:
                print(f"  [SKIP] {asset_key}: elapsed {elapsed:.0f}s fuera de ventana {scaled_start:.0f}-{scaled_end:.0f}s")
                continue
        else:
            scaled_end = getattr(profile, "entry_window_end", 210) * time_multiplier
            if not _is_in_entry_window(market, max_elapsed=scaled_end, duration_minutes=duration_minutes):
                print(f"  [SKIP] {asset_key}: fuera de ventana de entrada genérica")
                continue

        ok, reason = can_open_trade()
        if not ok:
            continue

        side = signal.direction
        if side == "NEUTRAL":
            continue

        # ── Smart Inversion (Falling Knife Protection) ─────────────────
        smart_invert_thresh = getattr(profile, "smart_invert_threshold", 0.0)
        if smart_invert_thresh > 0:
            orig_price = market.get("up_price", 0.0) if side == "UP" else market.get("down_price", 0.0)
            if 0 < orig_price < smart_invert_thresh:
                old_side = side
                side = "DOWN" if side == "UP" else "UP"
                signal.direction = side
                signal.score = -signal.score
                print(f"  [SMART-INVERT] {asset_key}: Precio original ${orig_price:.2f} < ${smart_invert_thresh:.2f}. ¡Señal invertida a {side}!")

        # Polymarket price filter
        if profile.poly_price_filter:
            directional_price = market.get("up_price", 0.5) if side == "UP" else market.get("down_price", 0.5)
            if directional_price > profile.poly_price_cap:
                print(f"  [SKIP] {asset_key} {side}: precio ${directional_price:.2f} > cap ${profile.poly_price_cap:.2f}")
                continue

            min_price = getattr(profile, "min_entry_price", 0.0)
            if min_price > 0 and directional_price < min_price:
                print(f"  [SKIP] {asset_key} {side}: precio ${directional_price:.2f} < floor ${min_price:.2f}")
                continue

        # ── Score ceiling: block momentum-exhaustion entries ─────────────
        max_score = getattr(profile, "max_signal_score", 1.0)
        if abs(signal.score) > max_score:
            print(
                f"  [SCORE-CEIL] {asset_key} {side}: "
                f"|score|={abs(signal.score):.3f} > ceiling {max_score:.2f} (momentum exhausted) -> SKIP"
            )
            continue

        # Entry price + REST pre-entry check
        # Get the correct CLOB token ID for this side
        token_id = market.get(f"{side.lower()}_token_id", "")

        # ── Velocity confirmation gate ─────────────────────────────────
        if getattr(profile, "velocity_confirmation", False) and token_id:
            from scalper.orderbook_ws import get_mid_velocity
            vel_window = getattr(profile, "velocity_window_sec", 30)
            vel_thresh = getattr(profile, "velocity_threshold", 0.05)
            velocity = get_mid_velocity(token_id, window_sec=vel_window)

            # Only block if velocity STRONGLY DISAGREES with the signal.
            # If velocity is 0.000 (no data), or weakly agrees/disagrees, let it pass.
            vel_blocks = (
                (side == "UP" and velocity <= -vel_thresh)
                or (side == "DOWN" and velocity >= vel_thresh)
            )

            if vel_blocks:
                print(
                    f"  [VELOCITY-BLOCK] {asset_key} {side}: "
                    f"velocity={velocity:+.4f} strongly opposes {side} "
                    f"(thresh={vel_thresh}) -> SKIP"
                )
                continue
            
            if velocity == 0.0:
                print(f"  [VELOCITY-PASS] {asset_key} {side}: velocity=0.000 (no data) -> PASS")
            else:
                print(f"  [VELOCITY-PASS] {asset_key} {side}: velocity={velocity:+.4f} no se opone -> PASS")

        # ── V5 Smart Execution Filters (Soft Penalties) ──────────────
        if token_id and getattr(profile, "penalty_per_failed_filter", 0) > 0:
            from scalper.orderbook_ws import get_imbalance, get_price_change, get_mid_velocity
            
            penalty = 0.0
            failed_filters = []
            
            # 1. Acceleration Decay
            if getattr(profile, "filter_accel_decay", False) and tick_manager:
                vel_15 = tick_manager.get_velocity(asset_key, 15)
                vel_60 = tick_manager.get_velocity(asset_key, 60)
                if vel_60 != 0:
                    # ratio of 15s speed vs 60s speed (normalized to per-second)
                    speed_15 = abs(vel_15 / 15)
                    speed_60 = abs(vel_60 / 60)
                    if speed_60 > 0 and speed_15 / speed_60 < 0.3:
                        penalty += profile.penalty_per_failed_filter
                        failed_filters.append(f"accel_decay({speed_15:.3f} vs {speed_60:.3f})")
            
            # 2. Orderbook Imbalance
            if getattr(profile, "filter_imbalance", False):
                imbalance = get_imbalance(token_id)
                imb_val = imbalance["up_imbalance"] if side == "UP" else imbalance["down_imbalance"]
                if imb_val > 3.0:
                    penalty += profile.penalty_per_failed_filter
                    failed_filters.append(f"imbalance({imb_val:.1f}x)")
            
            # 3. Fake Momentum
            if getattr(profile, "filter_fake_momentum", False) and tick_manager:
                poly_change = get_price_change(token_id, window_sec=120)
                binance_change = tick_manager.get_price_change(asset_key, 120)
                # If poly moved > 5x binance (in the direction of the trade)
                poly_reaction = abs(poly_change)
                bin_reaction = abs(binance_change)
                if poly_reaction > 0 and bin_reaction > 0 and poly_reaction / max(bin_reaction, 0.0001) > 5.0:
                    penalty += profile.penalty_per_failed_filter
                    failed_filters.append(f"fake_mom({poly_reaction:.1%} vs {bin_reaction:.1%})")
            
            # 4. Reversal Detection
            if getattr(profile, "filter_reversal", False):
                vel_15 = get_mid_velocity(token_id, window_sec=15)
                vel_30 = get_mid_velocity(token_id, window_sec=30)
                if (side == "UP" and vel_15 < -0.005 and vel_30 > 0.005) or (side == "DOWN" and vel_15 > 0.005 and vel_30 < -0.005):
                    penalty += profile.penalty_per_failed_filter
                    failed_filters.append(f"reversal({vel_15:+.3f})")
            
            # Apply penalties
            if penalty > 0:
                old_score = signal.score
                # Reduce absolute score towards zero
                if signal.score > 0:
                    signal.score = max(0, signal.score - penalty)
                else:
                    signal.score = min(0, signal.score + penalty)
                
                print(f"  [V5-SMART] {asset_key} {side}: Soft penalty -{penalty:.2f} applied for {','.join(failed_filters)}")
                print(f"  [V5-SMART] {asset_key} {side}: Score adjusted {old_score:+.3f} -> {signal.score:+.3f}")
                
                # Check if it still passes threshold
                if abs(signal.score) < profile.signal_threshold:
                    print(f"  [V5-SMART] {asset_key} {side}: Adjusted score < {profile.signal_threshold} -> SKIP")
                    continue


        if token_id:
            from scalper.live_client import check_entry_conditions
            entry_check = check_entry_conditions(token_id, max_spread=HFT_MAX_SPREAD, asset=asset_key, side=side)
            if not entry_check["can_enter"]:
                print(f"  [REST CHECK] {asset_key} {side}: {entry_check['reason']} -> SKIP")
                continue
            # Use REST best_ask as real entry price (replaces stale Gamma)
            entry_price = entry_check["best_ask"]
            print(f"  [REST CHECK] {asset_key} {side}: {entry_check['reason']}")
        else:
            # Fallback: Gamma price (no token_id)
            if side == "UP":
                entry_price = market.get("up_best_ask", market.get("up_price", 0.5))
                if entry_price <= 0:
                    entry_price = market.get("up_price", 0.5) + 0.01
            else:
                entry_price = market.get("down_best_ask", market.get("down_price", 0.5))
                if entry_price <= 0:
                    entry_price = market.get("down_price", 0.5) + 0.01

        if entry_price >= 0.95 or entry_price <= 0.05:
            continue

        # ── Re-apply profile filters against true REST price ──
        if getattr(profile, "poly_price_filter", False):
            if entry_price > profile.poly_price_cap:
                print(f"  [REST-REJECT] {asset_key} {side}: true price ${entry_price:.2f} > cap ${profile.poly_price_cap:.2f} -> SKIP")
                continue
            min_price = getattr(profile, "min_entry_price", 0.0)
            if min_price > 0 and entry_price < min_price:
                print(f"  [REST-REJECT] {asset_key} {side}: true price ${entry_price:.2f} < floor ${min_price:.2f} -> SKIP")
                continue

        # Sizing
        if profile.sizing == "kelly":
            stake = calculate_kelly_stake(
                profile.base_stake, signal.score,
                stats.get("capital", 1000),
                profile.max_position_pct,
            )
        elif profile.sizing == "delta_scaled":
            delta_magnitude = abs(signal.score)
            if delta_magnitude >= 0.10:
                stake = profile.base_stake * 1.5
            else:
                stake = profile.base_stake
            stake = min(stake, stats.get("capital", 1000) * profile.max_position_pct)
            stake = round(stake, 2)
        else:
            stake = profile.base_stake

        trade = open_trade(
            asset=asset_key,
            side=side,
            entry_price=entry_price,
            signal_score=signal.score,
            market_slug=market["slug"],
            gamma_id=market["gamma_id"],
            event_start=market["event_start"],
            event_end=market["event_end"],
            stake=stake,
            token_id=token_id,
        )

        if trade:
            print_trade_action("entry", trade)
            entries_made += 1

    if entries_made == 0 and not actions:
        print_no_signal_msg()

    # ── 6. Display ───────────────────────────────────────────
    open_pos = get_open_positions()
    print_open_positions(open_pos, markets)

    recent = get_recent_resolved(limit=5)
    print_recent_trades(recent)

    hindsight_results = review_sold_trades()
    print_hindsight(hindsight_results)

    hs_summary = get_hindsight_summary()
    print_hindsight_summary(hs_summary)

    # ── 7. LATENCY: Show pipeline latency diagnostics ──────
    try:
        from scalper.latency import format_latency_display
        print(f"\n{format_latency_display()}")
    except ImportError:
        pass

    return True


def run_scalper(
    target_assets: dict | None = None,
    max_cycles: int | None = None,
    strategy: str = "v1",
    duration_minutes: int = 5,
):
    """
    Main entry point for the HFT scalper bot.

    Args:
        target_assets: Dict of assets to trade (default: all from config)
        max_cycles: Maximum cycles to run (None = infinite)
        strategy: Strategy version — "v1", "v2", or "v3"
    """
    from scalper.strategy_profiles import get_profile
    from scalper.trader import set_active_trades_file

    profile = get_profile(strategy)

    # Set isolated trades file for this strategy
    set_active_trades_file(profile.trades_file)

    # Initialize Chainlink monitor for V3
    chainlink_monitor = None
    if profile.signal_source == "chainlink_delta":
        from scalper.chainlink_delta import ChainlinkDeltaMonitor
        chainlink_monitor = ChainlinkDeltaMonitor()

    # Initialize WebSocket tick manager for V4 and V6 (Binance confirmation)
    tick_manager = None
    if profile.signal_source in ("ticks_v4", "poly_velocity"):
        from scalper.binance_ws import BinanceTickManager
        tick_manager = BinanceTickManager()
        tick_manager.start()

    print_scalper_banner()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"  ⏰ Started: {now_str}")
    print(f"  🏷️  Strategy: {profile.label}")
    print(f"  🎯 Signal Threshold: {profile.signal_threshold}")
    # Sync profile base_stake with any CLI override
    import scalper.config as _cfg
    profile.base_stake = _cfg.HFT_STAKE
    print(f"  💵 Base Stake: ${_cfg.HFT_STAKE:.2f} ({profile.sizing} sizing)")
    print(f"  🔄 Poll interval: {_cfg.HFT_POLL_INTERVAL}s")
    print(f"  📁 Trades file: {profile.trades_file}")

    if profile.trailing_stop:
        print(f"  📈 Trailing stop: ON (trigger at +{profile.trailing_trigger:.0%})")
    print(f"  🎯 TP: {profile.take_profit:.0%} | SL: {profile.stop_loss:.0%}")

    if profile.max_open_positions < 4:
        print(f"  📊 Max positions: {profile.max_open_positions}")
    if profile.best_signal_only:
        print(f"  🎯 Best signal only: ON")
    if profile.poly_price_filter:
        print(f"  💲 Poly price cap: {profile.poly_price_cap:.2f}")

    assets_str = ", ".join((target_assets or HFT_ASSETS).keys())
    print(f"  📊 Assets: {assets_str}")
    print(f"\n  ▶️  Bot en ejecución. Presiona Ctrl+C para detener.\n")

    cycle = 0

    try:
        while True:
            cycle += 1

            if max_cycles and cycle > max_cycles:
                print(f"\n  ⏹️  Máximo de ciclos ({max_cycles}) alcanzado.\n")
                break

            if strategy == "v1":
                should_continue = _run_single_cycle(cycle, target_assets, duration_minutes=duration_minutes)
            else:
                should_continue = _run_single_cycle_profiled(
                    cycle,
                    target_assets=target_assets or HFT_ASSETS,
                    profile=profile,
                    chainlink_monitor=chainlink_monitor,
                    tick_manager=tick_manager,
                    duration_minutes=duration_minutes,
                )

            if not should_continue:
                break

            # ── Gain protection check ──────────────────────────
            stats = get_session_stats()
            current_capital = stats["capital"]
            starting_capital = stats["starting_capital"]

            peak = update_peak_capital(current_capital)
            stop_level = get_gain_protection_stop(starting_capital)

            if stop_level:
                print(f"  🛡️ Peak: ${peak:.2f} | Stop: ${stop_level:.2f} | Current: ${current_capital:.2f}")

            should_stop, reason = check_gain_protection(current_capital, starting_capital)
            if should_stop:
                print(f"\n  {reason}\n")
                break

            print(f"\n  💤 Próximo ciclo en {_cfg.HFT_POLL_INTERVAL}s...")
            time.sleep(_cfg.HFT_POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n  ⛔ Bot detenido por el usuario.\n")
    finally:
        if tick_manager:
            tick_manager.stop()

    # Final report
    print(f"\n{'═' * 80}")
    print(f"  📊 REPORTE FINAL DE SESIÓN — {profile.label}")
    print(f"{'═' * 80}")

    stats = get_session_stats()
    print_session_header(stats)

    recent = get_recent_resolved(limit=20)
    print_recent_trades(recent, limit=20)

    print(f"\n  Trades guardados en: {profile.trades_file}")
    print(f"  Total ciclos ejecutados: {cycle}\n")

