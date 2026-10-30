"""Run the SQL models in order, run the data-quality checks and compute headline metrics."""

from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
MODEL_FILES = ["01_staging.sql", "02_core.sql", "03_marts.sql"]
CHECKS_FILE = "04_checks.sql"


def build(raw_parquet: Path, db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    for f in MODEL_FILES:
        sql = (SQL_DIR / f).read_text().replace("{raw_path}", raw_parquet.as_posix())
        con.execute(sql)
    return con


def run_checks(con) -> pd.DataFrame:
    checks = con.execute((SQL_DIR / CHECKS_FILE).read_text()).df()
    failed = checks[(checks["severity"] == "hard") & (checks["failures"] > 0)]
    if not failed.empty:
        raise AssertionError(f"Data-quality checks failed:\n{failed.to_string(index=False)}")
    return checks


def _one(con, sql):
    return con.execute(sql).fetchone()[0]


def headline_metrics(con) -> dict:
    m = {}
    fy = con.execute("""
        SELECT fiscal_year, sum(revenue) AS revenue, count(DISTINCT customer_id) AS customers,
               count(DISTINCT invoice) FILTER (WHERE NOT is_cancellation) AS orders
        FROM fct_sales WHERE fiscal_year IN (2010, 2011) GROUP BY 1 ORDER BY 1""").df().set_index("fiscal_year")
    for y in (2010, 2011):
        if y in fy.index:
            m[f"fy{y}_revenue"] = float(fy.loc[y, "revenue"])
            m[f"fy{y}_customers"] = int(fy.loc[y, "customers"])
            m[f"fy{y}_orders"] = int(fy.loc[y, "orders"])
    if {"fy2010_revenue", "fy2011_revenue"} <= m.keys():
        m["revenue_growth"] = m["fy2011_revenue"] / m["fy2010_revenue"] - 1
        m["customer_growth"] = m["fy2011_customers"] / m["fy2010_customers"] - 1
    m["n_customers"] = int(_one(con, "SELECT count(*) FROM dim_customer"))
    m["n_lines"] = int(_one(con, "SELECT count(*) FROM fct_sales"))
    m["date_min"] = str(_one(con, "SELECT min(invoice_date) FROM fct_sales"))
    m["date_max"] = str(_one(con, "SELECT max(invoice_date) FROM fct_sales"))
    m["repeat_rate"] = float(_one(con, "SELECT avg((n_orders >= 2)::INT) FROM dim_customer WHERE n_orders > 0"))
    for share in (0.01, 0.10, 0.20):
        m[f"top{int(share * 100)}pct_revenue_share"] = float(_one(con, f"""
            SELECT max(cum_revenue_share) FROM mart_concentration WHERE cum_customer_share <= {share}"""))
    # cohort-size-weighted retention at month 3 and 12, only cohorts old enough to observe it
    for k in (1, 3, 6, 12):
        m[f"retention_m{k}"] = float(_one(con, f"""
            WITH eligible AS (
                SELECT cohort_month, cohort_size FROM mart_cohort_retention
                WHERE months_since_first = 0
                  AND cohort_month + INTERVAL {k} MONTH <= (SELECT date_trunc('month', max(invoice_date)) FROM fct_sales))
            SELECT sum(coalesce(r.active, 0)) / sum(e.cohort_size)
            FROM eligible e LEFT JOIN mart_cohort_retention r
              ON r.cohort_month = e.cohort_month AND r.months_since_first = {k}""") or 0)
    m["cancellation_rate"] = float(_one(con, """
        SELECT -sum(revenue) FILTER (WHERE is_cancellation) / sum(revenue) FILTER (WHERE NOT is_cancellation)
        FROM fct_sales"""))
    top_country = con.execute("""
        SELECT country, sum(revenue) / (SELECT sum(revenue) FROM fct_sales) AS share
        FROM fct_sales GROUP BY 1 ORDER BY 2 DESC LIMIT 1""").fetchone()
    m["top_country"], m["top_country_share"] = top_country[0], float(top_country[1])
    m["q4_share_fy2011"] = float(_one(con, """
        SELECT sum(revenue) FILTER (WHERE month(invoice_date) IN (9, 10, 11)) / sum(revenue)
        FROM fct_sales WHERE fiscal_year = 2011""") or 0)
    bridge = con.execute("SELECT step, value FROM mart_customer_bridge ORDER BY ord").df()
    m["customer_bridge"] = dict(zip(bridge["step"], bridge["value"].astype(float)))
    pv = con.execute("SELECT step, value FROM mart_price_volume ORDER BY ord").df()
    m["price_volume_bridge"] = dict(zip(pv["step"], pv["value"].astype(float)))
    rfm = con.execute("SELECT segment, revenue_share, customer_share FROM mart_rfm_summary").df()
    m["rfm"] = {r.segment: {"revenue_share": float(r.revenue_share), "customer_share": float(r.customer_share)}
                for r in rfm.itertuples()}
    return m
