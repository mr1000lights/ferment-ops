"""Configuration loading and shared paths."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# FDP_HOME lets an installed package (e.g. in Docker) find config/ and sql/.
REPO_ROOT = Path(os.environ.get("FDP_HOME", Path(__file__).resolve().parents[2]))
DEFAULT_CONFIG = REPO_ROOT / "config" / "pipeline.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


@dataclass(frozen=True)
class Settings:
    cfg: dict
    root: Path

    def path(self, key: str) -> Path:
        p = self.root / self.cfg["paths"][key]
        (p.parent if p.suffix else p).mkdir(parents=True, exist_ok=True)
        return p

    @property
    def raw(self) -> Path:
        return self.path("raw")

    @property
    def bronze(self) -> Path:
        return self.path("bronze")

    @property
    def warehouse(self) -> Path:
        return self.path("warehouse")

    @property
    def reports(self) -> Path:
        return self.path("reports")

    @property
    def models(self) -> Path:
        return self.path("models")

    def source(self, name: str) -> dict:
        return self.cfg["sources"][name]


def load_settings(config_path: Path | str | None = None, root: Path | None = None) -> Settings:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return Settings(cfg=cfg, root=Path(root) if root else REPO_ROOT)
