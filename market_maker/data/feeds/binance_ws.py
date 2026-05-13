"""
data/feeds/binance_ws.py — Binance WebSocket feed for real-time price data.
Provides spot price and trade stream via async WebSocket connection.
"""

import asyncio
import json
import time
from typing import Optional

import websockets
from loguru import logger

from config.settings import config
from utils.schemas import TradeEvent


class BinanceWSFeed:
    """
    Async WebSocket feed for Binance spot price + trade data.
    Connects to the aggTrade stream for real-time fill data.
    """

    def __init__(self, symbol: str, on_price_update=None, on_trade=None):
        """
        Args:
            symbol: Trading pair (e.g., "btcusdt")
            on_price_update: Async callback(symbol, price, timestamp_ms)
            on_trade: Async callback(TradeEvent)
        """
        self.symbol = symbol.lower()
        self.on_price_update = on_price_update
        self.on_trade = on_trade
        self._running = False
        self._ws = None
        self.last_price: float = 0.0
        self.last_update_ms: int = 0

    async def connect(self):
        """Connect to Binance WebSocket and start streaming."""
        stream = f"{self.symbol}@aggTrade"
        url = f"{config.binance_ws_url}/{stream}"

        self._running = True
        reconnect_delay = 1.0

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    reconnect_delay = 1.0  # Reset on successful connect
                    logger.info(f"[Feed] Binance WS connected: {self.symbol}")

                    async for message in ws:
                        if not self._running:
                            break
                        await self._process_message(message)

            except websockets.exceptions.ConnectionClosed:
                logger.warning(f"[Feed] Binance WS disconnected: {self.symbol}")
            except Exception as e:
                logger.error(f"[Feed] Binance WS error ({self.symbol}): {e}")

            if self._running:
                logger.info(f"[Feed] Reconnecting in {reconnect_delay:.0f}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)

    async def _process_message(self, raw_message: str):
        """Process a single aggTrade message."""
        try:
            data = json.loads(raw_message)

            price = float(data["p"])
            quantity = float(data["q"])
            is_buyer_maker = data["m"]  # True = seller aggressed
            timestamp_ms = int(data["T"])  # Trade time

            self.last_price = price
            self.last_update_ms = int(time.time() * 1000)

            # Price update callback
            if self.on_price_update:
                await self.on_price_update(self.symbol, price, self.last_update_ms)

            # Trade event callback
            if self.on_trade:
                trade = TradeEvent(
                    symbol=self.symbol.upper(),
                    timestamp_ms=timestamp_ms,
                    price=price,
                    quantity=quantity,
                    is_buyer_maker=is_buyer_maker,
                )
                await self.on_trade(trade)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"[Feed] Parse error ({self.symbol}): {e}")

    def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        logger.info(f"[Feed] Stopping Binance WS: {self.symbol}")
