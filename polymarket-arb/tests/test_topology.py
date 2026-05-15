"""
Tests for market topology builder.
"""
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
    CRITICAL: 5m and 15m markets have DIFFERENT timestamps (different grid sizes).
    The topology builder must still pair them by asset, not by timestamp.
    """
    markets = [
        # BTC 5m at timestamp 1778774100 (divisible by 300)
        _make_market("btc_5m", "btc-updown-5m-1778774100"),
        # BTC 15m at timestamp 1778773500 (divisible by 900) — DIFFERENT timestamp!
        _make_market("btc_15m", "btc-updown-15m-1778773500"),
    ]
    topo = build_topology(markets)
    
    # Should have one monotonicity group for BTC
    assert "BTC" in topo.monotonicity_pairs
    assert "btc_5m" in topo.monotonicity_pairs["BTC"]["5m"]
    assert "btc_15m" in topo.monotonicity_pairs["BTC"]["15m"]


def test_topology_monotonicity_multiple_windows() -> None:
    """Multiple 5m and 15m markets for same asset produce all combinations."""
    markets = [
        _make_market("btc_5m_a", "btc-updown-5m-1778774100"),
        _make_market("btc_5m_b", "btc-updown-5m-1778774400"),
        _make_market("btc_15m_a", "btc-updown-15m-1778773500"),
        _make_market("btc_15m_b", "btc-updown-15m-1778774400"),
    ]
    topo = build_topology(markets)
    
    assert "BTC" in topo.monotonicity_pairs
    assert len(topo.monotonicity_pairs["BTC"]["5m"]) == 2
    assert len(topo.monotonicity_pairs["BTC"]["15m"]) == 2


def test_topology_monotonicity_incomplete_asset() -> None:
    """Asset with only 5m (no 15m) should not appear in monotonicity pairs."""
    markets = [
        _make_market("eth_5m", "eth-updown-5m-1778774100"),
        # No ETH 15m market
    ]
    topo = build_topology(markets)
    
    # ETH should not be in monotonicity pairs
    assert "ETH" not in topo.monotonicity_pairs


def test_topology_multiple_assets() -> None:
    """Separate assets get separate monotonicity groups."""
    markets = [
        _make_market("btc_5m", "btc-updown-5m-1778774100"),
        _make_market("btc_15m", "btc-updown-15m-1778773500"),
        _make_market("eth_5m", "eth-updown-5m-1778774100"),
        _make_market("eth_15m", "eth-updown-15m-1778773500"),
    ]
    topo = build_topology(markets)
    
    assert "BTC" in topo.monotonicity_pairs
    assert "ETH" in topo.monotonicity_pairs
    assert len(topo.monotonicity_pairs) == 2
