"""
tests/test_step4.py — Tests for Inventory & Risk Management.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.inventory_manager import InventoryManager
from risk.exposure import ExposureTracker
from utils.pnl_engine import PnLEngine
from utils.schemas import (
    InventoryState, MarketState, FillRecord, PnLBreakdown,
)


def test_inventory_zero():
    """Zero inventory -> normal quoting, no skew."""
    print("[TEST] InventoryManager: zero inventory")

    mgr = InventoryManager()
    inv = mgr.get_or_create("btcusdt_60", "btcusdt", 60)

    assert inv.net_position == 0
    assert inv.utilization == 0.0
    assert mgr.get_quoting_mode("btcusdt_60") == MarketState.QUOTING_BOTH
    assert mgr.get_skew_boost("btcusdt_60") == 1.0
    one_sided, _ = mgr.should_one_sided_quote("btcusdt_60")
    assert not one_sided
    print("  [PASS] Zero inventory: normal quoting, skew_boost=1.0")


def test_inventory_soft_limit():
    """65% utilization -> increased skew boost (above 60% soft limit)."""
    print("\n[TEST] InventoryManager: above soft limit (65%)")

    mgr = InventoryManager()
    inv = mgr.get_or_create("btcusdt_60", "btcusdt", 60)

    # Simulate 65 fills (above the 60% soft limit)
    for i in range(65):
        fill = FillRecord(
            market_id="test", asset="btcusdt", window_minutes=60,
            side="BUY", price=0.50, size=1,
        )
        mgr.record_fill("btcusdt_60", fill)

    assert inv.net_position == 65
    assert abs(inv.utilization - 0.65) < 0.01
    boost = mgr.get_skew_boost("btcusdt_60")
    assert boost > 1.0, f"Boost should be > 1.0 above soft limit, got {boost}"
    print(f"  [PASS] Above soft limit: pos={inv.net_position}, util={inv.utilization:.1%}, boost={boost:.2f}")


def test_inventory_hard_limit():
    """85% utilization -> one-sided quoting."""
    print("\n[TEST] InventoryManager: hard limit (85%)")

    mgr = InventoryManager()
    inv = mgr.get_or_create("btcusdt_60", "btcusdt", 60)

    for i in range(85):
        fill = FillRecord(
            market_id="test", asset="btcusdt", window_minutes=60,
            side="BUY", price=0.50, size=1,
        )
        mgr.record_fill("btcusdt_60", fill)

    assert inv.net_position == 85
    mode = mgr.get_quoting_mode("btcusdt_60")
    assert mode == MarketState.ONE_SIDED, f"Expected ONE_SIDED, got {mode}"
    one_sided, blocked = mgr.should_one_sided_quote("btcusdt_60")
    assert one_sided
    assert blocked == "BUY", f"Should block BUY side when long, got {blocked}"
    print(f"  [PASS] Hard limit: pos={inv.net_position}, mode={mode.value}, blocked={blocked}")


def test_inventory_emergency():
    """100% utilization -> circuit breaker."""
    print("\n[TEST] InventoryManager: emergency (100%)")

    mgr = InventoryManager()
    inv = mgr.get_or_create("btcusdt_60", "btcusdt", 60)

    for i in range(100):
        fill = FillRecord(
            market_id="test", asset="btcusdt", window_minutes=60,
            side="BUY", price=0.50, size=1,
        )
        mgr.record_fill("btcusdt_60", fill)

    assert inv.net_position == 100
    assert mgr.needs_emergency_exit("btcusdt_60")
    mode = mgr.get_quoting_mode("btcusdt_60")
    assert mode == MarketState.EMERGENCY
    print(f"  [PASS] Emergency: pos={inv.net_position}, mode={mode.value}")


def test_pnl_round_trip():
    """Buy at bid, sell at ask -> spread PnL = spread earned."""
    print("\n[TEST] PnLEngine: round-trip spread PnL")

    pnl = PnLEngine()

    buy = FillRecord(
        market_id="test", asset="btcusdt", window_minutes=60,
        side="BUY", price=0.45, size=10, fee=0.0,
    )
    sell = FillRecord(
        market_id="test", asset="btcusdt", window_minutes=60,
        side="SELL", price=0.55, size=10, fee=0.0,
    )

    pnl.record_fill("btcusdt_60", buy)
    pnl.record_fill("btcusdt_60", sell)

    result = pnl.get_pnl()
    expected_spread = (0.55 - 0.45) * 10  # = 1.0
    assert abs(result.spread_pnl - expected_spread) < 0.001, \
        f"Spread PnL: {result.spread_pnl} != {expected_spread}"
    assert abs(result.total_pnl - expected_spread) < 0.001
    print(f"  [PASS] Spread PnL: ${result.spread_pnl:.4f} (expected ${expected_spread:.4f})")


def test_pnl_with_fees():
    """Round trip with maker fees."""
    print("\n[TEST] PnLEngine: round-trip with fees")

    pnl = PnLEngine()

    buy = FillRecord(
        market_id="test", asset="btcusdt", window_minutes=60,
        side="BUY", price=0.45, size=5, fee=0.01,
    )
    sell = FillRecord(
        market_id="test", asset="btcusdt", window_minutes=60,
        side="SELL", price=0.55, size=5, fee=0.01,
    )

    pnl.record_fill("btcusdt_60", buy)
    pnl.record_fill("btcusdt_60", sell)

    result = pnl.get_pnl()
    expected_spread = (0.55 - 0.45) * 5  # = 0.5
    expected_fees = -0.02  # Two fills, 0.01 each
    expected_total = expected_spread + expected_fees

    assert abs(result.spread_pnl - expected_spread) < 0.001
    assert abs(result.fee_pnl - expected_fees) < 0.001
    assert abs(result.total_pnl - expected_total) < 0.001
    print(f"  [PASS] Spread: ${result.spread_pnl:.4f}, Fees: ${result.fee_pnl:.4f}, "
          f"Total: ${result.total_pnl:.4f}")


def test_pnl_inventory_mtm():
    """Inventory PnL from MTM."""
    print("\n[TEST] PnLEngine: inventory MTM")

    pnl = PnLEngine()

    # Buy 10 at 0.45, don't sell yet
    buy = FillRecord(
        market_id="test", asset="btcusdt", window_minutes=60,
        side="BUY", price=0.45, size=10, fee=0.0,
    )
    pnl.record_fill("btcusdt_60", buy)

    # Create inventory state
    inv = InventoryState(
        market_id="test", asset="btcusdt", window_minutes=60,
        net_position=10, max_position=100, avg_entry_price=0.45,
    )

    # Fair value moved to 0.55 -> unrealized profit
    pnl.update_inventory_pnl(
        {"btcusdt_60": inv},
        {"btcusdt_60": 0.55},
    )

    result = pnl.get_pnl()
    expected_inv = (0.55 - 0.45) * 10  # = 1.0
    assert abs(result.inventory_pnl - expected_inv) < 0.001
    assert abs(result.unrealized_pnl - expected_inv) < 0.001
    print(f"  [PASS] Inventory PnL: ${result.inventory_pnl:.4f} (expected ${expected_inv:.4f})")


def test_exposure_tracker():
    """Exposure tracking basics."""
    print("\n[TEST] ExposureTracker: basic operations")

    exp = ExposureTracker(initial_capital=25.0)
    assert exp.available_capital == 25.0
    assert exp.capital_in_use == 0.0

    exp.record_buy(5.0)
    assert exp.available_capital == 20.0
    assert exp.capital_in_use == 5.0
    print(f"  [PASS] After buy: available=${exp.available_capital}, in_use=${exp.capital_in_use}")

    exp.record_sell(6.0)
    assert exp.available_capital == 26.0
    print(f"  [PASS] After sell: available=${exp.available_capital}")

    exp.record_realized_pnl(1.0)
    assert exp.realized_pnl == 1.0
    print(f"  [PASS] Realized PnL: ${exp.realized_pnl}")


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 4 TESTS: Inventory & Risk Management")
    print("=" * 60)

    test_inventory_zero()
    test_inventory_soft_limit()
    test_inventory_hard_limit()
    test_inventory_emergency()
    test_pnl_round_trip()
    test_pnl_with_fees()
    test_pnl_inventory_mtm()
    test_exposure_tracker()

    print()
    print("=" * 60)
    print("ALL STEP 4 TESTS PASSED")
    print("=" * 60)
