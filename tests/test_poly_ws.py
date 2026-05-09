import asyncio
import json
import websockets
import sys

# La URL oficial del WebSocket del CLOB de Polymarket
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

async def test_ws(token_ids):
    print(f"[*] Conectando a {WS_URL}...")
    
    try:
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
            print(f"[*] Conexión establecida. Suscribiendo a {len(token_ids)} tokens...")
            
            # Formato de suscripción oficial de Polymarket
            sub_msg = {
                "assets_ids": token_ids,
                "type": "market",
                "custom_feature_enabled": True,
            }
            
            await ws.send(json.dumps(sub_msg))
            print(f"[*] Suscripción enviada para: {token_ids[:3]}...")
            print("[*] Esperando datos en vivo de esos mercados... (Presiona Ctrl+C para detener)\n")
            print("-" * 60)
            
            msg_count = 0
            while True:
                raw_msg = await ws.recv()
                data = json.loads(raw_msg)
                
                # Ignorar mensajes irrelevantes de latido
                if isinstance(data, list):
                    continue
                
                event_type = data.get("event_type", "UNKNOWN")
                
                # Solo mostrar mensajes que correspondan a nuestros tokens
                if event_type in ["book", "price_change"]:
                    if event_type == "price_change":
                        changes = data.get("price_changes", data.get("changes", []))
                        if not any(c.get("asset_id") in token_ids for c in changes):
                            continue
                    elif event_type == "book":
                        if data.get("asset_id") not in token_ids:
                            continue

                print(f"\n[MENSAJE #{msg_count + 1}] Tipo: {event_type}")
                print(json.dumps(data, indent=2))
                
                msg_count += 1
                
    except asyncio.CancelledError:
        print("\n[*] Desconectando...")
    except Exception as e:
        print(f"\n[ERROR] {e}")

if __name__ == "__main__":
    from scalper.market_scanner import scan_active_markets
    
    print("[*] Buscando mercados de 5 minutos activos actualmente...")
    markets = scan_active_markets({"BTC": {}, "ETH": {}, "XRP": {}}, duration_minutes=5)
    
    active_tokens = []
    for asset, m in markets.items():
        if m.get("up_token_id"):
            active_tokens.append(m["up_token_id"])
        if m.get("down_token_id"):
            active_tokens.append(m["down_token_id"])
            
    if not active_tokens:
        print("[!] No se encontraron mercados activos de 5 minutos en este instante. Intenta en unos segundos.")
        sys.exit(1)
        
    print(f"[*] Se encontraron {len(active_tokens)} tokens activos.")
    try:
        asyncio.run(test_ws(active_tokens))
    except KeyboardInterrupt:
        print("\n[*] Script detenido por el usuario.")
