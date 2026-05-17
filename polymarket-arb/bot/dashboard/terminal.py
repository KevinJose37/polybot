"""
Terminal UI using rich.
"""
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.console import Console, Group

from datetime import datetime
from bot.utils.clocks import current_timestamp_ms
from bot.market_discovery.parsers import parse_market_slug

from bot.execution.position_manager import PositionManager
from bot.api.schemas import MarketSnapshot
from bot.orderbook.local_book import LocalOrderBook
from bot.paper_trading.stats import TradingStats

console = Console()


class TerminalDashboard:
    def __init__(self, mode: str, capital: float):
        self.mode = mode
        self.capital = capital
        self.layout = Layout()
        
        # Persistent mapping so we don't lose names of resolved markets
        self.token_to_name = {}
        self.token_to_base_name = {}
        self.token_to_ts = {}
        
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=3),
            Layout(name="footer", size=3),
        )
        
        self.layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1),
        )
        
        self.layout["left"].split_column(
            Layout(name="markets", size=12),
            Layout(name="resolved", ratio=1),
            Layout(name="opportunities", size=7),
        )
        
        self.layout["right"].split_column(
            Layout(name="stats", ratio=1),
            Layout(name="positions", ratio=1),
        )
        
    def update(
        self,
        position_manager: PositionManager,
        markets: list[MarketSnapshot],
        orderbooks: dict[str, LocalOrderBook],
        opportunities: list[dict],
        health_status: dict[str, bool],
        stats: TradingStats | None = None,
        warmup_until_ms: int = 0,
    ) -> None:
        """Refresh the layout with new data."""
        realized_pnl = position_manager.total_realized_pnl
        unrealized_pnl = position_manager.total_unrealized_pnl
        total_pnl = realized_pnl + unrealized_pnl
        equity = position_manager.get_equity(self.capital)
        pnl_pct = (total_pnl / self.capital) * 100 if self.capital > 0 else 0
        
        avail_capital = position_manager.get_available_capital(self.capital)
        pos_cost = sum(abs(p.size * p.avg_price) for p in position_manager.positions.values() if p.size != 0)
        
        mode_text = "[bold yellow]PAPER TRADING — NOT REAL MONEY[/]" if self.mode == "paper" else "[bold red]🔴 LIVE TRADING[/]"
        pnl_color = "green" if total_pnl >= 0 else "red"
        
        # ── Header ──
        uptime = stats.uptime_str if stats else "00:00:00"
        total_fees = stats.total_fees_paid if stats else 0.0
        
        # Warmup countdown
        now_ms = current_timestamp_ms()
        if warmup_until_ms > now_ms:
            rem_s = int((warmup_until_ms - now_ms) / 1000)
            m, s = divmod(rem_s, 60)
            warmup_text = f"[bold yellow]WARMUP: {m:02d}:{s:02d}[/]  \u2502  "
        else:
            warmup_text = ""
            
        header_text = (
            f"  \U0001f4b0 Capital: [bold]${self.capital:,.2f}[/]  \u2502  "
            f"\U0001f4b5 Cash: [bold cyan]${max(0, avail_capital):,.2f}[/]  \u2502  "
            f"\U0001f4e6 Positions: [bold yellow]${pos_cost + unrealized_pnl:,.2f}[/]  \u2502  "
            f"\U0001f4ca Equity: [bold]${equity:,.2f}[/]  \u2502  "
            f"PnL: [{pnl_color}]${total_pnl:,.2f} ({pnl_pct:+.2f}%)[/]  \u2502  "
            f"Fees: [red]-${total_fees:,.2f}[/]  \u2502  "
            f"{warmup_text}"
            f"\u23f1 {uptime}  \u2502  {mode_text}"
        )
        self.layout["header"].update(
            Panel(Text.from_markup(header_text), title="[bold]Polymarket Arb Bot[/]", border_style="cyan")
        )
        
        # ── Markets Table ──
        table = Table(title="Active Markets", expand=True, show_lines=False, border_style="dim")
        table.add_column("Market", style="dim", max_width=40)
        table.add_column("UP Bid", justify="right")
        table.add_column("UP Ask", justify="right")
        table.add_column("DN Bid", justify="right")
        table.add_column("DN Ask", justify="right")
        table.add_column("Σ Ask", justify="right")
        table.add_column("PnL", justify="right")
        
        for market in markets:
            if len(market.tokens) >= 2:
                yes_id = market.tokens[0].token_id
                no_id = market.tokens[1].token_id
                
                b_yes = orderbooks.get(yes_id)
                b_no = orderbooks.get(no_id)
                
                up_bid = b_yes.best_bid() if b_yes else None
                up_ask = b_yes.best_ask() if b_yes else None
                dn_bid = b_no.best_bid() if b_no else None
                dn_ask = b_no.best_ask() if b_no else None
                
                up_bid_s = f"{up_bid:.4f}" if up_bid else "---"
                up_ask_s = f"{up_ask:.4f}" if up_ask else "---"
                dn_bid_s = f"{dn_bid:.4f}" if dn_bid else "---"
                dn_ask_s = f"{dn_ask:.4f}" if dn_ask else "---"
                
                # Sum of asks — < 1.0 = potential parity arb
                if up_ask and dn_ask:
                    ask_sum = up_ask + dn_ask
                    sum_color = "green" if ask_sum < 1.0 else "red" if ask_sum > 1.02 else "yellow"
                    sum_s = f"[{sum_color}]{ask_sum:.4f}[/]"
                else:
                    sum_s = "---"
                
                # Aggregate PnL for this market pair — parity-aware valuation
                pos_yes = position_manager.get_position(yes_id)
                pos_no = position_manager.get_position(no_id)
                market_pnl = pos_yes.realized_pnl + pos_no.realized_pnl
                
                # Parity-aware unrealized: matched YES+NO → $1.00 guaranteed
                mid_prices = {}
                if up_bid is not None and up_ask is not None:
                    mid_prices[yes_id] = (up_bid + up_ask) / 2.0
                elif up_bid is not None: mid_prices[yes_id] = up_bid
                elif up_ask is not None: mid_prices[yes_id] = up_ask
                
                if dn_bid is not None and dn_ask is not None:
                    mid_prices[no_id] = (dn_bid + dn_ask) / 2.0
                elif dn_bid is not None: mid_prices[no_id] = dn_bid
                elif dn_ask is not None: mid_prices[no_id] = dn_ask

                unrealized = position_manager.get_pair_unrealized_pnl(yes_id, no_id, mid_prices)
                total_mkt_pnl = market_pnl + unrealized
                pnl_str = f"[green]+${total_mkt_pnl:.2f}[/]" if total_mkt_pnl >= 0 else f"[red]-${abs(total_mkt_pnl):.2f}[/]"
                
                # Shorten slug for display
                slug_short = market.slug[:38] if len(market.slug) > 38 else market.slug
                
                table.add_row(slug_short, up_bid_s, up_ask_s, dn_bid_s, dn_ask_s, sum_s, pnl_str)
                
        self.layout["markets"].update(Panel(table, border_style="blue"))
        self.layout["markets"].size = max(5, len(markets) + 4)
        
        # ── Opportunities ──
        if opportunities:
            lines = []
            for opp in opportunities[-5:]:
                if isinstance(opp, dict):
                    lines.append(f"[{opp['type']}] edge={opp['edge']*100:.2f}% | size=${opp['size']:.2f} | [{opp['color']}]{opp['status']}[/]")
                else:
                    lines.append(opp)
            opp_text = "\n".join(lines)
        else:
            opp_text = "[dim]Scanning for opportunities...[/]"
            
        self.layout["opportunities"].update(
            Panel(Text.from_markup(opp_text), title="[bold]Recent Opportunities[/]", border_style="magenta")
        )
        
        # Build token_id -> readable name mapping
        for m in markets:
            if not m.tokens: continue
            if m.tokens[0].token_id not in self.token_to_base_name:
                parsed = parse_market_slug(m.slug)
                if parsed.is_valid:
                    tf = "15 min" if parsed.timeframe == "15m" else "5 min"
                    name = f"{parsed.asset} up/down {tf}"
                    close_ts = parsed.timestamp + (300 if parsed.timeframe == "5m" else 900)
                else:
                    name = m.slug[:16]
                    close_ts = 0
                for t in m.tokens:
                    self.token_to_name[t.token_id] = f"{name} ({t.outcome})"
                    self.token_to_base_name[t.token_id] = name
                    self.token_to_ts[t.token_id] = close_ts

        # ── Resolved Positions ──
        res_table = Table(show_header=True, expand=True, border_style="dim")
        res_table.add_column("Market", max_width=32)
        res_table.add_column("Closure", justify="center")
        res_table.add_column("Side", justify="center")
        res_table.add_column("Size", justify="right")
        res_table.add_column("Avg Px", justify="right")
        res_table.add_column("Settle", justify="right")
        res_table.add_column("PnL", justify="right")

        if getattr(position_manager, "resolved_positions", None):
            resolved_list = list(position_manager.resolved_positions)
            for rp in reversed(resolved_list[-15:]):
                res_name = self.token_to_base_name.get(rp["market_id"], rp["market_id"][:15] + "…")
                
                market_ts = self.token_to_ts.get(rp["market_id"], 0)
                if market_ts > 0:
                    ts_str = datetime.fromtimestamp(market_ts).strftime("%H:%M")
                    res_name = f"{res_name} {ts_str}"
                
                settled_at = rp.get("settled_at", 0)
                if settled_at > 0:
                    closure_time = datetime.fromtimestamp(settled_at / 1000.0).strftime("%H:%M:%S")
                else:
                    closure_time = "---"
                    
                rpnl_color = "green" if rp["pnl"] >= 0 else "red"
                side = "[green]LONG[/]" if rp["size"] > 0 else "[red]SHORT[/]"
                settle_display = f"${rp['settle_price']:.2f}"
                if rp['settle_price'] == 1.0:
                    settle_display = "[green]$1.00 ✓[/]"
                elif rp['settle_price'] == 0.0:
                    settle_display = "[red]$0.00 ✗[/]"
                res_table.add_row(
                    res_name,
                    closure_time,
                    side,
                    f"{abs(rp['size']):.1f}",
                    f"${rp['avg_price']:.4f}",
                    settle_display,
                    f"[{rpnl_color}]${rp['pnl']:.2f}[/]",
                )
        else:
            res_table.add_row("[dim]No positions resolved yet[/]", "", "", "", "", "", "")

        self.layout["resolved"].update(
            Panel(res_table, title="[bold]Last Positions Resolved[/]", border_style="green")
        )

        # ── Stats Panel ──
        if stats:
            active_mids = {mid for mid, p in position_manager.positions.items() if p.size != 0}
            win_rate, wins, losses = stats.get_win_rate(active_mids)
            wr_color = "green" if win_rate >= 0.5 else "red"
            
            stats_table = Table(show_header=False, expand=True, border_style="dim", pad_edge=False)
            stats_table.add_column("Metric", style="bold", ratio=2)
            stats_table.add_column("Value", justify="right", ratio=1)
            stats_table.add_column("PnL", justify="right", ratio=1)
            
            stats_table.add_row("Win Rate", f"[{wr_color}]{win_rate*100:.1f}%[/] [dim]({wins}W/{losses}L)[/]", "")
            
            wr_by_type = stats.get_win_rates_by_type(active_mids)
            pnl_by_type = stats.get_pnl_by_type(active_mids)
            for t_type, data in wr_by_type.items():
                wr, w, l = data
                t_color = "green" if wr >= 0.5 else "red"
                t_pnl = pnl_by_type.get(t_type, 0.0)
                p_color = "green" if t_pnl >= 0 else "red"
                stats_table.add_row(
                    f"  {t_type}",
                    f"[{t_color}]{wr*100:.1f}%[/] [dim]({w}W/{l}L)[/]",
                    f"[{p_color}]${t_pnl:,.2f}[/]",
                )
                
            wr_by_market = stats.get_win_rates_by_market(self.token_to_base_name, active_mids)
            pnl_by_mkt = stats.get_pnl_by_market(self.token_to_base_name, active_mids)
            for mkt, data in wr_by_market.items():
                wr, w, l = data
                t_color = "green" if wr >= 0.5 else "red"
                m_pnl = pnl_by_mkt.get(mkt, 0.0)
                p_color = "green" if m_pnl >= 0 else "red"
                stats_table.add_row(
                    f"  {mkt}",
                    f"[{t_color}]{wr*100:.1f}%[/] [dim]({w}W/{l}L)[/]",
                    f"[{p_color}]${m_pnl:,.2f}[/]",
                )
                
            stats_table.add_row("Opps Detected", str(stats.opportunities_detected), "")
            stats_table.add_row("Opps Executed", str(stats.opportunities_executed), "")
            stats_table.add_row("Rejected (Risk)", str(stats.opportunities_rejected_risk), "")
            stats_table.add_row("Rejected (Dedup)", str(stats.opportunities_rejected_dedup), "")
            stats_table.add_row("Fills", str(stats.fills_count), "")
            stats_table.add_row("No Liquidity", str(stats.rejects_no_liquidity), "")
            stats_table.add_row("─" * 16, "─" * 10, "─" * 8)
            
            gross_pnl = stats.get_gross_pnl(active_mids)
            net_pnl = stats.get_net_pnl(active_mids)
            gross_color = "green" if gross_pnl >= 0 else "red"
            net_color = "green" if net_pnl >= 0 else "red"
            stats_table.add_row("Gross PnL", f"[{gross_color}]${gross_pnl:,.2f}[/]", "")
            stats_table.add_row("Total Fees", f"[red]-${stats.total_fees_paid:,.2f}[/]", "")
            stats_table.add_row("Net PnL", f"[{net_color}]${net_pnl:,.2f}[/]", "")
            stats_table.add_row("Volume", f"${stats.total_volume:,.2f}", "")
            stats_table.add_row("Avg Edge", f"{stats.avg_edge*100:.2f}%", "")
            stats_table.add_row("─" * 16, "─" * 10, "─" * 8)
            
            stats_table.add_row("Avail Capital", f"[cyan]${max(0, avail_capital):,.2f}[/]", "")
            
            self.layout["stats"].update(
                Panel(stats_table, title="[bold]Trading Stats[/]", border_style="green")
            )
        else:
            self.layout["stats"].update(Panel("[dim]No stats available[/]", title="Trading Stats"))
        
        # ── Positions Panel ──
        pos_table = Table(show_header=True, expand=True, border_style="dim")
        pos_table.add_column("Market", max_width=24)
        pos_table.add_column("Closes In", justify="right")
        pos_table.add_column("Side", justify="center")
        pos_table.add_column("Size", justify="right")
        pos_table.add_column("Avg Px", justify="right")
        pos_table.add_column("Notional", justify="right")
        pos_table.add_column("Realized", justify="right")
        
        active_positions = [
            (mid, p) for mid, p in position_manager.positions.items() if p.size != 0
        ]
        
        if active_positions:
            now_s = current_timestamp_ms() / 1000.0
            # Build set of token IDs that Polymarket currently reports as active
            active_token_ids = set()
            for m in markets:
                if m.active and not m.closed:
                    for t in m.tokens:
                        active_token_ids.add(t.token_id)
            for mid, p in active_positions[:10]:  # Show top 10
                side = "[green]LONG[/]" if p.size > 0 else "[red]SHORT[/]"
                rpnl_color = "green" if p.realized_pnl >= 0 else "red"
                readable_name = self.token_to_name.get(mid, mid[:10] + "…")
                notional = abs(p.size * p.avg_price)
                
                # Determine position status from Polymarket market state
                # (active_token_ids built from current discovery results)
                if mid in active_token_ids:
                    # Market is live on Polymarket — show countdown
                    close_ts = self.token_to_ts.get(mid, 0)
                    if close_ts > 0:
                        rem = int(close_ts - now_s)
                        if rem > 0:
                            m, s = divmod(rem, 60)
                            closes_in = f"{m:02d}:{s:02d}"
                        else:
                            closes_in = "[yellow]Closing...[/]"
                    else:
                        closes_in = "---"
                else:
                    # Market no longer in active discovery — Polymarket removed it
                    closes_in = "[dim]Settling...[/]"

                pos_table.add_row(
                    readable_name,
                    closes_in,
                    side,
                    f"{abs(p.size):.1f}",
                    f"{p.avg_price:.4f}",
                    f"${notional:,.2f}",
                    f"[{rpnl_color}]${p.realized_pnl:.2f}[/]",
                )
            if len(active_positions) > 10:
                pos_table.add_row(f"...+{len(active_positions)-10} more", "", "", "", "", "", "")
        else:
            pos_table.add_row("[dim]No open positions[/]", "", "", "", "", "", "")
        
        self.layout["positions"].update(
            Panel(pos_table, title=f"[bold]Positions ({len(active_positions)})[/]", border_style="yellow")
        )
        
        # ── Footer ──
        health_parts = []
        for k, v in health_status.items():
            health_parts.append(f"{k}: {'[green]●[/]' if v else '[red]●[/]'}")
        health_text = " │ ".join(health_parts)
        
        n_books = len(orderbooks)
        n_active = sum(1 for b in orderbooks.values() if not b.is_stale())
        n_stale = n_books - n_active
        
        footer_text = (
            f"  {health_text}  │  "
            f"Books: {n_active}/{n_books} active  │  "
            f"Stale: {'[red]' + str(n_stale) + '[/]' if n_stale > 0 else '[green]0[/]'}  │  "
            f"Markets: {len(markets)}"
        )
        self.layout["footer"].update(
            Panel(Text.from_markup(footer_text), title="[bold]System Health[/]", border_style="dim")
        )
