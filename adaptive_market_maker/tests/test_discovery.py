import pytest
from market_discovery.parsers import parse_market_slug

def test_parse_market_slug_valid():
    slug = "btc-updown-5m-1778715600"
    parsed = parse_market_slug(slug)
    assert parsed.is_valid is True
    assert parsed.asset == "BTC"
    assert parsed.timeframe == "5m"
    assert parsed.timestamp == 1778715600

def test_parse_market_slug_valid_eth_15m():
    slug = "eth-updown-15m-1778715600"
    parsed = parse_market_slug(slug)
    assert parsed.is_valid is True
    assert parsed.asset == "ETH"
    assert parsed.timeframe == "15m"

def test_parse_market_slug_invalid_asset():
    slug = "doge-updown-5m-1778715600"
    parsed = parse_market_slug(slug)
    assert parsed.is_valid is False

def test_parse_market_slug_invalid_timeframe():
    slug = "btc-updown-1h-1778715600"
    parsed = parse_market_slug(slug)
    assert parsed.is_valid is False

def test_parse_market_slug_invalid_format():
    slug = "btc-updown-5m-1778715600-extra"
    parsed = parse_market_slug(slug)
    assert parsed.is_valid is False

def test_parse_market_slug_not_updown():
    slug = "btc-price-5m-1778715600"
    parsed = parse_market_slug(slug)
    assert parsed.is_valid is False
