"""Due-diligence dashboard:  streamlit run app.py  (builds the database on first run)."""

import json
import sys
from pathlib import Path

import duckdb
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import run_pipeline  # noqa: E402

st.set_page_config(page_title="Customer Due Diligence", layout="wide")


@st.cache_resource
def connect():
    if not run_pipeline.DB.exists():
        with st.spinner("First run: downloading data and building the SQL models (a few minutes) ..."):
            run_pipeline.main()
    return duckdb.connect(str(run_pipeline.DB), read_only=True)


con = connect()
q = lambda sql: con.execute(sql).df()  # noqa: E731
m = json.loads((run_pipeline.OUT / "metrics.json").read_text())

st.title("Commercial Due Diligence: Online Retailer Customer Base")
if m.get("sample_mode"):
    st.warning("Synthetic sample data. Run `python run_pipeline.py` for the real dataset.")
st.caption(f"{m['date_min']} to {m['date_max']} | {m['n_lines']:,} product lines | {m['n_customers']:,} customers | "
           "fiscal year Dec-Nov | GBP, net of cancellations")

k = st.columns(6)
k[0].metric("FY2011 revenue", f"£{m.get('fy2011_revenue', 0) / 1e6:,.2f}m", f"{m.get('revenue_growth', 0):+.1%} YoY")
k[1].metric("FY2011 customers", f"{m.get('fy2011_customers', 0):,}", f"{m.get('customer_growth', 0):+.1%} YoY")
k[2].metric("Top 10% revenue share", f"{m['top10pct_revenue_share']:.0%}")
k[3].metric("Month-3 retention", f"{m['retention_m3']:.0%}")
k[4].metric("Repeat customers", f"{m['repeat_rate']:.0%}")
k[5].metric("Cancellation rate", f"{m['cancellation_rate']:.1%}")

tabs = st.tabs(["Trend", "Cohorts", "Segments (RFM)", "Concentration", "Growth bridges", "Geography", "SQL explorer"])

with tabs[0]:
    kp = q("SELECT * FROM mart_monthly_kpis")
    fig = go.Figure()
    fig.add_bar(x=kp["month"], y=kp["net_revenue"], name="Net revenue")
    fig.add_bar(x=kp["month"], y=kp["new_customer_revenue"], name="From new customers")
    fig.update_layout(barmode="overlay", title="Monthly net revenue (GBP)")
    st.plotly_chart(fig, width="stretch")
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.line(kp, x="month", y="avg_order_value", title="Average order value (GBP)"), width="stretch")
    c2.plotly_chart(px.line(kp, x="month", y="active_customers", title="Active customers per month"), width="stretch")

with tabs[1]:
    horizon = st.slider("Months after first purchase", 3, 24, 12)
    r = q(f"""SELECT strftime(cohort_month, '%Y-%m') AS cohort, months_since_first, retention
              FROM mart_cohort_retention WHERE months_since_first <= {horizon}""")
    piv = r.pivot(index="cohort", columns="months_since_first", values="retention")
    st.plotly_chart(px.imshow(piv, text_auto=".0%", aspect="auto", color_continuous_scale="Blues", zmax=0.5,
                              title="Cohort retention", labels=dict(x="Months since first purchase", y="Cohort")),
                    width="stretch")
    v = q("""SELECT strftime(cohort_month, '%Y-%m') AS cohort, months_since_first, cum_revenue_per_customer
             FROM mart_cohort_value WHERE months_since_first <= 12""")
    st.plotly_chart(px.line(v, x="months_since_first", y="cum_revenue_per_customer", color="cohort",
                            title="Cumulative revenue per customer by cohort (GBP)"), width="stretch")

with tabs[2]:
    seg = q("SELECT * FROM mart_rfm_summary")
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.bar(seg, x="segment", y=["customer_share", "revenue_share"], barmode="group",
                           title="Share of customers vs share of revenue").update_layout(yaxis_tickformat=".0%"),
                    width="stretch")
    rfm = q("SELECT * FROM mart_rfm")
    c2.plotly_chart(px.scatter(rfm, x="recency_days", y="monetary", color="segment", log_y=True,
                               title="Recency vs value (each dot = customer)", opacity=0.6), width="stretch")
    st.dataframe(seg, hide_index=True, width="stretch")

with tabs[3]:
    c = q("SELECT cum_customer_share, cum_revenue_share FROM mart_concentration")
    fig = px.line(c, x="cum_customer_share", y="cum_revenue_share", title="Lorenz curve: revenue concentration")
    fig.add_scatter(x=[0, 1], y=[0, 1], line=dict(dash="dash"), name="Equal")
    fig.update_layout(xaxis_tickformat=".0%", yaxis_tickformat=".0%")
    st.plotly_chart(fig, width="stretch")
    st.write(f"Top 1%: **{m['top1pct_revenue_share']:.0%}** of revenue | top 10%: **{m['top10pct_revenue_share']:.0%}** | "
             f"top 20%: **{m['top20pct_revenue_share']:.0%}**")
    st.dataframe(q("""SELECT c.customer_id, d.country, d.n_orders, round(c.net_revenue) AS net_revenue,
                             round(c.cum_revenue_share, 3) AS cum_share
                      FROM mart_concentration c JOIN dim_customer d USING (customer_id) ORDER BY rnk LIMIT 20"""),
                 hide_index=True, width="stretch")


def waterfall(df, title):
    measure = ["absolute"] + ["relative"] * (len(df) - 2) + ["total"]
    fig = go.Figure(go.Waterfall(x=df["step"], y=df["value"], measure=measure,
                                 text=[f"£{v / 1e3:,.0f}k" for v in df["value"]], textposition="outside"))
    fig.update_layout(title=title, showlegend=False)
    return fig


with tabs[4]:
    st.plotly_chart(waterfall(q("SELECT * FROM mart_customer_bridge ORDER BY ord"),
                              "Revenue bridge by customer type, FY2010 → FY2011"), width="stretch")
    st.plotly_chart(waterfall(q("SELECT * FROM mart_price_volume ORDER BY ord"),
                              "Price / volume / product-mix bridge, FY2010 → FY2011"), width="stretch")

with tabs[5]:
    ctry = q("SELECT * FROM mart_country WHERE fy2011_revenue IS NOT NULL")
    st.plotly_chart(px.bar(ctry.head(15), x="country", y=["fy2010_revenue", "fy2011_revenue"], barmode="group",
                           title="Top 15 countries by FY2011 revenue"), width="stretch")
    st.dataframe(ctry, hide_index=True, width="stretch")

with tabs[6]:
    st.markdown("Query any table: `fct_sales`, `fct_orders`, `dim_customer`, `dim_product`, `mart_*`. Read-only.")
    tables = q("SELECT table_name FROM information_schema.tables ORDER BY 1")
    st.caption(", ".join(tables["table_name"]))
    default = """-- Top 10 products by FY2011 revenue
SELECT p.description, round(sum(f.revenue)) AS revenue, sum(f.quantity) AS units
FROM fct_sales f JOIN dim_product p USING (stock_code)
WHERE f.fiscal_year = 2011
GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""
    sql = st.text_area("SQL", default, height=180)
    if st.button("Run query"):
        head = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).strip().lower()
        if not head.startswith(("select", "with")):
            st.error("Only SELECT / WITH queries are allowed.")
        else:
            try:
                st.dataframe(q(sql), width="stretch")
            except Exception as exc:
                st.error(str(exc))
