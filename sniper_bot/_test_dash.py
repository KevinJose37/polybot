import asyncio
from sniper_bot.config import SniperConfig
from sniper_bot.ws_manager import OrderbookManager
from sniper_bot.scanner import scan_markets
from sniper_bot.lifecycle import MarketLifecycleManager
from sniper_bot.signal_engine import SignalEngine
from sniper_bot.dashboard import Dashboard

async def test_dashboard():
    cfg = SniperConfig()
    cfg.no_dashboard = True  # We won't run the full UI
    ws_mgr = OrderbookManager(cfg.ws_url)
    lc = MarketLifecycleManager(60)
    sig = SignalEngine(cfg, ws_mgr, lc)
    dash = Dashboard(cfg, ws_mgr, lc, sig, None, None, None)

    # Fake market scan
    markets = scan_markets(cfg)
    tokens = []
    for asset, info in markets.items():
        lc.register_market(asset, info.slug, info.event_start, info.event_end)
        sig.register_tokens(asset, info.up_token_id, info.down_token_id)
        if info.up_token_id: tokens.append(info.up_token_id)
        if info.down_token_id: tokens.append(info.down_token_id)
    
    print(f"Found tokens: {len(tokens)}")
    
    # Subscribe and wait
    asyncio.create_task(ws_mgr.run())
    await ws_mgr.subscribe(tokens)
    print("Subscribed. Waiting 5 seconds for data...")
    await asyncio.sleep(5)
    
    print("\n--- Snapshots captured ---")
    print(f"WS snapshots count: {len(ws_mgr._snapshots)}")
    
    print("\n--- Signal Engine Token Map ---")
    for tid, mapping in sig._token_map.items():
        print(f"  {mapping[0]} {mapping[1]}: {tid[:10]}...")
    
    print("\n--- Dashboard Table Render Test ---")
    panel = dash._render_markets()
    from rich.console import Console
    Console().print(panel)
    
    ws_mgr.stop()

asyncio.run(test_dashboard())
