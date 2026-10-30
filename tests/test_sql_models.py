"""Build the SQL models on tiny hand-made data where every answer is known."""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from cdd import data, pipeline  # noqa: E402


def _rows():
    # (invoice, stock_code, description, qty, date, price, customer, country)
    return [
        ("1001", "10001", "MUG", 10, "2010-01-15", 2.0, 1, "United Kingdom"),     # A, FY2010: 20
        ("1002", "10002", "BAG", 5, "2010-03-10", 4.0, 2, "France"),              # B, FY2010: 20
        ("1003", "10001", "MUG", 10, "2011-01-20", 2.5, 1, "United Kingdom"),     # A, FY2011: 25
        ("1004", "10003", "LAMP", 2, "2011-02-01", 15.0, 3, "Germany"),           # C new, FY2011: 30
        ("C1005", "10001", "MUG", -2, "2011-01-25", 2.5, 1, "United Kingdom"),    # A cancels 5
        ("1006", "POST", "POSTAGE", 1, "2011-01-20", 15.0, 1, "United Kingdom"),  # fee, excluded
        ("1007", "10002", "BAG", 1, "2011-02-02", 0.0, 2, "France"),              # zero price, excluded
        ("1001", "10001", "MUG", 10, "2010-01-15", 2.0, 1, "United Kingdom"),     # exact duplicate
    ]


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("db")
    df = pd.DataFrame(_rows(), columns=["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate",
                                        "Price", "Customer ID", "Country"])
    df = data.standardise(df)
    raw = tmp / "raw.parquet"
    c = duckdb.connect()
    c.register("df", df)
    c.execute(f"COPY df TO '{raw.as_posix()}' (FORMAT PARQUET)")
    c.close()
    con = pipeline.build(raw, tmp / "t.duckdb")
    yield con
    con.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_staging_rules(con):
    assert one(con, "SELECT count(*) FROM stg_transactions") == 6          # duplicate and zero price removed
    assert one(con, "SELECT count(*) FROM stg_transactions WHERE is_non_product") == 1
    assert one(con, "SELECT count(*) FROM fct_sales") == 5
    assert one(con, "SELECT count(*) FROM fct_sales WHERE is_cancellation") == 1


def test_fiscal_year_and_revenue(con):
    assert one(con, "SELECT sum(revenue) FROM fct_sales WHERE fiscal_year = 2010") == pytest.approx(40)
    assert one(con, "SELECT sum(revenue) FROM fct_sales WHERE fiscal_year = 2011") == pytest.approx(50)


def test_customer_bridge(con):
    b = dict(con.execute("SELECT step, value FROM mart_customer_bridge").fetchall())
    assert b["Lost customers"] == pytest.approx(-20)          # B bought only in FY2010
    assert b["Retained: spent less"] == pytest.approx(0)
    assert b["Retained: spent more"] == pytest.approx(0)      # A: 20 -> 20 (25 - 5 cancelled)
    assert b["New customers"] == pytest.approx(30)            # C
    assert b["FY2011 revenue (identified customers)"] == pytest.approx(50)


def test_price_volume_bridge(con):
    b = dict(con.execute("SELECT step, value FROM mart_price_volume").fetchall())
    # MUG: q 10 -> 10, p 2.0 -> 2.5 => price +5, volume 0; BAG discontinued -20; LAMP new +30
    assert b["Price effect (continuing products)"] == pytest.approx(5)
    assert b["Volume effect (continuing products)"] == pytest.approx(0)
    assert b["Discontinued products"] == pytest.approx(-20)
    assert b["New products"] == pytest.approx(30)


def test_checks_pass_and_cohorts(con):
    checks = pipeline.run_checks(con)
    assert (checks.loc[checks["severity"] == "hard", "failures"] == 0).all()
    assert one(con, "SELECT cohort_month FROM dim_customer WHERE customer_id = 1") == pd.Timestamp("2010-01-01").date()
    assert one(con, "SELECT n_orders FROM dim_customer WHERE customer_id = 1") == 2


def test_sample_pipeline_runs(tmp_path):
    raw = tmp_path / "s.parquet"
    c = duckdb.connect()
    c.register("df", data.make_sample(n_customers=200))
    c.execute(f"COPY df TO '{raw.as_posix()}' (FORMAT PARQUET)")
    c.close()
    con = pipeline.build(raw, tmp_path / "s.duckdb")
    pipeline.run_checks(con)
    m = pipeline.headline_metrics(con)
    assert 0 < m["top10pct_revenue_share"] <= 1
    assert 0 <= m["retention_m3"] <= 1
