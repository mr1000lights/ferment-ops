"""Data-quality checks.

Each check is a SQL query returning the number of offending rows (0 = pass).
Results are appended to ``dq.check_results`` so quality is tracked over runs.
``error`` checks stop the pipeline; ``warn`` checks are reported only.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import duckdb
import pandas as pd

log = logging.getLogger("fdp.quality")


@dataclass(frozen=True)
class Check:
    name: str
    layer: str
    severity: str          # "error" | "warn"
    description: str
    sql: str               # must return a single integer: number of failing rows


CHECKS: list[Check] = [
    # ---------- bronze: did we land what we expected? ----------
    Check("bronze_timeseries_not_empty", "bronze", "error",
          "IndPenSim time series has rows",
          "SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM bronze.indpensim_timeseries"),
    Check("bronze_expected_batch_count", "bronze", "error",
          "Exactly 100 batches derived from time resets",
          "SELECT abs(count(DISTINCT batch_id) - 100) FROM bronze.indpensim_timeseries"),
    Check("bronze_batch_id_reconciles_with_source_label", "bronze", "warn",
          "Derived batch_id equals the (label-shifted) batch reference column",
          "SELECT count(*) FROM bronze.indpensim_timeseries "
          "WHERE batch_ref_label_shifted IS NOT NULL AND batch_ref_label_shifted <> batch_id"),
    Check("bronze_statistics_cover_all_batches", "bronze", "error",
          "Every batch in the time series has a statistics row",
          "SELECT count(*) FROM (SELECT DISTINCT batch_id FROM bronze.indpensim_timeseries) t "
          "ANTI JOIN bronze.indpensim_batch_statistics s USING (batch_id)"),
    Check("bronze_required_commodities_present", "bronze", "error",
          "Pink Sheet contains sugar (world), soybean meal and fishmeal",
          "SELECT 3 - count(DISTINCT commodity_code) FROM bronze.worldbank_commodity_prices "
          "WHERE commodity_code IN ('sugar_world','soybean_meal','fishmeal')"),
    # ---------- silver: is the data valid? ----------
    Check("silver_timeseries_unique_key", "silver", "error",
          "(batch_id, time_h) is unique",
          "SELECT count(*) - count(DISTINCT (batch_id, time_h)) FROM silver.batch_timeseries"),
    Check("silver_ph_physical_range", "silver", "error",
          "pH within physically plausible 4-9",
          "SELECT count(*) FROM silver.batch_timeseries WHERE ph NOT BETWEEN 4 AND 9"),
    Check("silver_temperature_physical_range", "silver", "error",
          "Temperature within 280-320 K",
          "SELECT count(*) FROM silver.batch_timeseries WHERE temperature_k NOT BETWEEN 280 AND 320"),
    Check("silver_no_negative_concentrations", "silver", "error",
          "Substrate, DO and product are non-negative",
          "SELECT count(*) FROM silver.batch_timeseries "
          "WHERE substrate_g_l < 0 OR dissolved_oxygen_mg_l < 0 OR product_g_l < 0"),
    Check("silver_sampling_interval_regular", "silver", "warn",
          "No sampling gaps longer than 1 h inside a batch",
          "SELECT count(*) FROM (SELECT time_h - lag(time_h) OVER (PARTITION BY batch_id ORDER BY time_h) AS gap "
          "FROM silver.batch_timeseries) WHERE gap > 1.0"),
    Check("silver_commodity_unique_month", "silver", "error",
          "One price per commodity per month",
          "SELECT count(*) - count(DISTINCT (commodity_code, month)) FROM silver.commodity_prices_monthly"),
    Check("silver_commodity_unit_recognised", "silver", "warn",
          "All commodity units converted to USD/t",
          "SELECT count(*) FROM silver.commodity_prices_monthly WHERE price_usd_per_t IS NULL"),
    Check("silver_fx_plausible", "silver", "error",
          "USD per EUR between 0.5 and 2.0",
          "SELECT count(*) FROM silver.fx_usd_per_eur WHERE usd_per_eur NOT BETWEEN 0.5 AND 2.0"),
    Check("silver_fx_fresh", "silver", "warn",
          "Latest FX month is at most 3 months old",
          "SELECT CASE WHEN max(month) < current_date - INTERVAL 4 MONTH THEN 1 ELSE 0 END "
          "FROM silver.fx_usd_per_eur"),
    # ---------- gold: do the numbers make sense? ----------
    Check("gold_yield_in_range", "gold", "error",
          "Product yield on sugar between 0 and 1 kg/kg",
          "SELECT count(*) FROM gold.fct_batch_kpis WHERE yield_kg_per_kg_sugar NOT BETWEEN 0 AND 1"),
    Check("gold_kpis_reconcile_with_source", "gold", "warn",
          "Computed product mass correlates (r >= 0.8) with source-reported harvest",
          "SELECT CASE WHEN corr(product_mass_kg, reported_harvest_end) >= 0.8 THEN 0 ELSE 1 END "
          "FROM gold.fct_batch_kpis"),
    Check("gold_fx_join_complete", "gold", "warn",
          "Every commodity month found an FX rate",
          "SELECT (SELECT count(*) FROM silver.commodity_prices_monthly WHERE price_usd_per_t IS NOT NULL "
          "AND month >= (SELECT min(month) FROM silver.fx_usd_per_eur) "
          "AND month <= (SELECT max(month) FROM silver.fx_usd_per_eur)) "
          "- (SELECT count(*) FROM gold.fct_commodity_prices_eur)"),
]


class DataQualityError(RuntimeError):
    pass


def run_checks(con: duckdb.DuckDBPyConnection, layers: tuple[str, ...] | None = None,
               fail_on_error: bool = True) -> pd.DataFrame:
    run_id = uuid.uuid4().hex[:12]
    ran_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    rows = []
    for c in CHECKS:
        if layers and c.layer not in layers:
            continue
        try:
            failing = int(con.execute(c.sql).fetchone()[0] or 0)
            status = "pass" if failing == 0 else ("fail" if c.severity == "error" else "warn")
            msg = ""
        except duckdb.Error as exc:
            failing, status, msg = -1, "fail", str(exc).splitlines()[0]
        rows.append({"run_id": run_id, "ran_at": ran_at, "check_name": c.name, "layer": c.layer,
                     "severity": c.severity, "description": c.description,
                     "failing_rows": failing, "status": status, "message": msg})
        log.info("DQ %-5s %-48s failing=%s", status.upper(), c.name, failing)
    res = pd.DataFrame(rows)
    con.execute("CREATE SCHEMA IF NOT EXISTS dq")
    con.register("_dq", res)
    con.execute("CREATE TABLE IF NOT EXISTS dq.check_results AS SELECT * FROM _dq WHERE 1=0")
    con.execute("INSERT INTO dq.check_results SELECT * FROM _dq")
    con.unregister("_dq")
    failed = res[res["status"] == "fail"]
    if fail_on_error and len(failed):
        raise DataQualityError(f"{len(failed)} blocking check(s) failed: {failed['check_name'].tolist()}")
    return res
