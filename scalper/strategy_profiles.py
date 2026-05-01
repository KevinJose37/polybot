"""
scalper/strategy_profiles.py — Perfiles de configuración por estrategia.

Cada estrategia (v1, v2, v3) tiene su propio perfil con:
  - Archivo de trades separado
  - Reglas de entry/exit
  - Fuente de señal
  - Método de sizing
"""

from dataclasses import dataclass, field


@dataclass
class StrategyProfile:
    """Configuration profile for a trading strategy version."""

    name: str
    label: str                   # Display name for dashboard
    trades_file: str

    # ── Signal ─────────────────────────────────────────────────
    signal_source: str           # "technical", "technical_v2", "chainlink_delta"
    signal_threshold: float      # Minimum |score| to enter

    # ── Entry timing ───────────────────────────────────────────
    entry_mode: str              # "anytime" or "late"
    entry_window_start: int = 0  # seconds elapsed before entry allowed (late mode)
    entry_window_end: int = 270  # seconds elapsed after which entry blocked

    # ── Exit rules ─────────────────────────────────────────────
    take_profit: float = 0.15
    stop_loss: float = 0.30
    signal_reversal: float = 0.60
    trailing_stop: bool = False
    trailing_trigger: float = 0.20  # move SL to break-even at this %

    # ── Sizing ─────────────────────────────────────────────────
    sizing: str = "flat"         # "flat", "kelly", "delta_scaled"
    base_stake: float = 10.0
    max_position_pct: float = 0.05  # max 5% of capital per trade

    # ── Chainlink-specific (v3) ────────────────────────────────
    chainlink_delta_threshold: float = 0.05  # % delta to trigger signal
    chainlink_confirm_readings: int = 3       # sustained readings needed
    use_technical_confirmation: bool = False   # require tech signal alignment


# ═══════════════════════════════════════════════════════════════
# Strategy Profiles
# ═══════════════════════════════════════════════════════════════


PROFILES: dict[str, StrategyProfile] = {
    "v1": StrategyProfile(
        name="v1",
        label="V1 — Technical Scalper (Original)",
        trades_file="hft_trades.json",
        signal_source="technical",
        signal_threshold=0.40,
        entry_mode="anytime",
        take_profit=0.15,
        stop_loss=0.30,
        signal_reversal=0.60,
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
    ),
    "v2": StrategyProfile(
        name="v2",
        label="V2 — Enhanced Technical + Trailing Stop + Kelly",
        trades_file="hft_trades_v2.json",
        signal_source="technical_v2",
        signal_threshold=0.40,
        entry_mode="anytime",
        take_profit=0.35,
        stop_loss=0.30,
        signal_reversal=0.60,
        trailing_stop=True,
        trailing_trigger=0.20,
        sizing="kelly",
        base_stake=10.0,
    ),
    "v3": StrategyProfile(
        name="v3",
        label="V3 — Chainlink Delta (Late Entry)",
        trades_file="hft_trades_v3.json",
        signal_source="chainlink_delta",
        signal_threshold=0.05,
        entry_mode="late",
        entry_window_start=210,
        entry_window_end=270,
        take_profit=0.35,
        stop_loss=0.30,
        signal_reversal=0.60,
        trailing_stop=True,
        trailing_trigger=0.20,
        sizing="delta_scaled",
        base_stake=10.0,
        chainlink_delta_threshold=0.05,
        chainlink_confirm_readings=3,
        use_technical_confirmation=True,
    ),
}


def get_profile(strategy: str) -> StrategyProfile:
    """Get strategy profile by name. Defaults to v1."""
    return PROFILES.get(strategy, PROFILES["v1"])
