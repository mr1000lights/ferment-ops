"""Command-line entry point.

    fdp sample                 # generate offline sample files (same layout as live)
    fdp ingest --mode live     # download public sources -> raw -> bronze
    fdp transform              # silver -> DQ -> gold -> DQ
    fdp ml                     # MSPC early warning + yield model
    fdp report                 # HTML report
    fdp run --mode live        # everything, in order
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import quality, sample_data, warehouse
from .config import load_settings
from .ingest import SOURCES

log = logging.getLogger("fdp")


def step_ingest(settings, mode: str, force: bool = False, only: str | None = None) -> None:
    marker = settings.raw / "SAMPLE_DATA"
    if mode == "live" and marker.exists():
        # Raw zone still holds synthetic files: replace them with real downloads.
        log.info("sample files found in raw zone - removing them before live downloads")
        for src in settings.cfg["sources"].values():
            (settings.raw / src["landing_file"]).unlink(missing_ok=True)
        marker.unlink()
    for name, fn in SOURCES.items():
        if only and name != only:
            continue
        fn(settings, mode=mode, force=force)


def step_transform(settings, fail_on_error: bool = True):
    with warehouse.connect(settings) as con:
        warehouse.register_bronze(con, settings)
        warehouse.load_reference(con, settings)
        warehouse.run_models(con, layers=("silver",))
        quality.run_checks(con, layers=("bronze", "silver"), fail_on_error=fail_on_error)
        warehouse.run_models(con, layers=("gold",))
        return quality.run_checks(con, layers=("gold",), fail_on_error=fail_on_error)


def step_ml(settings):
    from .ml import early_warning
    return early_warning.run(settings)


def step_report(settings):
    from .report import build_report
    return build_report.build(settings)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fdp", description="Fermentation Data Platform POC")
    p.add_argument("--config", help="path to pipeline.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    ing = sub.add_parser("ingest")
    ing.add_argument("--mode", choices=["live", "sample"], default="live")
    ing.add_argument("--source", choices=list(SOURCES))
    ing.add_argument("--force", action="store_true", help="re-download even if raw file exists")
    sub.add_parser("transform")
    sub.add_parser("ml")
    sub.add_parser("report")
    run = sub.add_parser("run")
    run.add_argument("--mode", choices=["live", "sample"], default="sample")
    run.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    settings = load_settings(args.config)
    t0 = time.time()
    if args.cmd == "sample":
        sample_data.write_all(settings)
    elif args.cmd == "ingest":
        step_ingest(settings, args.mode, args.force, args.source)
    elif args.cmd == "transform":
        step_transform(settings)
    elif args.cmd == "ml":
        step_ml(settings)
    elif args.cmd == "report":
        step_report(settings)
    elif args.cmd == "run":
        if args.mode == "sample":
            sample_data.write_all(settings)
        step_ingest(settings, args.mode, args.force)
        step_transform(settings)
        step_ml(settings)
        out = step_report(settings)
        log.info("report: %s", out)
    log.info("done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
