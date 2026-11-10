-- Cleaned bioreactor time series: typed, de-duplicated, unit-normalised, phase-tagged.
WITH dedup AS (
    SELECT *,
           row_number() OVER (PARTITION BY batch_id, time_h ORDER BY sample_seq) AS rn
    FROM bronze.indpensim_timeseries
    WHERE time_h IS NOT NULL AND batch_id IS NOT NULL
)
SELECT
    batch_id,
    CAST(time_h AS DOUBLE)                           AS time_h,
    sample_seq,
    CASE WHEN time_h < 40 THEN 'growth' ELSE 'production' END AS phase,
    -- actuators
    aeration_rate,
    sugar_feed_l_h,
    acid_flow_l_h,
    base_flow_l_h,
    water_injection_l_h,
    -- online sensors
    substrate_g_l,
    dissolved_oxygen_mg_l,
    ph,
    temperature_k,
    temperature_k - 273.15                           AS temperature_c,
    vessel_volume_l,
    co2_offgas_pct,
    o2_offgas_pct,
    oxygen_uptake_rate,
    carbon_evolution_rate,
    product_g_l,
    -- offline lab results (sparse)
    product_offline_g_l,
    biomass_offline_g_l,
    viscosity_offline_cp,
    CAST(coalesce(fault_flag, 0) AS INTEGER)         AS fault_flag,
    _source_file,
    _ingested_at
FROM dedup
WHERE rn = 1
