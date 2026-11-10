"""World Bank Pink Sheet (monthly commodity prices) -> bronze (long format)."""
from __future__ import annotations

import logging
import re

import pandas as pd
import requests

from ..config import Settings
from .common import USER_AGENT, download, record_manifest, write_bronze

log = logging.getLogger("fdp.ingest.worldbank")
PERIOD_RE = re.compile(r"^\d{4}M\d{2}$")


def discover_url(page: str) -> str | None:
    """The file URL changes each year; fall back to scraping the commodities page."""
    try:
        html = requests.get(page, timeout=60, headers={"User-Agent": USER_AGENT}).text
    except requests.RequestException:
        return None
    m = re.search(r'href="([^"]+CMO-Historical-Data-Monthly\.xlsx)"', html)
    if not m:
        return None
    href = m.group(1)
    return href if href.startswith("http") else "https://www.worldbank.org" + href


def parse_monthly_sheet(xlsx_path, sheet: str, commodity_patterns: dict[str, str]) -> pd.DataFrame:
    grid = pd.read_excel(xlsx_path, sheet_name=sheet, header=None, dtype=object)
    first_col = grid.iloc[:, 0].astype(str).str.strip()
    data_rows = first_col[first_col.str.match(PERIOD_RE)].index
    if len(data_rows) == 0:
        raise ValueError("no YYYYMmm period rows found - sheet layout changed?")
    first_data = data_rows[0]
    # Header = the row with the most text cells above the first data row; units below it.
    above = grid.iloc[:first_data]
    header_idx = above.notna().sum(axis=1).idxmax()
    names = grid.iloc[header_idx].tolist()
    units = grid.iloc[header_idx + 1].tolist() if header_idx + 1 < first_data else [None] * len(names)

    compiled = {re.compile(p, re.IGNORECASE): code for p, code in commodity_patterns.items()}
    frames = []
    for j, name in enumerate(names):
        if j == 0 or not isinstance(name, str):
            continue
        code = next((c for rx, c in compiled.items() if rx.search(name.strip())), None)
        if code is None:
            continue
        values = pd.to_numeric(grid.iloc[data_rows, j], errors="coerce")
        frames.append(
            pd.DataFrame(
                {
                    "period": first_col.loc[data_rows].values,
                    "commodity_code": code,
                    "source_name": name.strip(),
                    "unit": str(units[j]).strip() if units[j] is not None else None,
                    "price_usd": values.values,
                }
            )
        )
    if not frames:
        raise ValueError("none of the configured commodities were found in the sheet")
    out = pd.concat(frames, ignore_index=True)
    log.info("Pink Sheet: commodities found = %s", sorted(out["commodity_code"].unique()))
    return out


def ingest(settings: Settings, mode: str = "live", force: bool = False) -> None:
    src = settings.source("worldbank_pinksheet")
    raw_file = settings.raw / src["landing_file"]
    url = src["url"]
    if mode == "live":
        try:
            download(url, raw_file, force=force)
        except requests.HTTPError:
            url = discover_url(src["discovery_page"]) or url
            log.info("retrying with discovered URL %s", url)
            download(url, raw_file, force=True)
    elif not raw_file.exists():
        raise FileNotFoundError(f"{raw_file} missing - run `fdp sample` first")
    record_manifest(settings.raw, "worldbank_pinksheet", raw_file, url, mode)
    df = parse_monthly_sheet(raw_file, src["sheet"], src["commodities"])
    write_bronze(df, settings.bronze, "worldbank_commodity_prices", src["landing_file"])
