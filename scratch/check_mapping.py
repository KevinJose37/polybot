import duckdb
con = duckdb.connect()
res = con.execute("SELECT CAST(market AS VARCHAR), CAST(asset_id AS VARCHAR) FROM read_parquet('data/parquet/polymarket_orderbook_btc_only.parquet') WHERE asset_id IS NOT NULL LIMIT 5").fetchall()
for r in res:
    print(r)
