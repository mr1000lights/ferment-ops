-- One row per batch: yield, productivity, process stability and data reconciliation.
WITH ts AS (
    SELECT *,
           time_h - coalesce(lag(time_h) OVER (PARTITION BY batch_id ORDER BY time_h), 0) AS dt_h
    FROM silver.batch_timeseries
),
agg AS (
    SELECT
        batch_id,
        max(time_h)                                                   AS duration_h,
        arg_max(product_g_l, time_h)                                  AS final_product_g_l,
        arg_max(vessel_volume_l, time_h)                              AS final_volume_l,
        sum(sugar_feed_l_h * dt_h)                                    AS sugar_feed_l,
        avg(dissolved_oxygen_mg_l)                                    AS avg_do_mg_l,
        min(dissolved_oxygen_mg_l) FILTER (WHERE time_h >= 20)        AS min_do_mg_l,
        max(temperature_k) - min(temperature_k) FILTER (WHERE time_h >= 20) AS temp_range_k,
        stddev_samp(ph) FILTER (WHERE time_h >= 20)                   AS ph_std,
        max(biomass_offline_g_l)                                      AS peak_biomass_g_l,
        sum(fault_flag)                                               AS fault_samples
    FROM ts
    GROUP BY batch_id
),
envelope AS (
    SELECT t.batch_id,
           avg(CASE WHEN t.dissolved_oxygen_mg_l < g.do_p05 OR t.dissolved_oxygen_mg_l > g.do_p95
                     OR t.substrate_g_l < g.substrate_p05 OR t.substrate_g_l > g.substrate_p95
                    THEN 1.0 ELSE 0.0 END)                            AS share_outside_golden_envelope
    FROM silver.batch_timeseries t
    JOIN gold.fct_golden_batch_profile g
      ON g.time_bucket_h = CAST(floor(t.time_h / 2) * 2 AS INTEGER)
    GROUP BY t.batch_id
),
exc AS (
    SELECT batch_id, count(*) AS n_excursions, sum(duration_h) AS excursion_hours
    FROM gold.fct_process_excursions
    GROUP BY batch_id
)
SELECT
    d.batch_id,
    d.batch_code,
    d.control_strategy,
    d.is_fault_batch,
    a.duration_h,
    a.final_product_g_l,
    a.final_volume_l,
    a.final_product_g_l * a.final_volume_l / 1000.0                   AS product_mass_kg,
    a.sugar_feed_l * e.sugar_feed_concentration_g_l / 1000.0          AS sugar_fed_kg,
    (a.final_product_g_l * a.final_volume_l / 1000.0)
        / nullif(a.sugar_feed_l * e.sugar_feed_concentration_g_l / 1000.0, 0) AS yield_kg_per_kg_sugar,
    a.final_product_g_l / nullif(a.duration_h, 0)                     AS productivity_g_l_h,
    a.avg_do_mg_l,
    a.min_do_mg_l,
    a.temp_range_k,
    a.ph_std,
    a.peak_biomass_g_l,
    coalesce(x.n_excursions, 0)                                       AS n_excursions,
    coalesce(x.excursion_hours, 0)                                    AS excursion_hours,
    env.share_outside_golden_envelope,
    a.fault_samples,
    s.reported_harvest_end,
    s.reported_yield_total
FROM gold.dim_batch d
JOIN agg a USING (batch_id)
CROSS JOIN ref.unit_economics e
LEFT JOIN envelope env USING (batch_id)
LEFT JOIN exc x USING (batch_id)
LEFT JOIN silver.batch_statistics s USING (batch_id)
ORDER BY d.batch_id
