-- One row per batch: control regime (published reference) + fault flag (source statistics).
WITH batches AS (
    SELECT DISTINCT batch_id FROM bronze.indpensim_timeseries
)
SELECT
    b.batch_id,
    printf('B%03d', b.batch_id)                          AS batch_code,
    coalesce(cs.strategy, 'Unknown')                     AS control_strategy,
    coalesce(st.fault_flag, 0) = 1                       AS is_fault_batch
FROM batches b
LEFT JOIN ref.control_strategy cs
       ON b.batch_id BETWEEN cs.batch_from AND cs.batch_to
LEFT JOIN bronze.indpensim_batch_statistics st
       ON st.batch_id = b.batch_id
ORDER BY b.batch_id
