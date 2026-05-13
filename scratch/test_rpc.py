import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()
CTF = '0x4d97dcd97ec945f40cf65f87097cae10914c82b0'
TOPIC = '0xc3d58168c5ea7395338b82a5d996e384c3c3a4f895ab1574aa9a786016147171'
wallet = '0x89b5cdaaa4866c1e738406712012a630b4078beb'
wallet_topic = '0x' + wallet[2:].zfill(64)
rpc = f"https://polygon-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_API_KEY')}"

payload = {
    'jsonrpc':'2.0',
    'id':1,
    'method':'eth_getLogs',
    'params':[{
        'address': CTF,
        'topics': [TOPIC, None, None, wallet_topic],
        'fromBlock': '0x52C3000', # roughly enough
        'toBlock': 'latest'
    }]
}
r = requests.post(rpc, json=payload).json()
logs = r.get('result', [])
print(f'Found {len(logs)} logs for this wallet as to_addr')

if isinstance(logs, list) and len(logs) > 0:
    print('Last log tx hash:', logs[-1].get('transactionHash'))
