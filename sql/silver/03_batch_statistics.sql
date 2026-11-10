-- Source-reported harvest statistics per batch (used to reconcile our own KPIs).
SELECT
    CAST(batch_id AS INTEGER)                   AS batch_id,
    CAST(product_harvested_during_kg AS DOUBLE) AS reported_harvest_during,
    CAST(product_harvested_end_kg AS DOUBLE)    AS reported_harvest_end,
    CAST(product_yield_total_kg AS DOUBLE)      AS reported_yield_total,
    CAST(fault_flag AS INTEGER) = 1             AS reported_fault
FROM bronze.indpensim_batch_statistics
