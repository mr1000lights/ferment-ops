-- ECB monthly average reference rate: US dollars per 1 euro.
SELECT
    CAST(strptime(time_period || '-01', '%Y-%m-%d') AS DATE) AS month,
    CAST(obs_value AS DOUBLE)                                 AS usd_per_eur
FROM bronze.ecb_fx_usd_eur
WHERE currency = 'USD' AND currency_denom = 'EUR' AND obs_value IS NOT NULL
