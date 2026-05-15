"""
Tests for market discovery parsers.
"""
from bot.market_discovery.parsers import parse_market_slug


def test_parse_valid_slug() -> None:
    # 5m
    slug1 = "btc-updown-5m-1778715600"
    parsed1 = parse_market_slug(slug1)
    assert parsed1.is_valid
    assert parsed1.asset == "BTC"
    assert parsed1.timeframe == "5m"
    assert parsed1.timestamp == 1778715600

    # 15m
    slug2 = "eth-updown-15m-1778715600"
    parsed2 = parse_market_slug(slug2)
    assert parsed2.is_valid
    assert parsed2.asset == "ETH"
    assert parsed2.timeframe == "15m"
    assert parsed2.timestamp == 1778715600


def test_parse_invalid_slug() -> None:
    # completely wrong
    parsed = parse_market_slug("will-donald-trump-win")
    assert not parsed.is_valid
    
    # missing updown
    parsed2 = parse_market_slug("btc-something-5m-1778715600")
    assert not parsed2.is_valid
    
    # missing timestamp
    parsed3 = parse_market_slug("btc-updown-5m-invalid")
    assert not parsed3.is_valid


def test_parse_slug_wrong_asset() -> None:
    """Unsupported asset is rejected by the parser."""
    parsed = parse_market_slug("doge-updown-5m-1778715600")
    assert not parsed.is_valid


def test_parse_slug_wrong_timeframe() -> None:
    """Unsupported timeframe is rejected by the parser."""
    parsed = parse_market_slug("btc-updown-1h-1778715600")
    assert not parsed.is_valid


def test_parse_slug_extra_hyphens() -> None:
    """Slugs with extra segments should be rejected (strict 4-part format)."""
    parsed = parse_market_slug("btc-updown-5m-extra-1778715600")
    assert not parsed.is_valid


def test_parse_slug_uppercase() -> None:
    """Uppercase asset should still parse correctly."""
    parsed = parse_market_slug("BTC-updown-5m-1778715600")
    assert parsed.is_valid
    assert parsed.asset == "BTC"


def test_parse_slug_trailing_whitespace() -> None:
    """Trailing whitespace should be handled gracefully."""
    parsed = parse_market_slug("btc-updown-5m-1778715600 ")
    assert parsed.is_valid
    assert parsed.asset == "BTC"

