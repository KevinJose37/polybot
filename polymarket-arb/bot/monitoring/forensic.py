"""
Forensic logger for structured trade-level JSON records.

Writes one JSON line per opportunity (executed or skipped) to a JSONL file.
Designed for post-hoc analysis: trade reconstruction, slippage audit,
legging risk detection, and parameter optimization.
"""
import json
import structlog
from pathlib import Path
from typing import Optional

from bot.arbitrage.opportunity import ArbOpportunity, ArbType
from bot.orderbook.local_book import LocalOrderBook
from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)


class ForensicLogger:
    """Writes structured JSON records for forensic analysis."""

    def __init__(self, log_dir: str = "logs"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(exist_ok=True)
        self._file = open(self._log_dir / "forensic.jsonl", "a", buffering=1)

    def _write(self, record: dict) -> None:
        try:
            self._file.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.error("forensic_write_error", error=str(e))

    def log_executed_opportunity(
        self,
        opp: ArbOpportunity,
        acks: list,
        orderbooks: dict[str, LocalOrderBook],
        fee_rates: dict[str, float],
        slippage_est: float,
        kelly_multiplier: float,
        fill_details: list[dict],
        legging_gap_ms: int = 0,
    ) -> None:
        """Log a fully executed opportunity with lifecycle data."""
        legs_data = []
        total_fees = 0.0
        total_cost = 0.0
        total_revenue = 0.0

        for i, leg in enumerate(opp.legs):
            book = orderbooks.get(leg.market_id)
            age_ms = (current_timestamp_ms() - book.last_updated_ts) if book else -1

            # L2 context at detection
            l2 = {}
            if book:
                if leg.side == "BUY":
                    depth = book.ask_depth(levels=1)
                    l2["top_ask"] = book.best_ask()
                    l2["ask_depth"] = depth[0][1] if depth else 0.0
                else:
                    depth = book.bid_depth(levels=1)
                    l2["top_bid"] = book.best_bid()
                    l2["bid_depth"] = depth[0][1] if depth else 0.0
                l2["time_since_last_ws_update_ms"] = age_ms

            # Execution details
            fd = fill_details[i] if i < len(fill_details) else {}
            ack = acks[i] if i < len(acks) else None
            fee_paid = fd.get("fee", 0.0)
            total_fees += fee_paid

            fill_price = fd.get("fill_price", leg.price)
            filled_size = fd.get("filled_size", leg.size)

            if leg.side == "BUY":
                total_cost += fill_price * filled_size + fee_paid
            else:
                total_revenue += fill_price * filled_size - fee_paid

            # Determine leg role
            if opp.type == ArbType.MONOTONICITY:
                role = "SELL_5M" if leg.side == "SELL" else "BUY_15M"
            elif leg.side == "BUY":
                role = "UP_BUY" if i == 0 else "DOWN_BUY"
            else:
                role = "UP_SELL" if i == 0 else "DOWN_SELL"

            legs_data.append({
                "leg_id": f"leg_{i+1}",
                "token_id": leg.market_id,
                "role": role,
                "l2_context": l2,
                "execution": {
                    "requested_price": leg.price,
                    "requested_size": leg.size,
                    "fill_price": fill_price,
                    "filled_size": filled_size,
                    "fill_latency_ms": fd.get("latency_ms", 0),
                    "status": ack.status if ack else "UNKNOWN",
                    "fee_paid_usd": fee_paid,
                },
            })

        # Compute slippage vs theoretical
        if opp.type in (ArbType.PARITY, ArbType.EXHAUSTIVE):
            # BUY parity: expected cost per share = sum(asks) + fees + slippage
            theoretical_cost = sum(l.price for l in opp.legs) + slippage_est * len(opp.legs)
            actual_cost = (total_cost / opp.size) if opp.size > 0 else 0
            slippage_delta = actual_cost - theoretical_cost
        else:
            slippage_delta = 0.0

        # Unhedged exposure
        sizes = [fd.get("filled_size", l.size) for fd, l in zip(fill_details, opp.legs)]
        if len(sizes) == 2:
            unhedged = abs(sizes[0] - sizes[1]) * max(l.price for l in opp.legs)
        else:
            unhedged = 0.0

        record = {
            "opp_id": opp.opportunity_id,
            "type": "EXECUTED",
            "strategy_type": opp.type.value,
            "detection_time_ms": opp.timestamp_ms,
            "log_time_ms": current_timestamp_ms(),
            "theoretical": {
                "edge_pct": round(opp.edge, 6),
                "expected_profit_usd": round(opp.edge * opp.size, 4),
                "fee_rate_used": fee_rates.get(opp.legs[0].market_id, 0.0) if opp.legs else 0.0,
                "slippage_est_used": slippage_est,
                "kelly_multiplier": kelly_multiplier,
                "target_notional_usd": round(opp.size, 2),
            },
            "legs": legs_data,
            "execution_metrics": {
                "legging_gap_ms": legging_gap_ms,
                "unhedged_exposure_usd": round(unhedged, 4),
                "actual_vs_theoretical_slippage": round(slippage_delta, 6),
                "total_fees_usd": round(total_fees, 4),
            },
            "outcome": {
                "status": "OPEN",
                "realized_pnl_usd": 0.0,
            },
        }
        self._write(record)

    def log_skipped_opportunity(
        self,
        opp: ArbOpportunity,
        reason: str,
        details: str,
        filters_passed: list[str],
        filters_failed: list[str],
        orderbooks: dict[str, LocalOrderBook] | None = None,
    ) -> None:
        """Log a skipped opportunity for opportunity-cost analysis."""
        legs_data = []
        for i, leg in enumerate(opp.legs):
            l2 = {}
            if orderbooks:
                book = orderbooks.get(leg.market_id)
                if book:
                    age_ms = current_timestamp_ms() - book.last_updated_ts
                    if leg.side == "BUY":
                        l2["top_ask"] = book.best_ask()
                        depth = book.ask_depth(levels=1)
                        l2["ask_depth"] = depth[0][1] if depth else 0.0
                    else:
                        l2["top_bid"] = book.best_bid()
                        depth = book.bid_depth(levels=1)
                        l2["bid_depth"] = depth[0][1] if depth else 0.0
                    l2["time_since_last_ws_update_ms"] = age_ms

            legs_data.append({
                "leg_id": f"leg_{i+1}",
                "token_id": leg.market_id,
                "l2_context": l2,
            })

        record = {
            "opp_id": f"SKIP-{opp.opportunity_id}",
            "type": "SKIPPED",
            "strategy_type": opp.type.value,
            "detection_time_ms": opp.timestamp_ms,
            "log_time_ms": current_timestamp_ms(),
            "theoretical": {
                "edge_pct": round(opp.edge, 6),
                "expected_profit_usd": round(opp.edge * opp.size, 4),
            },
            "legs": legs_data,
            "skip_reason": {
                "primary_reason": reason,
                "details": details,
                "filters_passed": filters_passed,
                "filters_failed": filters_failed,
            },
        }
        self._write(record)

    def log_settlement(
        self,
        opp_id: str,
        realized_pnl: float,
        duration_held_s: float,
    ) -> None:
        """Log settlement of a previously executed opportunity."""
        record = {
            "opp_id": opp_id,
            "type": "SETTLEMENT",
            "log_time_ms": current_timestamp_ms(),
            "outcome": {
                "status": "SETTLED",
                "realized_pnl_usd": round(realized_pnl, 4),
                "duration_held_s": round(duration_held_s, 2),
                "settlement_time_ms": current_timestamp_ms(),
            },
        }
        self._write(record)

    def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.close()
