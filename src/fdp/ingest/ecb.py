"""ECB SDMX REST API (USD per EUR, monthly average) -> bronze."""
from __future__ import annotations

import logging

import pandas as pd

from ..config import Settings
from .common import download, record_manifest, write_bronze

log = logging.getLogger("fdp.ingest.ecb")


def ingest(settings: Settings, mode: str = "live", force: bool = False) -> None:
    src = settings.source("ecb_fx")
    raw_file = settings.raw / src["landing_file"]
    if mode == "live":
        # API data changes monthly, so always refresh it.
        download(src["url"], raw_file, params=src.get("params"), force=True)
    elif not raw_file.exists():
        raise FileNotFoundError(f"{raw_file} missing - run `fdp sample` first")
    record_manifest(settings.raw, "ecb_fx", raw_file, src["url"], mode)
    df = pd.read_csv(raw_file)
    expected = {"TIME_PERIOD", "OBS_VALUE", "CURRENCY", "CURRENCY_DENOM"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"ECB response missing columns {missing}")
    keep = ["KEY", "CURRENCY", "CURRENCY_DENOM", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"]
    df = df[[c for c in keep if c in df.columns]].rename(columns=str.lower)
    write_bronze(df, settings.bronze, "ecb_fx_usd_eur", src["landing_file"])
