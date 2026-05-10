import os
from dotenv import load_dotenv
load_dotenv()

fields = ["POLY_PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE", "POLY_FUNDER_ADDRESS", "POLY_SIGNATURE_TYPE"]
for f in fields:
    v = os.getenv(f, "")
    if v:
        print(f"{f}: SET ({len(v)} chars) -> {v[:8]}...")
    else:
        print(f"{f}: EMPTY !!!")
