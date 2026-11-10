"""Shared fixture: run the full pipeline once on sample data in a temp directory."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fdp import cli, sample_data
from fdp.config import REPO_ROOT, load_settings


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    root = tmp_path_factory.mktemp("fdp")
    shutil.copytree(REPO_ROOT / "config", root / "config")
    s = load_settings(root / "config" / "pipeline.yaml", root=root)
    sample_data.write_all(s)
    cli.step_ingest(s, "sample")
    return s


@pytest.fixture(scope="session")
def built(settings):
    cli.step_transform(settings)
    metrics = cli.step_ml(settings)
    report = cli.step_report(settings)
    return {"settings": settings, "metrics": metrics, "report": Path(report)}
