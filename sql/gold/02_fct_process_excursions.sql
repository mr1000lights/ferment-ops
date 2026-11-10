-- Out-of-spec excursions (gaps-and-islands): a run of >= min_points consecutive
-- samples outside the process specification for one variable.
WITH long AS (
    SELECT batch_id, time_h, sample_seq, 'temperature_k' AS variable, temperature_k AS value FROM silver.batch_timeseries
    UNION ALL
    SELECT batch_id, time_h, sample_seq, 'ph', ph FROM silver.batch_timeseries
    UNION ALL
    SELECT batch_id, time_h, sample_seq, 'dissolved_oxygen_mg_l', dissolved_oxygen_mg_l FROM silver.batch_timeseries
),
flagged AS (
    SELECT l.*, s.low, s.high, s.min_points,
           (l.value < s.low OR l.value > s.high) AS out_of_spec
    FROM long l
    JOIN ref.process_spec s USING (variable)
    WHERE l.time_h >= 20            -- ignore inoculation / start-up transient
),
islands AS (
    SELECT *,
           sample_seq - row_number() OVER (PARTITION BY batch_id, variable ORDER BY sample_seq) AS grp
    FROM flagged
    WHERE out_of_spec
)
SELECT
    batch_id,
    variable,
    min(time_h)                         AS start_h,
    max(time_h)                         AS end_h,
    round(max(time_h) - min(time_h) + 0.2, 1) AS duration_h,
    count(*)                            AS n_points,
    min(value)                          AS min_value,
    max(value)                          AS max_value,
    any_value(low)                      AS spec_low,
    any_value(high)                     AS spec_high
FROM islands
GROUP BY batch_id, variable, grp
HAVING count(*) >= any_value(min_points)
ORDER BY batch_id, start_h
