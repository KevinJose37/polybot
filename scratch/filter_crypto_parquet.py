"""
Script para filtrar el parquet original dejando solo los mercados de Bitcoin (y crypto validados) 
y calcular el peso del archivo resultante.
"""
import duckdb
import os
import sys

# Asegurar encoding correcto
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PARQUET_ORIGINAL = r"D:\Proyectos\polystudio\polystudio\data\parquet\polymarket_orderbook_2026-05-13T22.parquet"
PARQUET_NUEVO = r"D:\Proyectos\polystudio\polystudio\data\parquet\polymarket_orderbook_btc_only.parquet"

# Mercados Bitcoin (y Ethereum/Solana/XRP) identificados en el paso previo
crypto_markets = [
    '0x21ad7c19fb5512f3f2b2797eeec74f9dc1e88eb06cc3a5ce16e4a131ba434015', # BTC 6PM
    '0xf4af07fa18c30fd68c12a8bd317a88d211b534c0ceb8e364e035a2fc60ab4ef6', # BTC 6:25-6:30
    '0xd8605cd27e30445d34228dd1dfa6d04aa7686c894421c66474f848f4ab49784d', # BTC 6:50-6:55
    '0xdeffb7af0704433254dd5ade86d7ad7d599d3f4a2b069b21c9f707b5b90e144b', # BTC 6:00-6:15
    '0x17908d6f20a4a6e0e26c773a62a40e7bfe9f097689d0962764870b76268cb568', # BTC 6:30-6:35
    '0x96cde3ba9e168ba76f1ee9823fb4c81efcb8e75a7b803a4712c5f69f39128644', # BTC 6:00-6:05
    '0x068a32c7d9a6c935c72fc713d9bd56721add97a2cb189afebad56775f0a0d4b2', # BTC 6:45-7:00
    '0x93791003fca0dc13ce4a889ce418b55ada26ebd0b3684a0dccf6bc62141d50c1', # BTC 6:55-7:00
    '0xa7ac6ee85befcc8d70b1c1ff4400a93fd4f2dc18dbb8e19bb5a1b3c95e00b875', # ETH 6PM
    '0xfa906289d1ced8d2974646e3e44de0fe313b98c19a93f41249964528d25d19bd', # BTC 6:35-6:40
    '0xb9aa7a113f68d2465ed4e64a14699e04f3efa6711ef05dfef610adba0f9302ca', # BTC 6:30-6:45
    '0x984ae49c8b123a1d08406210f3e26d8b7c2743dbce9f705b15b369c9b5f39641', # BTC 6:45-6:50
    '0x97b1ec1a36d7027c88842f72e891e5df8b7135e6162137cd21dc725f48f435fb', # BTC 6:20-6:25
    '0x642d684164be26798b902c7a8e49ca7616d67a127a6f23f86e9e8f6681720d29', # ETH 6:30-6:45
    '0xc9be06dc703aba30b01614e9e79fa239809c44766d11b3b24fdbb9101d2cc1f0', # ETH 6:45-7:00
    '0x11633a9ef89ce0d5bd62ac605ce6f30d9e08a2872bc0e5728362cd998b4c0262', # SOL 6:00-6:15
    '0x72434d4ee35db13abb0baec947b9a2602370aa74d538f8fbf52c3c98dc66723b', # XRP 6PM
    '0x827cfa5e0198fd2dcd62309362143591e7035f8e5603a1158d689620edb550dd', # BTC > 78k
    '0x705c572a186b3a2084bf3d32589f2954224b35e236cecc0090f6b3eab6f63458', # SOL 6PM
    '0x7a1746d81ccd64fefc5b4bf3844934372d86ecc77adba0fbc16b8b7ed66fc885', # ETH 6:00-6:15
    '0x5f8ce9f66d02dc7ab5808d88af8113009393248383e7426166ce52eb3212c019', # BTC 76k-78k
    '0xf07353172d4e73c2b51c52c6274c73434391316b2a4d3f3f26ad2a39b343361e'  # BTC > 76k May 15
]

cids_str = ", ".join([f"'{cid}'" for cid in crypto_markets])

con = duckdb.connect()
con.execute("SET memory_limit='1GB'")

print(f"Buscando tamaño original del archivo...")
orig_size_mb = os.path.getsize(PARQUET_ORIGINAL) / (1024 * 1024)
print(f"Tamaño original: {orig_size_mb:.2f} MB")

print(f"Exportando {len(crypto_markets)} mercados crypto a nuevo archivo parquet...")

# Exportar solo los mercados crypto usando compresión estándar ZSTD
query = f"""
    COPY (
        SELECT * FROM read_parquet('{PARQUET_ORIGINAL}')
        WHERE CAST(market AS VARCHAR) IN ({cids_str})
    ) TO '{PARQUET_NUEVO}' (FORMAT PARQUET, COMPRESSION ZSTD);
"""

con.execute(query)

new_size_mb = os.path.getsize(PARQUET_NUEVO) / (1024 * 1024)
row_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{PARQUET_NUEVO}')").fetchone()[0]

print("\n--- Resultados del filtrado ---")
print(f"Archivo guardado en: {PARQUET_NUEVO}")
print(f"Nuevo tamaño: {new_size_mb:.2f} MB")
print(f"Reducción de tamaño: {orig_size_mb - new_size_mb:.2f} MB ({(1 - new_size_mb/orig_size_mb)*100:.2f}% de reducción)")
print(f"Filas conservadas: {row_count:,} filas puramente crypto.")
