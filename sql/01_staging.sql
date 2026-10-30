-- 01 STAGING: clean the raw extract. One row per invoice line.
-- Rules (each one is a data-quality finding worth mentioning in an interview):
--   * Invoices starting with 'C' are cancellations (negative quantity).
--   * Stock codes without any digit are fees, postage or manual adjustments, not products.
--   * Lines with price <= 0 are write-offs or test entries.
--   * Many lines have no customer_id (the checks report how many); they count toward revenue
--     but not toward customer analytics.
--   * Exact duplicate lines are removed.

CREATE OR REPLACE TABLE stg_transactions AS
WITH raw AS (
    SELECT DISTINCT * FROM read_parquet('{raw_path}')
)
SELECT
    trim(invoice)                                   AS invoice,
    upper(trim(stock_code))                         AS stock_code,
    trim(description)                               AS description,
    CAST(quantity AS INTEGER)                       AS quantity,
    CAST(invoice_date AS TIMESTAMP)                 AS invoice_ts,
    CAST(invoice_date AS DATE)                      AS invoice_date,
    CAST(price AS DOUBLE)                           AS price,
    CAST(customer_id AS BIGINT)                     AS customer_id,
    trim(country)                                   AS country,
    invoice LIKE 'C%'                               AS is_cancellation,
    NOT regexp_matches(stock_code, '[0-9]')         AS is_non_product,
    quantity * price                                AS line_revenue,
    -- Fiscal year Dec-Nov gives two complete 12-month periods in this dataset.
    CASE WHEN month(invoice_date) = 12 THEN year(invoice_date) + 1 ELSE year(invoice_date) END AS fiscal_year
FROM raw
WHERE price > 0
  AND invoice_date IS NOT NULL;

-- Product sales only (excludes fees and adjustments); net of cancellations.
CREATE OR REPLACE VIEW stg_product_sales AS
SELECT * FROM stg_transactions WHERE NOT is_non_product;
