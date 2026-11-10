"""Liveness dashboard for the EIA grid pipeline. Reads MotherDuck (read-only token)."""
import os

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title="EIA Grid Pipeline", layout="wide")


def token():
    try:
        return st.secrets["MOTHERDUCK_TOKEN"]
    except Exception:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
        return os.environ["MOTHERDUCK_TOKEN"]


@st.cache_resource
def con():
    return duckdb.connect(f"md:eia?motherduck_token={token()}")


@st.cache_data(ttl=300)
def q(sql, params=None):
    return con().execute(sql, params or []).df()


st.title("⚡ US Grid Pipeline — is it alive?")
st.caption("EIA hourly data → GitHub Actions (hourly cron) → MotherDuck → this page")

# ---- liveness ----
tot = q("SELECT count(*) n, max(period) latest, max(ingested_at) last_write FROM eia_hourly").iloc[0]
runs = q("""SELECT run_at, rows_fetched, rows_new, status, max_period
            FROM ingest_log WHERE kind='hourly' ORDER BY run_at""")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total rows", f"{int(tot.n):,}")
c2.metric("Latest data hour (UTC)", str(tot.latest))
c3.metric("Last write (UTC)", str(pd.to_datetime(tot.last_write).floor("min")))
c4.metric("Hourly runs logged", f"{len(runs):,}",
          f"{(runs.status=='ok').sum()} ok / {(runs.status!='ok').sum()} failed")

st.subheader("Rows added per automated run")
if len(runs):
    runs["run_at"] = pd.to_datetime(runs["run_at"])
    st.altair_chart(
        alt.Chart(runs).mark_bar().encode(
            x="run_at:T", y=alt.Y("rows_new:Q", title="new rows"),
            color=alt.Color("status:N", scale=alt.Scale(domain=["ok", "error"], range=["#2a9d8f", "#e76f51"])),
            tooltip=["run_at", "rows_fetched", "rows_new", "status"],
        ).properties(height=220), width="stretch")
    st.caption("Runs that add few or zero rows are normal: each run re-fetches a 72h overlap and the upsert dedups it.")

# ---- region explorer ----
regions = q("SELECT DISTINCT respondent FROM eia_hourly ORDER BY 1").respondent.tolist()
region = st.selectbox("Balancing authority", regions, index=regions.index("CISO") if "CISO" in regions else 0)

left, right = st.columns(2)
with left:
    st.subheader("Demand, last 7 days (MW)")
    d = q("""SELECT period, value FROM eia_hourly
             WHERE respondent=? AND dataset='region' AND series='D'
               AND period > (SELECT max(period) FROM eia_hourly WHERE respondent=? AND dataset='region' AND series='D') - INTERVAL 7 DAY
             ORDER BY period""", [region, region])
    st.altair_chart(alt.Chart(d).mark_line().encode(x="period:T", y=alt.Y("value:Q", title="MW", scale=alt.Scale(zero=False))).properties(height=300), width="stretch")
with right:
    st.subheader("Generation by fuel, last 3 days (MW)")
    g = q("""SELECT period, series AS fuel, value FROM eia_hourly
             WHERE respondent=? AND dataset='fuel'
               AND period > (SELECT max(period) FROM eia_hourly WHERE respondent=? AND dataset='fuel') - INTERVAL 3 DAY
             ORDER BY period""", [region, region])
    st.altair_chart(alt.Chart(g).mark_area().encode(x="period:T", y=alt.Y("value:Q", stack=True, title="MW"), color="fuel:N").properties(height=300), width="stretch")

# ---- live vs history ----
st.subheader(f"{region}: last 24h demand vs. same hour, same month, prior years")
cmp = q("""
WITH last AS (SELECT max(period) mx FROM eia_hourly WHERE respondent=? AND dataset='region' AND series='D'),
hist AS (
  SELECT hour(period) AS hr, avg(value) AS hist_avg
  FROM eia_hourly, last
  WHERE respondent=? AND dataset='region' AND series='D'
    AND month(period)=month(last.mx) AND year(period)<year(last.mx)
  GROUP BY 1),
now AS (
  SELECT hour(period) AS hr, avg(value) AS recent
  FROM eia_hourly, last
  WHERE respondent=? AND dataset='region' AND series='D'
    AND period > last.mx - INTERVAL 24 HOUR
  GROUP BY 1)
SELECT now.hr, now.recent, hist.hist_avg FROM now JOIN hist USING (hr) ORDER BY hr
""", [region, region, region])
m = cmp.melt("hr", var_name="series", value_name="MW")
st.altair_chart(alt.Chart(m).mark_line(point=True).encode(x=alt.X("hr:O", title="hour of day (UTC)"), y=alt.Y("MW:Q", scale=alt.Scale(zero=False)), color="series:N").properties(height=300), width="stretch")
st.caption("Joins the live trickle (last 24h) to the seeded history (same calendar month, 2019 onward).")
