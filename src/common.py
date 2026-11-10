"""Shared helpers: EIA API v2 client (paginated, retrying) and DB access."""
import os
import time
from datetime import datetime, timedelta, timezone

import duckdb
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = "https://api.eia.gov/v2/electricity/rto"
PAGE = 5000
REGIONS = ["CISO", "ERCO", "PJM", "MISO", "NYIS", "ISNE", "SWPP", "BPAT"]

# dataset name -> (endpoint, facet that identifies the series)
DATASETS = {
    "fuel": ("fuel-type-data", "fueltype"),
    "region": ("region-data", "type"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS eia_hourly (
    period     TIMESTAMP NOT NULL,   -- hour, UTC
    respondent VARCHAR   NOT NULL,   -- balancing authority
    dataset    VARCHAR   NOT NULL,   -- 'fuel' | 'region'
    series     VARCHAR   NOT NULL,   -- fuel type or region metric (D, DF, NG, TI)
    value      DOUBLE,
    units      VARCHAR,
    ingested_at TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (period, respondent, dataset, series)
);
CREATE TABLE IF NOT EXISTS ingest_log (
    run_at     TIMESTAMP DEFAULT current_timestamp,
    kind       VARCHAR,              -- 'seed' | 'hourly'
    rows_fetched INTEGER,
    rows_new   INTEGER,
    max_period TIMESTAMP,
    status     VARCHAR,
    note       VARCHAR
);
"""


def connect():
    """MotherDuck if MOTHERDUCK_TOKEN is set, else a local file (for dev)."""
    if os.getenv("MOTHERDUCK_TOKEN"):
        con = duckdb.connect("md:")
        con.execute("CREATE DATABASE IF NOT EXISTS eia")
        con.execute("USE eia")
    else:
        con = duckdb.connect("data/local.duckdb")
    con.execute(SCHEMA)
    return con


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H")


def fetch(dataset, respondent, start, end=None, retries=5):
    """Yield row dicts for one dataset/region between start and end (datetimes, UTC)."""
    endpoint, facet = DATASETS[dataset]
    key = os.environ["EIA_API_KEY"]
    offset = 0
    while True:
        params = {
            "api_key": key, "frequency": "hourly", "data[0]": "value",
            "facets[respondent][]": respondent,
            "start": fmt(start), "length": PAGE, "offset": offset,
            "sort[0][column]": "period", "sort[0][direction]": "asc",
        }
        if end:
            params["end"] = fmt(end)
        for attempt in range(retries):
            try:
                r = requests.get(f"{BASE}/{endpoint}/data/", params=params, timeout=60)
                if r.status_code == 200:
                    break
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.RequestException(f"HTTP {r.status_code}")
                r.raise_for_status()
            except requests.RequestException:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt * 2)
        body = r.json()["response"]
        rows = body.get("data", [])
        for d in rows:
            if d.get("value") is None:
                continue
            yield {
                "period": datetime.strptime(d["period"], "%Y-%m-%dT%H"),
                "respondent": d["respondent"],
                "dataset": dataset,
                "series": d[facet],
                "value": float(d["value"]),
                "units": d.get("value-units"),
            }
        offset += PAGE
        if len(rows) < PAGE:
            return
        time.sleep(0.3)  # be polite


def upsert(con, rows):
    """Idempotent write: PK (period, respondent, dataset, series). Returns #new rows."""
    if not rows:
        return 0
    import pandas  # noqa: F401  (duckdb registers DataFrames)
    df = pandas.DataFrame(rows)
    con.register("incoming", df)
    before = con.execute("SELECT count(*) FROM eia_hourly").fetchone()[0]
    con.execute("""
        INSERT OR REPLACE INTO eia_hourly (period, respondent, dataset, series, value, units)
        SELECT period, respondent, dataset, series, value, units FROM incoming
    """)
    con.unregister("incoming")
    after = con.execute("SELECT count(*) FROM eia_hourly").fetchone()[0]
    return after - before


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
