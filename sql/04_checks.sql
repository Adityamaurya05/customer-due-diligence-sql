-- 04 DATA-QUALITY CHECKS. Each query returns: check, failures, detail.
-- 'failures' must be 0 for a hard check; 'info' checks are reported but don't fail the run.

SELECT 'hard' AS severity, 'fct_sales reconciles to staging product revenue' AS check_name,
       CASE WHEN abs((SELECT sum(revenue) FROM fct_sales) - (SELECT sum(line_revenue) FROM stg_product_sales)) < 0.01
            THEN 0 ELSE 1 END AS failures, '' AS detail
UNION ALL
SELECT 'hard', 'no completed line has negative quantity',
       count(*), '' FROM fct_sales WHERE NOT is_cancellation AND quantity < 0
UNION ALL
SELECT 'hard', 'no cancellation has positive quantity',
       count(*), '' FROM fct_sales WHERE is_cancellation AND quantity > 0
UNION ALL
SELECT 'hard', 'dim_customer has one row per customer',
       count(*) - count(DISTINCT customer_id), '' FROM dim_customer
UNION ALL
SELECT 'hard', 'cohort retention month 0 equals 100%',
       count(*), '' FROM mart_cohort_retention WHERE months_since_first = 0 AND abs(retention - 1) > 1e-9
UNION ALL
SELECT 'hard', 'customer bridge adds up',
       CASE WHEN abs((SELECT sum(value) FROM mart_customer_bridge WHERE ord BETWEEN 1 AND 6)
                    - (SELECT value FROM mart_customer_bridge WHERE ord = 7)) < 0.01 THEN 0 ELSE 1 END, ''
UNION ALL
SELECT 'hard', 'price/volume bridge adds up',
       CASE WHEN abs((SELECT sum(value) FROM mart_price_volume WHERE ord BETWEEN 1 AND 5)
                    - (SELECT value FROM mart_price_volume WHERE ord = 6)) < 0.01 THEN 0 ELSE 1 END, ''
UNION ALL
SELECT 'info', 'lines without customer_id',
       count(*) FILTER (WHERE customer_id IS NULL),
       printf('%.1f%% of product lines, %.1f%% of revenue',
              100.0 * count(*) FILTER (WHERE customer_id IS NULL) / count(*),
              100.0 * sum(revenue) FILTER (WHERE customer_id IS NULL) / sum(revenue))
FROM fct_sales
UNION ALL
SELECT 'info', 'non-product lines excluded (postage, fees, adjustments)',
       count(*), printf('%.0f of revenue', sum(line_revenue)) FROM stg_transactions WHERE is_non_product
UNION ALL
SELECT 'info', 'cancellation lines', count(*), printf('%.0f of revenue reversed', -sum(revenue))
FROM fct_sales WHERE is_cancellation;
