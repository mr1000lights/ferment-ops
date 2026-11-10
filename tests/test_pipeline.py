import duckdb
import pytest

from fdp import quality
from fdp.warehouse import connect


def q(settings, sql):
    with connect(settings, read_only=True) as con:
        return con.execute(sql).df()


def test_all_blocking_checks_pass(built):
    res = q(built["settings"], "SELECT * FROM dq.check_results")
    assert (res.status != "fail").all(), res[res.status == "fail"]


def test_gold_kpis_one_row_per_batch(built):
    k = q(built["settings"], "SELECT * FROM gold.fct_batch_kpis")
    assert len(k) == 100 and k.batch_id.is_unique
    assert k.yield_kg_per_kg_sugar.between(0, 1).all()
    assert set(k.control_strategy) == {"Recipe driven", "Operator controlled", "Raman APC", "Fault scenario"}


def test_excursion_logic_on_known_case(built):
    """Gaps-and-islands: only runs >= min_points count as excursions."""
    s = built["settings"]
    with duckdb.connect() as con:
        con.execute("""CREATE TABLE t AS SELECT * FROM (VALUES
            (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6),      -- 6 consecutive
            (1, 10), (1, 11)                                     -- 2 consecutive (ignored)
        ) v(batch_id, sample_seq)""")
        runs = con.execute("""
            SELECT batch_id, count(*) n FROM (
              SELECT *, sample_seq - row_number() OVER (PARTITION BY batch_id ORDER BY sample_seq) grp FROM t)
            GROUP BY batch_id, grp HAVING count(*) >= ?""",
            [s.cfg["process_spec"]["min_points_for_excursion"]]).fetchall()
    assert runs == [(1, 6)]


def test_unit_economics_math(built):
    s = built["settings"]
    e = s.cfg["unit_economics"]
    row = q(s, "SELECT * FROM gold.fct_protein_unit_economics ORDER BY month DESC LIMIT 1").iloc[0]
    expected = row.sugar_eur_t * e["molasses_price_vs_sugar"] / (
        e["biomass_yield_on_sugar"] * e["protein_content_of_biomass"]) / e["molasses_sugar_content"]
    assert row.feedstock_eur_per_t_protein == pytest.approx(expected)


def test_fx_conversion(built):
    r = q(built["settings"], "SELECT * FROM gold.fct_commodity_prices_eur LIMIT 50")
    assert (r.price_eur_per_t - r.price_usd_per_t / r.usd_per_eur).abs().max() < 1e-9


def test_ml_detects_faults_and_beats_baseline(built):
    m = built["metrics"]
    assert m["mspc"]["fault_detection_rate"] >= 0.8
    assert m["yield"]["cv_mae_kg"] < m["yield"]["baseline_mae_kg"]


def test_quality_gate_blocks_bad_data(built):
    s = built["settings"]
    with connect(s) as con:
        con.execute("CREATE OR REPLACE TABLE silver.fx_usd_per_eur_backup AS SELECT * FROM silver.fx_usd_per_eur")
        con.execute("INSERT INTO silver.fx_usd_per_eur VALUES (DATE '1999-01-01', 42.0)")
        with pytest.raises(quality.DataQualityError):
            quality.run_checks(con, layers=("silver",))
        con.execute("CREATE OR REPLACE TABLE silver.fx_usd_per_eur AS SELECT * FROM silver.fx_usd_per_eur_backup")
        con.execute("DROP TABLE silver.fx_usd_per_eur_backup")


def test_report_written(built):
    html = built["report"].read_text(encoding="utf-8")
    assert "Sample data" in html and "Early warning" in html
    assert html.count("plotly") > 0
