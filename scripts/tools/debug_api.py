"""
Diagnóstico v2: probar la API de Gamma (la API principal de Polymarket 
para explorar mercados) en lugar de solo el CLOB.
"""
import io
import sys
import json
import requests

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── API de Gamma (frontend de Polymarket) ──
GAMMA_URL = "https://gamma-api.polymarket.com"

print("=" * 80)
print("PRUEBA 1: Gamma API — Buscar mercados crypto por tag")
print("=" * 80)

# Probar endpoints de Gamma
endpoints_to_try = [
    ("/markets", {"tag": "crypto", "active": "true", "closed": "false", "limit": 50}),
    ("/markets", {"tag": "Crypto", "active": "true", "closed": "false", "limit": 50}),
    ("/markets", {"slug_contains": "bitcoin", "active": "true", "closed": "false", "limit": 50}),
    ("/markets", {"active": "true", "closed": "false", "limit": 20}),
    ("/events", {"tag": "crypto", "active": "true", "closed": "false", "limit": 20}),
    ("/events", {"active": "true", "closed": "false", "limit": 20}),
]

for endpoint, params in endpoints_to_try:
    url = f"{GAMMA_URL}{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        print(f"\n  GET {endpoint} {params}")
        print(f"  Status: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                print(f"  Results: {len(data)} items")
                for item in data[:3]:
                    q = item.get("question", item.get("title", item.get("slug", "")))
                    vol = item.get("volume", item.get("volume_num", 0))
                    active = item.get("active", "?")
                    tags = item.get("tags", [])
                    print(f"    → {q[:70]} | vol={vol} | tags={tags}")
            elif isinstance(data, dict):
                print(f"  Keys: {list(data.keys())[:10]}")
                items = data.get("data", data.get("markets", data.get("events", [])))
                if isinstance(items, list):
                    print(f"  Results: {len(items)} items")
                    for item in items[:3]:
                        q = item.get("question", item.get("title", ""))
                        print(f"    → {q[:70]}")
        else:
            print(f"  Body: {resp.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")

# ── Probar CLOB con parámetros diferentes ──
print("\n" + "=" * 80)
print("PRUEBA 2: CLOB API — Mercados activos con filtro")  
print("=" * 80)

CLOB_URL = "https://clob.polymarket.com"
clob_endpoints = [
    ("/markets", {"active": "true", "closed": "false", "limit": 20}),
    ("/markets", {"limit": 5}),  # ver estructura raw
]

for endpoint, params in clob_endpoints:
    url = f"{CLOB_URL}{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        print(f"\n  GET {endpoint} {params}")
        print(f"  Status: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                print(f"  Results: {len(data)} items")
                if data:
                    print(f"  Keys de primer item: {sorted(data[0].keys())}")
                    print(f"  Ejemplo: {json.dumps(data[0], indent=2)[:800]}")
            elif isinstance(data, dict):
                items = data.get("data", data.get("markets", []))
                nc = data.get("next_cursor", "none")
                print(f"  Keys: {list(data.keys())}")
                print(f"  next_cursor: {nc}")
                if isinstance(items, list) and items:
                    print(f"  Results: {len(items)} items")
                    print(f"  Keys de primer item: {sorted(items[0].keys())}")
                    first = items[0]
                    print(f"  question: {first.get('question', '')[:80]}")
                    print(f"  active: {first.get('active')}")
                    print(f"  closed: {first.get('closed')}")
                    print(f"  volume: {first.get('volume')}")
                    print(f"  volume_num: {first.get('volume_num')}")
                    print(f"  tags: {first.get('tags')}")
                    print(f"  end_date_iso: {first.get('end_date_iso')}")
                    print(f"  tokens: {json.dumps(first.get('tokens', []), indent=2)[:400]}")
    except Exception as e:
        print(f"  Error: {e}")
