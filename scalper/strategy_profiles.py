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

    # ── Position limits ────────────────────────────────────────
    max_open_positions: int = 4      # max simultaneous positions
    best_signal_only: bool = False   # only enter on strongest signal per cycle

    # ── Chainlink-specific (v3) ────────────────────────────────
    chainlink_delta_threshold: float = 0.05  # % delta to trigger signal
    chainlink_confirm_readings: int = 3       # sustained readings needed
    use_technical_confirmation: bool = False   # require tech signal alignment

    # ── Polymarket price filter (v4) ──────────────────────────
    poly_price_filter: bool = False    # reject entry if market already priced in
    poly_price_cap: float = 0.62       # max poly price in your direction to enter

    # ── Price band filter (v2opt3) ──────────────────────────────
    min_entry_price: float = 0.0       # block entry if price below this (market decided)

    # ── Signal score ceiling (v2opt3) ────────────────────────────
    max_signal_score: float = 1.0      # block entry if |score| above this (momentum exhausted)
    
    # ── Execution filters ──────────────────────────────────────────
    max_spread: float = 0.03           # Max bid-ask spread for entry

    # ── Smart Inversion (Falling Knife Protection) ─────────────
    smart_invert_threshold: float = 0.0  # invert signal if original entry price is below this

    # ── Orderbook velocity confirmation (v2opt3) ───────────────────
    velocity_confirmation: bool = False  # require Polymarket book to move in signal direction
    velocity_window_sec: int = 30        # look-back window in seconds
    velocity_threshold: float = 0.02    # minimum |velocity| to confirm (e.g. $0.02 move in 30s)

    # ── Hold-to-resolution mode ────────────────────────────────
    hold_to_resolution: bool = False   # skip all TP/SL/reversal exits; let market resolve

    # ── V5 Smart Execution Filters ─────────────────────────────
    filter_accel_decay: bool = False
    filter_imbalance: bool = False
    filter_fake_momentum: bool = False
    filter_reversal: bool = False
    penalty_per_failed_filter: float = 0.10

    # ── V8 Choppy Mean Reversion ───────────────────────────────
    regime_window: int = 5               # cycles to detect regime
    regime_trending_threshold: int = 4   # of N cycles, how many same-dir = TRENDING
    reversion_threshold: float = 0.008   # min deviation from MA to enter
    ma_window_sec: int = 60              # SMA lookback in seconds
    time_stop_sec: int = 0               # exit if not resolved in N seconds (0 = disabled)
    
    # ── V11 Sniper ──────────────────────────────────────────────────
    sniper_trigger_price: float = 0.53

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
        min_entry_price=0.30,
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
        min_entry_price=0.30,
        poly_price_filter=True,
        poly_price_cap=0.65,
    ),
    "v3": StrategyProfile(
        name="v3",
        label="V3 — Chainlink Delta (Late Entry)",
        trades_file="hft_trades_v3.json",
        signal_source="chainlink_delta",
        signal_threshold=0.30,
        entry_mode="late",
        entry_window_start=120,
        entry_window_end=270,
        take_profit=0.35,
        stop_loss=0.30,
        signal_reversal=0.60,
        trailing_stop=True,
        trailing_trigger=0.20,
        sizing="delta_scaled",
        base_stake=10.0,
        chainlink_delta_threshold=0.012,
        chainlink_confirm_readings=3,
        use_technical_confirmation=False,
        min_entry_price=0.30,
        smart_invert_threshold=0.45,
    ),

    # ── Optimized variants (position limits + best signal) ────
    "v1opt": StrategyProfile(
        name="v1opt",
        label="V1-OPT — Technical Scalper (Best Signal + Max 2 Pos)",
        trades_file="hft_trades_v1opt.json",
        signal_source="technical",
        signal_threshold=0.40,
        entry_mode="anytime",
        take_profit=0.15,
        stop_loss=0.30,
        signal_reversal=0.60,
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=2,
        best_signal_only=True,
        min_entry_price=0.30,
    ),
    "v2opt": StrategyProfile(
        name="v2opt",
        label="V2-OPT — Enhanced Technical (Best Signal + Max 3 Pos)",
        trades_file="hft_trades_v2opt.json",
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
        max_open_positions=3,
        best_signal_only=True,
        min_entry_price=0.30,
        max_signal_score=0.80,
    ),

    # ── V4: Real-time ticks + Polymarket signal ───────────────
    "v4": StrategyProfile(
        name="v4",
        label="V4 — Real-Time Ticks + Polymarket Signal",
        trades_file="hft_trades_v4.json",
        signal_source="ticks_v4",
        signal_threshold=0.35,
        entry_mode="anytime",
        take_profit=0.20,
        stop_loss=0.30,
        signal_reversal=0.60,
        trailing_stop=True,
        trailing_trigger=0.15,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=2,
        best_signal_only=True,
        poly_price_filter=True,
        poly_price_cap=0.62,
        min_entry_price=0.30,
    ),

    # ── V2OPT2: Hold-to-resolution, early entry only, tight price cap ─
    "v2opt2": StrategyProfile(
        name="v2opt2",
        label="V2OPT2 — Hold-to-Resolution (2min window, $0.58 cap)",
        trades_file="hft_trades_v2opt2.json",
        signal_source="technical_v2",
        signal_threshold=0.40,
        entry_mode="anytime",
        entry_window_end=120,          # Only enter in the first 2 minutes
        take_profit=0.35,              # Ignored — hold_to_resolution overrides
        stop_loss=0.30,               # Ignored — hold_to_resolution overrides
        signal_reversal=0.60,          # Ignored — hold_to_resolution overrides
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=2,
        best_signal_only=True,
        poly_price_filter=True,
        poly_price_cap=0.58,
        min_entry_price=0.30,
        max_signal_score=0.80,
        hold_to_resolution=True,
    ),

    # ── V2OPT3: Polymarket velocity-first, price band, score ceiling ───
    "v2opt3": StrategyProfile(
        name="v2opt3",
        label="V2OPT3 — Velocity-First (Poly orderbook > Binance)",
        trades_file="hft_trades_v2opt3.json",
        signal_source="technical_v2",     # Binance signal as secondary filter
        signal_threshold=0.35,             # min |score| to pass
        entry_mode="late",                 # use entry_window_start/end
        entry_window_start=20,             # skip first 20s (allow book to form)
        entry_window_end=180,              # no entry after 3 min (market decided)
        take_profit=0.35,                  # ignored — hold_to_resolution overrides
        stop_loss=0.30,                    # ignored — hold_to_resolution overrides
        signal_reversal=0.60,              # ignored — hold_to_resolution overrides
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=3,
        best_signal_only=True,
        # Price band
        poly_price_filter=True,
        poly_price_cap=0.65,               # max price (upper band)
        min_entry_price=0.32,              # min price (lower band — market not yet decided)
        # Score ceiling (blocks exhausted momentum)
        max_signal_score=0.80,
        # Velocity gate
        velocity_confirmation=True,
        velocity_window_sec=30,
        velocity_threshold=0.02,
        # Always hold to resolution
        hold_to_resolution=True,
    ),

    # ── V5: Smart Execution with Soft Penalty Filters ──────────
    "v5": StrategyProfile(
        name="v5",
        label="V5 — Smart Execution (Soft Penalty Filters)",
        trades_file="hft_trades_v5.json",
        signal_source="ticks_v4",       # Binance ticks for fast velocity calculation
        signal_threshold=0.30,          # Lower base threshold
        entry_mode="anytime",
        take_profit=0.35,               # Ignored — hold_to_resolution overrides
        stop_loss=0.30,                 # Ignored — hold_to_resolution overrides
        signal_reversal=0.60,           # Ignored — hold_to_resolution overrides
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=3,
        best_signal_only=True,
        poly_price_filter=True,
        poly_price_cap=0.65,
        min_entry_price=0.30,
        # Smart filters
        filter_accel_decay=True,
        filter_imbalance=True,
        filter_fake_momentum=True,
        filter_reversal=True,
        penalty_per_failed_filter=0.15, # Deduct from signal score if a filter fails
        hold_to_resolution=True,
    ),

    # ── COPY: Blind Copy Trading ──────────
    "copy": StrategyProfile(
        name="copy",
        label="Copy Trading",
        trades_file="hft_trades_copy.json",
        signal_source="none",
        signal_threshold=0.0,
        entry_mode="anytime",
        base_stake=1.0, 
        poly_price_filter=False, 
        max_signal_score=2.0,
        hold_to_resolution=True,
    ),
    
    # ── V6: Early Scalper (Quick Take Profit) ──────────
    "v6": StrategyProfile(
        name="v6",
        label="V6 — Early Scalper (Poly Velocity)",
        trades_file="hft_trades_v6.json",
        signal_source="poly_velocity",   # Polymarket orderbook velocity (instant)
        signal_threshold=0.15,           # $0.0075 mid-price move in 10s
        entry_mode="late",
        entry_window_start=5,            # Ready at second 5 (WS warms in ~3s)
        entry_window_end=120,            # Strict 120s window
        take_profit=0.08,                # Tighter TP: $0.50→$0.54 (achievable)
        stop_loss=0.20,                  # Stop loss at 20%
        signal_reversal=0.60,            # Reversal exit
        trailing_stop=True,
        trailing_trigger=0.05,           # Start trailing at +5%
        sizing="flat",
        base_stake=10.0,
        max_open_positions=99,           # No limit on positions
        best_signal_only=False,          # Enter the 2 best that pass
        poly_price_filter=True,
        poly_price_cap=0.55,             # Widened: velocity signal justifies more
        min_entry_price=0.45,            # Widened: capture more opportunities
        max_signal_score=2.0,
        hold_to_resolution=False,        # Active trading (TP/SL)
    ),

    # ── V7: Production Bot (Camino B: Binance-Guided + Poly Confirmation) ─
    # Signal:   Binance Technical V2 (proven to generate reliable early signals)
    # Filter:   Polymarket velocity (blocks entry ONLY if it strongly contradicts)
    # Timing:   0-120s entry window (momentum is freshest here)
    # Exit:     Hold-to-resolution (mathematically proven superior)
    "v7": StrategyProfile(
        name="v7",
        label="V7 — Production (Binance Signal + Poly Confirm + Hold)",
        trades_file="hft_trades_v7.json",
        signal_source="technical_v2",   # Primary signal
        signal_threshold=0.16,          # Lowered from 0.40 to fit typical Binance EMA ranges
        velocity_confirmation=True,     # Secondary filter
        velocity_threshold=0.05,        # Low threshold (only block strong contradictions)
        entry_mode="anytime",
        entry_window_end=120,
        take_profit=99.0,               # Effectively disabled (hold to resolution)
        stop_loss=99.0,                 # Effectively disabled (hold to resolution)
        signal_reversal=99.0,           # Effectively disabled (hold to resolution)
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=2,
        best_signal_only=True,
        poly_price_filter=True,
        poly_price_cap=0.65,            # Max entry price (widened — 0.54 produced 0 trades overnight)
        min_entry_price=0.35,           # Min entry price (widened — 0.46 too restrictive)
        max_signal_score=2.0,
        hold_to_resolution=True,        # THE key insight: never sell early
    ),
    "v8": StrategyProfile(
        name="v8",
        label="V8 — Choppy Mean Reversion",
        trades_file="hft_trades_v8.json",
        signal_source="mean_reversion_v8",
        signal_threshold=0.20,           # Lower threshold (reversion signals are subtler)
        entry_mode="anytime",
        entry_window_end=120,            # Only first 2 min (fast entries)
        take_profit=0.12,                # Tight TP (small moves in choppy)
        stop_loss=0.15,                  # Tight SL (mandatory in choppy)
        signal_reversal=0.60,
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=2,
        best_signal_only=True,
        poly_price_filter=True,
        poly_price_cap=0.65,             # Widened from 0.58 (too tight overnight)
        min_entry_price=0.35,            # Widened from 0.42 (missed entries)
        hold_to_resolution=False,        # NEVER hold in choppy — always TP/SL
        regime_window=5,                 
        regime_trending_threshold=4,
        reversion_threshold=0.002,       # Relaxed from 0.004 to catch "trending-within-choppy"
        ma_window_sec=60,
        time_stop_sec=90,                # sell at market after 90s
    ),

    # ── V9: Meta Strategy (Context-Aware Selector) ─────────────
    # Uses V2opt3's execution engine but adds a historical lookup gate.
    # Signal:  technical_v2 (same as V2opt3) + V9 context-aware confidence gate
    # Gate:    Lookup table built from ~1,100 historical trades
    # Action:  BLOCK entries in historically bad contexts, PENALIZE marginal ones
    # Fallback: V2opt3 behavior when lookup has insufficient data
    "v9": StrategyProfile(
        name="v9",
        label="V9 — Meta Strategy (Context-Aware Selector)",
        trades_file="hft_trades_v9.json",
        signal_source="meta_v9",           # Routes to signals_v9.py
        signal_threshold=0.35,             # Same as V2opt3
        entry_mode="late",
        entry_window_start=20,
        entry_window_end=180,
        take_profit=0.35,
        stop_loss=0.30,
        signal_reversal=0.60,
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=2,              # Conservative: 2 instead of 3
        best_signal_only=True,
        poly_price_filter=True,
        poly_price_cap=0.65,
        min_entry_price=0.32,
        max_signal_score=0.80,
        velocity_confirmation=True,
        velocity_window_sec=30,
        velocity_threshold=0.02,
        hold_to_resolution=True,
    ),
    # ── V11: Spread Sniper (Frontrun Static Imbalance) ─────────
    "v11": StrategyProfile(
        name="v11",
        label="V11 — Spread Sniper (Frontrun Initial Imbalance)",
        trades_file="hft_trades_v11.json",
        signal_source="poly_sniper",     
        signal_threshold=0.5,            # Need 1.0 from our sniper signal
        entry_mode="late",               
        entry_window_start=5,            # Wait 5 seconds for book to populate
        entry_window_end=120,            # Ampliado a 120 segundos
        take_profit=0.20,                # TP Gigante ($0.20 por contrato)
        stop_loss=99.0,                  # Sin Stop Loss (solo TP o resolución final)
        signal_reversal=99.0,            # Sin salidas por reversión
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=99,
        best_signal_only=False,
        poly_price_filter=True,          # V11: FILTRO ACTIVADO
        poly_price_cap=0.55,             # Tope máximo absoluto: $0.55 (no comprar caro)
        min_entry_price=0.05,            # Min entry
        sniper_trigger_price=0.505,      # Triggers immediately on 0.51 (using 0.505 for float safety)
        max_spread=0.06,                 # Permitir spreads iniciales más grandes
        hold_to_resolution=False,        # Quick TP is the goal
    ),
    # ── V12: Event-Driven Sniper (Zero-Latency) ─────────
    "v12": StrategyProfile(
        name="v12",
        label="V12 — Event-Driven Sniper (Zero-Latency)",
        trades_file="hft_trades_v12.json",
        signal_source="poly_sniper_v12",     
        signal_threshold=0.5,            
        entry_mode="late",               
        entry_window_start=0,            # Triggered by WS callback
        entry_window_end=120,            
        take_profit=0.20,                
        stop_loss=99.0,                  
        signal_reversal=99.0,            
        trailing_stop=False,
        sizing="flat",
        base_stake=10.0,
        max_open_positions=99,
        best_signal_only=False,
        poly_price_filter=True,          
        poly_price_cap=0.55,             
        min_entry_price=0.05,            
        sniper_trigger_price=0.505,      
        max_spread=0.06,                 
        hold_to_resolution=False,        
    ),
}


def get_profile(strategy: str) -> StrategyProfile:
    """Get strategy profile by name. Defaults to v1."""
    return PROFILES.get(strategy, PROFILES["v1"])
