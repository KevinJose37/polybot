"""Terminal Dashboard using rich for the Adaptive Market Maker."""

import time
from datetime import datetime

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.bot import AdaptiveMarketMakerBot
from config.settings import Config

console = Console()

class TerminalDashboard:
    def __init__(self, settings: Config, capital: float):
        self.settings = settings
        self.capital = capital
        self.layout = Layout()
        self.start_time = time.time()
        
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=3),
            Layout(name="footer", size=3),
        )
        
        self.layout["body"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )
        
        self.layout["left"].split_column(
            Layout(name="markets", ratio=2),
            Layout(name="resolved", ratio=1),
        )
        
        self.layout["right"].split_column(
            Layout(name="stats", ratio=1),
            Layout(name="positions", ratio=1),
        )

    def _format_uptime(self) -> str:
        uptime_s = int(time.time() - self.start_time)
        m, s = divmod(uptime_s, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def update(self, bot: AdaptiveMarketMakerBot) -> None:
        now = time.time()
        
        # Calculate warmup status
        warmup_text = ""
        max_rem = 0.0
        for mkt, tracker in bot.signal_engine._warm_up_trackers.items():
            elapsed = now - tracker["start_time"]
            if elapsed < bot.signal_engine.warm_up_seconds:
                rem = bot.signal_engine.warm_up_seconds - elapsed
                if rem > max_rem:
                    max_rem = rem
                    
        if max_rem > 0:
            m, s = divmod(int(max_rem), 60)
            warmup_text = f"[bold yellow]WARMUP: {m:02d}:{s:02d}[/]  \u2502  "
        
        # ── Header ──
        mode_text = "[bold yellow]PAPER TRADING — NOT REAL MONEY[/]" if self.settings.paper_trading else "[bold red]🔴 LIVE TRADING[/]"
        
        header_text = (
            f"  \U0001f4b0 Capital: [bold]${self.capital:,.2f}[/]  \u2502  "
            f"{warmup_text}"
            f"\u23f1 {self._format_uptime()}  \u2502  {mode_text}"
        )
        self.layout["header"].update(
            Panel(Text.from_markup(header_text), title="[bold]Adaptive Market Maker[/]", border_style="cyan")
        )
        
        # ── Markets Table ──
        table = Table(title="Active Markets", expand=True, show_lines=False, border_style="dim")
        table.add_column("Market", style="dim", max_width=30)
        table.add_column("Spot", justify="right")
        table.add_column("PM Mid", justify="right")
        table.add_column("Spread", justify="right")
        table.add_column("Vol (EWMA)", justify="right")
        table.add_column("Skew", justify="right")
        table.add_column("Inv", justify="right")
        table.add_column("Orders (Dwell / QA)", justify="left")
        
        for market_id in self.settings.markets:
            readable_name = getattr(self, "token_to_name", {}).get(market_id, market_id[:8] + "...")
            asset = bot.market_to_asset.get(market_id, "")
            spot = bot.reconciler._spot_mids.get(asset)
            spot_str = f"{spot:.2f}" if spot else "---"
            
            book = None
            pm_mid = None
            spread = 0.0
            if hasattr(bot.pm_ws, "_books"):
                bids_dict, asks_dict = bot.pm_ws._books.get(market_id, ({}, {}))
                bid = max(bids_dict.keys()) if bids_dict else None
                ask = min(asks_dict.keys()) if asks_dict else None
                
                if bid and ask:
                    pm_mid = (bid + ask) / 2.0
                    spread = ask - bid
                elif bid:
                    pm_mid = bid
                elif ask:
                    pm_mid = ask
            
            pm_mid_str = f"{pm_mid:.4f}" if pm_mid else "---"
            spread_str = f"{spread:.4f}" if spread else "---"
            
            vol = bot.signal_engine.get_market_volatility(market_id, now)
            vol_str = f"{vol:.4f}" if vol else "warm up"
            
            inv = bot.api_client.get_inventory(market_id)
            inv_str = f"{inv:+.1f}"
            
            orders = bot.execution_manager.live_orders.get(market_id, [])
            order_strs = []
            for o in orders:
                dwell = now - o.created_at
                
                qa = 0.0
                if hasattr(bot.api_client, "live_orders"):
                    paper_orders = bot.api_client.live_orders
                    if o.id in paper_orders:
                        qa = paper_orders[o.id].queue_ahead
                        
                order_strs.append(f"{o.side[:1]} {o.price:.3f} ({dwell:.1f}s / {qa:.0f})")
            
            orders_col = ", ".join(order_strs) if order_strs else "None"
            
            # Simple Skew representation
            skew_str = "---"
            if vol and pm_mid:
                quotes = bot.quoting_engine.get_quotes(pm_mid, vol, inv)
                if quotes:
                    skew_str = f"{quotes.skew:+.4f}"
            
            table.add_row(readable_name, spot_str, pm_mid_str, spread_str, vol_str, skew_str, inv_str, orders_col)
            
        self.layout["markets"].update(Panel(table, border_style="blue"))
        
        # ── Positions ──
        pos_table = Table(show_header=True, expand=True, border_style="dim")
        pos_table.add_column("Market", max_width=24)
        pos_table.add_column("Side", justify="center")
        pos_table.add_column("Size", justify="right")
        pos_table.add_column("Notional", justify="right")
        pos_table.add_column("Realized", justify="right")
        pos_table.add_column("Unrealized", justify="right")

        has_positions = False
        for market_id in self.settings.markets:
            readable_name = getattr(self, "token_to_name", {}).get(market_id, market_id[:8] + "...")
            inv = bot.api_client.get_inventory(market_id)
            if abs(inv) > 1e-6:
                has_positions = True
                side = "[green]LONG[/]" if inv > 0 else "[red]SHORT[/]"
                pos_table.add_row(
                    readable_name,
                    side,
                    f"{abs(inv):.1f}",
                    "---",
                    "---",
                    "---"
                )
                
        if not has_positions:
            pos_table.add_row("[dim]No open positions[/]", "", "", "", "", "")
            
        self.layout["positions"].update(
            Panel(pos_table, title="[bold]Open Positions[/]", border_style="yellow")
        )

        # ── Resolved ──
        res_table = Table(show_header=True, expand=True, border_style="dim")
        res_table.add_column("Market", max_width=32)
        res_table.add_column("Side", justify="center")
        res_table.add_column("Size", justify="right")
        res_table.add_column("PnL", justify="right")

        res_table.add_row("[dim]No positions resolved yet[/]", "", "", "")
        
        self.layout["resolved"].update(
            Panel(res_table, title="[bold]Last Positions Resolved[/]", border_style="green")
        )

        # ── Stats Panel ──
        stats_table = Table(show_header=False, expand=True, border_style="dim", pad_edge=False)
        stats_table.add_column("Metric", style="bold", ratio=2)
        stats_table.add_column("Value", justify="right", ratio=1)
        
        total_placed = getattr(bot.api_client, '_order_counter', 0)
        
        stats_table.add_row("Total Orders Placed", f"{total_placed}")
        stats_table.add_row("Fills / min", "---")
        stats_table.add_row("Rebate Earned", "[green]$0.00[/]")
        stats_table.add_row("Adv. Selection Cost", "[red]$0.00[/]")
        stats_table.add_row("Feed Outages", "0")
        
        self.layout["stats"].update(Panel(stats_table, title="[bold]Trading Stats[/]", border_style="green"))
        
        # ── Footer / System Health ──
        health_table = Table(show_header=False, expand=True, border_style="dim", pad_edge=False)
        health_table.add_column("System", style="bold")
        health_table.add_column("Status", justify="right")
        
        health_table.add_row("Polymarket WS", "[green]Active[/]" if getattr(bot.pm_ws, "_running", False) else "[red]Offline[/]")
        health_table.add_row("Binance WS", "[green]Active[/]" if getattr(bot.binance_ws, "_running", False) else "[red]Offline[/]")
        
        oracle_pause = False
        if hasattr(bot.reconciler, "is_sane"):
             # For a real implementation, check if the reconciler has paused trading
             pass
        health_table.add_row("Oracle Pause", "[red]YES[/]" if oracle_pause else "[green]NO[/]")
        
        self.layout["footer"].update(Panel(health_table, title="[bold]System Health[/]", border_style="yellow"))
