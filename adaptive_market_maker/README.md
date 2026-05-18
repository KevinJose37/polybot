# Adaptive Market Maker

This is the Adaptive Maker-Side Market Making Bot for Polymarket.

## Features
- **Volatility-Driven Quoting**: Uses EWMA on Binance spot prices to dynamically adjust the spread.
- **Inventory Skewing**: Adjusts quotes bid/ask based on your current inventory.
- **Paper Trading Engine**: A high-fidelity simulated engine for queue position deduction and realistic latency bounds.
- **Continuous Market Discovery**: Automatically rolls and subscribes to the Polymarket 5m and 15m intervals.

## How to Run

To run the paper trader with terminal dashboard:

```bash
python -m cli.papertrade --capital 30
```

## Adding New Markets or Timeframes

The bot dynamically discovers active markets by querying the Polymarket REST API. By default, it targets `BTC`, `ETH`, `XRP`, and `SOL` across `5m` and `15m` windows.

To add new markets or modify timeframes:

1. Open `market_discovery/parsers.py`.
2. Update the target lists:
   ```python
   # Target configurations for dynamic discovery
   TARGET_ASSETS = ["btc", "eth", "xrp", "sol", "doge"]  # Added doge
   TARGET_WINDOWS = ["5m", "15m", "30m"]                 # Added 30m
   ```
3. Update the divisor calculation in `market_discovery/discovery.py` to support new timeframes:
   ```python
   # Example: mapping 30m to seconds
   divisor = 5 * 60 if window == "5m" else 15 * 60
   if window == "30m":
       divisor = 30 * 60
   ```

The `LifecycleManager` will automatically pick up these changes, poll the REST API for the new rolling windows, and instruct the WebSocket feed to subscribe to the newly discovered `clobTokenIds`.
