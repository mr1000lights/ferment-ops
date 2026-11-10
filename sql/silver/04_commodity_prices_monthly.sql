-- Monthly commodity prices, normalised to USD per metric tonne.
SELECT
    make_date(CAST(substr(period, 1, 4) AS INTEGER), CAST(substr(period, 6, 2) AS INTEGER), 1) AS month,
    commodity_code,
    source_name,
    unit                                            AS source_unit,
    CASE
        WHEN lower(unit) LIKE '%/kg%' THEN price_usd * 1000
        WHEN lower(unit) LIKE '%/mt%' THEN price_usd
        ELSE NULL
    END                                             AS price_usd_per_t
FROM bronze.worldbank_commodity_prices
WHERE price_usd IS NOT NULL AND price_usd > 0
