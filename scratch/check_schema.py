import duckdb
con = duckdb.connect()
print(con.execute("SELECT DISTINCT asset_id FROM read_parquet('data/parquet/polymarket_orderbook_btc_only.parquet')").fetchall())
