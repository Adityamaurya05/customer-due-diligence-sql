"""Download the UCI Online Retail II dataset and land it as Parquet for DuckDB."""

import io
import zipfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_PARQUET = DATA_DIR / "raw_transactions.parquet"
UCI_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"

COLUMNS = ["invoice", "stock_code", "description", "quantity", "invoice_date", "price", "customer_id", "country"]
# Online Retail II and the older Online Retail dataset name columns differently.
ALIASES = {
    "invoice": ["Invoice", "InvoiceNo"], "stock_code": ["StockCode"], "description": ["Description"],
    "quantity": ["Quantity"], "invoice_date": ["InvoiceDate"], "price": ["Price", "UnitPrice"],
    "customer_id": ["Customer ID", "CustomerID"], "country": ["Country"],
}


def standardise(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for std, names in ALIASES.items():
        hit = next((n for n in names if n in df.columns), None)
        if hit is None:
            raise ValueError(f"Missing column for '{std}'. Found: {list(df.columns)}")
        rename[hit] = std
    df = df.rename(columns=rename)[COLUMNS].copy()
    df["invoice"] = df["invoice"].astype(str)
    df["stock_code"] = df["stock_code"].astype(str)
    df["description"] = df["description"].astype("string")
    df["customer_id"] = pd.to_numeric(df["customer_id"], errors="coerce")
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    return df


def download(dest=DATA_DIR) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    resp = requests.get(UCI_URL, timeout=300)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith((".xlsx", ".csv")))
        zf.extract(name, dest)
        return dest / name


def find_local(dest=DATA_DIR):
    for pat in ("*.xlsx", "*.csv"):
        hits = sorted(dest.rglob(pat))
        if hits:
            return hits[0]
    return None


def land(sample=False, refresh=False) -> Path:
    """Return a Parquet file of raw transactions (creating it if needed)."""
    if sample:
        out = DATA_DIR / "sample_transactions.parquet"
        df = make_sample()
    else:
        if RAW_PARQUET.exists() and not refresh:
            return RAW_PARQUET
        src = find_local() or download()
        print(f"    reading {src.name} (the Excel file has two sheets and about 1M rows; this takes a few minutes once)")
        if src.suffix == ".csv":
            df = pd.read_csv(src)
        else:
            df = pd.concat(pd.read_excel(src, sheet_name=None).values(), ignore_index=True)
        df = standardise(df)
        out = RAW_PARQUET
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.register("df", df)
    con.execute(f"COPY df TO '{out.as_posix()}' (FORMAT PARQUET)")
    con.close()
    return out


def make_sample(n_customers=1500, seed=11) -> pd.DataFrame:
    """Synthetic transactions with the same schema, for tests and offline demos ONLY."""
    rng = np.random.default_rng(seed)
    products = [(f"{20000 + i}", f"PRODUCT {i}", round(float(rng.uniform(0.5, 15)), 2)) for i in range(300)]
    countries = ["United Kingdom"] * 8 + ["Germany", "France", "EIRE", "Netherlands"]
    start = pd.Timestamp("2009-12-01")
    rows, inv = [], 500000
    for c in range(n_customers):
        cid = 12000 + c
        country = countries[rng.integers(len(countries))]
        first = start + pd.Timedelta(days=int(rng.integers(0, 700)))
        n_orders = 1 + rng.geometric(0.25)
        wholesale = rng.random() < 0.05
        for k in range(n_orders):
            d = first + pd.Timedelta(days=int(k * rng.integers(15, 90)))
            if d > pd.Timestamp("2011-12-09"):
                break
            inv += 1
            cancel = rng.random() < 0.03
            for _ in range(rng.integers(1, 12)):
                code, desc, price = products[rng.integers(len(products))]
                qty = int(rng.integers(1, 24) * (10 if wholesale else 1))
                if d.year == 2011:
                    price = round(price * 1.04, 2)
                rows.append((("C" if cancel else "") + str(inv), code, desc, -qty if cancel else qty,
                             d + pd.Timedelta(hours=int(rng.integers(8, 18))), price, cid, country))
    rows.append((str(inv + 1), "POST", "POSTAGE", 1, start + pd.Timedelta(days=5), 18.0, 12000.0, "Germany"))
    rows.append((str(inv + 2), "22222", "NO CUSTOMER", 2, start + pd.Timedelta(days=6), 3.0, None, "United Kingdom"))
    df = pd.DataFrame(rows, columns=COLUMNS)
    return standardise(df.rename(columns={"invoice": "Invoice", "stock_code": "StockCode", "description": "Description",
                                          "quantity": "Quantity", "invoice_date": "InvoiceDate", "price": "Price",
                                          "customer_id": "Customer ID", "country": "Country"}))
