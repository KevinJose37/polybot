"""
Ohanism Cloner: Extractor de historial de trades de Polygon a través de RPC.
Diseñado para aplicar Behavioral Cloning a carteras rentables de Polymarket.
"""
import asyncio
import aiohttp
import json
import time
import os

# Configuración
TARGET_WALLET = "0x89b5cdaaa4866c1e738406712012a630b4078beb"
# Endpoint público de Polygon. En producción es mejor usar Alchemy o Infura
POLYGON_RPC = "https://polygon-rpc.com" 

# El contrato de Conditional Tokens (CTF) de Polymarket en Polygon
CTF_CONTRACT = "0x4D97DCd97eC945f40CF65F87097CAe4c274da7e7"

OUTPUT_DIR = r"D:\Proyectos\polystudio\polystudio\data\ml_features"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "ohanism_trades_raw.json")

async def fetch_erc1155_transfers(session, wallet_address, start_block, end_block):
    """
    Busca eventos TransferSingle y TransferBatch en el contrato CTF 
    donde el destino o el origen sea la wallet objetivo.
    """
    # Hash del evento TransferSingle: 
    # TransferSingle(address operator, address from, address to, uint256 id, uint256 value)
    EVENT_SIGNATURE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
    
    wallet_padded = "0x000000000000000000000000" + wallet_address.lower()[2:]
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getLogs",
        "params": [{
            "address": CTF_CONTRACT,
            "fromBlock": hex(start_block),
            "toBlock": hex(end_block),
            "topics": [
                EVENT_SIGNATURE,
                None, # Operator
                None, # From
                wallet_padded # To (Cuando compra/recibe)
            ]
        }],
        "id": 1
    }
    
    async with session.post(POLYGON_RPC, json=payload) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get('result', [])
        return []

async def fetch_block_timestamp(session, block_number_hex):
    payload = {"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": [block_number_hex, False], "id": 1}
    async with session.post(POLYGON_RPC, json=payload) as resp:
        if resp.status == 200:
            data = await resp.json()
            return int(data['result']['timestamp'], 16)
        return 0

async def clone_wallet():
    print("=" * 80)
    print(f"INICIANDO CLONACIÓN DE BILLETERA: {TARGET_WALLET}")
    print("=" * 80)
    
    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}
        async with session.post(POLYGON_RPC, json=payload) as resp:
            data = await resp.json()
            current_block = int(data['result'], 16)
            
    print(f"[1/3] Bloque actual en Polygon: {current_block:,}")
    
    chunk_size = 3000
    all_logs = []
    parsed_trades = []
    
    async with aiohttp.ClientSession() as session:
        test_start = current_block - 15000
        
        for i, b in enumerate(range(test_start, current_block, chunk_size)):
            end_b = min(b + chunk_size, current_block)
            logs = await fetch_erc1155_transfers(session, TARGET_WALLET, b, end_b)
            if logs:
                for log in logs:
                    # data = 64 bytes hex string (first 32 is id, next 32 is value)
                    raw_data = log['data'][2:]
                    if len(raw_data) >= 128:
                        token_id = str(int(raw_data[:64], 16))
                        value = int(raw_data[64:128], 16)
                        
                        # Optimization: we should batch get block timestamps in production, 
                        # but for this test we'll fetch it individually
                        ts = await fetch_block_timestamp(session, log['blockNumber'])
                        
                        parsed_trades.append({
                            "token_id": token_id,
                            "size": value,
                            "timestamp": ts,
                            "block": int(log['blockNumber'], 16)
                        })
                print(f"  -> Procesados {len(logs)} logs en bloque {b:,} a {end_b:,}")
            await asyncio.sleep(0.5)
            
    print(f"\\n[3/3] Extracción exitosa. {len(parsed_trades)} trades parseados.")
    
    if parsed_trades:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(OUTPUT_FILE, "w") as f:
            json.dump(parsed_trades, f, indent=2)
        print(f"Trades procesados guardados en {OUTPUT_FILE}")
        
if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(clone_wallet())
