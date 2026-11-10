-- "Golden batch" envelope: percentile bands per 2-hour bucket, built from
-- normal (non-fault) batches only. Used to judge any new batch against history.
SELECT
    CAST(floor(t.time_h / 2) * 2 AS INTEGER)              AS time_bucket_h,
    count(DISTINCT t.batch_id)                            AS n_batches,
    quantile_cont(t.product_g_l, 0.05)                    AS product_p05,
    quantile_cont(t.product_g_l, 0.50)                    AS product_p50,
    quantile_cont(t.product_g_l, 0.95)                    AS product_p95,
    quantile_cont(t.dissolved_oxygen_mg_l, 0.05)          AS do_p05,
    quantile_cont(t.dissolved_oxygen_mg_l, 0.50)          AS do_p50,
    quantile_cont(t.dissolved_oxygen_mg_l, 0.95)          AS do_p95,
    quantile_cont(t.substrate_g_l, 0.05)                  AS substrate_p05,
    quantile_cont(t.substrate_g_l, 0.50)                  AS substrate_p50,
    quantile_cont(t.substrate_g_l, 0.95)                  AS substrate_p95,
    quantile_cont(t.temperature_k, 0.05)                  AS temperature_p05,
    quantile_cont(t.temperature_k, 0.95)                  AS temperature_p95,
    quantile_cont(t.ph, 0.05)                             AS ph_p05,
    quantile_cont(t.ph, 0.95)                             AS ph_p95
FROM silver.batch_timeseries t
JOIN silver.batch_master m USING (batch_id)
WHERE NOT m.is_fault_batch
GROUP BY 1
HAVING count(DISTINCT t.batch_id) >= 10
ORDER BY 1
