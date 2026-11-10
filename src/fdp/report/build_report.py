"""Self-contained HTML report built from the gold and ml layers."""
from __future__ import annotations

import html
import json
import logging
import math
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go

from ..config import Settings
from ..warehouse import connect

log = logging.getLogger("fdp.report")

# Reference data-viz palette (validated categorical order) + neutrals.
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
CRIT = "#d03b3b"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e7e6e2", "#fcfcfb"
BAND = "rgba(42,120,214,0.16)"
STRATEGY_ORDER = ["Recipe driven", "Operator controlled", "Raman APC", "Fault scenario"]
STRATEGY_COLOR = dict(zip(STRATEGY_ORDER, [S1, S2, S3, S4], strict=True))

_first_fig = True


def _layout(fig: go.Figure, title: str, ytitle: str, xtitle: str = "", height: int = 360) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=15, color=INK)),
        height=height, margin=dict(l=60, r=24, t=48, b=96),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, sans-serif", size=12, color=INK2),
        legend=dict(orientation="h", y=-0.2, yanchor="top", x=0, xanchor="left", title=None),
        hoverlabel=dict(bgcolor="white", font_size=12),
    )
    fig.update_xaxes(title=xtitle, gridcolor=GRID, zeroline=False, linecolor=GRID, ticks="")
    fig.update_yaxes(title=ytitle, gridcolor=GRID, zeroline=False, linecolor=GRID, ticks="")
    return fig


def _html(fig: go.Figure) -> str:
    global _first_fig
    out = fig.to_html(full_html=False, include_plotlyjs="inline" if _first_fig else False,
                      config={"displaylogo": False, "responsive": True})
    _first_fig = False
    return out


def _table(df: pd.DataFrame, fmt: dict | None = None) -> str:
    fmt = fmt or {}
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c in fmt and pd.notna(v):
                v = fmt[c](v)
            cells.append(f"<td>{html.escape('' if pd.isna(v) else str(v))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def build(settings: Settings) -> str:
    global _first_fig
    _first_fig = True
    sample_mode = (settings.raw / "SAMPLE_DATA").exists()
    with connect(settings, read_only=True) as con:
        q = lambda s: con.execute(s).df()
        kpis = q("SELECT * FROM gold.fct_batch_kpis")
        golden = q("SELECT * FROM gold.fct_golden_batch_profile ORDER BY time_bucket_h")
        alarms = q("SELECT a.*, k.control_strategy FROM ml.mspc_batch_alarms a JOIN gold.fct_batch_kpis k USING (batch_id)")
        metrics = {r.model: json.loads(r.metrics_json) for r in q("SELECT * FROM ml.model_metrics").itertuples()}
        preds = q("SELECT * FROM ml.yield_predictions")
        econ = q("SELECT * FROM gold.fct_protein_unit_economics WHERE month >= (SELECT max(month) FROM gold.fct_protein_unit_economics) - INTERVAL 10 YEAR ORDER BY month")
        exc = q("SELECT * FROM gold.fct_process_excursions ORDER BY batch_id, start_h")
        dq = q("SELECT layer, check_name, severity, status, failing_rows FROM dq.check_results "
               "QUALIFY row_number() OVER (PARTITION BY check_name ORDER BY ran_at DESC) = 1 "
               "ORDER BY CASE layer WHEN 'bronze' THEN 0 WHEN 'silver' THEN 1 ELSE 2 END, check_name")
        lineage = q("""
            SELECT schema_name AS layer, table_name AS "table", estimated_size AS approx_rows
            FROM duckdb_tables() WHERE schema_name IN ('silver','gold','ml','dq','ref')
            ORDER BY CASE schema_name WHEN 'ref' THEN 0 WHEN 'silver' THEN 1 WHEN 'gold' THEN 2 WHEN 'ml' THEN 3 ELSE 4 END, table_name""")
        n_rows = con.execute("SELECT count(*) FROM silver.batch_timeseries").fetchone()[0]
        # featured batches for the trajectory and monitoring charts
        fault_pick = int(kpis[kpis.is_fault_batch].sort_values("yield_kg_per_kg_sugar").batch_id.iloc[0])
        normal_pick = int(alarms[(alarms.role == "holdout_normal") & (~alarms.flagged)].batch_id.iloc[0]) \
            if ((alarms.role == "holdout_normal") & (~alarms.flagged)).any() else int(alarms[alarms.role == "holdout_normal"].batch_id.iloc[0])
        traj = q(f"SELECT batch_id, time_h, product_g_l, dissolved_oxygen_mg_l, fault_flag FROM silver.batch_timeseries WHERE batch_id = {fault_pick} ORDER BY time_h")
        mon = q(f"SELECT * FROM ml.mspc_scores WHERE batch_id IN ({fault_pick}, {normal_pick}) ORDER BY batch_id, time_h")

    mspc, ym = metrics["mspc_pca"], metrics["early_yield_ridge"]
    fault_rows = traj[traj.fault_flag == 1]
    fault_win = (fault_rows.time_h.min(), fault_rows.time_h.max()) if len(fault_rows) else None

    # ---------- Fig 1: yield by control strategy ----------
    f1 = go.Figure()
    for s in STRATEGY_ORDER:
        d = kpis[kpis.control_strategy == s]
        if d.empty:
            continue
        f1.add_trace(go.Box(
            y=d.yield_kg_per_kg_sugar, name=s, boxpoints="all", jitter=0.4, pointpos=0,
            marker=dict(color=STRATEGY_COLOR[s], size=8, line=dict(color=SURFACE, width=2)),
            line=dict(color=STRATEGY_COLOR[s], width=2), fillcolor="rgba(0,0,0,0)",
            customdata=d[["batch_code", "product_mass_kg", "duration_h"]],
            hovertemplate="%{customdata[0]}<br>Yield %{y:.3f} kg/kg<br>Product %{customdata[1]:,.0f} kg"
                          "<br>Duration %{customdata[2]:.0f} h<extra></extra>"))
    _layout(f1, "Product yield on sugar by control regime", "kg product per kg sugar fed")
    f1.update_layout(showlegend=False, margin=dict(b=40))

    # ---------- Fig 2/3: golden batch envelope vs worst fault batch ----------
    def envelope(col_lo, col_mid, col_hi, ycol, title, ytitle):
        f = go.Figure()
        x = golden.time_bucket_h
        f.add_trace(go.Scatter(x=x, y=golden[col_hi], line=dict(width=0), hoverinfo="skip", showlegend=False))
        f.add_trace(go.Scatter(x=x, y=golden[col_lo], fill="tonexty", fillcolor=BAND, line=dict(width=0),
                               name="Normal batches, 5th–95th pct", hoverinfo="skip"))
        if col_mid:
            f.add_trace(go.Scatter(x=x, y=golden[col_mid], name="Normal median",
                                   line=dict(color=S1, width=2),
                                   hovertemplate="%{x} h · median %{y:.2f}<extra></extra>"))
        f.add_trace(go.Scatter(x=traj.time_h, y=traj[ycol], name=f"B{fault_pick:03d} (fault batch)",
                               line=dict(color=S2, width=2),
                               hovertemplate="%{x:.1f} h · %{y:.2f}<extra>B%{meta}</extra>", meta=f"{fault_pick:03d}"))
        if fault_win:
            f.add_vrect(x0=fault_win[0], x1=fault_win[1], fillcolor="rgba(208,59,59,0.08)", line_width=0,
                        annotation_text="fault window", annotation_position="top left",
                        annotation_font=dict(size=11, color=INK2))
        return _layout(f, title, ytitle, "Batch time (h)")

    f2 = envelope("product_p05", "product_p50", "product_p95", "product_g_l",
                  "Product concentration vs golden-batch envelope", "Product (g/L)")
    f3 = envelope("do_p05", "do_p50", "do_p95", "dissolved_oxygen_mg_l",
                  "Dissolved oxygen vs golden-batch envelope", "DO (mg/L)")

    # ---------- Fig 4: MSPC Hotelling T² ----------
    f4 = go.Figure()
    for b, color in [(normal_pick, S1), (fault_pick, S2)]:
        d = mon[mon.batch_id == b]
        label = f"B{b:03d} ({'fault batch' if b == fault_pick else 'unseen normal batch'})"
        f4.add_trace(go.Scatter(x=d.time_h, y=d.t2, name=label, line=dict(color=color, width=2),
                                hovertemplate="%{x:.1f} h · T² %{y:.1f}<extra>" + label + "</extra>"))
    f4.add_hline(y=mspc["t2_limit"], line=dict(color=INK2, width=1.5, dash="dash"),
                 annotation_text="99% control limit", annotation_position="top left",
                 annotation_font=dict(size=11, color=INK2))
    if fault_win:
        f4.add_vrect(x0=fault_win[0], x1=fault_win[1], fillcolor="rgba(208,59,59,0.08)", line_width=0)
    _layout(f4, "Early warning: Hotelling T² per sample", "T² (log scale)", "Batch time (h)")
    top = max(mon.t2.quantile(0.995) * 1.5, mspc["t2_limit"] * 4)
    f4.update_yaxes(type="log", range=[math.log10(max(mon.t2.min(), 0.1)), math.log10(top)], dtick=1)

    # ---------- Fig 5: early yield prediction ----------
    lo, hi = preds[["actual_kg", "predicted_kg"]].min().min(), preds[["actual_kg", "predicted_kg"]].max().max()
    f5 = go.Figure()
    f5.add_trace(go.Scatter(x=[lo, hi], y=[lo, hi], mode="lines", line=dict(color=MUTED, width=1.5, dash="dot"),
                            hoverinfo="skip", showlegend=False))
    f5.update_layout(margin=dict(b=48))
    f5.add_trace(go.Scatter(x=preds.actual_kg, y=preds.predicted_kg, mode="markers", showlegend=False,
                            marker=dict(color=S1, size=9, line=dict(color=SURFACE, width=2)),
                            customdata=preds.batch_id,
                            hovertemplate="B%{customdata:03d}<br>Actual %{x:,.0f} kg<br>Predicted %{y:,.0f} kg<extra></extra>"))
    _layout(f5, f"Final product predicted at hour {ym['early_phase_hours']} (5-fold CV)",
            "Predicted (kg)", "Actual final product (kg)")

    # ---------- Fig 6: protein economics ----------
    f6 = go.Figure()
    series = [("feedstock_eur_per_t_protein", "Microbial protein: molasses feedstock (est.)", S1),
              ("fishmeal_eur_per_t_protein", "Fishmeal", S2),
              ("soybean_meal_eur_per_t_protein", "Soybean meal", S3)]
    for col, name, color in series:
        d = econ[["month", col]].dropna()
        f6.add_trace(go.Scatter(x=d.month, y=d[col], name=name, line=dict(color=color, width=2),
                                hovertemplate="%{x|%b %Y} · €%{y:,.0f} per t protein<extra>" + name + "</extra>"))
        if len(d):
            f6.add_annotation(x=d.month.iloc[-1], y=d[col].iloc[-1], text=f"€{d[col].iloc[-1]:,.0f}",
                              showarrow=False, xanchor="left", xshift=6, font=dict(size=11, color=INK))
    _layout(f6, "Cost of one tonne of protein, EUR (monthly)", "EUR per tonne of protein", height=380)
    f6.update_layout(margin=dict(r=70))

    # ---------- tiles ----------
    latest = econ.dropna(subset=["feedstock_eur_per_t_protein"]).iloc[-1] if len(econ) else None
    dq_pass = int((dq.status == "pass").sum())
    tiles = [
        ("Batches processed", f"{kpis.batch_id.nunique()}", f"{n_rows:,} sensor samples"),
        ("Median yield (normal)", f"{kpis[~kpis.is_fault_batch].yield_kg_per_kg_sugar.median():.3f}", "kg product / kg sugar"),
        ("Faults detected", f"{mspc['fault_detection_rate']:.0%}",
         f"median {mspc['median_detection_delay_h']:.1f} h after onset" if mspc.get("median_detection_delay_h") is not None else ""),
        ("False alarms", f"{mspc['holdout_false_alarm_rate']:.0%}", "on unseen normal batches"),
        ("Yield forecast error", f"±{ym['cv_mae_kg']:,.0f} kg", f"vs ±{ym['baseline_mae_kg']:,.0f} kg naive"),
        ("Data quality", f"{dq_pass}/{len(dq)}", "checks passing"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="tl">{t}</div><div class="tv">{v}</div><div class="ts">{s}</div></div>'
                         for t, v, s in tiles)

    det = alarms[alarms.role == "fault"][["batch_id", "fault_start_h", "first_alarm_h", "detection_delay_h", "flagged"]].copy()
    det["batch_id"] = det.batch_id.map(lambda b: f"B{b:03d}")
    det.columns = ["Batch", "Fault onset (h)", "First alarm (h)", "Delay (h)", "Detected"]
    exc_t = exc[["batch_id", "variable", "start_h", "duration_h", "min_value", "max_value"]].copy()
    exc_t["batch_id"] = exc_t.batch_id.map(lambda b: f"B{b:03d}")
    dq_t = dq[["layer", "check_name", "severity", "status", "failing_rows"]].rename(
        columns={"layer": "Layer", "check_name": "Check", "severity": "Severity", "status": "Status", "failing_rows": "Failing rows"})
    lineage = lineage.rename(columns={"layer": "Layer", "table": "Table", "approx_rows": "Rows"})

    banner = ("""<div class="banner"><b>Sample data.</b> This report was built from synthetic files that mirror the
    layout of the public sources, so the pipeline could be tested offline. Run <code>fdp run --mode live</code>
    to rebuild it from IndPenSim, the World Bank Pink Sheet and the ECB.</div>""" if sample_mode else "")
    a = settings.cfg["unit_economics"]
    econ_note = (f"Assumptions (config/pipeline.yaml): molasses = {a['molasses_price_vs_sugar']:.0%} of world sugar price and "
                 f"{a['molasses_sugar_content']:.0%} sugar; biomass yield {a['biomass_yield_on_sugar']} t/t sugar; "
                 f"{a['protein_content_of_biomass']:.0%} protein in biomass; fishmeal {a['fishmeal_protein_content']:.0%} and "
                 f"soybean meal {a['soybean_meal_protein_content']:.0%} protein. Feedstock only: excludes energy, "
                 f"nitrogen, capex and downstream processing.")
    if latest is not None:
        econ_note = (f"At {pd.Timestamp(latest.month):%b %Y} prices, feedstock for one tonne of microbial protein is "
                     f"about €{latest.feedstock_eur_per_t_protein:,.0f}, against €{latest.fishmeal_eur_per_t_protein:,.0f} "
                     f"for a tonne of protein bought as fishmeal. At the planned 15,000 t/year Leuna capacity, "
                     f"that is roughly €{latest.annual_feedstock_meur_at_capacity:,.1f}M of feedstock per year. ") + econ_note

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fermentation Data Platform – Batch & Market Report</title>
<style>
:root {{ --ink:{INK}; --ink2:{INK2}; --muted:{MUTED}; --grid:{GRID}; --surface:{SURFACE}; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#f4f3f0; color:var(--ink); font:14px/1.5 Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:32px 24px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:-0.01em; }}
h2 {{ font-size:18px; margin:40px 0 4px; }}
.sub {{ color:var(--ink2); margin:0 0 20px; }}
p.lead {{ color:var(--ink2); margin:0 0 14px; max-width:820px; }}
.banner {{ background:#fff4e0; border:1px solid #f1d9a8; border-radius:10px; padding:12px 16px; margin:0 0 20px; color:#4a3b16; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:12px; }}
.tile {{ background:var(--surface); border:1px solid var(--grid); border-radius:12px; padding:14px 16px; }}
.tl {{ color:var(--ink2); font-size:12px; }} .tv {{ font-size:26px; font-weight:650; margin:2px 0; }} .ts {{ color:var(--muted); font-size:12px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:860px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.card {{ background:var(--surface); border:1px solid var(--grid); border-radius:12px; padding:8px; overflow:hidden; }}
.note {{ color:var(--ink2); font-size:12.5px; margin:8px 4px 0; max-width:900px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; background:var(--surface); }}
th, td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--grid); font-variant-numeric:tabular-nums; }}
th {{ color:var(--ink2); font-weight:600; background:#f8f7f4; }}
.tbl {{ border:1px solid var(--grid); border-radius:12px; overflow:auto; max-height:420px; }}
code {{ background:#eeede9; padding:1px 5px; border-radius:4px; font-size:12.5px; }}
.flow {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:8px 0 16px; }}
.flow span.s {{ background:var(--surface); border:1px solid var(--grid); border-radius:8px; padding:6px 10px; font-size:12.5px; }}
.flow span.a {{ color:var(--muted); }}
footer {{ margin-top:48px; color:var(--muted); font-size:12px; }}
</style></head><body><div class="wrap">
<h1>Fermentation Data Platform – batch &amp; market report</h1>
<p class="sub">Proof of concept on public data · generated {generated}</p>
{banner}
<div class="flow"><span class="s">IndPenSim · World Bank · ECB API</span><span class="a">→</span>
<span class="s">raw (checksummed)</span><span class="a">→</span><span class="s">bronze parquet</span><span class="a">→</span>
<span class="s">silver (clean SQL)</span><span class="a">→</span><span class="s">DQ gate</span><span class="a">→</span>
<span class="s">gold marts</span><span class="a">→</span><span class="s">ML early warning</span><span class="a">→</span><span class="s">this report</span></div>
<div class="tiles">{tiles_html}</div>

<h2>1 · Batch performance</h2>
<p class="lead">Yield on sugar is the number that drives cost per tonne at scale. Each dot is one batch.</p>
<div class="card">{_html(f1)}</div>

<h2>2 · Golden-batch comparison</h2>
<p class="lead">The band shows where 90% of normal batches sit at each hour. Batch B{fault_pick:03d}, the lowest-yield fault batch, is overlaid; the shaded area is its recorded fault window.</p>
<div class="grid2"><div class="card">{_html(f2)}</div><div class="card">{_html(f3)}</div></div>

<h2>3 · Early warning (multivariate process monitoring)</h2>
<p class="lead">A PCA model trained only on normal batches scores every sample. A batch is flagged after {settings.cfg['ml']['consecutive_alarms']} consecutive samples above the 99% limit ({mspc['n_components']} principal components).</p>
<div class="card">{_html(f4)}</div>
<div class="grid2" style="margin-top:16px"><div class="tbl">{_table(det, {"Fault onset (h)": lambda v: f"{v:.1f}", "First alarm (h)": lambda v: f"{v:.1f}", "Delay (h)": lambda v: f"{v:+.1f}"})}</div>
<div class="tbl">{_table(exc_t.rename(columns={"batch_id": "Batch", "variable": "Variable", "start_h": "Start (h)", "duration_h": "Duration (h)", "min_value": "Min", "max_value": "Max"}), {"Start (h)": lambda v: f"{v:.1f}", "Min": lambda v: f"{v:.2f}", "Max": lambda v: f"{v:.2f}"}) if len(exc_t) else "<p class='note'>No out-of-spec excursions.</p>"}</div></div>
<p class="note">Left: detection per known fault batch (a negative delay means the model alarmed before the recorded fault start). Right: rule-based out-of-spec excursions against the process specification.</p>

<h2>4 · Yield forecast while the batch is running</h2>
<p class="lead">A ridge regression sees only the first {ym['early_phase_hours']} hours of each normal batch. Cross-validated R² = {ym['cv_r2']:.2f}; mean error {ym['cv_mae_kg']:,.0f} kg against {ym['baseline_mae_kg']:,.0f} kg for a naive average.</p>
<div class="card">{_html(f5)}</div>

<h2>5 · Feedstock economics vs conventional protein</h2>
<p class="lead">World Bank prices converted to EUR with ECB monthly rates, expressed per tonne of protein so the three are comparable.</p>
<div class="card">{_html(f6)}</div>
<p class="note">{html.escape(econ_note)}</p>

<h2>6 · Data quality and lineage</h2>
<div class="grid2"><div class="tbl">{_table(dq_t)}</div><div class="tbl">{_table(lineage)}</div></div>

<footer>Sources: IndPenSim (Goldrick et al., Mendeley Data, CC BY 4.0) · World Bank Commodity Price Data (Pink Sheet, CC BY 4.0) ·
ECB Data Portal, EXR.M.USD.EUR.SP00.A. IndPenSim simulates penicillin fermentation and is used here as a stand-in for
bioreactor data; it is not MicroHarvest data.</footer>
</div></body></html>"""
    out = settings.reports / "fermentation_report.html"
    out.write_text(page, encoding="utf-8")
    log.info("report written: %s (%.1f MB)", out, out.stat().st_size / 1e6)
    return str(out)
