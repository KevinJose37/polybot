"""
main.py — Polymarket Market Maker Bot.
Full async orchestrator: data feeds, fair value, quoting, risk, fills, PnL.
Production-grade paper trading with FV-crossing fill simulation.
"""

import asyncio
import os
import sys
import time
import logging

from loguru import logger
from collections import deque

# Suppress standard library logging noise
logging.getLogger().setLevel(logging.CRITICAL)

from config.settings import config
from data.market_discovery import MarketDiscovery
from data.feeds.binance_ws import BinanceWSFeed
from data.feeds.polymarket_ws import PolymarketFeed
from data.feed_health import FeedHealthMonitor
from core.fair_value import FairValueEngine
from core.quote_engine import QuoteEngine, SuspendQuoting
from core.tau import TauCalculator
from risk.toxicity_monitor import ToxicityMonitor
from risk.circuit_breaker import CircuitBreaker
from risk.inventory_manager import InventoryManager
from risk.exposure import ExposureTracker
from execution.fill_simulator import FillSimulator
from utils.pnl_engine import PnLEngine
from utils.schemas import (
    MarketRuntimeState, MarketState, MarketInfo, InventoryState,
    TradeEvent, QuotePair,
)


# ══════════════════════════════════════════════════════════════
# Global State
# ══════════════════════════════════════════════════════════════

active_markets: dict[str, MarketRuntimeState] = {}
spot_prices: dict[str, float] = {}          # asset -> latest spot price
poly_feeds: dict[str, PolymarketFeed] = {}  # market_key -> PolymarketFeed
latest_logs: deque = deque(maxlen=12)
_start_time: float = 0.0


# ══════════════════════════════════════════════════════════════
# Discovery Worker
# ══════════════════════════════════════════════════════════════

async def discovery_worker(
    discovery: MarketDiscovery,
    inv_manager: InventoryManager,
    fill_simulator: FillSimulator,
):
    """Discover new markets every 30 seconds."""
    while True:
        try:
            discovered = await discovery.discover_all_markets(
                config.assets, config.windows
            )
            for key, market_info in discovered.items():
                if key not in active_markets:
                    # Set strike from current spot price (price at window start)
                    asset = market_info.asset
                    if asset in spot_prices and market_info.strike_price == 0:
                        market_info.strike_price = spot_prices[asset]
                        logger.info(
                            f"[Discovery] Strike set for {key}: ${market_info.strike_price:,.2f}"
                        )

                    active_markets[key] = MarketRuntimeState(
                        market_info=market_info,
                        state=MarketState.INITIALIZING,
                        inventory=inv_manager.get_or_create(
                            key, market_info.asset, market_info.window_minutes,
                            market_info.market_id,
                        ),
                    )
                    logger.info(f"[Discovery] New: {key} | {market_info.slug}")
                elif active_markets[key].market_info.slug != market_info.slug:
                    # Market rotated — set strike from current spot
                    asset = market_info.asset
                    if asset in spot_prices:
                        market_info.strike_price = spot_prices[asset]

                    inv_manager.reset(key)
                    fill_simulator.reset(key)  # Reset FV tracking on rotation
                    active_markets[key] = MarketRuntimeState(
                        market_info=market_info,
                        state=MarketState.INITIALIZING,
                        inventory=inv_manager.get_or_create(
                            key, market_info.asset, market_info.window_minutes,
                            market_info.market_id,
                        ),
                    )
                    logger.info(f"[Discovery] Rotated: {key} -> {market_info.slug}")
                else:
                    # Existing market: set strike if not yet set
                    mrt = active_markets[key]
                    if mrt.market_info.strike_price == 0 and market_info.asset in spot_prices:
                        mrt.market_info.strike_price = spot_prices[market_info.asset]
                        logger.info(
                            f"[Discovery] Strike set (deferred) for {key}: "
                            f"${mrt.market_info.strike_price:,.2f}"
                        )

            await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Discovery] Error: {e}")
            await asyncio.sleep(5.0)


# ══════════════════════════════════════════════════════════════
# Price Feed Callbacks
# ══════════════════════════════════════════════════════════════

async def on_price_update(
    symbol: str, price: float, timestamp_ms: int,
    circuit_breaker: CircuitBreaker,
    feed_health: FeedHealthMonitor,
):
    """Called on each Binance price update."""
    spot_prices[symbol] = price
    feed_health.update(f"binance_{symbol}", timestamp_ms)
    circuit_breaker.update_feed_timestamp(symbol, timestamp_ms)
    circuit_breaker.update_spot_price(symbol, price)


async def on_trade_event(
    trade: TradeEvent,
    toxicity_monitor: ToxicityMonitor,
):
    """
    Called on each Binance trade. Used for toxicity tracking only.
    Fill simulation is now handled in the quote engine via FV crossings.
    """
    asset = trade.symbol.lower()

    # Update toxicity for all active markets of this asset
    for key, mrt in list(active_markets.items()):
        if mrt.market_info.asset != asset:
            continue
        if mrt.state == MarketState.INACTIVE:
            continue
        toxicity_monitor.record_trade(key, trade)


# ══════════════════════════════════════════════════════════════
# Odds Updater Worker
# ══════════════════════════════════════════════════════════════

async def odds_updater_worker():
    """Fetch Polymarket odds for all active markets."""
    while True:
        try:
            for key, mrt in list(active_markets.items()):
                if mrt.state == MarketState.INACTIVE:
                    continue

                mi = mrt.market_info
                if key not in poly_feeds:
                    poly_feeds[key] = PolymarketFeed(
                        mi.token_id_yes, mi.token_id_no, mi.market_id,
                    )

                odds = await poly_feeds[key].get_market_odds()
                if odds:
                    mrt.odds = odds

            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Odds] Error: {e}")
            await asyncio.sleep(5.0)


# ══════════════════════════════════════════════════════════════
# Quote Engine Worker (with integrated fill simulation)
# ══════════════════════════════════════════════════════════════

async def quote_engine_worker(
    fv_engine: FairValueEngine,
    quote_engine: QuoteEngine,
    toxicity_monitor: ToxicityMonitor,
    circuit_breaker: CircuitBreaker,
    inv_manager: InventoryManager,
    fill_simulator: FillSimulator,
    pnl_engine: PnLEngine,
    exposure: ExposureTracker,
):
    """
    Main quoting loop. Recomputes quotes and checks for FV-crossing fills.
    Runs every 500ms.
    """
    cycle_count = 0

    while True:
        try:
            cycle_count += 1
            now_ms = int(time.time() * 1000)

            # Check global circuit breaker
            if circuit_breaker.is_globally_halted():
                if cycle_count % 20 == 0:
                    logger.warning(f"[Quote] Global halt: {circuit_breaker.get_halt_reason()}")
                await asyncio.sleep(1.0)
                continue

            for key, mrt in list(active_markets.items()):
                asset = mrt.market_info.asset
                mi = mrt.market_info

                # Skip inactive markets
                if mrt.state == MarketState.INACTIVE:
                    continue

                # Check circuit breaker
                can_quote, reason = circuit_breaker.can_quote(key, asset)
                if not can_quote:
                    if mrt.state != MarketState.SUSPENDED:
                        mrt.state = MarketState.SUSPENDED
                        mrt.current_quotes = None
                    continue

                # Get spot price
                spot = spot_prices.get(asset)
                if spot is None:
                    continue

                # Compute tau
                tau = TauCalculator.compute_tau(mi.end_date_ts)

                # Get strike price (set during discovery from spot at window start)
                strike = mi.strike_price
                if strike <= 0:
                    continue

                # Compute fair value using Black-Scholes binary option
                fv = fv_engine.compute_fair_value(
                    asset, strike, tau,
                    spot_override=spot,
                )

                if fv is None or fv.is_stale:
                    continue

                mrt.fair_value = fv
                new_fv = fv.probability

                # ── Fill simulation: check if FV crossed our quotes ──
                if mrt.current_quotes is not None:
                    fills = fill_simulator.check_fill_on_fv_update(
                        market_key=key,
                        quotes=mrt.current_quotes,
                        new_fv=new_fv,
                        asset=asset,
                        window_minutes=mi.window_minutes,
                    )
                    for fill in fills:
                        # Update inventory
                        inv_manager.record_fill(key, fill)

                        # Update PnL
                        pnl_engine.record_fill(key, fill)

                        # Update exposure
                        if fill.side == "BUY":
                            exposure.record_buy(fill.price * fill.size)
                        else:
                            exposure.record_sell(fill.price * fill.size)

                        # Track fill
                        mrt.fills.append(fill)
                        mrt.last_fill_ms = fill.timestamp_ms

                        latest_logs.appendleft(
                            f"FILL {key} {fill.side} {fill.size}@{fill.price:.4f} "
                            f"| FV={new_fv:.3f} | net={mrt.inventory.net_position if mrt.inventory else '?'}"
                        )
                        logger.info(
                            f"[Fill] {key} {fill.side} {fill.size}@{fill.price:.4f} "
                            f"| FV={new_fv:.3f} "
                            f"| net={mrt.inventory.net_position if mrt.inventory else '?'}"
                        )
                else:
                    # Initialize FV tracker even if no quotes yet
                    fill_simulator.check_fill_on_fv_update(
                        market_key=key, quotes=QuotePair(0, 0, 0, 0),
                        new_fv=new_fv, asset=asset,
                        window_minutes=mi.window_minutes,
                    )

                # Get toxicity metrics
                tox = toxicity_monitor.get_toxicity(key)
                mrt.toxicity = tox

                # Get book spread from Poly feed
                book_spread = None
                if key in poly_feeds:
                    book_spread = poly_feeds[key].get_book_spread()

                # Get inventory
                inv = inv_manager.get(key)
                if inv is None:
                    inv = inv_manager.get_or_create(key, asset, mi.window_minutes)

                # Compute quotes
                try:
                    quotes = quote_engine.compute_quotes(
                        fair_value=new_fv,
                        inventory=inv,
                        tau_seconds=tau,
                        volatility=fv.volatility,
                        toxicity=tox,
                        book_spread=book_spread,
                    )
                    mrt.current_quotes = quotes
                    mrt.last_quote_update_ms = now_ms

                    # Update market state based on inventory
                    inv_mode = inv_manager.get_quoting_mode(key)
                    if inv_mode == MarketState.EMERGENCY:
                        mrt.state = MarketState.EMERGENCY
                    elif inv_mode == MarketState.ONE_SIDED:
                        mrt.state = MarketState.ONE_SIDED
                    elif toxicity_monitor.is_defensive(key):
                        mrt.state = MarketState.DEFENSIVE
                    else:
                        mrt.state = MarketState.QUOTING_BOTH

                except SuspendQuoting as e:
                    mrt.state = MarketState.SUSPENDED
                    mrt.current_quotes = None

            # Update inventory PnL
            fair_values = {}
            for key, mrt in active_markets.items():
                if mrt.fair_value:
                    fair_values[key] = mrt.fair_value.probability
            inventories = inv_manager.get_all_inventories()
            pnl_engine.update_inventory_pnl(inventories, fair_values)

            # Update portfolio circuit breaker
            pnl = pnl_engine.get_pnl()
            circuit_breaker.update_portfolio_value(
                config.initial_capital + pnl.total_pnl
            )
            circuit_breaker.check_daily_loss()

            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Quote] Error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            await asyncio.sleep(1.0)


# ══════════════════════════════════════════════════════════════
# Dashboard Worker
# ══════════════════════════════════════════════════════════════

def _clear_screen():
    """Clear the terminal screen."""
    if sys.platform == "win32":
        os.system("cls")
    else:
        os.system("clear")


def _format_uptime(start_time: float) -> str:
    """Format elapsed time as HH:MM:SS."""
    elapsed = int(time.time() - start_time)
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


async def dashboard_worker(
    pnl_engine: PnLEngine,
    inv_manager: InventoryManager,
    exposure: ExposureTracker,
    circuit_breaker: CircuitBreaker,
    feed_health: FeedHealthMonitor,
):
    """Print clean dashboard to console every 5 seconds."""
    while True:
        try:
            await asyncio.sleep(5.0)
            pnl = pnl_engine.get_pnl()
            stats = pnl_engine.get_stats()

            n_markets = len(active_markets)
            n_quoting = sum(1 for m in active_markets.values()
                           if m.state in (MarketState.QUOTING_BOTH, MarketState.ONE_SIDED))
            n_suspended = sum(1 for m in active_markets.values()
                             if m.state == MarketState.SUSPENDED)
            n_defensive = sum(1 for m in active_markets.values()
                             if m.state == MarketState.DEFENSIVE)

            mode = "PAPER" if config.paper_trading else "LIVE"
            uptime = _format_uptime(_start_time)

            # ── Clear and redraw ──
            _clear_screen()

            lines = []
            lines.append("=" * 80)
            lines.append(
                f"  POLYMARKET MM | {time.strftime('%H:%M:%S UTC', time.gmtime())} "
                f"| {mode} | Uptime: {uptime} | Capital: ${config.initial_capital:.0f}"
            )
            lines.append("=" * 80)

            # ── Capital & PnL ──
            pnl_sign = "+" if pnl.total_pnl >= 0 else ""
            pnl_pct = (pnl.total_pnl / config.initial_capital * 100) if config.initial_capital > 0 else 0
            lines.append(
                f"  💰 CAPITAL  Available: ${exposure.available_capital:>8.2f} | "
                f"In Use: ${exposure.capital_in_use:>8.2f} | "
                f"Total: ${exposure.total_capital:>8.2f}"
            )
            pnl_emoji = "📈" if pnl.total_pnl >= 0 else "📉"
            lines.append(
                f"  {pnl_emoji} PnL      Total: ${pnl_sign}{pnl.total_pnl:>7.4f} ({pnl_pct:+.2f}%) | "
                f"Realized: ${pnl.realized_pnl:>7.4f} | "
                f"Unrealized: ${pnl.unrealized_pnl:>7.4f}"
            )
            lines.append(
                f"  📊 ATTRIB   Spread: ${pnl.spread_pnl:>7.4f} | "
                f"Inventory: ${pnl.inventory_pnl:>7.4f} | "
                f"Fees: ${pnl.fee_pnl:>7.4f}"
            )
            lines.append("-" * 80)

            # ── Markets ──
            lines.append(
                f"  🌐 MARKETS  Active: {n_quoting} | "
                f"Suspended: {n_suspended} | "
                f"Defensive: {n_defensive} | "
                f"Total: {n_markets}"
            )
            lines.append(
                f"  ⚡ FILLS    Total: {stats['total_fills']} | "
                f"Buys: {stats['buy_fills']} | "
                f"Sells: {stats['sell_fills']} | "
                f"Win Ratio: {stats.get('win_ratio', 0.0):.1f}% 🏆"
            )
            lines.append("-" * 80)

            # ── Per-market table ──
            lines.append(
                f"  {'MARKET':>14}  {'STATE':>8}  {'FV':>6}  "
                f"{'BID':>6} {'ASK':>6} {'SPRD':>5}  "
                f"{'NET':>5} {'UTIL':>10}  {'DIR':>5}"
            )
            for key, mrt in sorted(active_markets.items()):
                inv = mrt.inventory
                if inv is None:
                    continue

                util_pct = inv.utilization * 100
                bar_len = int(min(util_pct, 100) / 10)
                bar = "#" * bar_len + "." * (10 - bar_len)

                fv_str = f"{mrt.fair_value.probability:.3f}" if mrt.fair_value else " --  "
                state = mrt.state.value[:8]

                if mrt.current_quotes:
                    q = mrt.current_quotes
                    bid_str = f"{q.bid_price:.3f}"
                    ask_str = f"{q.ask_price:.3f}"
                    sprd_str = f"{q.spread:.3f}"
                else:
                    bid_str = "  -- "
                    ask_str = "  -- "
                    sprd_str = " -- "

                direction = "LONG" if inv.is_long else ("SHORT" if inv.is_short else "FLAT")

                lines.append(
                    f"  {key:>14}  {state:>8}  {fv_str:>6}  "
                    f"{bid_str:>6} {ask_str:>6} {sprd_str:>5}  "
                    f"{inv.net_position:>+5d} [{bar}]  {direction:>5}"
                )

            lines.append("-" * 80)

            # ── Spot Prices ──
            prices_parts = []
            for a in sorted(spot_prices.keys()):
                p = spot_prices[a]
                name = a.replace("usdt", "").upper()
                if p >= 1000:
                    prices_parts.append(f"{name}: ${p:>10,.2f}")
                elif p >= 1:
                    prices_parts.append(f"{name}: ${p:>10.4f}")
                else:
                    prices_parts.append(f"{name}: ${p:>10.6f}")
            lines.append(f"  💲 PRICES   {' | '.join(prices_parts)}")

            # ── Feed Health ──
            feed_status = feed_health.get_status()
            feed_parts = []
            for n, s in sorted(feed_status.items()):
                status_icon = "🟢" if s["status"] == "LIVE" else "🔴"
                name = n.replace("binance_", "").replace("usdt", "").upper()
                feed_parts.append(f"{name}:{status_icon}")
            lines.append(f"  📡 FEEDS    {' | '.join(feed_parts)}")

            # ── Recent Activity ──
            if latest_logs:
                lines.append("-" * 80)
                lines.append("  📝 RECENT ACTIVITY:")
                for log in list(latest_logs)[:6]:
                    lines.append(f"    {log}")

            lines.append("=" * 80)

            output = "\n".join(lines)
            print(output, flush=True)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Dashboard] Error: {e}")


# ══════════════════════════════════════════════════════════════
# Auto-save Worker
# ══════════════════════════════════════════════════════════════

async def autosave_worker(pnl_engine: PnLEngine):
    """Save PnL and fills every 2 minutes."""
    while True:
        try:
            await asyncio.sleep(120)
            pnl_engine.save_to_file()
            logger.debug("[Autosave] PnL data saved")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Autosave] Error: {e}")


# ══════════════════════════════════════════════════════════════
# Main Orchestrator
# ══════════════════════════════════════════════════════════════

async def run_bot():
    """Initialize all components and start the bot."""
    global _start_time
    _start_time = time.time()

    # ── Logging ──
    logger.remove()
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    # File: full DEBUG trace
    logger.add("logs/mm_bot.log", mode="w", level="DEBUG")
    # Console: only WARNING+ (dashboard handles INFO display)
    logger.add(sys.stderr, level="WARNING",
               format="<red>{time:HH:mm:ss}</red> | <level>{level: <8}</level> | {message}")

    _clear_screen()
    print("=" * 60)
    print("  POLYMARKET MARKET MAKER BOT")
    print("=" * 60)
    print(f"  Mode:       {'PAPER TRADING' if config.paper_trading else 'LIVE'}")
    print(f"  Capital:    ${config.initial_capital:.2f}")
    print(f"  Assets:     {', '.join(a.replace('usdt','').upper() for a in config.assets)}")
    print(f"  Windows:    {config.windows} min")
    print(f"  Fill prob:  {config.default_fill_probability:.0%}")
    print(f"  Feed stale: {config.stale_feed_threshold_ms}ms")
    print("=" * 60)
    print("  Starting workers...\n")

    logger.info("=" * 60)
    logger.info("POLYMARKET MARKET MAKER BOT STARTED")
    logger.info(f"Mode: {'PAPER TRADING' if config.paper_trading else 'LIVE'}")
    logger.info(f"Capital: ${config.initial_capital:.2f}")
    logger.info(f"Assets: {config.assets}")
    logger.info(f"Windows: {config.windows}")
    logger.info("=" * 60)

    # ── Initialize Components ──
    discovery = MarketDiscovery()
    fv_engine = FairValueEngine()
    quote_engine = QuoteEngine()
    toxicity_monitor = ToxicityMonitor()
    circuit_breaker = CircuitBreaker()
    inv_manager = InventoryManager()
    exposure = ExposureTracker()
    fill_simulator = FillSimulator()
    pnl_engine = PnLEngine()
    feed_health = FeedHealthMonitor(stale_threshold_ms=config.stale_feed_threshold_ms)

    background_tasks = []

    # ── Start Discovery ──
    background_tasks.append(asyncio.create_task(
        discovery_worker(discovery, inv_manager, fill_simulator)
    ))

    # ── Start Binance WS Feeds ──
    feeds = []
    for asset in config.assets:
        def make_price_cb():
            async def cb(sym, price, ts):
                await on_price_update(sym, price, ts, circuit_breaker, feed_health)
            return cb

        def make_trade_cb():
            async def cb(trade):
                await on_trade_event(trade, toxicity_monitor)
            return cb

        feed = BinanceWSFeed(
            symbol=asset,
            on_price_update=make_price_cb(),
            on_trade=make_trade_cb(),
        )
        feeds.append(feed)
        background_tasks.append(asyncio.create_task(feed.connect()))

    # ── Start Polymarket Odds Updater ──
    background_tasks.append(asyncio.create_task(odds_updater_worker()))

    # ── Start Quote Engine (with integrated fill simulation) ──
    background_tasks.append(asyncio.create_task(
        quote_engine_worker(
            fv_engine, quote_engine, toxicity_monitor,
            circuit_breaker, inv_manager, fill_simulator,
            pnl_engine, exposure,
        )
    ))

    # ── Start Dashboard ──
    background_tasks.append(asyncio.create_task(
        dashboard_worker(pnl_engine, inv_manager, exposure, circuit_breaker, feed_health)
    ))

    # ── Start Autosave ──
    background_tasks.append(asyncio.create_task(autosave_worker(pnl_engine)))

    logger.info("[Main] All workers started. Bot running.")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(60.0)
    except asyncio.CancelledError:
        logger.info("[Main] Shutdown signal received...")
    finally:
        # Save final state
        pnl_engine.save_to_file()
        logger.info("[Main] Final PnL saved")

        for feed in feeds:
            feed.stop()
        for key, pf in poly_feeds.items():
            await pf.close()
        await discovery.close()
        for task in background_tasks:
            task.cancel()
        logger.info("[Main] Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n  Bot stopped by user.")
