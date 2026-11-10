"""IndPenSim fermentation batches -> bronze.

Source quirks handled here (documented, tested):
* ~2,200 Raman spectrum columns (headers are wavenumbers like "2400") are
  dropped at read time so the 2.5 GB CSV streams in chunks.
* Column labels carry a short code in parentheses, e.g. "pH(pH:pH)"; we map on
  the code, not the full label, so cosmetic label changes do not break us.
* The batch-reference labels are shifted by one column in the published file.
  We derive batch_id from the time reset between batches and keep the shifted
  column for a reconciliation check in the data-quality layer.
"""
from __future__ import annotations

import logging
import re
import zipfile

import pandas as pd

from ..config import Settings
from .common import download, record_manifest, write_bronze

log = logging.getLogger("fdp.ingest.indpensim")

CODE_TO_COLUMN = {
    "Fg": "aeration_rate",
    "RPM": "agitator_rpm",
    "Fs": "sugar_feed_l_h",
    "Fa": "acid_flow_l_h",
    "Fb": "base_flow_l_h",
    "Fc": "cooling_water_l_h",
    "Fh": "heating_water_l_h",
    "Fw": "water_injection_l_h",
    "pressure": "air_head_pressure_bar",
    "Fremoved": "dumped_broth_l_h",
    "S": "substrate_g_l",
    "DO2": "dissolved_oxygen_mg_l",
    "P": "product_g_l",
    "V": "vessel_volume_l",
    "Wt": "vessel_weight_kg",
    "pH": "ph",
    "T": "temperature_k",
    "Q": "generated_heat_kj",
    "CO2outgas": "co2_offgas_pct",
    "Fpaa": "paa_flow_l_h",
    "PAA_offline": "paa_offline_g_l",
    "Foil": "oil_flow_l_h",
    "NH3_offline": "nh3_offline_g_l",
    "OUR": "oxygen_uptake_rate",
    "O2": "o2_offgas_pct",
    "P_offline": "product_offline_g_l",
    "X_offline": "biomass_offline_g_l",
    "CER": "carbon_evolution_rate",
    "NH3_shots": "ammonia_shots_kg",
    "Viscosity_offline": "viscosity_offline_cp",
    "Fault_ref": "fault_flag",
    "Control_ref": "operator_control_flag",
    "PAT_ref": "batch_ref_label_shifted",   # holds batch ids in the published file
}
CODE_RE = re.compile(r"\(([^:()]+):")


def canonical_name(label: str) -> str | None:
    label = label.strip()
    if label.lower().startswith("time"):
        return "time_h"
    m = CODE_RE.search(label)
    if m:
        return CODE_TO_COLUMN.get(m.group(1).strip())
    return None


def is_raman(label: str) -> bool:
    return label.strip().replace(".", "", 1).isdigit()


def _find_member(zf: zipfile.ZipFile, name: str) -> str:
    for m in zf.namelist():
        if m.endswith(name):
            return m
    raise FileNotFoundError(f"{name} not found in archive; members: {zf.namelist()[:10]}")


def read_timeseries(zf: zipfile.ZipFile, member: str, chunksize: int = 20_000) -> pd.DataFrame:
    with zf.open(member) as fh:
        header = pd.read_csv(fh, nrows=0).columns
    keep = [c for c in header if not is_raman(c)]
    mapping = {c: canonical_name(c) for c in keep}
    unmapped = [c for c, v in mapping.items() if v is None]
    if unmapped:
        log.info("ignoring %s unmapped label columns: %s", len(unmapped), unmapped)
    usecols = [c for c, v in mapping.items() if v]
    chunks = []
    with zf.open(member) as fh:
        for chunk in pd.read_csv(fh, usecols=usecols, chunksize=chunksize, low_memory=False):
            chunks.append(chunk.rename(columns=mapping))
    df = pd.concat(chunks, ignore_index=True)
    # Derive batch id: time resets to its start value when a new batch begins.
    df["batch_id"] = (df["time_h"].diff() < 0).cumsum().astype(int) + 1
    df["sample_seq"] = df.groupby("batch_id").cumcount() + 1
    return df


def read_statistics(zf: zipfile.ZipFile, member: str) -> pd.DataFrame:
    with zf.open(member) as fh:
        df = pd.read_csv(fh)
    rename = {}
    for c in df.columns:
        lc = c.lower()
        if lc.startswith("batch"):
            rename[c] = "batch_id"
        elif "during" in lc:
            rename[c] = "product_harvested_during_kg"
        elif "end_of_batch" in lc or "end of batch" in lc:
            rename[c] = "product_harvested_end_kg"
        elif "yield_total" in lc:
            rename[c] = "product_yield_total_kg"
        elif lc.startswith("fault"):
            rename[c] = "fault_flag"
    return df.rename(columns=rename)


def ingest(settings: Settings, mode: str = "live", force: bool = False) -> None:
    src = settings.source("indpensim")
    raw_file = settings.raw / src["landing_file"]
    if mode == "live":
        download(src["url"], raw_file, timeout=600, force=force)
    elif not raw_file.exists():
        raise FileNotFoundError(f"{raw_file} missing - run `fdp sample` first")
    record_manifest(settings.raw, "indpensim", raw_file, src["url"], mode)

    with zipfile.ZipFile(raw_file) as zf:
        ts = read_timeseries(zf, _find_member(zf, src["timeseries_member"]))
        stats = read_statistics(zf, _find_member(zf, src["statistics_member"]))
    log.info("IndPenSim: %s rows, %s batches", len(ts), ts["batch_id"].nunique())
    write_bronze(ts, settings.bronze, "indpensim_timeseries", src["landing_file"])
    write_bronze(stats, settings.bronze, "indpensim_batch_statistics", src["landing_file"])
