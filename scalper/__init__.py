"""
scalper — High-Frequency Trading module for Polymarket 5-minute
crypto Up/Down prediction markets.

Supports: BTC, ETH, SOL, XRP
Strategy: Technical analysis signals → paper trade → auto-resolve
"""

from scalper.config import HFT_ASSETS, HFT_STAKE, HFT_SIGNAL_THRESHOLD

__all__ = ["HFT_ASSETS", "HFT_STAKE", "HFT_SIGNAL_THRESHOLD"]
