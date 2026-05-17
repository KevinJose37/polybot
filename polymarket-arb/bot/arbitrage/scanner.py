"""
Arbitrage orchestrator.
"""
import structlog

from bot.api.schemas import MarketSnapshot
from bot.orderbook.local_book import LocalOrderBook
from bot.market_discovery.market_relationships import MarketTopology
from bot.arbitrage.opportunity import ArbOpportunity
from bot.arbitrage.monotonicity import detect_monotonicity
from bot.arbitrage.exhaustive_sets import detect_exhaustive_parity
from bot.settings import Settings
from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)


class ArbitrageScanner:
    """
    Orchestrates the pure detector functions against the current orderbook state.
    """
    def __init__(self, settings: Settings, topology: MarketTopology, fee_rates: dict[str, float] | None = None, position_manager=None):
        self.settings = settings
        self.topology = topology
        self.fee_rates = fee_rates or {}
        self.position_manager = position_manager
        self._scan_count: int = 0
        self._total_opportunities: int = 0

    def scan(self, orderbooks: dict[str, LocalOrderBook]) -> list[ArbOpportunity]:
        """
        Runs all detectors and returns valid opportunities.
        orderbooks is a map of token_id -> LocalOrderBook
        """
        opportunities = []
        
        capital = self.settings.starting_capital
        if self.position_manager:
            if self.settings.trading.capital_source == "total_equity":
                capital = self.position_manager.get_total_equity(capital)
            else:  # "available_cash" (default, conservative)
                capital = self.position_manager.get_available_capital(capital)
        default_fee = self.settings.trading.polymarket_fee
        slippage = self.settings.trading.slippage_est
        min_edge = self.settings.trading.min_edge
        min_notional = self.settings.trading.min_notional
        
        skipped_stale = 0
        skipped_no_data = 0

        # 1. Type A & Type C (Parity & Exhaustive Sets)
        for market_id in self.topology.parity_markets:
            market = self.topology.markets[market_id]
            if len(market.tokens) != 2:
                continue
                
            yes_token = market.tokens[0].token_id
            no_token = market.tokens[1].token_id
            
            book_yes = orderbooks.get(yes_token)
            book_no = orderbooks.get(no_token)
            
            if not book_yes or not book_no:
                skipped_no_data += 1
                continue

            if book_yes.is_stale() or book_no.is_stale():
                skipped_stale += 1
                continue

            yes_bid, yes_ask = book_yes.best_bid(), book_yes.best_ask()
            no_bid, no_ask = book_no.best_bid(), book_no.best_ask()
            
            if None in (yes_bid, yes_ask, no_bid, no_ask):
                skipped_no_data += 1
                continue

            yes_ask_depth = book_yes.ask_depth(levels=1)
            no_ask_depth = book_no.ask_depth(levels=1)
            yes_bid_depth = book_yes.bid_depth(levels=1)
            no_bid_depth = book_no.bid_depth(levels=1)

            if not yes_ask_depth or not no_ask_depth or not yes_bid_depth or not no_bid_depth:
                skipped_no_data += 1
                continue

            _, yes_ask_vol = yes_ask_depth[0]
            _, no_ask_vol = no_ask_depth[0]
            _, yes_bid_vol = yes_bid_depth[0]
            _, no_bid_vol = no_bid_depth[0]

            # Type A is subsumed by Type C (exhaustive checks both BUY and SELL parity)
            # Running both would double-execute on the same BUY-side dislocation.

            up_fee = self.fee_rates.get(yes_token, default_fee)
            down_fee = self.fee_rates.get(no_token, default_fee)
            
            inventory_up = 0.0
            inventory_down = 0.0
            if self.position_manager:
                inventory_up = self.position_manager.get_position(yes_token).size
                inventory_down = self.position_manager.get_position(no_token).size

            exhaustive_opp = detect_exhaustive_parity(
                market_id=market.id,
                token_up_id=yes_token,
                token_down_id=no_token,
                up_bid=yes_bid, # type: ignore
                up_ask=yes_ask, # type: ignore
                down_bid=no_bid, # type: ignore
                down_ask=no_ask, # type: ignore
                up_ask_vol=yes_ask_vol,
                down_ask_vol=no_ask_vol,
                up_bid_vol=yes_bid_vol,
                down_bid_vol=no_bid_vol,
                inventory_up=inventory_up,
                inventory_down=inventory_down,
                up_fee_rate=up_fee,
                down_fee_rate=down_fee,
                slippage=slippage,
                min_edge=min_edge,
                min_notional=min_notional,
                capital=capital,
                multiplier=self.settings.trading.kelly_fraction_multiplier,
                gas_fee_est=self.settings.trading.gas_fee_per_leg * 2
            )
            if exhaustive_opp:
                exhaustive_opp.timestamp_ms = current_timestamp_ms()
                opportunities.append(exhaustive_opp)

        # 2. Type B (Monotonicity) — iterate paired 5m and 15m markets (same asset, same timestamp)
        for market_5m_id, market_15m_id in self.topology.monotonicity_pairs:
            market_5m = self.topology.markets.get(market_5m_id)
            market_15m = self.topology.markets.get(market_15m_id)
            
            if not market_5m or not market_15m:
                continue
            if len(market_5m.tokens) < 2 or len(market_15m.tokens) < 1:
                continue

            no_5m = market_5m.tokens[1].token_id
            yes_15m = market_15m.tokens[0].token_id
            
            book_5m_no = orderbooks.get(no_5m)
            book_15m_yes = orderbooks.get(yes_15m)
                    
            if not book_5m_no or not book_15m_yes:
                skipped_no_data += 1
                continue

            if book_5m_no.is_stale() or book_15m_yes.is_stale():
                skipped_stale += 1
                continue

            ask_5m_no = book_5m_no.best_ask()
            ask_15m_yes = book_15m_yes.best_ask()
            
            if ask_5m_no is None or ask_15m_yes is None:
                skipped_no_data += 1
                continue

            depth_5m_no = book_5m_no.ask_depth(levels=1)
            depth_15m_yes = book_15m_yes.ask_depth(levels=1)

            if not depth_5m_no or not depth_15m_yes:
                skipped_no_data += 1
                continue

            _, vol_5m_no = depth_5m_no[0]
            _, vol_15m_yes = depth_15m_yes[0]

            fee_5m = self.fee_rates.get(no_5m, default_fee)
            fee_15m = self.fee_rates.get(yes_15m, default_fee)

            mono_opp = detect_monotonicity(
                market_5m_id=market_5m_id,
                market_15m_id=market_15m_id,
                token_no_5m=no_5m,
                token_yes_15m=yes_15m,
                ask_5m_no=ask_5m_no,
                ask_15m_yes=ask_15m_yes,
                vol_5m_no=vol_5m_no,
                vol_15m_yes=vol_15m_yes,
                fee_rate_5m=fee_5m,
                fee_rate_15m=fee_15m,
                slippage=slippage,
                min_edge=min_edge,
                min_notional=min_notional,
                capital=capital,
                multiplier=self.settings.trading.kelly_fraction_multiplier,
                gas_fee_est=self.settings.trading.gas_fee_per_leg * 2
            )
            if mono_opp:
                mono_opp.timestamp_ms = current_timestamp_ms()
                opportunities.append(mono_opp)
        
        # Heartbeat log every 200 scans (keeps logs clean)
        self._scan_count += 1
        self._total_opportunities += len(opportunities)
        if self._scan_count % 200 == 0:
            logger.info(
                "scanner_heartbeat",
                scans=self._scan_count,
                total_opps=self._total_opportunities,
                active_markets=len(self.topology.parity_markets),
                found_this_scan=len(opportunities),
            )
                    
        return opportunities
