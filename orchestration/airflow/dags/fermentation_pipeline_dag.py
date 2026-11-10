"""Airflow DAG for the Fermentation Data Platform POC.

Daily run: the three sources are ingested in parallel, then transforms with
data-quality gates, then the ML layer and the report. Each task calls the same
functions as the `fdp` CLI, so local runs and orchestrated runs share one code path.

Deploy: put this repo on the Airflow workers' PYTHONPATH (``pip install -e .``)
and copy this file into the dags/ folder. Set the Airflow Variable
``fdp_ingest_mode`` to ``live`` (default) or ``sample``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator

from fdp import cli
from fdp.config import load_settings
from fdp.ingest import SOURCES

default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "retry_exponential_backoff": True,
}


def _ingest(source: str, **_):
    mode = Variable.get("fdp_ingest_mode", default_var="live")
    SOURCES[source](load_settings(), mode=mode)


def _transform(**_):
    cli.step_transform(load_settings())     # raises DataQualityError on a failed blocking check


def _ml(**_):
    return cli.step_ml(load_settings())


def _report(**_):
    return cli.step_report(load_settings())


with DAG(
    dag_id="fermentation_data_platform",
    description="Bioprocess + commodity + FX data -> warehouse -> early warning -> report",
    start_date=datetime(2026, 11, 1),
    schedule="0 6 * * *",
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["poc", "bioprocess", "duckdb"],
) as dag:
    ingest_tasks = [
        PythonOperator(task_id=f"ingest_{name}", python_callable=_ingest, op_kwargs={"source": name},
                       execution_timeout=timedelta(minutes=45))
        for name in SOURCES
    ]
    transform = PythonOperator(task_id="transform_and_quality_gate", python_callable=_transform)
    ml = PythonOperator(task_id="ml_early_warning", python_callable=_ml)
    report = PythonOperator(task_id="build_report", python_callable=_report)

    ingest_tasks >> transform >> ml >> report
