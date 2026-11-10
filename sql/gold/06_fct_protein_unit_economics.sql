-- Illustrative scenario: molasses feedstock cost per tonne of microbial protein vs the
-- market price of a tonne of protein bought as fishmeal or soybean meal.
-- Every assumption lives in ref.unit_economics (config/pipeline.yaml).
WITH wide AS (
    SELECT
        month,
        max(price_eur_per_t) FILTER (WHERE commodity_code = 'sugar_world')  AS sugar_eur_t,
        max(price_eur_per_t) FILTER (WHERE commodity_code = 'fishmeal')     AS fishmeal_eur_t,
        max(price_eur_per_t) FILTER (WHERE commodity_code = 'soybean_meal') AS soybean_meal_eur_t
    FROM gold.fct_commodity_prices_eur
    GROUP BY month
)
SELECT
    w.month,
    w.sugar_eur_t,
    w.sugar_eur_t * e.molasses_price_vs_sugar                                   AS molasses_eur_t_est,
    1.0 / (e.biomass_yield_on_sugar * e.protein_content_of_biomass)
        / e.molasses_sugar_content                                              AS molasses_t_per_t_protein,
    w.sugar_eur_t * e.molasses_price_vs_sugar
        / (e.biomass_yield_on_sugar * e.protein_content_of_biomass)
        / e.molasses_sugar_content                                              AS feedstock_eur_per_t_protein,
    w.fishmeal_eur_t / e.fishmeal_protein_content                               AS fishmeal_eur_per_t_protein,
    w.soybean_meal_eur_t / e.soybean_meal_protein_content                       AS soybean_meal_eur_per_t_protein,
    w.sugar_eur_t * e.molasses_price_vs_sugar
        / e.biomass_yield_on_sugar / e.molasses_sugar_content
        * e.leuna_capacity_t_per_year / 1e6                                     AS annual_feedstock_meur_at_capacity
FROM wide w
CROSS JOIN ref.unit_economics e
WHERE w.sugar_eur_t IS NOT NULL
ORDER BY w.month
