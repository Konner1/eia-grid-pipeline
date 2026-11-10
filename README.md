# EIA Grid Pipeline

Hourly US electricity data (demand + generation by fuel) for 8 balancing authorities.

EIA API v2 -> GitHub Actions (hourly cron) -> MotherDuck (DuckDB) -> Grafana

- `src/seed.py` one-time bulk load (raw parquet saved locally, then loaded from disk)
- `src/ingest.py` hourly idempotent upsert, resumes from MAX(period) with a 72h overlap
- Secrets (`EIA_API_KEY`, `MOTHERDUCK_TOKEN`) live in GitHub Actions secrets / local `.env`, never in the repo.
