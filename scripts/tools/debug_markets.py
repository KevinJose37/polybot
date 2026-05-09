"""
Script de diagnóstico: examina los mercados de Polymarket para entender
qué keywords usan los mercados crypto y qué estructura tienen.
"""
import io
import sys
import json
import requests

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CLOB_URL = "https://clob.polymarket.com"

# Paso 1: Obtener mercados
print("Obteniendo mercados de Polymarket CLOB API...")
all_markets = []
next_cursor = None

for page in range(50):
    params = {"limit": 100}
    if next_cursor:
        params["next_cursor"] = next_cursor
    resp = requests.get(f"{CLOB_URL}/markets", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    
    markets = data if isinstance(data, list) else data.get("data", data.get("markets", []))
    if isinstance(markets, list):
        all_markets.extend(markets)
    
    next_cursor = data.get("next_cursor") if isinstance(data, dict) else None
    if not next_cursor or next_cursor == "LTE=":
        break
    
    if page % 10 == 0:
        print(f"  Página {page}, {len(all_markets)} mercados acumulados...")

print(f"\nTotal mercados obtenidos: {len(all_markets)}")

# Paso 2: Buscar mercados que mencionan crypto
crypto_keywords = ["btc", "eth", "bitcoin", "ethereum", "price", "crypto", 
                    "solana", "sol", "xrp", "token", "coin", "$100", "$50",
                    "$1,000", "$10,000", "$100,000", "$200,000"]

print("\n" + "="*80)
print("MERCADOS QUE MENCIONAN KEYWORDS CRYPTO")
print("="*80)

found = []
for m in all_markets:
    q = m.get("question", "").lower()
    desc = m.get("description", "").lower()
    tags = str(m.get("tags", "")).lower()
    combined = f"{q} {desc} {tags}"
    
    for kw in crypto_keywords:
        if kw.lower() in combined:
            active = m.get("active", False)
            closed = m.get("closed", True)
            volume = m.get("volume", m.get("volume_num", 0)) or 0
            found.append({
                "question": m.get("question", "")[:80],
                "keyword": kw,
                "active": active,
                "closed": closed,
                "volume": float(volume),
                "end_date": str(m.get("end_date_iso") or m.get("end_date") or "")[:20],
                "tokens": len(m.get("tokens", [])),
                "condition_id": m.get("condition_id", "")[:20],
            })
            break

# Ordenar por volumen
found.sort(key=lambda x: x["volume"], reverse=True)

print(f"\nEncontrados: {len(found)} mercados con keywords crypto\n")
for i, f in enumerate(found[:30]):
    status = "✅" if f["active"] and not f["closed"] else "❌"
    print(f"  {status} Vol=${f['volume']:>12,.0f} | KW={f['keyword']:>10} | "
          f"Active={f['active']} Closed={f['closed']} | {f['question']}")

# Paso 3: Examinar estructura de un mercado ejemplo
print("\n" + "="*80)
print("ESTRUCTURA DE UN MERCADO EJEMPLO (primer mercado con volumen > 0)")
print("="*80)

for m in all_markets:
    vol = float(m.get("volume", 0) or 0)
    if vol > 0:
        # Imprimir todas las keys
        print(f"\nKeys disponibles: {sorted(m.keys())}\n")
        for k, v in sorted(m.items()):
            if k == "tokens":
                print(f"  {k}: {json.dumps(v, indent=4)[:500]}")
            elif isinstance(v, str) and len(v) > 100:
                print(f"  {k}: {v[:100]}...")
            else:
                print(f"  {k}: {v}")
        break

# Paso 4: Contar mercados por estado
active_count = sum(1 for m in all_markets if m.get("active") and not m.get("closed"))
print(f"\n{'='*80}")
print(f"RESUMEN:")
print(f"  Total mercados: {len(all_markets)}")
print(f"  Activos (active=True, closed=False): {active_count}")
print(f"  Con keywords crypto: {len(found)}")
print(f"  Crypto activos: {sum(1 for f in found if f['active'] and not f['closed'])}")
print(f"  Crypto con volumen > 0: {sum(1 for f in found if f['volume'] > 0)}")
print(f"  Crypto con volumen > $50k: {sum(1 for f in found if f['volume'] > 50000)}")
