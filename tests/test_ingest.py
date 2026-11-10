import zipfile

import pandas as pd

from fdp.ingest.indpensim import canonical_name, is_raman, read_timeseries
from fdp.ingest.worldbank import parse_monthly_sheet


def test_label_mapping_uses_short_codes():
    assert canonical_name("pH(pH:pH)") == "ph"
    assert canonical_name("Temperature(T:K)") == "temperature_k"
    assert canonical_name("Oxygen Uptake Rate(OUR:(g min^{-1}))") == "oxygen_uptake_rate"
    assert canonical_name("PAA flow(Fpaa:PAA flow (L/h))") == "paa_flow_l_h"
    assert canonical_name("Time (h)") == "time_h"
    assert canonical_name("1- No Raman spec") is None


def test_raman_columns_detected():
    assert is_raman("2400") and is_raman("399.5")
    assert not is_raman("pH(pH:pH)")


def test_batch_id_derived_from_time_reset(settings):
    src = settings.source("indpensim")
    with zipfile.ZipFile(settings.raw / src["landing_file"]) as zf:
        member = next(m for m in zf.namelist() if m.endswith(src["timeseries_member"]))
        df = read_timeseries(zf, member)
    assert df["batch_id"].nunique() == 100
    assert (df["batch_id"] == df["batch_ref_label_shifted"]).all()
    assert not any(c.isdigit() for c in df.columns)          # Raman dropped


def test_pinksheet_parser_finds_commodities_and_units(settings):
    src = settings.source("worldbank_pinksheet")
    df = parse_monthly_sheet(settings.raw / src["landing_file"], src["sheet"], src["commodities"])
    assert {"sugar_world", "soybean_meal", "fishmeal"} <= set(df.commodity_code)
    assert df["period"].str.match(r"^\d{4}M\d{2}$").all()
    sugar_eu = df[df.commodity_code == "sugar_eu"]
    assert sugar_eu["price_usd"].isna().any()                 # "…" placeholders -> NaN, not errors


def test_bronze_has_load_metadata(settings):
    df = pd.read_parquet(settings.bronze / "ecb_fx_usd_eur.parquet")
    assert {"_source_file", "_ingested_at"} <= set(df.columns)
