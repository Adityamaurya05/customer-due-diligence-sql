"""Customer due diligence pipeline (SQL on DuckDB).

    python run_pipeline.py            # real UCI Online Retail II data (downloads once, ~45 MB)
    python run_pipeline.py --sample   # synthetic data, offline smoke test only
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "src"))
from cdd import data, pipeline  # noqa: E402

OUT = Path(__file__).parent / "outputs"
DB = OUT / "retail.duckdb"
MARTS = ["mart_monthly_kpis", "mart_cohort_retention", "mart_rfm_summary", "mart_customer_bridge",
         "mart_price_volume", "mart_country", "mart_cohort_value"]


def charts(con, figs: Path):
    figs.mkdir(parents=True, exist_ok=True)
    k = con.execute("SELECT * FROM mart_monthly_kpis").df()
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(k["month"], k["net_revenue"] / 1e3, width=20, color="#1f3a5f", label="Net revenue")
    ax.bar(k["month"], k["new_customer_revenue"].fillna(0) / 1e3, width=20, color="#2a9d8f", label="of which new customers")
    ax.set(title="Monthly net revenue (GBP thousands)", ylabel="GBP k")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figs / "monthly_revenue.png", dpi=130)
    plt.close(fig)

    r = con.execute("""SELECT strftime(cohort_month, '%Y-%m') AS cohort, months_since_first, retention
                       FROM mart_cohort_retention WHERE months_since_first <= 12""").df()
    piv = r.pivot(index="cohort", columns="months_since_first", values="retention")
    fig, ax = plt.subplots(figsize=(9, 7))
    im = ax.imshow(piv.values, cmap="Blues", aspect="auto", vmin=0, vmax=0.5)
    ax.set_yticks(range(len(piv)), piv.index, fontsize=7)
    ax.set_xticks(range(piv.shape[1]), piv.columns)
    ax.set(title="Cohort retention (share of cohort active in month N)", xlabel="Months since first purchase")
    fig.colorbar(im, ax=ax, format=plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    fig.tight_layout()
    fig.savefig(figs / "cohort_retention.png", dpi=130)
    plt.close(fig)

    c = con.execute("SELECT cum_customer_share, cum_revenue_share FROM mart_concentration").df()
    fig, ax = plt.subplots(figsize=(5, 4.5))
    ax.plot(c["cum_customer_share"], c["cum_revenue_share"], color="#1f3a5f")
    ax.plot([0, 1], [0, 1], ls="--", color="#8d99ae")
    ax.set(title="Revenue concentration (Lorenz curve)", xlabel="Share of customers (largest first)",
           ylabel="Share of revenue")
    fig.tight_layout()
    fig.savefig(figs / "concentration.png", dpi=130)
    plt.close(fig)


def flag(cond_red, red, green):
    return f"RED FLAG: {red}" if cond_red else f"GREEN FLAG: {green}"


def findings(m, checks, sample) -> str:
    warn = "> **WARNING: SYNTHETIC sample data. Do not quote these numbers.**\n\n" if sample else ""
    cb, pv = m["customer_bridge"], m["price_volume_bridge"]
    change = cb["FY2011 revenue (identified customers)"] - cb["FY2010 revenue (identified customers)"]
    new_share_of_change = cb["New customers"] / change if change else float("nan")
    rfm = m["rfm"]
    at_risk = rfm.get("At risk (was valuable)", {}).get("revenue_share", 0)
    champions = rfm.get("Champions", {}).get("revenue_share", 0)
    pe, ve = pv["Price effect (continuing products)"], pv["Volume effect (continuing products)"]
    info = checks[checks["severity"] == "info"]
    season = ("Demand is seasonal, so working capital and inventory peak ahead of the holiday season."
              if m["q4_share_fy2011"] > 0.30 else "There is no strong autumn peak.")
    q3 = ("Why do most first-time buyers not return, and what does acquisition cost?" if m["repeat_rate"] < 0.5
          else "What drives repeat purchasing, and how much does it cost to acquire a new customer?")
    return f"""# Commercial Due Diligence: Customer Base of a UK Online Gift Retailer
{warn}*Data: UCI Online Retail II, {m['date_min']} to {m['date_max']}, {m['n_lines']:,} product lines, {m['n_customers']:,}
identified customers. Fiscal years run Dec-Nov. Revenue in GBP, net of cancellations.*

## Answer first
Net revenue went from **GBP {m.get('fy2010_revenue', 0):,.0f}** (FY2010) to **GBP {m.get('fy2011_revenue', 0):,.0f}**
(FY2011), a change of **{m.get('revenue_growth', 0):+.1%}**. Identified customers changed {m.get('customer_growth', 0):+.1%}.

## Key findings (red/green thresholds are my judgement and are shown in brackets)
1. **Concentration.** The top 1% of customers generate {m['top1pct_revenue_share']:.0%} of revenue and the top 10% generate
   {m['top10pct_revenue_share']:.0%}. {flag(m['top10pct_revenue_share'] > 0.5, 'more than half of revenue sits with 10% of customers, so key-account churn is the main risk', 'revenue is not dependent on a handful of accounts')} [red if top 10% > 50%].
2. **Retention.** On average {m['retention_m1']:.0%} of a cohort buys again in month 1, {m['retention_m3']:.0%} in month 3 and
   {m['retention_m12']:.0%} in month 12. {m['repeat_rate']:.0%} of customers ever place a second order.
   {flag(m['repeat_rate'] < 0.5, 'most customers buy only once, so growth depends on acquisition', 'a majority of customers return')} [red if repeat rate < 50%].
3. **Growth quality (customer bridge).** New customers contributed GBP {cb['New customers']:,.0f}, lost customers
   GBP {cb['Lost customers']:,.0f}, retained customers who spent more +GBP {cb['Retained: spent more']:,.0f} and retained customers
   who spent less GBP {cb['Retained: spent less']:,.0f}. New customers explain {new_share_of_change:.0%} of the net change.
4. **Price vs volume.** On continuing products, the price effect was GBP {pe:,.0f} and the volume effect GBP {ve:,.0f}.
   New products added GBP {pv['New products']:,.0f}; discontinued products removed GBP {pv['Discontinued products']:,.0f}.
   {flag(abs(pe) > abs(ve), 'growth is mostly price-led, so test whether it is sustainable', 'growth is volume-led rather than price-led')} [red if |price effect| > |volume effect|].
5. **Customer health (RFM).** "Champions" hold {champions:.0%} of revenue. "At risk" customers (previously valuable, now lapsing)
   hold {at_risk:.0%}, which is revenue to defend in the first 100 days.
6. **Geography and seasonality.** {m['top_country']} is {m['top_country_share']:.0%} of revenue. Sep-Nov is {m['q4_share_fy2011']:.0%}
   of FY2011 revenue (25% would be flat). {season}
7. **Cancellations** reverse {m['cancellation_rate']:.1%} of gross revenue.

## Data quality notes
""" + "\n".join(f"* {r.check_name}: {r.failures:,} ({r.detail})" for r in info.itertuples()) + f"""
* All hard checks passed (fact table reconciles to staging; both bridges add up; one row per customer).

## Questions for management (next phase)
1. Who are the top 1% of accounts (wholesalers?), and are any contracts at risk?
2. What explains the price effect: list-price increases or mix within product codes?
3. {q3}
4. Can the missing customer IDs be recovered (e.g. guest checkouts)?
"""


def resume_bullets(m) -> str:
    return f"""# Resume bullets (generated from YOUR run)

**Commercial Due Diligence: Customer Analytics in SQL** | SQL (DuckDB), Python, Streamlit | [GitHub link]
- Built a layered SQL model (staging → star schema → 8 analytical marts) over {m['n_lines']:,} transactions with automated data-quality checks (reconciliation, bridge integrity, uniqueness) that run on every build
- Ran a PE-style customer due diligence: cohort retention ({m['retention_m3']:.0%} at month 3), RFM segmentation, revenue concentration (top 10% = {m['top10pct_revenue_share']:.0%} of revenue) and a {m.get('revenue_growth', 0):+.1%} revenue bridge split into new, lost and retained customers and price vs volume effects
- Delivered an answer-first findings memo with red/green flags and management questions, plus a Streamlit dashboard with a read-only SQL explorer
"""


def main(sample=False, refresh=False):
    OUT.mkdir(exist_ok=True)
    print("1/4 Landing raw data ...")
    raw = data.land(sample=sample, refresh=refresh)
    print("2/4 Building SQL models in DuckDB ...")
    con = pipeline.build(raw, DB)
    checks = pipeline.run_checks(con)
    print(checks.to_string(index=False))
    print("3/4 Computing metrics and exporting marts ...")
    m = pipeline.headline_metrics(con) | {"sample_mode": sample}
    for t in MARTS:
        con.execute(f"COPY {t} TO '{(OUT / (t + '.csv')).as_posix()}' (HEADER)")
    (OUT / "metrics.json").write_text(json.dumps(m, indent=2, default=str))
    print("4/4 Writing charts, findings memo and resume bullets ...")
    charts(con, OUT / "figures")
    (OUT / "dd_findings.md").write_text(findings(m, checks, sample))
    (OUT / "resume_bullets.md").write_text(resume_bullets(m))
    con.close()
    print(f"\nDone. FY revenue growth {m.get('revenue_growth', 0):+.1%}, top-10% share {m['top10pct_revenue_share']:.0%}, "
          f"month-3 retention {m['retention_m3']:.0%}")
    if sample:
        print("NOTE: synthetic sample data - do not quote these numbers.")
    return m


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    main(sample=a.sample, refresh=a.refresh)
