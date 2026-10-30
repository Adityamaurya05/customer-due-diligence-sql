-- 03 MARTS: the analyses behind each due-diligence question.

-- Q1. How is the business trending? ------------------------------------------
CREATE OR REPLACE TABLE mart_monthly_kpis AS
WITH m AS (
    SELECT date_trunc('month', s.invoice_date) AS month,
           sum(s.revenue)                                                   AS net_revenue,
           sum(s.revenue) FILTER (WHERE NOT s.is_cancellation)              AS gross_revenue,
           -sum(s.revenue) FILTER (WHERE s.is_cancellation)                 AS cancelled_revenue,
           count(DISTINCT s.invoice) FILTER (WHERE NOT s.is_cancellation)   AS orders,
           count(DISTINCT s.customer_id)                                    AS active_customers,
           sum(s.revenue) FILTER (WHERE date_trunc('month', s.invoice_date) = c.cohort_month) AS new_customer_revenue
    FROM fct_sales s
    LEFT JOIN dim_customer c USING (customer_id)
    GROUP BY 1
)
SELECT *,
       gross_revenue / NULLIF(orders, 0)              AS avg_order_value,
       cancelled_revenue / NULLIF(gross_revenue, 0)   AS cancellation_rate
FROM m
ORDER BY month;

-- Q2. Do customers come back? (monthly cohort retention) --------------------
CREATE OR REPLACE TABLE mart_cohort_retention AS
WITH activity AS (
    SELECT DISTINCT s.customer_id, c.cohort_month,
           datediff('month', c.cohort_month, date_trunc('month', s.invoice_date)) AS months_since_first
    FROM fct_sales s JOIN dim_customer c USING (customer_id)
    WHERE NOT s.is_cancellation
),
sizes AS (SELECT cohort_month, count(*) AS cohort_size FROM dim_customer WHERE cohort_month IS NOT NULL GROUP BY 1)
SELECT a.cohort_month, a.months_since_first, s.cohort_size,
       count(DISTINCT a.customer_id)                     AS active,
       count(DISTINCT a.customer_id) / s.cohort_size     AS retention
FROM activity a JOIN sizes s USING (cohort_month)
GROUP BY a.cohort_month, a.months_since_first, s.cohort_size
ORDER BY 1, 2;

-- Q3. Who are the customers? (RFM segmentation at the end of the data) -------
CREATE OR REPLACE TABLE mart_rfm AS
WITH snapshot AS (SELECT max(invoice_date) + INTERVAL 1 DAY AS as_of_date FROM fct_sales),
base AS (
    SELECT c.customer_id, c.country,
           datediff('day', c.last_order_date, (SELECT as_of_date FROM snapshot)) AS recency_days,
           c.n_orders AS frequency, c.net_revenue AS monetary
    FROM dim_customer c
    WHERE c.net_revenue > 0 AND c.last_order_date IS NOT NULL
),
scored AS (
    SELECT *,
           ntile(5) OVER (ORDER BY recency_days DESC) AS r,   -- 5 = most recent
           ntile(5) OVER (ORDER BY frequency)         AS f,
           ntile(5) OVER (ORDER BY monetary)          AS m
    FROM base
)
SELECT *,
       CASE
           WHEN r >= 4 AND f >= 4               THEN 'Champions'
           WHEN r >= 3 AND f >= 3               THEN 'Loyal'
           WHEN r >= 4 AND f <= 2               THEN 'New / promising'
           WHEN r <= 2 AND f >= 4               THEN 'At risk (was valuable)'
           WHEN r <= 2 AND f <= 2               THEN 'Hibernating'
           ELSE 'Needs attention'
       END AS segment
FROM scored;

CREATE OR REPLACE TABLE mart_rfm_summary AS
SELECT segment, count(*) AS customers, sum(monetary) AS revenue,
       sum(monetary) / sum(sum(monetary)) OVER () AS revenue_share,
       count(*) / sum(count(*)) OVER ()           AS customer_share,
       median(recency_days) AS median_recency_days, avg(frequency) AS avg_orders
FROM mart_rfm GROUP BY segment ORDER BY revenue DESC;

-- Q4. How concentrated is revenue? (key-customer risk) ----------------------
CREATE OR REPLACE TABLE mart_concentration AS
WITH ranked AS (
    SELECT customer_id, net_revenue,
           row_number() OVER (ORDER BY net_revenue DESC)                         AS rnk,
           count(*) OVER ()                                                       AS n,
           sum(net_revenue) OVER (ORDER BY net_revenue DESC ROWS UNBOUNDED PRECEDING) AS cum_rev,
           sum(net_revenue) OVER ()                                               AS total_rev
    FROM dim_customer WHERE net_revenue > 0
)
SELECT rnk, customer_id, net_revenue, rnk / n AS cum_customer_share, cum_rev / total_rev AS cum_revenue_share
FROM ranked ORDER BY rnk;

-- Q5. What drove revenue growth? (customer bridge FY2010 -> FY2011) ---------
CREATE OR REPLACE TABLE mart_customer_bridge AS
WITH fy AS (
    SELECT customer_id,
           coalesce(sum(revenue) FILTER (WHERE fiscal_year = 2010), 0) AS rev0,
           coalesce(sum(revenue) FILTER (WHERE fiscal_year = 2011), 0) AS rev1
    FROM fct_sales WHERE customer_id IS NOT NULL AND fiscal_year IN (2010, 2011)
    GROUP BY customer_id
)
-- Every customer falls into exactly one bucket, so the steps sum to the change in revenue.
SELECT 'FY2010 revenue (identified customers)' AS step, sum(rev0) AS value, 1 AS ord FROM fy
UNION ALL SELECT 'Lost customers',           coalesce(sum(rev1 - rev0) FILTER (WHERE rev0 > 0 AND rev1 <= 0), 0), 2 FROM fy
UNION ALL SELECT 'Retained: spent more',     coalesce(sum(rev1 - rev0) FILTER (WHERE rev0 > 0 AND rev1 > 0 AND rev1 >= rev0), 0), 3 FROM fy
UNION ALL SELECT 'Retained: spent less',     coalesce(sum(rev1 - rev0) FILTER (WHERE rev0 > 0 AND rev1 > 0 AND rev1 < rev0), 0), 4 FROM fy
UNION ALL SELECT 'New customers',            coalesce(sum(rev1 - rev0) FILTER (WHERE rev0 <= 0 AND rev1 > 0), 0), 5 FROM fy
UNION ALL SELECT 'Other (net-negative accounts)', coalesce(sum(rev1 - rev0) FILTER (WHERE rev0 <= 0 AND rev1 <= 0), 0), 6 FROM fy
UNION ALL SELECT 'FY2011 revenue (identified customers)', sum(rev1), 7 FROM fy
ORDER BY ord;

-- Q6. Price vs volume vs mix (product bridge FY2010 -> FY2011) --------------
CREATE OR REPLACE TABLE mart_price_volume AS
WITH p AS (
    SELECT stock_code,
           sum(quantity) FILTER (WHERE fiscal_year = 2010) AS q0,
           sum(revenue)  FILTER (WHERE fiscal_year = 2010) AS r0,
           sum(quantity) FILTER (WHERE fiscal_year = 2011) AS q1,
           sum(revenue)  FILTER (WHERE fiscal_year = 2011) AS r1
    FROM fct_sales WHERE NOT is_cancellation AND fiscal_year IN (2010, 2011)
    GROUP BY stock_code
),
both_years AS (
    SELECT *, r0 / q0 AS p0, r1 / q1 AS p1 FROM p WHERE q0 > 0 AND q1 > 0
)
SELECT 'FY2010 gross product revenue' AS step, (SELECT sum(r0) FROM p) AS value, 1 AS ord
UNION ALL SELECT 'Price effect (continuing products)',  sum((p1 - p0) * q1), 2 FROM both_years
UNION ALL SELECT 'Volume effect (continuing products)', sum((q1 - q0) * p0), 3 FROM both_years
UNION ALL SELECT 'Discontinued products', -(SELECT coalesce(sum(r0), 0) FROM p WHERE q0 > 0 AND coalesce(q1, 0) <= 0), 4
UNION ALL SELECT 'New products',           (SELECT coalesce(sum(r1), 0) FROM p WHERE coalesce(q0, 0) <= 0 AND q1 > 0), 5
UNION ALL SELECT 'FY2011 gross product revenue', (SELECT sum(r1) FROM p), 6
ORDER BY ord;

-- Q7. Geographic mix ---------------------------------------------------------
CREATE OR REPLACE TABLE mart_country AS
SELECT country,
       sum(revenue) FILTER (WHERE fiscal_year = 2010) AS fy2010_revenue,
       sum(revenue) FILTER (WHERE fiscal_year = 2011) AS fy2011_revenue,
       sum(revenue) FILTER (WHERE fiscal_year = 2011) / NULLIF(sum(revenue) FILTER (WHERE fiscal_year = 2010), 0) - 1 AS growth,
       count(DISTINCT customer_id) AS customers
FROM fct_sales GROUP BY country ORDER BY fy2011_revenue DESC NULLS LAST;

-- Q8. Customer value by cohort: cumulative revenue per customer -------------
CREATE OR REPLACE TABLE mart_cohort_value AS
WITH rev AS (
    SELECT c.cohort_month,
           datediff('month', c.cohort_month, date_trunc('month', s.invoice_date)) AS months_since_first,
           sum(s.revenue) AS revenue
    FROM fct_sales s JOIN dim_customer c USING (customer_id)
    GROUP BY 1, 2
),
sizes AS (SELECT cohort_month, count(*) AS cohort_size FROM dim_customer WHERE cohort_month IS NOT NULL GROUP BY 1)
SELECT r.cohort_month, r.months_since_first, s.cohort_size,
       sum(r.revenue) OVER (PARTITION BY r.cohort_month ORDER BY r.months_since_first) / s.cohort_size
           AS cum_revenue_per_customer
FROM rev r JOIN sizes s USING (cohort_month)
ORDER BY 1, 2;
