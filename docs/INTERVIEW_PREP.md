# Interview Prep: Customer Due Diligence in SQL

This project proves two things recruiters screen hard for: **SQL** (every BA interview has a SQL round) and
**commercial judgement** (consulting and PE due diligence).

## 60-second pitch
> "I simulated a private-equity commercial due diligence on a UK online retailer with about a million transaction
> lines. I built a layered SQL model in DuckDB (staging, a star schema, and one mart per diligence question)
> with automated checks that fail the build if the numbers don't reconcile. The key findings: revenue grew
> **[X]%**, but **[Y]%** of the change came from new customers, the top 10% of customers drive **[Z]%** of revenue,
> and month-3 retention is only **[R]%**, so growth depends on acquisition. I wrote it up as an answer-first memo
> with the questions I'd put to management."

## SQL concepts to explain fluently
* **Window functions:** `NTILE(5) OVER (ORDER BY ...)` for RFM, `SUM() OVER (ORDER BY ... ROWS UNBOUNDED PRECEDING)` for the Lorenz curve, `row_number()` for ranking.
* **FILTER clause:** `sum(revenue) FILTER (WHERE fiscal_year = 2011)`, a cleaner conditional aggregation than `CASE WHEN`.
* **CTEs:** every mart is a chain of readable `WITH` steps.
* **Star schema:** facts (events with numbers) vs dimensions (descriptive attributes). Why: simpler joins, consistent definitions.
* **Cohort analysis:** assign each customer to the month of their first *completed* order, then count who's active N months later.
* **NULL handling:** in the customer bridge, a customer missing from one year has NULL revenue. `NULL > 0` is NULL, not false, so without `coalesce` they'd silently fall out of every bucket and the bridge wouldn't add up. This is a great story to tell.

## Practise writing these from scratch (common interview questions)
1. Monthly revenue and month-over-month growth (use `lag()`).
2. Top 3 products per country (`row_number() OVER (PARTITION BY country ORDER BY revenue DESC)` + `QUALIFY` or a subquery).
3. Customers who bought in 2010 but not 2011 (anti-join: `LEFT JOIN ... WHERE b.customer_id IS NULL`).
4. Second-highest order value per customer.
5. 7-day rolling average revenue.
6. Retention: % of January customers who bought again in February.

Use the **SQL explorer tab** in the dashboard to practise on real data.

## Due-diligence concepts
* **Customer revenue bridge:** splits growth into new, lost and retained customers. Investors pay more for growth
  from retained customers spending more (net revenue retention) than for growth that needs constant acquisition.
* **Price / volume / mix:** price effect = (P1 - P0) x Q1; volume effect = (Q1 - Q0) x P0, on continuing products.
  New and discontinued products are separate. Price-led growth is easier to reverse.
* **Concentration:** high dependence on a few accounts is a deal risk (key-account churn, bargaining power).
* **Cohort quality:** if newer cohorts retain worse than older ones, growth is being bought with lower-quality customers.

## Likely questions
**Q: Why DuckDB and not MySQL/Postgres?** It's an in-process analytical (columnar) database. It needs no server,
it's fast on millions of rows, and it's free. The SQL is standard, so the models would port to Snowflake or Postgres
with small changes.

**Q: How did you validate the results?** Hard checks in `04_checks.sql` (reconciliation, bridges sum exactly,
uniqueness, 100% month-0 retention), plus unit tests on a tiny hand-built dataset where I know every answer.

**Q: What would you ask management?** See the last section of the memo. Asking good questions is half of due diligence.

**Q: What's missing?** Margin data. Revenue growth with falling margins is not the same business, so I'd ask for COGS by SKU.
