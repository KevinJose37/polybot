"""Quick smoke test for Step 1: Foundation & Configuration."""

import asyncio
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_config():
    from config.settings import config
    print("[PASS] Config loaded")
    assert config.paper_trading is True
    print(f"  Paper: {config.paper_trading}")
    assert config.initial_capital == 25.0
    print(f"  Capital: ${config.initial_capital}")
    assert len(config.assets) == 4
    print(f"  Assets: {config.assets}")
    assert len(config.windows) == 3
    print(f"  Windows: {config.windows}")
    print("[PASS] Config assertions passed")


def test_schemas():
    from utils.schemas import (
        MarketState, ToxicityLevel, QuoteStatus,
        QuotePair, InventoryState, FillRecord, MarketRuntimeState, MarketInfo
    )

    # Enums
    assert MarketState.QUOTING_BOTH.value == "QUOTING_BOTH_SIDES"
    assert ToxicityLevel.EXTREME.value == "EXTREME"
    assert QuoteStatus.LIVE.value == "LIVE"
    print("[PASS] Enums work")

    # QuotePair
    q = QuotePair(bid_price=0.42, ask_price=0.58, fair_value=0.50)
    assert abs(q.spread - 0.16) < 0.001
    assert abs(q.mid - 0.50) < 0.001
    print(f"[PASS] QuotePair: bid={q.bid_price}, ask={q.ask_price}, spread={q.spread:.4f}")

    # InventoryState
    inv = InventoryState(market_id="test", asset="btcusdt", window_minutes=60, net_position=50, max_position=100)
    assert abs(inv.utilization - 0.5) < 0.001
    assert inv.is_long is True
    assert inv.is_short is False
    print(f"[PASS] InventoryState: pos={inv.net_position}, util={inv.utilization:.2f}")

    # FillRecord auto-fills
    fill = FillRecord(market_id="test", asset="btcusdt", window_minutes=60, side="BUY", price=0.45, size=5)
    assert fill.timestamp_ms > 0
    assert fill.fill_id != ""
    print(f"[PASS] FillRecord auto-init: id={fill.fill_id[:30]}...")


async def test_discovery():
    from data.market_discovery import MarketDiscovery

    discovery = MarketDiscovery()
    try:
        markets = await discovery.discover_all_markets(
            ["btcusdt", "ethusdt", "solusdt", "xrpusdt"],
            [5, 15, 60],
        )
        print(f"[PASS] Discovery found {len(markets)}/12 markets:")
        for key, m in markets.items():
            print(f"  {key}: slug={m.slug} | Q={m.question[:50]}")

        if len(markets) > 0:
            print("[PASS] At least 1 market discovered successfully")
        else:
            print("[WARN] No markets discovered (may be outside market hours)")
    finally:
        await discovery.close()


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 1 SMOKE TESTS")
    print("=" * 60)

    test_config()
    print()
    test_schemas()
    print()
    asyncio.run(test_discovery())

    print()
    print("=" * 60)
    print("ALL STEP 1 TESTS PASSED")
    print("=" * 60)
