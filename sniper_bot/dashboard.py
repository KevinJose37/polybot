"""
sniper_bot/dashboard.py — Rich terminal HFT-style dashboard.

8 panels focused on execution quality, not P&L.
Uses rich.live.Live for flicker-free updates at ~6 FPS.
"""
import time
import asyncio
import logging
from datetime import datetime, timezone

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import BarColumn, Progress, TextColumn
from rich import box

from .config import SniperConfig
from .ws_manager import OrderbookManager, WSHealth
from .lifecycle import MarketLifecycleManager, Phase
from .signal_engine import SignalEngine
from .executor import Executor
from .positions import PositionManager
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger("sniper_bot.dashboard")

# Color helpers
def _c(val: float, good_thresh: float = 0, bad_thresh: float = 0,
       invert: bool = False, fmt: str = ".4f") -> str:
    """Colorize a numeric value."""
    formatted = f"{val:{fmt}}"
    if invert:
        good_thresh, bad_thresh = bad_thresh, good_thresh
    if val > good_thresh:
        return f"[green]{formatted}[/green]"
    if val < bad_thresh:
        return f"[red]{formatted}[/red]"
    return f"[yellow]{formatted}[/yellow]"


def _health_color(val: float, warn: float, crit: float) -> str:
    if val <= warn:
        return "[green]"
    if val <= crit:
        return "[yellow]"
    return "[red]"


class Dashboard:
    """Rich terminal dashboard with 8 panels."""

    def __init__(
        self,
        config: SniperConfig,
        ws_mgr: OrderbookManager,
        lifecycle: MarketLifecycleManager,
        signal_engine: SignalEngine,
        executor: Executor,
        positions: PositionManager,
        circuit_breaker: CircuitBreaker,
    ):
        self.config = config
        self._ws = ws_mgr
        self._lc = lifecycle
        self._sig = signal_engine
        self._exe = executor
        self._pos = positions
        self._cb = circuit_breaker
        self._start_time = time.time()
        self._console = Console()

    def build_layout(self) -> Layout:
        """Build the full dashboard layout."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=10),
        )
        layout["body"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )
        layout["left"].split_column(
            Layout(name="markets", ratio=3),
            Layout(name="signals", ratio=2),
            Layout(name="positions", ratio=2),
        )
        layout["right"].split_column(
            Layout(name="execution", ratio=2),
            Layout(name="performance", ratio=2),
            Layout(name="system", ratio=2),
        )
        layout["footer"].split_row(
            Layout(name="events", ratio=1),
        )
        return layout

    def render(self) -> Layout:
        """Render all panels into the layout."""
        layout = self.build_layout()
        layout["header"].update(self._render_header())
        layout["markets"].update(self._render_markets())
        layout["signals"].update(self._render_signals())
        layout["positions"].update(self._render_positions())
        layout["execution"].update(self._render_execution())
        layout["performance"].update(self._render_performance())
        layout["system"].update(self._render_system())
        layout["events"].update(self._render_events())
        return layout

    # ── Header ────────────────────────────────────────────────

    def _render_header(self) -> Panel:
        h = self._ws.health()
        uptime = time.time() - self._start_time
        uptime_str = f"{int(uptime//3600):02d}:{int((uptime%3600)//60):02d}:{int(uptime%60):02d}"

        ws_status = "[green]CONNECTED[/green]" if h.connected else "[red]DISCONNECTED[/red]"
        latency_color = _health_color(h.avg_parse_ms, 5, 20)
        lat_str = f"{latency_color}{h.avg_parse_ms:.1f}ms[/]"

        mode_color = "[yellow]" if self.config.mode == "PAPER" else "[red bold]"
        mode_str = f"{mode_color}{self.config.mode}[/]"

        utc_now = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]

        cb_status = ""
        if self._cb.is_halted:
            cb_status = " | [red bold]⛔ HALTED[/]"

        header = (
            f" [bold]SNIPER BOT[/bold] | {mode_str} | WS: {ws_status} | "
            f"Latency: {lat_str} | Reconnects: {h.reconnect_count} | "
            f"Tokens: {h.tokens_tracked} | Uptime: {uptime_str} | "
            f"UTC: {utc_now}{cb_status}"
        )
        return Panel(Text.from_markup(header), style="bold", height=3, box=box.SIMPLE)

    # ── Markets Panel ─────────────────────────────────────────

    def _render_markets(self) -> Panel:
        table = Table(box=box.SIMPLE_HEAVY, expand=True, padding=(0, 1))
        table.add_column("Asset", style="bold cyan", width=5)
        table.add_column("Phase", width=8)
        table.add_column("UP B/A", width=12)
        table.add_column("DN B/A", width=12)
        table.add_column("Spread", width=7)
        table.add_column("Mid", width=7)
        table.add_column("Imbal", width=7)
        table.add_column("Depth B/A", width=11)
        table.add_column("Remain", width=7)

        for asset in self.config.assets:
            state = self._lc.get(asset)
            if not state:
                table.add_row(asset, "[dim]--[/]", *["--"] * 5)
                continue

            phase = state.phase()
            phase_colors = {
                Phase.PENDING: "[dim]PENDING[/]",
                Phase.ENTRY: "[green bold]ENTRY[/]",
                Phase.HOLD: "[yellow]HOLD[/]",
                Phase.EXIT: "[red]EXIT[/]",
                Phase.RESOLVED: "[dim]RESOLVED[/]",
            }
            phase_str = phase_colors.get(phase, str(phase.value))

            # Find tokens for this asset
            up_book = down_book = None
            for tid, mapping in self._sig._token_map.items():
                if mapping[0] == asset:
                    if mapping[1] == "UP":
                        up_book = self._ws.get_book(tid)
                    elif mapping[1] == "DOWN":
                        down_book = self._ws.get_book(tid)

            def format_book(book, is_up=True):
                if not book:
                    return "[dim]--[/]"
                ba_str = f"${book.best_bid:.3f}/${book.best_ask:.3f}"
                if book.best_ask <= 0.05 or book.best_bid >= 0.95:
                    ba_str = f"[dim]{ba_str}[/]"
                elif 0.40 <= book.best_ask <= 0.60:
                    ba_str = f"[yellow]{ba_str}[/]"
                return ba_str

            up_ba = format_book(up_book, is_up=True)
            dn_ba = format_book(down_book, is_up=False)

            if up_book:
                spread_str = _c(up_book.spread, good_thresh=-1, bad_thresh=0.03, invert=True, fmt=".3f")
                mid_str = f"${up_book.mid_price:.3f}"
                imb_str = _c(up_book.imbalance, good_thresh=0.1, bad_thresh=-0.1, fmt="+.3f")
                depth_str = f"{up_book.bid_depth:.0f}/{up_book.ask_depth:.0f}"
            else:
                spread_str = mid_str = imb_str = depth_str = "[dim]--[/]"

            remain = f"{state.seconds_remaining():.0f}s"
            table.add_row(asset, phase_str, up_ba, dn_ba, spread_str, mid_str, imb_str, depth_str, remain)

        return Panel(table, title="[bold cyan]Markets (UP/DOWN)[/]", border_style="cyan", box=box.ROUNDED)

    # ── Signals Panel ─────────────────────────────────────────

    def _render_signals(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("Time", width=12)
        table.add_column("Asset", width=5)
        table.add_column("Dir", width=4)
        table.add_column("Ask", width=7)
        table.add_column("Spread", width=7)
        table.add_column("Imbal", width=7)
        table.add_column("Score", width=6)
        table.add_column("Status", min_width=20)

        for sig in list(self._sig.signal_log)[-10:]:
            ts = datetime.fromtimestamp(sig.timestamp, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            dir_color = "[green]" if sig.direction == "UP" else "[red]"
            dir_str = f"{dir_color}{sig.direction}[/]"

            if sig.accepted:
                status = "[green bold]EXECUTED[/green bold]"
            else:
                status = f"[red]{sig.reject_reason[:25]}[/red]"

            score_str = _c(sig.score, good_thresh=0.6, bad_thresh=0.3, fmt=".2f")

            table.add_row(
                ts, sig.asset, dir_str, f"${sig.best_ask:.3f}",
                f"${sig.spread:.3f}", f"{sig.imbalance:+.3f}",
                score_str, status,
            )

        return Panel(table, title="[bold]Signals[/]", border_style="white", box=box.ROUNDED)

    # ── Positions Panel ───────────────────────────────────────

    def _render_positions(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("Asset", width=5)
        table.add_column("Dir", width=4)
        table.add_column("Entry", width=7)
        table.add_column("Bid", width=7)
        table.add_column("Ask", width=7)
        table.add_column("TP", width=7)
        table.add_column("Dist", width=7)
        table.add_column("uPnL", width=8)
        table.add_column("Time", width=6)
        table.add_column("Status", width=10)

        for pos in self._pos.get_open():
            book = self._ws.get_book(pos.token_id)
            cur_bid = book.best_bid if book else 0.0
            cur_ask = book.best_ask if book else 0.0

            upnl = pos.unrealized_pnl(cur_bid)
            dist = pos.tp_distance(cur_bid)
            time_str = f"{pos.time_alive_s:.0f}s"

            upnl_str = _c(upnl, good_thresh=0, bad_thresh=0, fmt="+.3f")
            dist_str = _c(dist, good_thresh=-1, bad_thresh=0.05, invert=True, fmt=".3f")

            status_colors = {
                "OPEN": "[green]OPEN[/]",
                "AWAITING_RESOLUTION": "[yellow]AWAIT[/]",
            }
            status_str = status_colors.get(pos.status, pos.status)

            table.add_row(
                pos.asset, pos.direction,
                f"${pos.entry_price:.3f}", f"${cur_bid:.3f}", f"${cur_ask:.3f}",
                f"${pos.tp_price:.3f}", dist_str, upnl_str, time_str, status_str,
            )

        if not self._pos.get_open():
            table.add_row(*["[dim]--[/]"] * 10)

        return Panel(table, title="[bold]Positions[/]", border_style="green", box=box.ROUNDED)

    # ── Execution Panel ───────────────────────────────────────

    def _render_execution(self) -> Panel:
        m = self._exe.metrics.as_dict()
        sig_m = self._sig.metrics()

        lines = [
            f"[bold]Entries:[/] {m['entries_filled']}/{m['entries_attempted']}"
            f" (rejected: {m['entries_rejected']})",
            f"[bold]Rejection rate:[/] {sig_m['rejection_rate']*100:.1f}%",
            f"[bold]Avg slippage:[/] {_c(m['avg_slippage'], bad_thresh=0.005, good_thresh=-1, invert=True, fmt='.4f')}",
            f"[bold]Avg book age:[/] {_c(m['avg_book_age_ms'], bad_thresh=500, good_thresh=-1, invert=True, fmt='.0f')}ms",
            f"[bold]Signal→Entry:[/] {_c(m['avg_signal_to_entry_ms'], bad_thresh=100, good_thresh=-1, invert=True, fmt='.0f')}ms",
            f"[bold]Stale entries:[/] {m['stale_book_entries']}",
            "",
            f"[bold]Maker fills:[/] [green]{m['maker_fills']}[/]",
            f"[bold]Resolution W/L:[/] [green]{m['resolution_wins']}[/]/[red]{m['resolution_losses']}[/]",
        ]

        # Top rejection reasons
        if sig_m["top_rejections"]:
            lines.append("")
            lines.append("[bold dim]Top rejections:[/]")
            for reason, count in list(sig_m["top_rejections"].items())[:3]:
                lines.append(f"  {reason}: {count}")

        content = "\n".join(lines)
        return Panel(content, title="[bold]Execution Quality[/]",
                     border_style="yellow", box=box.ROUNDED)

    # ── Performance Panel ─────────────────────────────────────

    def _render_performance(self) -> Panel:
        m = self._pos.metrics()

        # Calculate real unrealized PnL using live books
        unrealized = 0.0
        for pos in self._pos.get_open():
            book = self._ws.get_book(pos.token_id)
            if book:
                unrealized += pos.unrealized_pnl(book.best_bid)

        total_pnl = m["total_pnl"] + unrealized
        capital = self.config.capital
        roi_pct = (total_pnl / capital * 100) if capital > 0 else 0.0

        total_str = _c(total_pnl, good_thresh=0, bad_thresh=0, fmt="+.2f")
        pnl_str = _c(m["total_pnl"], good_thresh=0, bad_thresh=0, fmt="+.2f")
        wr_str = _c(m["win_rate"] * 100, good_thresh=55, bad_thresh=45, fmt=".1f")
        upnl_str = _c(unrealized, good_thresh=0, bad_thresh=0, fmt="+.2f")
        roi_str = _c(roi_pct, good_thresh=0, bad_thresh=0, fmt="+.1f")

        lines = [
            f"[bold]Total P&L:[/] ${total_str}  ({roi_str}%)",
            f"  Realized: ${pnl_str}  |  Unrealized: ${upnl_str}",
            "",
            f"[bold]Win Rate:[/] {wr_str}%  ({m['wins']}W/{m['losses']}L)",
            f"[bold]Expectancy:[/] ${m['expectancy']:+.4f}",
            f"[bold]Avg Win:[/] [green]${m['avg_win']:+.4f}[/]  |  [bold]Avg Loss:[/] [red]${m['avg_loss']:+.4f}[/]",
            f"[bold]Maker Fill Rate:[/] {m['maker_fill_rate']*100:.0f}%",
            f"[bold]Avg Time to Fill:[/] {m['avg_time_to_fill_s']:.0f}s",
            f"[bold]Capital in Use:[/] ${m['capital_in_use']:.0f}",
            f"[bold]Consec Losses:[/] {_c(m['consecutive_losses'], bad_thresh=3, good_thresh=-1, invert=True, fmt='.0f')}",
        ]
        content = "\n".join(lines)
        return Panel(content, title="[bold]Performance[/]",
                     border_style="magenta", box=box.ROUNDED)

    # ── System Panel ──────────────────────────────────────────

    def _render_system(self) -> Panel:
        h = self._ws.health()

        mps_color = _health_color(1.0 / max(0.1, h.msgs_per_second) if h.msgs_per_second > 0 else 999, 1, 5)
        q_color = _health_color(h.queue_backlog, 100, 1000)
        age_color = _health_color(h.last_msg_age_ms, 1000, 5000)

        lines = [
            f"[bold]WS msgs/sec:[/] {mps_color}{h.msgs_per_second:.1f}[/]",
            f"[bold]Parse time:[/] {_c(h.avg_parse_ms, bad_thresh=10, good_thresh=-1, invert=True, fmt='.2f')}ms",
            f"[bold]Slow parses:[/] {h.slow_parses}",
            f"[bold]Queue backlog:[/] {q_color}{h.queue_backlog}[/]",
            f"[bold]Dropped msgs:[/] {h.dropped_msgs}",
            f"[bold]Reconnects:[/] {h.reconnect_count}",
            f"[bold]Last msg age:[/] {age_color}{h.last_msg_age_ms:.0f}ms[/]",
            f"[bold]Total recv:[/] {h.msgs_received:,}",
            f"[bold]Total parsed:[/] {h.msgs_parsed:,}",
        ]

        # Circuit breaker
        cb = self._cb.status_summary()
        if cb["halted"]:
            lines.append(f"\n[red bold]⛔ CIRCUIT BREAKER: {cb['reason']}[/]")
        else:
            lines.append(f"\n[green]✓ Circuit breaker OK[/]")

        content = "\n".join(lines)
        return Panel(content, title="[bold]System Health[/]",
                     border_style="blue", box=box.ROUNDED)

    # ── Events Panel ──────────────────────────────────────────

    def _render_events(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("Time", width=12)
        table.add_column("Type", width=18)
        table.add_column("Asset", width=5)
        table.add_column("Detail", min_width=30)

        events = self._exe.events[-12:]
        for evt in events:
            ts = datetime.fromtimestamp(evt["ts"], tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            etype = evt["type"]

            type_colors = {
                "ENTRY": "[green]",
                "MAKER_FILL": "[green bold]",
                "RESOLUTION_WIN": "[green]",
                "RESOLUTION_LOSS": "[red]",
                "REJECTED": "[yellow]",
                "CIRCUIT_BREAKER": "[red bold]",
                "LIVE_ERROR": "[red]",
            }
            color = type_colors.get(etype, "[white]")
            type_str = f"{color}{etype}[/]"

            table.add_row(ts, type_str, evt.get("asset", ""), evt.get("detail", "")[:60])

        if not events:
            table.add_row("[dim]--[/]", "[dim]Waiting for events...[/]", "", "")

        return Panel(table, title="[bold]Events[/]", border_style="white", box=box.ROUNDED)

    # ── Run ───────────────────────────────────────────────────

    async def run(self) -> None:
        """Async dashboard render loop."""
        with Live(self.render(), console=self._console,
                  refresh_per_second=self.config.refresh_fps,
                  screen=True) as live:
            while True:
                try:
                    live.update(self.render())
                    await asyncio.sleep(1.0 / self.config.refresh_fps)
                except Exception as e:
                    logger.error("Dashboard render error: %s", e)
                    await asyncio.sleep(1.0)
