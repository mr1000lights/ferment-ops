"""Offline sample-data generator.

Writes files with the SAME layout as the three public sources so the full
pipeline can run (in CI, or without internet) exactly as it does on live data:

* ``100_Batches_IndPenSim.zip`` - same CSV member names, same column headers
  (including the known label shift in the batch-reference columns) and a few
  Raman spectrum columns, from a simplified fed-batch kinetic model.
* ``CMO-Historical-Data-Monthly.xlsx`` - same sheet name, header rows and
  ``YYYYMmm`` period format as the World Bank Pink Sheet.
* ``EXR_M_USD_EUR.csv`` - same columns as the ECB SDMX ``csvdata`` response.

Numbers are synthetic. Reports built from them are watermarked "SAMPLE DATA".
"""
from __future__ import annotations

import logging
import zipfile

import numpy as np
import pandas as pd

from .config import Settings

log = logging.getLogger("fdp.sample")

# Column headers exactly as published in 100_Batches_IndPenSim_V3.csv
INDPENSIM_COLUMNS = [
    "Time (h)",
    "Aeration rate(Fg:L/h)",
    "Agitator RPM(RPM:RPM)",
    "Sugar feed rate(Fs:L/h)",
    "Acid flow rate(Fa:L/h)",
    "Base flow rate(Fb:L/h)",
    "Heating/cooling water flow rate(Fc:L/h)",
    "Heating water flow rate(Fh:L/h)",
    "Water for injection/dilution(Fw:L/h)",
    "Air head pressure(pressure:bar)",
    "Dumped broth flow(Fremoved:L/h)",
    "Substrate concentration(S:g/L)",
    "Dissolved oxygen concentration(DO2:mg/L)",
    "Penicillin concentration(P:g/L)",
    "Vessel Volume(V:L)",
    "Vessel Weight(Wt:Kg)",
    "pH(pH:pH)",
    "Temperature(T:K)",
    "Generated heat(Q:kJ)",
    "carbon dioxide percent in off-gas(CO2outgas:%)",
    "PAA flow(Fpaa:PAA flow (L/h))",
    "PAA concentration offline(PAA_offline:PAA (g L^{-1}))",
    "Oil flow(Foil:L/hr)",
    "NH_3 concentration off-line(NH3_offline:NH3 (g L^{-1}))",
    "Oxygen Uptake Rate(OUR:(g min^{-1}))",
    "Oxygen in percent in off-gas(O2:O2  (%))",
    "Offline Penicillin concentration(P_offline:P(g L^{-1}))",
    "Offline Biomass concentratio(X_offline:X(g L^{-1}))",
    "Carbon evolution rate(CER:g/h)",
    "Ammonia shots(NH3_shots:kgs)",
    "Viscosity(Viscosity_offline:centPoise)",
    "Fault reference(Fault_ref:Fault ref)",
    "0 - Recipe driven 1 - Operator controlled(Control_ref:Control ref)",
    "1- No Raman spec",
    "1-Raman spec recorded",
    "Batch reference(Batch_ref:Batch ref)",
    "2-PAT control(PAT_ref:PAT ref)",
]
RAMAN_COLUMNS = ["2400", "2399", "2398", "2397", "2396"]


def _simulate_batch(batch: int, rng: np.random.Generator) -> pd.DataFrame:
    dt = 0.2
    duration = float(rng.choice(np.arange(200, 291, 10)))
    t = np.round(np.arange(dt, duration + 1e-9, dt), 1)
    n = len(t)

    if batch <= 30:
        strategy, noise = 0, 0.6      # recipe driven
    elif batch <= 60:
        strategy, noise = 1, 1.2      # operator controlled
    else:
        strategy, noise = 2, 0.3      # Raman / APC controlled
    fault = batch >= 91
    fault_type = (batch - 91) % 4 if fault else -1   # 0 aeration, 1 temp, 2 pH, 3 feed pump
    fault_start = rng.uniform(70, 150) if fault else np.inf
    fault_len = rng.uniform(20, 45)

    mu_max = rng.normal(0.11, 0.008)
    X, S, P, V = 0.5, 1.0, 0.0, 58_000.0
    DO, pH, T = 15.0, 6.5, 298.0
    out = np.zeros((n, 16))
    extra_rows: list[tuple[float, float, float]] = []
    damage = 1.0
    for i, ti in enumerate(t):
        in_fault = fault and fault_start <= ti <= fault_start + fault_len
        # feed + aeration recipe
        if ti < 40:
            Fs = 8 + 2 * ti
        else:
            Fs = 80 + {0: 0.0, 1: rng.normal(0, 6), 2: 25 * np.tanh((0.35 - S) * 3)}[strategy]
        Fg = 30 if ti < 20 else (65 if ti < 120 else 75)
        if in_fault and fault_type == 3:
            Fs = 2.0
        if in_fault and fault_type == 0:
            Fg = 20

        # environment
        T_set = 298.0
        T += 0.15 * (T_set - T) + rng.normal(0, 0.02 * noise)
        if in_fault and fault_type == 1:
            T += 0.25
        pH += 0.2 * (6.5 - pH) + rng.normal(0, 0.006 * noise)
        if in_fault and fault_type == 2:
            pH -= 0.03

        # kinetics (Monod growth, product formation inhibited by stress)
        mu = mu_max * S / (0.15 + S) * min(1.0, DO / 4.0) * np.exp(-((T - 298) ** 2) / 4) * (
            1 - X / 45
        )
        stress = np.exp(-((pH - 6.5) ** 2) / 0.02) * np.exp(-((T - 298) ** 2) / 2)
        qp = 0.05 * (S / (0.05 + S + S**2 / 3)) * min(1.0, DO / 5.0) * stress
        if in_fault:                      # faults stop production and leave lasting damage
            qp = 0.0
            damage = max(damage - 0.003, 0.6)
        qp *= damage
        dX = mu * X
        dS = -(mu / 0.47 + 0.03) * X + Fs * 600 / V
        dP = qp * X - 0.002 * P
        X = max(X + dX * dt, 0.1)
        S = max(S + dS * dt * 0.02, 0.0005)
        P = max(P + dP * dt, 0.0)
        V += (Fs + 0.5 * Fg - 18) * dt
        OUR = 0.012 * X * (mu / mu_max + 0.25) * 60
        DO_target = 15 * (Fg / 75) - 0.11 * OUR
        DO += 0.4 * (DO_target - DO) + rng.normal(0, 0.05 * noise)
        DO = max(DO, 0.05)
        co2 = 0.4 + 0.06 * OUR + rng.normal(0, 0.01)

        out[i] = [
            Fg, 100, Fs, max(rng.normal(0, 0.05), 0), max(55 + rng.normal(0, 10), 0),
            max(60 + 40 * np.sin(ti / 15) + rng.normal(0, 5), 0.0001), 0.0001, 0.0,
            0.9 if ti < 120 else 1.1, 0.0, S, DO, P, V, T, pH,
        ]
        extra_rows.append((OUR, co2, X))

    extra = np.array(extra_rows)
    df = pd.DataFrame(index=range(n))
    df["Time (h)"] = t
    cols = INDPENSIM_COLUMNS
    df[cols[1]], df[cols[2]], df[cols[3]] = out[:, 0], out[:, 1], out[:, 2]
    df[cols[4]], df[cols[5]], df[cols[6]] = out[:, 3], out[:, 4], out[:, 5]
    df[cols[7]], df[cols[8]], df[cols[9]] = out[:, 6], out[:, 7], out[:, 8]
    df[cols[10]] = 0.0
    df[cols[11]] = out[:, 10] + rng.normal(0, 0.003, n).clip(min=0)
    df[cols[12]] = out[:, 11]
    df[cols[13]] = out[:, 12]
    df[cols[14]] = out[:, 13]
    df[cols[15]] = out[:, 13] * 1.04
    df[cols[16]] = out[:, 15]
    df[cols[17]] = out[:, 14]
    df[cols[18]] = extra[:, 0] * 12 + rng.normal(0, 2, n)
    df[cols[19]] = extra[:, 1]
    df[cols[20]] = 5.0
    df[cols[21]] = np.nan
    df[cols[22]] = 22.0
    df[cols[23]] = np.nan
    df[cols[24]] = extra[:, 0]
    df[cols[25]] = 20.9 - extra[:, 1] * 0.9
    df[cols[26]] = np.nan
    df[cols[27]] = np.nan
    df[cols[28]] = extra[:, 1] * 30
    df[cols[29]] = 0.0
    df[cols[30]] = np.nan
    offline = (np.round(t * 10) % 120 == 0)   # every 12 h
    df.loc[offline, cols[26]] = df.loc[offline, cols[13]] * rng.normal(1, 0.03, offline.sum())
    df.loc[offline, cols[27]] = extra[offline, 2] * rng.normal(1, 0.04, offline.sum())
    df.loc[offline, cols[30]] = 10 + extra[offline, 2] * 2.2
    df.loc[offline, cols[21]] = rng.normal(1.0, 0.1, offline.sum())
    df.loc[offline, cols[23]] = rng.normal(1.5, 0.1, offline.sum())
    in_fault_mask = fault & (t >= fault_start) & (t <= fault_start + fault_len)
    df[cols[31]] = in_fault_mask.astype(int)
    df[cols[32]] = int(strategy == 1)
    df[cols[33]] = 2 if strategy == 2 else 1     # PAT / Raman flag (label-shifted)
    df[cols[34]] = batch                         # batch id (label-shifted)
    df[cols[35]] = int(strategy == 2)
    df[cols[36]] = batch
    for c in RAMAN_COLUMNS:
        df[c] = rng.normal(1000, 50, n)
    return df


def write_indpensim_zip(settings: Settings, n_batches: int = 100, seed: int = 7) -> None:
    src = settings.source("indpensim")
    rng = np.random.default_rng(seed)
    frames, stats = [], []
    for b in range(1, n_batches + 1):
        df = _simulate_batch(b, rng)
        frames.append(df)
        p_end = df["Penicillin concentration(P:g/L)"].iloc[-1] * df["Vessel Volume(V:L)"].iloc[-1]
        p_during = 0.45 * p_end
        stats.append(
            {
                "Batch ref": b,
                "Penicllin_harvested_during_batch(kg)": round(p_during, 1),
                "Penicllin_harvested_end_of_batch (kg)": round(p_end, 1),
                "Penicllin_yield_total (kg)": round(p_during + p_end, 1),
                "Fault ref(0-NoFault 1-Fault)": int(b >= 91),
            }
        )
    ts = pd.concat(frames, ignore_index=True)
    target = settings.raw / src["landing_file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"Mendeley_data/{src['timeseries_member']}", ts.to_csv(index=False))
        zf.writestr(f"Mendeley_data/{src['statistics_member']}", pd.DataFrame(stats).to_csv(index=False))
    log.info("sample IndPenSim: %s batches, %s rows -> %s", n_batches, len(ts), target)


def write_pinksheet_xlsx(settings: Settings, seed: int = 11) -> None:
    src = settings.source("worldbank_pinksheet")
    rng = np.random.default_rng(seed)
    periods = pd.period_range("2010-01", "2026-09", freq="M")
    n = len(periods)

    def walk(start, vol, lo, hi):
        x = [start]
        for _ in range(n - 1):
            x.append(float(np.clip(x[-1] * np.exp(rng.normal(0, vol)), lo, hi)))
        return np.round(x, 3)

    data = {
        "Sugar, world": walk(0.45, 0.05, 0.22, 0.65),
        "Sugar, EU": walk(0.42, 0.02, 0.33, 0.55),
        "Soybean meal": walk(420, 0.05, 300, 620),
        "Fish meal": walk(1600, 0.03, 1250, 2100),
        "Soybeans": walk(450, 0.05, 330, 680),
        "Maize": walk(230, 0.05, 150, 330),
    }
    units = {"Sugar, world": "($/kg)", "Sugar, EU": "($/kg)", "Soybean meal": "($/mt)",
             "Fish meal": "($/mt)", "Soybeans": "($/mt)", "Maize": "($/mt)"}
    data["Sugar, EU"] = data["Sugar, EU"].astype(object)
    data["Sugar, EU"][-20:] = "…"                  # series discontinued, as in the real file
    rows = [
        ["World Bank Commodity Price Data (The Pink Sheet)"] + [None] * len(data),
        ["Monthly prices in nominal US dollars, 1960 to present"] + [None] * len(data),
        ["(SAMPLE FILE generated by fdp.sample_data)"] + [None] * len(data),
        [None] + [None] * len(data),
        [None] + list(data.keys()),
        [None] + [units[k] for k in data],
    ]
    for i, p in enumerate(periods):
        rows.append([f"{p.year}M{p.month:02d}"] + [data[k][i] for k in data])
    target = settings.raw / src["landing_file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(target, engine="openpyxl") as xw:
        pd.DataFrame([["Annual Prices (sample placeholder)"]]).to_excel(
            xw, sheet_name="Annual Prices (Nominal)", header=False, index=False)
        pd.DataFrame(rows).to_excel(xw, sheet_name=src["sheet"], header=False, index=False)
    log.info("sample Pink Sheet: %s months -> %s", n, target)


def write_ecb_csv(settings: Settings, seed: int = 13) -> None:
    src = settings.source("ecb_fx")
    rng = np.random.default_rng(seed)
    periods = pd.period_range("2000-01", "2026-10", freq="M")
    x, vals = 1.0, []
    for _ in periods:
        x = float(np.clip(x * np.exp(rng.normal(0, 0.02)), 0.85, 1.55))
        vals.append(round(x, 4))
    df = pd.DataFrame(
        {
            "KEY": "EXR.M.USD.EUR.SP00.A", "FREQ": "M", "CURRENCY": "USD",
            "CURRENCY_DENOM": "EUR", "EXR_TYPE": "SP00", "EXR_SUFFIX": "A",
            "TIME_PERIOD": [str(p) for p in periods], "OBS_VALUE": vals,
            "OBS_STATUS": "A", "TITLE": "US dollar/Euro (SAMPLE)",
        }
    )
    target = settings.raw / src["landing_file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(target, index=False)
    log.info("sample ECB FX: %s months -> %s", len(df), target)


def write_all(settings: Settings) -> None:
    write_indpensim_zip(settings)
    write_pinksheet_xlsx(settings)
    write_ecb_csv(settings)
    (settings.raw / "SAMPLE_DATA").write_text(
        "Files in this folder were generated by fdp.sample_data and are synthetic.\n"
    )
