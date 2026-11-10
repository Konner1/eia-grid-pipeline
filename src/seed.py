"""One-time bulk load. Saves raw pages locally (parquet) first, then loads from disk.

Usage: python src/seed.py [--start 2019-01-01] [--regions CISO,ERCO]
Re-running skips regions/datasets whose raw file already exists (seeding etiquette).
"""
import argparse
import os
from datetime import datetime

import pandas as pd

from common import DATASETS, REGIONS, connect, fetch, upsert, utcnow

ap = argparse.ArgumentParser()
ap.add_argument("--start", default="2019-01-01")
ap.add_argument("--regions", default=",".join(REGIONS))
args = ap.parse_args()
start = datetime.strptime(args.start, "%Y-%m-%d")
end = utcnow()
os.makedirs("data/raw", exist_ok=True)

# 1) download once -> local parquet
for region in args.regions.split(","):
    for ds in DATASETS:
        path = f"data/raw/{ds}_{region}.parquet"
        if os.path.exists(path):
            print(f"skip {path} (already downloaded)")
            continue
        rows = list(fetch(ds, region, start, end))
        pd.DataFrame(rows).to_parquet(path)
        print(f"saved {path}: {len(rows):,} rows")

# 2) bulk-load from disk
con = connect()
con.execute("""
    INSERT OR REPLACE INTO eia_hourly (period, respondent, dataset, series, value, units)
    SELECT period, respondent, dataset, series, value, units FROM read_parquet('data/raw/*.parquet')
""")
n, mx = con.execute("SELECT count(*), max(period) FROM eia_hourly").fetchone()
con.execute("INSERT INTO ingest_log (kind, rows_fetched, rows_new, max_period, status, note) VALUES ('seed', ?, ?, ?, 'ok', ?)",
            [n, n, mx, f"start={args.start}"])
print(f"loaded: {n:,} rows, latest period {mx}")
