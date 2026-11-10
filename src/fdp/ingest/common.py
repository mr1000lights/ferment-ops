"""Shared ingestion helpers: downloads, checksums, manifests, bronze writes."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("fdp.ingest")

USER_AGENT = "ferment-ops/0.1 (+https://github.com/mr1000lights)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def download(url: str, target: Path, params: dict | None = None, retries: int = 3,
             timeout: int = 120, force: bool = False) -> Path:
    """Stream a file to the raw zone. Idempotent: skips if present unless force=True."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        log.info("raw file present, skipping download: %s", target.name)
        return target
    tmp = target.with_suffix(target.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            log.info("downloading %s (attempt %s)", url, attempt)
            with requests.get(url, params=params, stream=True, timeout=timeout,
                              headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    fh.writelines(r.iter_content(chunk_size=1 << 20))
            tmp.replace(target)
            return target
        except requests.RequestException as exc:
            log.warning("download failed: %s", exc)
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)
    return target


def record_manifest(raw_root: Path, source: str, path: Path, url: str, mode: str) -> dict:
    entry = {
        "source": source,
        "file": str(path.relative_to(raw_root)),
        "url": url,
        "mode": mode,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "recorded_at": utc_now(),
    }
    with open(raw_root / "_manifest.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def write_bronze(df: pd.DataFrame, bronze_root: Path, table: str, source_file: str) -> Path:
    """Write a bronze parquet table with load metadata columns."""
    df = df.copy()
    df["_source_file"] = source_file
    df["_ingested_at"] = utc_now()
    out = bronze_root / f"{table}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    log.info("bronze %-32s %8d rows -> %s", table, len(df), out.name)
    return out
