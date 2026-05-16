"""Tests for market topology builder."""
from bot.market_discovery.market_relationships import build_topology, MarketTopology
from bot.api.schemas import MarketSnapshot, Token


def _make_market(market_id: str, slug: str) -> MarketSnapshot:
    """Helper to create a minimal MarketSnapshot for testing."""
    return MarketSnapshot(
        id=market_id,
        slug=slug,
        question=f"Will price go up? ({slug})",
        active=True,
        closed=False,
        tokens=[
            Token(token_id=f"{market_id}_yes", outcome="Yes"),
            Token(token_id=f"{market_id}_no", outcome="No"),
        ]
    )


def test_topology_parity_markets() -> None:
    """All valid markets should be parity targets."""
    markets = [
        _make_market("m1", "btc-updown-5m-1778774100"),
        _make_market("m2", "btc-updown-15m-1778773500"),
        _make_market("m3", "eth-updown-5m-1778774100"),
    ]
    topo = build_topology(markets)
    
    assert set(topo.parity_markets) == {"m1", "m2", "m3"}
    assert len(topo.markets) == 3


def test_topology_monotonicity_pairs_different_timestamps() -> None:
    """
    5m and 15m markets with DIFFERENT timestamps (different grid sizes)
    should still be paired by asset for monotonicity.
    """
    markets = [
        # BTC 5m at timestamp 1778774100 (divisible by 300)
        _make_market("btc_5m", "btc-updown-5m-1778774100"),
        # BTC 15m at timestamp 1778773500 (divisible by 900) — DIFFERENT timestamp
        _make_market("btc_15m", "btc-updown-15m-1778773500"),
    ]
    topo = build_topology(markets)
    
    # Should have one monotonicity pair: (5m, 15m) for BTC
    assert len(topo.monotonicity_pairs) == 1
    assert topo.monotonicity_pairs[0] == ("btc_5m", "btc_15m")


def test_topology_monotonicity_multiple_windows() -> None:
    """Multiple 5m and 15m markets for same asset produce all combinations."""
    markets = [
        _make_market("btc_5m_a", "btc-updown-5m-1778774100"),
        _make_market("btc_5m_b", "btc-updown-5m-1778774400"),
        _make_market("btc_15m_a", "btc-updown-15m-1778773500"),
        _make_market("btc_15m_b", "btc-updown-15m-1778774400"),
    ]
    topo = build_topology(markets)
    
    # 2 × 2 = 4 cross-product pairs
    assert len(topo.monotonicity_pairs) == 4
    # Every pair should be (5m_market, 15m_market)
    for m5, m15 in topo.monotonicity_pairs:
        assert "5m" in m5
        assert "15m" in m15


def test_topology_monotonicity_incomplete_asset() -> None:
    """Asset with only 5m (no 15m) should not appear in monotonicity pairs."""
    markets = [
        _make_market("eth_5m", "eth-updown-5m-1778774100"),
        # No ETH 15m market
    ]
    topo = build_topology(markets)
    
    assert len(topo.monotonicity_pairs) == 0


def test_topology_multiple_assets() -> None:
    """Separate assets get separate monotonicity groups — no cross-asset pairing."""
    markets = [
        _make_market("btc_5m", "btc-updown-5m-1778774100"),
        _make_market("btc_15m", "btc-updown-15m-1778773500"),
        _make_market("eth_5m", "eth-updown-5m-1778774100"),
        _make_market("eth_15m", "eth-updown-15m-1778773500"),
    ]
    topo = build_topology(markets)
    
    # 1 BTC pair + 1 ETH pair = 2 total
    assert len(topo.monotonicity_pairs) == 2
    
    assets_paired = set()
    for m5, m15 in topo.monotonicity_pairs:
        # Extract asset prefix
        asset = m5.split("_")[0]
        assets_paired.add(asset)
        # Ensure no cross-asset pairing
        assert m15.startswith(asset)
    
    assert assets_paired == {"btc", "eth"}
