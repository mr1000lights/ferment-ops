"""ML layer.

1. Multivariate statistical process monitoring (MSPC) - the standard approach in
   bioprocessing. A PCA model is fitted on normal-operation batches only; every
   new sample gets a Hotelling T^2 (unusual inside the model) and SPE/Q
   (unusual outside the model) score. A batch raises an alarm after N
   consecutive samples above the 99th-percentile control limit.

2. Early yield prediction - predicts the final product mass of a batch from
   features of its first ``early_phase_hours`` only, so operators get a forecast
   while the batch is still running.
"""
from __future__ import annotations

import json
import logging

import joblib
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.linear_model import RidgeCV
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..config import Settings
from ..warehouse import connect

log = logging.getLogger("fdp.ml")


def _in(series: pd.Series, bounds: list[int]) -> pd.Series:
    return series.between(bounds[0], bounds[1])


def _first_alarm(flags: np.ndarray, times: np.ndarray, k: int) -> float | None:
    run = 0
    for f, t in zip(flags, times, strict=True):
        run = run + 1 if f else 0
        if run >= k:
            return float(t)
    return None


def fit_mspc(ts: pd.DataFrame, cfg: dict):
    variables = cfg["monitoring_variables"]
    # Remove the shared time trajectory: model deviations from the normal-batch
    # mean profile at the same time bucket ("batch-wise" normalisation).
    ts = ts.copy()
    ts["bucket"] = (ts["time_h"] // 2).astype(int)
    train_mask = _in(ts["batch_id"], cfg["train_batches"])
    grouped = ts[train_mask].groupby("bucket")
    support = grouped["batch_id"].nunique()
    profile = grouped[variables].agg(["mean", "std"])
    profile = profile[support >= 10]          # need enough batches for a stable reference
    means = profile.xs("mean", axis=1, level=1)
    stds = profile.xs("std", axis=1, level=1).replace(0, np.nan)
    # A variable that is constant in normal operation (e.g. a fixed setpoint) still
    # needs a sensible scale: floor each std at 5% of that variable's typical spread.
    floor = (ts.loc[train_mask, variables].std() * 0.05).clip(lower=1e-6)
    stds = stds.fillna(stds.median()).clip(lower=floor, axis=1)
    # buckets beyond the reference (longer batches) reuse the last supported bucket
    full = range(int(ts["bucket"].min()), int(ts["bucket"].max()) + 1)
    means, stds = means.reindex(full).ffill().bfill(), stds.reindex(full).ffill().bfill()
    z = (ts[variables] - means.reindex(ts["bucket"]).values) / stds.reindex(ts["bucket"]).values
    z = z.fillna(0.0)

    pca = PCA(n_components=cfg["pca_variance"], svd_solver="full").fit(z[train_mask])
    scores = pca.transform(z)
    t2 = np.sum(scores**2 / pca.explained_variance_, axis=1)
    resid = z.values - pca.inverse_transform(scores)
    spe = np.sum(resid**2, axis=1)
    q = cfg["control_limit_quantile"]
    t2_lim = float(np.quantile(t2[train_mask], q))
    spe_lim = float(np.quantile(spe[train_mask], q))
    out = ts[["batch_id", "time_h"]].copy()
    out["t2"], out["spe"] = t2, spe
    out["t2_limit"], out["spe_limit"] = t2_lim, spe_lim
    out["alarm"] = (t2 > t2_lim) | (spe > spe_lim)
    model = {"pca": pca, "profile_mean": means, "profile_std": stds, "variables": variables,
             "t2_limit": t2_lim, "spe_limit": spe_lim}
    return out, model


def evaluate_mspc(scores: pd.DataFrame, cfg: dict, fault_windows: pd.DataFrame) -> pd.DataFrame:
    k = cfg["consecutive_alarms"]
    rows = []
    for b, g in scores.sort_values(["batch_id", "time_h"]).groupby("batch_id"):
        g = g[g["time_h"] >= 20]   # ignore start-up transient
        first = _first_alarm(g["alarm"].values, g["time_h"].values, k)
        if _in(pd.Series([b]), cfg["train_batches"]).iloc[0]:
            role = "train_normal"
        elif _in(pd.Series([b]), cfg["fault_batches"]).iloc[0]:
            role = "fault"
        else:
            role = "holdout_normal"
        rows.append({"batch_id": int(b), "role": role, "flagged": first is not None,
                     "first_alarm_h": first, "alarm_share": float(g["alarm"].mean())})
    res = pd.DataFrame(rows).merge(fault_windows, on="batch_id", how="left")
    res["detection_delay_h"] = res["first_alarm_h"] - res["fault_start_h"]
    return res


def early_features(ts: pd.DataFrame, hours: float) -> pd.DataFrame:
    early = ts[ts["time_h"] <= hours]
    feats = early.groupby("batch_id").agg(
        do_mean=("dissolved_oxygen_mg_l", "mean"),
        do_min=("dissolved_oxygen_mg_l", "min"),
        substrate_mean=("substrate_g_l", "mean"),
        substrate_std=("substrate_g_l", "std"),
        temp_std=("temperature_k", "std"),
        ph_std=("ph", "std"),
        co2_mean=("co2_offgas_pct", "mean"),
        our_mean=("oxygen_uptake_rate", "mean"),
        feed_mean=("sugar_feed_l_h", "mean"),
        product_at_cutoff=("product_g_l", "last"),
        volume_at_cutoff=("vessel_volume_l", "last"),
    )
    slope = early[early["time_h"] >= hours - 10].groupby("batch_id").apply(
        lambda g: np.polyfit(g["time_h"], g["product_g_l"], 1)[0] if len(g) > 2 else 0.0,
        include_groups=False,
    )
    feats["product_slope_last10h"] = slope
    return feats.fillna(0.0)


def run(settings: Settings) -> dict:
    cfg = settings.cfg["ml"]
    with connect(settings) as con:
        ts = con.execute(
            "SELECT t.*, k.duration_h FROM silver.batch_timeseries t "
            "JOIN gold.fct_batch_kpis k USING (batch_id) ORDER BY batch_id, time_h"
        ).df()
        kpis = con.execute("SELECT * FROM gold.fct_batch_kpis").df()
        fault_windows = con.execute(
            "SELECT batch_id, min(time_h) AS fault_start_h, max(time_h) AS fault_end_h "
            "FROM silver.batch_timeseries WHERE fault_flag = 1 GROUP BY batch_id"
        ).df()

        # ---- 1. MSPC early warning ----
        scores, model = fit_mspc(ts, cfg)
        alarms = evaluate_mspc(scores, cfg, fault_windows)
        faults = alarms[alarms.role == "fault"]
        holdout = alarms[alarms.role == "holdout_normal"]
        mspc_metrics = {
            "n_components": int(model["pca"].n_components_),
            "fault_detection_rate": float(faults["flagged"].mean()),
            "holdout_false_alarm_rate": float(holdout["flagged"].mean()),
            "median_detection_delay_h": float(faults["detection_delay_h"].median())
            if faults["detection_delay_h"].notna().any() else None,
            "t2_limit": model["t2_limit"],
            "spe_limit": model["spe_limit"],
        }
        log.info("MSPC: %s", mspc_metrics)

        # ---- 2. Early yield prediction (normal batches) ----
        hours = cfg["early_phase_hours"]
        feats = early_features(ts, hours)
        data = feats.join(kpis.set_index("batch_id")[["product_mass_kg", "is_fault_batch"]])
        normal = data[~data["is_fault_batch"]]
        X, y = normal.drop(columns=["product_mass_kg", "is_fault_batch"]), normal["product_mass_kg"]
        reg = make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 3, 30)))
        cv = KFold(n_splits=5, shuffle=True, random_state=42)
        pred = cross_val_predict(reg, X, y, cv=cv)
        reg.fit(X, y)
        baseline = np.full_like(y.values, y.mean(), dtype=float)
        yield_metrics = {
            "early_phase_hours": hours,
            "n_batches": len(y),
            "cv_r2": float(r2_score(y, pred)),
            "cv_mae_kg": float(mean_absolute_error(y, pred)),
            "baseline_mae_kg": float(mean_absolute_error(y, baseline)),
        }
        coefs = pd.Series(reg[-1].coef_, index=X.columns).sort_values(key=abs, ascending=False)
        log.info("Yield model: %s", yield_metrics)
        preds = pd.DataFrame({"batch_id": y.index, "actual_kg": y.values, "predicted_kg": pred})

        # ---- persist ----
        con.execute("CREATE SCHEMA IF NOT EXISTS ml")
        tables = {
            "mspc_scores": scores,
            "mspc_batch_alarms": alarms,
            "yield_predictions": preds,
            "yield_feature_importance": coefs.rename_axis("feature").rename("coefficient").reset_index(),
            "model_metrics": pd.DataFrame(
                [{"model": "mspc_pca", "metrics_json": json.dumps(mspc_metrics)},
                 {"model": "early_yield_ridge", "metrics_json": json.dumps(yield_metrics)}]
            ),
        }
        for name, df in tables.items():
            con.register("_t", df)
            con.execute(f"CREATE OR REPLACE TABLE ml.{name} AS SELECT * FROM _t")
            con.unregister("_t")
            log.info("ml table     ml.%-28s %8d rows", name, len(df))

    joblib.dump(model, settings.models / "mspc_pca.joblib")
    joblib.dump(reg, settings.models / "early_yield_ridge.joblib")
    return {"mspc": mspc_metrics, "yield": yield_metrics}
