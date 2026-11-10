-- Batch dimension for reporting.
SELECT
    m.batch_id,
    m.batch_code,
    m.control_strategy,
    m.is_fault_batch,
    min(t.time_h)  AS first_sample_h,
    max(t.time_h)  AS duration_h,
    count(*)       AS n_samples
FROM silver.batch_master m
JOIN silver.batch_timeseries t USING (batch_id)
GROUP BY ALL
ORDER BY m.batch_id
