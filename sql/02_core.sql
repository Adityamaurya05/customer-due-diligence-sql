-- 02 CORE MODEL: star schema (one fact table, two dimensions).

CREATE OR REPLACE TABLE fct_sales AS
SELECT
    invoice, invoice_date, invoice_ts, fiscal_year, customer_id, stock_code, description, country,
    quantity, price, line_revenue AS revenue, is_cancellation
FROM stg_product_sales;

CREATE OR REPLACE TABLE dim_customer AS
SELECT
    customer_id,
    arg_max(country, invoice_ts)                       AS country,           -- latest known country
    -- cohort = month of the first COMPLETED order (a cancellation is not a purchase)
    min(invoice_date) FILTER (WHERE NOT is_cancellation)                      AS first_order_date,
    max(invoice_date) FILTER (WHERE NOT is_cancellation)                      AS last_order_date,
    CAST(date_trunc('month', min(invoice_date) FILTER (WHERE NOT is_cancellation)) AS DATE) AS cohort_month,
    count(DISTINCT invoice) FILTER (WHERE NOT is_cancellation) AS n_orders,
    sum(revenue)                                       AS net_revenue
FROM fct_sales
WHERE customer_id IS NOT NULL
GROUP BY customer_id;

CREATE OR REPLACE TABLE dim_product AS
SELECT
    stock_code,
    mode(description)                                  AS description,
    median(price) FILTER (WHERE NOT is_cancellation)   AS median_price,
    sum(quantity)                                      AS net_units,
    sum(revenue)                                       AS net_revenue
FROM fct_sales
GROUP BY stock_code;

-- Order (invoice) level, completed orders only
CREATE OR REPLACE TABLE fct_orders AS
SELECT
    invoice, any_value(customer_id) AS customer_id, min(invoice_date) AS order_date,
    any_value(fiscal_year) AS fiscal_year, any_value(country) AS country,
    sum(revenue) AS order_value, sum(quantity) AS units, count(*) AS lines
FROM fct_sales
WHERE NOT is_cancellation
GROUP BY invoice;
