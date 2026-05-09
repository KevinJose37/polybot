#!/usr/bin/env python3
"""
generate_api_keys.py — Genera API keys V2 usando REST API directo.

Firma EIP-712 manualmente y hace el request con headers de navegador
para evitar bloqueo de Cloudflare.
"""

import io
import sys

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import time
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data

# ── Config ───────────────────────────────────────────────────
PRIVATE_KEY = "75e630376c1ace88e261b583f1c44f48e34a53e98f3273ceb1755058a1675985"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

# ── Derive signer address ───────────────────────────────────
account = Account.from_key(PRIVATE_KEY)
ADDRESS = account.address
print(f"  Wallet: {ADDRESS}")

# ── Get server timestamp ────────────────────────────────────
try:
    r = requests.get(f"{CLOB_HOST}/time", timeout=5)
    server_time = str(int(r.text.strip().strip('"')))
    print(f"  Server time: {server_time}")
except Exception:
    server_time = str(int(time.time()))
    print(f"  Using local time: {server_time}")

# ── Sign EIP-712 ClobAuth struct ────────────────────────────
nonce = 0

domain_data = {
    "name": "ClobAuthDomain",
    "version": "1",
    "chainId": CHAIN_ID,
}

types = {
    "ClobAuth": [
        {"name": "address", "type": "address"},
        {"name": "timestamp", "type": "string"},
        {"name": "nonce", "type": "uint256"},
        {"name": "message", "type": "string"},
    ]
}

value = {
    "address": ADDRESS,
    "timestamp": server_time,
    "nonce": nonce,
    "message": "This message attests that I control the given wallet",
}

print("  Signing EIP-712...")
signable = encode_typed_data(
    domain_data=domain_data,
    message_types=types,
    message_data=value,
)
signed = account.sign_message(signable)
signature = signed.signature.hex()
if not signature.startswith("0x"):
    signature = "0x" + signature

print(f"  Signature: {signature[:20]}...")

# ── L1 Headers ──────────────────────────────────────────────
l1_headers = {
    "POLY_ADDRESS": ADDRESS,
    "POLY_SIGNATURE": signature,
    "POLY_TIMESTAMP": server_time,
    "POLY_NONCE": str(nonce),
    # Browser-like headers to avoid Cloudflare
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}

# ── Step 1: Try DERIVE (GET) ────────────────────────────────
print()
print("  ⏳ Intentando DERIVAR keys existentes (GET)...")
try:
    r = requests.get(
        f"{CLOB_HOST}/auth/derive-api-key",
        headers=l1_headers,
        timeout=10,
    )
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        creds = r.json()
        print()
        print("=" * 60)
        print("  🎉 KEYS DERIVADAS EXITOSAMENTE")
        print("=" * 60)
        print(f"  POLY_API_KEY={creds.get('apiKey', creds.get('api_key', ''))}")
        print(f"  POLY_API_SECRET={creds.get('secret', creds.get('api_secret', ''))}")
        print(f"  POLY_API_PASSPHRASE={creds.get('passphrase', creds.get('api_passphrase', ''))}")
        print("=" * 60)
        exit(0)
    else:
        print(f"  Derive falló: {r.text[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# ── Step 2: Try CREATE (POST) ────────────────────────────────
print()
print("  ⏳ Intentando CREAR nuevas keys (POST)...")
try:
    r = requests.post(
        f"{CLOB_HOST}/auth/api-key",
        headers=l1_headers,
        timeout=10,
    )
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        creds = r.json()
        print()
        print("=" * 60)
        print("  🎉 KEYS CREADAS EXITOSAMENTE")
        print("=" * 60)
        print(f"  POLY_API_KEY={creds.get('apiKey', creds.get('api_key', ''))}")
        print(f"  POLY_API_SECRET={creds.get('secret', creds.get('api_secret', ''))}")
        print(f"  POLY_API_PASSPHRASE={creds.get('passphrase', creds.get('api_passphrase', ''))}")
        print("=" * 60)
        exit(0)
    elif r.status_code == 403 and "cloudflare" in r.text.lower():
        print("  ❌ Bloqueado por Cloudflare (IP no permitida)")
        print("  → Ejecuta este script desde un país permitido")
    else:
        print(f"  Create falló: {r.text[:300]}")
except Exception as e:
    print(f"  Error: {e}")

print()
print("  ❌ No se pudieron generar las keys.")
print("  Ejecuta este script desde una IP no bloqueada.")