"""DuckDB warehouse: bronze views, reference seeds, and ordered SQL models."""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd

from .config import REPO_ROOT, Settings

log = logging.getLogger("fdp.warehouse")
SQL_DIR = REPO_ROOT / "sql"
LAYERS = ("silver", "gold")


def connect(settings: Settings, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(settings.warehouse), read_only=read_only)


def register_bronze(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    for pq in sorted(Path(settings.bronze).glob("*.parquet")):
        con.execute(
            f"CREATE OR REPLACE VIEW bronze.{pq.stem} AS SELECT * FROM read_parquet('{pq.as_posix()}')"
        )
        log.info("bronze view  bronze.%s", pq.stem)


def load_reference(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Seeds from config: process spec, economics assumptions, control strategy map."""
    cfg = settings.cfg
    con.execute("CREATE SCHEMA IF NOT EXISTS ref")
    spec = pd.DataFrame(
        [
            {"variable": k, "low": v.get("low"), "high": v.get("high")}
            for k, v in cfg["process_spec"].items()
            if isinstance(v, dict)
        ]
    )
    spec["min_points"] = cfg["process_spec"]["min_points_for_excursion"]
    spec[["low", "high"]] = spec[["low", "high"]].astype("Float64")   # None -> SQL NULL
    econ = pd.DataFrame([cfg["unit_economics"]])
    strat = pd.DataFrame(cfg["reference"]["control_strategy_ranges"]).rename(
        columns={"from": "batch_from", "to": "batch_to"}
    )
    for name, df in {"process_spec": spec, "unit_economics": econ, "control_strategy": strat}.items():
        con.register("_tmp", df)
        con.execute(f"CREATE OR REPLACE TABLE ref.{name} AS SELECT * FROM _tmp")
        con.unregister("_tmp")
        log.info("ref seed     ref.%s (%s rows)", name, len(df))


def run_models(con: duckdb.DuckDBPyConnection, layers=LAYERS) -> list[str]:
    built = []
    for layer in layers:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {layer}")
        for f in sorted((SQL_DIR / layer).glob("*.sql")):
            name = f.stem.split("_", 1)[1]      # 01_batch_timeseries -> batch_timeseries
            sql = f.read_text(encoding="utf-8")
            con.execute(f"CREATE OR REPLACE TABLE {layer}.{name} AS\n{sql}")
            n = con.execute(f"SELECT count(*) FROM {layer}.{name}").fetchone()[0]
            log.info("model %-6s %-34s %8d rows", layer, name, n)
            built.append(f"{layer}.{name}")
    return built


def build(settings: Settings) -> list[str]:
    with connect(settings) as con:
        register_bronze(con, settings)
        load_reference(con, settings)
        return run_models(con)
