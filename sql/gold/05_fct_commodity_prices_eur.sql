-- Commodity prices converted to EUR per tonne with the ECB monthly average rate.
SELECT
    p.month,
    p.commodity_code,
    p.source_name,
    p.price_usd_per_t,
    fx.usd_per_eur,
    p.price_usd_per_t / fx.usd_per_eur AS price_eur_per_t
FROM silver.commodity_prices_monthly p
JOIN silver.fx_usd_per_eur fx USING (month)
WHERE p.price_usd_per_t IS NOT NULL
ORDER BY p.commodity_code, p.month
