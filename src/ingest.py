"""Hourly job. Per series group, resume from MAX(period) minus an overlap window.

Overlap handles EIA revisions and missed runs; upsert makes re-fetching harmless.
A run that finds nothing new is normal. API failure => logged, run exits non-zero,
next run backfills automatically from MAX(period).
"""
import sys
from datetime import timedelta

from common import DATASETS, REGIONS, connect, fetch, upsert, utcnow

OVERLAP = timedelta(hours=72)

con = connect()
fetched = new = 0
status, note = "ok", ""
try:
    for region in REGIONS:
        for ds in DATASETS:
            last = con.execute(
                "SELECT max(period) FROM eia_hourly WHERE respondent=? AND dataset=?", [region, ds]
            ).fetchone()[0]
            start = (last - OVERLAP) if last else utcnow() - timedelta(days=7)
            rows = list(fetch(ds, region, start))
            fetched += len(rows)
            new += upsert(con, rows)
except Exception as e:  # log, then fail the workflow visibly
    status, note = "error", str(e)[:300]

mx = con.execute("SELECT max(period) FROM eia_hourly").fetchone()[0]
con.execute("INSERT INTO ingest_log (kind, rows_fetched, rows_new, max_period, status, note) VALUES ('hourly', ?, ?, ?, ?, ?)",
            [fetched, new, mx, status, note])
print(f"{status}: fetched={fetched} new={new} max_period={mx} {note}")
sys.exit(0 if status == "ok" else 1)
