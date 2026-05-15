"""
Formatters for dashboard display values.
"""


def format_price(price: float | None) -> str:
    """Format a price for display, showing '---' for missing values."""
    return f"{price:.4f}" if price is not None else "---"


def format_pnl(pnl: float) -> str:
    """Format PnL with color markup for Rich."""
    if pnl >= 0:
        return f"[green]+${pnl:,.2f}[/]"
    return f"[red]-${abs(pnl):,.2f}[/]"


def format_percentage(value: float) -> str:
    """Format a ratio as a percentage string."""
    return f"{value * 100:.2f}%"


def format_exposure(current: float, limit: float) -> str:
    """Format exposure with utilization coloring."""
    pct = (current / limit * 100) if limit > 0 else 0
    color = "green" if pct < 70 else "yellow" if pct < 90 else "red"
    return f"[{color}]${current:,.2f}[/] / ${limit:,.2f} ({pct:.0f}%)"


def format_uptime(seconds: float) -> str:
    """Format seconds into HH:MM:SS."""
    s = int(seconds)
    h, remainder = divmod(s, 3600)
    m, sec = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def format_side(side: str) -> str:
    """Format BUY/SELL with color."""
    if side == "BUY":
        return "[green]BUY[/]"
    return "[red]SELL[/]"
