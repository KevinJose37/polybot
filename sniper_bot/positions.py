"""
sniper_bot/positions.py — Position tracking and JSON persistence.
"""
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("sniper_bot.positions")


@dataclass
class Position:
    """A single trade position."""
    id: str                          # Unique ID (timestamp-based)
    asset: str
    direction: str                   # UP / DOWN
    token_id: str
    entry_price: float
    entry_time: float
    shares: float
    stake: float
    tp_price: float
    tp_order_id: str | None = None   # Live mode: CLOB order ID
    status: str = "OPEN"             # OPEN / AWAITING_RESOLUTION / CLOSED
    exit_price: float | None = None
    exit_time: float | None = None
    pnl: float | None = None
    fill_type: str | None = None     # MAKER / RESOLUTION_WIN / RESOLUTION_LOSS
    slippage: float = 0.0            # Entry slippage (paper mode)
    book_age_at_entry_ms: float = 0.0
    signal_to_entry_ms: float = 0.0
    max_favorable_price: float = 0.0 # High water mark for the asset
    market_context: dict = field(default_factory=dict)  # Spread, Depth, Imbal at entry
    
    # ── Telemetry & Analytics ──
    engine: str = "heur"             # "heur" or "xgb"
    elapsed_at_entry_s: float = 0.0  # seconds since market discovery
    filters_passed: list[str] = field(default_factory=list)
    filters_failed: list[str] = field(default_factory=list)
    first_adverse_move_s: float | None = None
    price_at_10s: float | None = None
    price_at_30s: float | None = None
    price_at_60s: float | None = None
    price_at_120s: float | None = None
    fill_attempts: int = 0
    reject_reason: str = ""          # Why this signal was rejected (for SKIPPED trades)

    @property
    def is_open(self) -> bool:
        return self.status in ("OPEN", "AWAITING_RESOLUTION")

    @property
    def time_alive_s(self) -> float:
        if self.exit_time:
            return self.exit_time - self.entry_time
        return time.time() - self.entry_time

    def unrealized_pnl(self, current_bid: float) -> float:
        """Unrealized P&L if we sold at current_bid."""
        if not self.is_open:
            return self.pnl or 0.0
        value = self.shares * current_bid
        cost = self.shares * self.entry_price
        return round(value - cost, 4)

    def tp_distance(self, current_bid: float) -> float:
        """How far current_bid is from TP."""
        return round(self.tp_price - current_bid, 4)


class PositionManager:
    """Manages all positions with JSON persistence."""

    def __init__(self, trades_file: str = "data/sniper_trades.json"):
        self._positions: dict[str, Position] = {}  # id → Position
        self._closed: list[Position] = []
        self._skipped: list[Position] = []         # SKIPPED ghost trades
        self._trades_file = Path(trades_file)
        self._trades_file.parent.mkdir(parents=True, exist_ok=True)

        # Metrics
        self.consecutive_losses: int = 0
        self.total_pnl: float = 0.0
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.maker_fills: int = 0
        self.resolution_fills: int = 0
        self.total_time_to_fill: float = 0.0

        self._load()

    def open_position(self, pos: Position) -> None:
        """Record a new position."""
        self._positions[pos.id] = pos
        self.total_trades += 1
        self._save()
        logger.info("OPEN %s %s %s @ $%.4f → TP $%.4f (%.1f shares)",
                     pos.id[:8], pos.asset, pos.direction,
                     pos.entry_price, pos.tp_price, pos.shares)

    def record_skipped(self, pos: Position) -> None:
        """Record a rejected signal as a skipped ghost trade."""
        pos.status = "SKIPPED"
        self._skipped.append(pos)
        # Limit memory usage for skipped trades
        if len(self._skipped) > 200:
            self._skipped.pop(0)
        self._save()

    def close_position(self, pos_id: str, exit_price: float,
                       fill_type: str) -> Position | None:
        """Close a position with fill info."""
        pos = self._positions.get(pos_id)
        if not pos:
            return None

        pos.exit_price = exit_price
        pos.exit_time = time.time()
        pos.fill_type = fill_type
        pos.status = "CLOSED"

        # Calculate PnL
        if fill_type == "RESOLUTION_LOSS":
            pos.pnl = round(-pos.stake, 4)
        else:
            pos.pnl = round(pos.shares * (exit_price - pos.entry_price), 4)

        self.total_pnl += pos.pnl

        if pos.pnl > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1

        if fill_type == "MAKER":
            self.maker_fills += 1
            self.total_time_to_fill += pos.time_alive_s
        else:
            self.resolution_fills += 1

        # Move to closed list
        del self._positions[pos_id]
        self._closed.append(pos)
        self._save()

        logger.info("CLOSE %s %s → $%.4f (%s) PnL: $%.4f",
                     pos.id[:8], pos.asset, exit_price, fill_type, pos.pnl)
        return pos

    def set_awaiting_resolution(self, pos_id: str) -> None:
        """Mark position as awaiting market resolution."""
        pos = self._positions.get(pos_id)
        if pos:
            pos.status = "AWAITING_RESOLUTION"

    def get_open(self) -> list[Position]:
        return [p for p in self._positions.values() if p.is_open]

    def get_by_asset(self, asset: str) -> Position | None:
        """Get open position for an asset (max 1 per asset)."""
        for p in self._positions.values():
            if p.asset == asset and p.is_open:
                return p
        return None

    def get_by_token(self, token_id: str) -> Position | None:
        for p in self._positions.values():
            if p.token_id == token_id and p.is_open:
                return p
        return None

    @property
    def open_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.is_open)

    def metrics(self) -> dict:
        """Performance metrics for dashboard."""
        total = self.wins + self.losses
        win_rate = self.wins / total if total > 0 else 0.0
        avg_win = 0.0
        avg_loss = 0.0
        if self._closed:
            wins = [p.pnl for p in self._closed if p.pnl and p.pnl > 0]
            losses = [p.pnl for p in self._closed if p.pnl and p.pnl < 0]
            avg_win = sum(wins) / len(wins) if wins else 0.0
            avg_loss = sum(losses) / len(losses) if losses else 0.0

        maker_fill_rate = self.maker_fills / max(1, self.maker_fills + self.resolution_fills)
        avg_time_fill = self.total_time_to_fill / max(1, self.maker_fills)

        # Capital in use
        capital_in_use = sum(p.stake for p in self._positions.values() if p.is_open)
        unrealized = sum(p.unrealized_pnl(0.50) for p in self._positions.values() if p.is_open)

        return {
            "total_trades": total,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(win_rate, 3),
            "total_pnl": round(self.total_pnl, 2),
            "unrealized_pnl": round(unrealized, 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "expectancy": round(avg_win * win_rate + avg_loss * (1 - win_rate), 4) if total > 0 else 0.0,
            "maker_fill_rate": round(maker_fill_rate, 3),
            "avg_time_to_fill_s": round(avg_time_fill, 1),
            "consecutive_losses": self.consecutive_losses,
            "capital_in_use": round(capital_in_use, 2),
            "open_positions": self.open_count,
        }

    def _save(self) -> None:
        """Persist to JSON."""
        try:
            data = {
                "open": [asdict(p) for p in self._positions.values()],
                "closed": [asdict(p) for p in self._closed[-200:]],  # Keep last 200
                "skipped": [asdict(p) for p in self._skipped[-200:]], # Keep last 200
                "metrics": {
                    "total_pnl": self.total_pnl,
                    "wins": self.wins,
                    "losses": self.losses,
                    "consecutive_losses": self.consecutive_losses,
                    "maker_fills": self.maker_fills,
                    "resolution_fills": self.resolution_fills,
                },
            }
            with open(self._trades_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save positions: %s", e)

    def _load(self) -> None:
        """Load from JSON on startup."""
        if not self._trades_file.exists():
            return
        try:
            with open(self._trades_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for pd_ in data.get("open", []):
                pos = Position(**{k: v for k, v in pd_.items() if k in Position.__dataclass_fields__})
                self._positions[pos.id] = pos

            for pd_ in data.get("closed", []):
                pos = Position(**{k: v for k, v in pd_.items() if k in Position.__dataclass_fields__})
                self._closed.append(pos)

            for pd_ in data.get("skipped", []):
                pos = Position(**{k: v for k, v in pd_.items() if k in Position.__dataclass_fields__})
                self._skipped.append(pos)

            m = data.get("metrics", {})
            self.total_pnl = m.get("total_pnl", 0.0)
            self.wins = m.get("wins", 0)
            self.losses = m.get("losses", 0)
            self.consecutive_losses = m.get("consecutive_losses", 0)
            self.maker_fills = m.get("maker_fills", 0)
            self.resolution_fills = m.get("resolution_fills", 0)

            logger.info("Loaded %d open, %d closed positions from %s",
                         len(self._positions), len(self._closed), self._trades_file)
        except Exception as e:
            logger.error("Failed to load positions: %s", e)
