"""
t2_wdo_ptax_v0 -- Fase 0 (mapa descritivo, ZERO trades)
Campanha: PTAX fixing windows (WDO) - H_drift / H_rev
Pre-registro: mt5/campaigns/t2_wdo_ptax_v0/PREREGISTRATION.md

Regras inegociaveis:
- Nenhum dado >= 2025-01-01 entra em NENHUMA estatistica (OOS sagrado).
  Filtro aplicado logo apos carregar cada parquet.
- So estatistica descritiva. Nenhuma simulacao de trade / PnL.
- Gates e janelas sao as do pre-registro, nao inventar novas.

Convencao de barra (M5 e M15): datetime_b3 = INICIO da barra.
Preco no instante T = close da barra rotulada (T - timeframe).

Script autossuficiente: pandas, numpy, pyarrow apenas.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

M5_PATH = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history\WDO_cont_N_M5.parquet"
M15_PATH = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history\WDO_cont_N_M15.parquet"

OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\t2_wdo_ptax_v0\phase0")

OOS_CUTOFF = pd.Timestamp("2025-01-01")

WINDOWS = [
    pd.Timedelta(hours=10, minutes=0),
    pd.Timedelta(hours=11, minutes=0),
    pd.Timedelta(hours=12, minutes=0),
    pd.Timedelta(hours=13, minutes=0),
]
WINDOW_LABELS = ["10:00", "11:00", "12:00", "13:00"]

N_BOOT = 1000
BOOT_SEED = 42

D1_THRESH_BPS = 3.0
D2_MIN_N_YEAR = 60
R2_THRESH_BPS = 4.0
R1_T_THRESH = -2.0

LOG_LINES = []


def log(msg=""):
    print(msg)
    LOG_LINES.append(str(msg))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def bps(x):
    return x * 10000.0


def newey_west_lag(n):
    return int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))


def ols_nw(x, y):
    """OLS y ~ 1 + x with Newey-West (Bartlett kernel) HAC t-stats.
    Returns dict with intercept, slope, se_slope, t_slope, n, lag.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    X = np.column_stack([np.ones(n), x])
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    g = X * resid[:, None]  # n x 2 score contributions

    L = newey_west_lag(n)
    Gamma0 = (g.T @ g) / n
    S = Gamma0.copy()
    for lag in range(1, L + 1):
        w = 1.0 - lag / (L + 1.0)
        Gl = (g[lag:].T @ g[:-lag]) / n
        S += w * (Gl + Gl.T)

    Var_beta = n * (XtX_inv @ S @ XtX_inv)
    se = np.sqrt(np.diag(Var_beta))
    t = beta / se
    return {
        "intercept": beta[0], "slope": beta[1],
        "se_slope": se[1], "t_slope": t[1],
        "n": n, "nw_lag": L,
    }


def ols_slope_sign(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xm = x - x.mean()
    ym = y - y.mean()
    denom = np.sum(xm * xm)
    if denom == 0:
        return np.nan
    return np.sum(xm * ym) / denom


def bootstrap_ci_mean(values, n_boot=N_BOOT, seed=BOOT_SEED):
    values = np.asarray(values, dtype=float)
    n = len(values)
    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boot_means[i] = values[idx].mean()
    lo, hi = np.percentile(boot_means, [2.5, 97.5])
    return float(lo), float(hi)


def fmt_ci_bps(mean, lo, hi):
    return f"{bps(mean):+.2f} bps [IC95 {bps(lo):+.2f}, {bps(hi):+.2f}]"


# ----------------------------------------------------------------------
# Load + filter (OOS sagrado applied immediately after load)
# ----------------------------------------------------------------------

log("=" * 78)
log("t2_wdo_ptax_v0 -- Fase 0 (mapa descritivo) -- WDO PTAX fixing windows")
log("=" * 78)

m5_raw = pd.read_parquet(M5_PATH)
m5_raw["datetime_b3"] = pd.to_datetime(m5_raw["datetime_b3"])
m5_full_min, m5_full_max = m5_raw["datetime_b3"].min(), m5_raw["datetime_b3"].max()

m5is = m5_raw[m5_raw["datetime_b3"] < OOS_CUTOFF].copy()
m5is = m5is.sort_values("datetime_b3").reset_index(drop=True)
m5is = m5is.drop_duplicates(subset="datetime_b3", keep="first")

m15_raw = pd.read_parquet(M15_PATH)
m15_raw["datetime_b3"] = pd.to_datetime(m15_raw["datetime_b3"])
m15_full_min, m15_full_max = m15_raw["datetime_b3"].min(), m15_raw["datetime_b3"].max()

m15is = m15_raw[m15_raw["datetime_b3"] < OOS_CUTOFF].copy()
m15is = m15is.sort_values("datetime_b3").reset_index(drop=True)
m15is = m15is.drop_duplicates(subset="datetime_b3", keep="first")

n_dropped_oos_m5 = len(m5_raw) - len(m5_raw[m5_raw["datetime_b3"] < OOS_CUTOFF])
n_dropped_oos_m15 = len(m15_raw) - len(m15_raw[m15_raw["datetime_b3"] < OOS_CUTOFF])

log("\n--- Passo 2: inspecao de dados ---")
log(f"M5 arquivo completo: {m5_full_min} -> {m5_full_max}  (n={len(m5_raw)} barras)")
log(f"M5 IS (< {OOS_CUTOFF.date()}): {m5is['datetime_b3'].min()} -> {m5is['datetime_b3'].max()}  "
    f"(n={len(m5is)} barras, {n_dropped_oos_m5} barras OOS descartadas)")
n_days_m5 = m5is["datetime_b3"].dt.normalize().nunique()
log(f"M5 IS: n dias unicos de pregao = {n_days_m5}")

log(f"\nM15 arquivo completo: {m15_full_min} -> {m15_full_max}  (n={len(m15_raw)} barras)")
log(f"M15 IS (< {OOS_CUTOFF.date()}): {m15is['datetime_b3'].min()} -> {m15is['datetime_b3'].max()}  "
    f"(n={len(m15is)} barras, {n_dropped_oos_m15} barras OOS descartadas)")
n_days_m15 = m15is["datetime_b3"].dt.normalize().nunique()
log(f"M15 IS: n dias unicos de pregao = {n_days_m15}")

# ----------------------------------------------------------------------
# Build close-price index (datetime -> close), for O(1) lookups
# ----------------------------------------------------------------------

closes_m5 = m5is.set_index("datetime_b3")["close"].sort_index()
closes_m5 = closes_m5[~closes_m5.index.duplicated(keep="first")]

closes_m15 = m15is.set_index("datetime_b3")["close"].sort_index()
closes_m15 = closes_m15[~closes_m15.index.duplicated(keep="first")]

days_m5 = pd.DatetimeIndex(sorted(m5is["datetime_b3"].dt.normalize().unique()))
days_m15 = pd.DatetimeIndex(sorted(m15is["datetime_b3"].dt.normalize().unique()))

# ----------------------------------------------------------------------
# eom flag: last trading day of month PRESENT IN DATA (M5 IS calendar)
# ----------------------------------------------------------------------

cal_df = pd.DataFrame({"date": days_m5})
cal_df["ym"] = cal_df["date"].dt.to_period("M")
eom_dates = set(cal_df.groupby("ym")["date"].max())

# ----------------------------------------------------------------------
# Build per-window daily M5 tables: ret_pre, ret_post, validity, eom, year
# ----------------------------------------------------------------------

bar_inspection = []
window_tables = {}  # label -> DataFrame (valid days only)

log("\n--- Inspecao de barras ao redor das 4 janelas (M5 IS) ---")
log(f"{'janela':>7} | {'n_dias_total':>12} | {'n_validos':>9} | {'%validos':>8} | "
    f"{'miss_t1(W-35)':>13} | {'miss_t2(W-5)':>12} | {'miss_t3(W+5)':>12} | {'miss_t4(W+35)':>13}")

for w_td, w_lab in zip(WINDOWS, WINDOW_LABELS):
    w_dt = days_m5 + w_td
    t1 = w_dt - pd.Timedelta(minutes=35)
    t2 = w_dt - pd.Timedelta(minutes=5)
    t3 = w_dt + pd.Timedelta(minutes=5)
    t4 = w_dt + pd.Timedelta(minutes=35)

    c1 = closes_m5.reindex(t1).to_numpy()
    c2 = closes_m5.reindex(t2).to_numpy()
    c3 = closes_m5.reindex(t3).to_numpy()
    c4 = closes_m5.reindex(t4).to_numpy()

    miss1, miss2, miss3, miss4 = np.isnan(c1), np.isnan(c2), np.isnan(c3), np.isnan(c4)
    valid = ~(miss1 | miss2 | miss3 | miss4)

    ret_pre = c2 / c1 - 1.0
    ret_post = c4 / c3 - 1.0

    df_w = pd.DataFrame({
        "date": days_m5,
        "window": w_lab,
        "ret_pre": ret_pre,
        "ret_post": ret_post,
        "valid": valid,
    })
    df_w["eom"] = df_w["date"].isin(eom_dates)
    df_w["year"] = df_w["date"].dt.year

    df_valid = df_w[df_w["valid"]].drop(columns="valid").reset_index(drop=True)
    window_tables[w_lab] = df_valid

    n_total = len(df_w)
    n_valid = valid.sum()
    pct_valid = 100.0 * n_valid / n_total
    bar_inspection.append({
        "window": w_lab, "n_dias_total": int(n_total), "n_validos": int(n_valid),
        "pct_validos": round(pct_valid, 1),
        "miss_t1": int(miss1.sum()), "miss_t2": int(miss2.sum()),
        "miss_t3": int(miss3.sum()), "miss_t4": int(miss4.sum()),
    })
    log(f"{w_lab:>7} | {n_total:>12} | {n_valid:>9} | {pct_valid:>7.1f}% | "
        f"{int(miss1.sum()):>13} | {int(miss2.sum()):>12} | {int(miss3.sum()):>12} | {int(miss4.sum()):>13}")

# consolidated daily_windows_m5 output
daily_windows_all = []
for w_lab in WINDOW_LABELS:
    daily_windows_all.append(window_tables[w_lab])
daily_windows_m5 = pd.concat(daily_windows_all, ignore_index=True)
daily_windows_m5 = daily_windows_m5[["date", "window", "ret_pre", "ret_post", "eom", "year"]]
daily_windows_m5.to_csv(OUT_DIR / "daily_windows_m5.csv", index=False)
log(f"\nSalvo: {OUT_DIR / 'daily_windows_m5.csv'}  (n={len(daily_windows_m5)} linhas)")

# ----------------------------------------------------------------------
# Contexto: perfil intradiario de vol M5 IS (media |ret| por horario de barra)
# ----------------------------------------------------------------------

m5is_sorted = m5is.sort_values("datetime_b3").copy()
m5is_sorted["date_only"] = m5is_sorted["datetime_b3"].dt.normalize()
m5is_sorted["ret"] = m5is_sorted.groupby("date_only")["close"].pct_change()
m5is_sorted["time_of_day"] = m5is_sorted["datetime_b3"].dt.time
vol_profile = (
    m5is_sorted.dropna(subset=["ret"])
    .groupby("time_of_day")["ret"]
    .apply(lambda s: bps(s.abs().mean()))
    .reset_index(name="mean_abs_ret_bps")
)
vol_profile["n"] = (
    m5is_sorted.dropna(subset=["ret"]).groupby("time_of_day")["ret"].size().values
)

log("\n--- Perfil intradiario de volatilidade M5 IS (media |ret| por barra, bps) ---")
log(f"{'horario':>8} | {'bps_medio':>10} | {'n':>6}")
for _, row in vol_profile.iterrows():
    log(f"{str(row['time_of_day']):>8} | {row['mean_abs_ret_bps']:>10.2f} | {int(row['n']):>6}")

# highlight PTAX windows explicitly in the profile (bars covering W:00-W:10)
log("\nDestaque janelas PTAX (barras @W:00 e @W:05, dentro da consulta 10min):")
for w_lab in WINDOW_LABELS:
    h, m = int(w_lab.split(":")[0]), int(w_lab.split(":")[1])
    sel = vol_profile[vol_profile["time_of_day"].isin(
        [pd.Timestamp(f"2000-01-01 {h:02d}:{mm:02d}:00").time() for mm in [m, m + 5]]
    )]
    for _, r in sel.iterrows():
        log(f"  janela {w_lab} -> barra {r['time_of_day']}: {r['mean_abs_ret_bps']:.2f} bps (n={int(r['n'])})")

# ----------------------------------------------------------------------
# M15 grid (robustez), IS 2021-2024
# ----------------------------------------------------------------------

m15_window_tables = {}
for w_td, w_lab in zip(WINDOWS, WINDOW_LABELS):
    w_dt = days_m15 + w_td
    a1 = w_dt - pd.Timedelta(minutes=45)   # bar for price(W-30)
    a2 = w_dt - pd.Timedelta(minutes=15)   # bar for price(W)
    b1 = w_dt                              # bar for price(W+15)
    b2 = w_dt + pd.Timedelta(minutes=30)   # bar for price(W+45)

    ca1 = closes_m15.reindex(a1).to_numpy()
    ca2 = closes_m15.reindex(a2).to_numpy()
    cb1 = closes_m15.reindex(b1).to_numpy()
    cb2 = closes_m15.reindex(b2).to_numpy()

    valid15 = ~(np.isnan(ca1) | np.isnan(ca2) | np.isnan(cb1) | np.isnan(cb2))
    ret_pre15 = ca2 / ca1 - 1.0
    ret_post15 = cb2 / cb1 - 1.0

    df15 = pd.DataFrame({
        "date": days_m15, "ret_pre": ret_pre15, "ret_post": ret_post15, "valid": valid15,
    })
    m15_window_tables[w_lab] = df15[df15["valid"]].drop(columns="valid").reset_index(drop=True)

# ----------------------------------------------------------------------
# Per-window analyses 1-4 (M5 IS) + gates
# ----------------------------------------------------------------------

results = {}
gates_rows = []

log("\n" + "=" * 78)
log("ANALISES POR JANELA (M5 IS)")
log("=" * 78)

for w_lab in WINDOW_LABELS:
    df = window_tables[w_lab]
    n = len(df)
    log(f"\n----- Janela {w_lab} (n={n} dias validos) -----")

    res = {"n": n}

    # --- Item 1: H_drift descriptive (ret_pre, ret_post) ---
    pre_mean = df["ret_pre"].mean()
    pre_lo, pre_hi = bootstrap_ci_mean(df["ret_pre"].values)
    post_mean = df["ret_post"].mean()
    post_lo, post_hi = bootstrap_ci_mean(df["ret_post"].values)

    log(f"[1] ret_pre  media: {fmt_ci_bps(pre_mean, pre_lo, pre_hi)}")
    log(f"[1] ret_post media: {fmt_ci_bps(post_mean, post_lo, post_hi)}")

    res["ret_pre_mean_bps"] = bps(pre_mean)
    res["ret_pre_ci95_bps"] = [bps(pre_lo), bps(pre_hi)]
    res["ret_post_mean_bps"] = bps(post_mean)
    res["ret_post_ci95_bps"] = [bps(post_lo), bps(post_hi)]

    # breakdown by year
    year_breakdown = []
    log("     breakdown por ano (ret_pre):")
    for yr, g in df.groupby("year"):
        m = g["ret_pre"].mean()
        year_breakdown.append({"year": int(yr), "n": len(g), "ret_pre_mean_bps": bps(m)})
        flag = "" if len(g) >= D2_MIN_N_YEAR else "  (n<60, nao conta p/ gate)"
        log(f"       {yr}: n={len(g):>4}  ret_pre_mean={bps(m):+7.2f} bps{flag}")
    res["year_breakdown_ret_pre"] = year_breakdown

    year_breakdown_post = []
    for yr, g in df.groupby("year"):
        m = g["ret_post"].mean()
        year_breakdown_post.append({"year": int(yr), "n": len(g), "ret_post_mean_bps": bps(m)})
    res["year_breakdown_ret_post"] = year_breakdown_post

    # --- Item 2: H_rev - OLS post ~ pre + fade conditional mean ---
    ols_res = ols_nw(df["ret_pre"].values, df["ret_post"].values)
    log(f"[2] OLS ret_post ~ ret_pre: slope={ols_res['slope']:.4f}  "
        f"t_NW={ols_res['t_slope']:.2f}  (lag={ols_res['nw_lag']}, n={ols_res['n']})")

    sign_pre = np.sign(df["ret_pre"].values)
    fade = np.where(sign_pre != 0, -sign_pre * df["ret_post"].values, np.nan)
    fade_valid = fade[~np.isnan(fade)]
    fade_mean = fade_valid.mean()
    fade_lo, fade_hi = bootstrap_ci_mean(fade_valid)
    log(f"[2] fade cond. media ret_post: {fmt_ci_bps(fade_mean, fade_lo, fade_hi)}  (n={len(fade_valid)})")

    res["ols_slope"] = ols_res["slope"]
    res["ols_t_nw"] = ols_res["t_slope"]
    res["ols_nw_lag"] = ols_res["nw_lag"]
    res["fade_mean_bps"] = bps(fade_mean)
    res["fade_ci95_bps"] = [bps(fade_lo), bps(fade_hi)]

    # per-year slope sign (for R3)
    year_slope = []
    log("     breakdown por ano (slope sign post~pre):")
    for yr, g in df.groupby("year"):
        if len(g) >= 5:
            s = ols_slope_sign(g["ret_pre"].values, g["ret_post"].values)
        else:
            s = np.nan
        year_slope.append({"year": int(yr), "n": len(g), "slope": float(s) if not np.isnan(s) else None})
        flag = "" if len(g) >= D2_MIN_N_YEAR else "  (n<60, nao conta p/ gate)"
        log(f"       {yr}: n={len(g):>4}  slope={s:+.4f}{flag}")
    res["year_breakdown_slope"] = year_slope

    # --- Item 3: robustness, exclude top/bottom 1% of ret_pre ---
    p1, p99 = np.percentile(df["ret_pre"].values, [1, 99])
    df_trim = df[(df["ret_pre"] > p1) & (df["ret_pre"] < p99)].reset_index(drop=True)
    n_trim = len(df_trim)

    trim_pre_mean = df_trim["ret_pre"].mean()
    trim_pre_lo, trim_pre_hi = bootstrap_ci_mean(df_trim["ret_pre"].values)
    trim_ols = ols_nw(df_trim["ret_pre"].values, df_trim["ret_post"].values)
    sign_pre_t = np.sign(df_trim["ret_pre"].values)
    fade_t = np.where(sign_pre_t != 0, -sign_pre_t * df_trim["ret_post"].values, np.nan)
    fade_t_valid = fade_t[~np.isnan(fade_t)]
    fade_t_mean = fade_t_valid.mean()
    fade_t_lo, fade_t_hi = bootstrap_ci_mean(fade_t_valid)

    log(f"[3] robustez (exclui top/bottom 1% ret_pre, n={n_trim}):")
    log(f"     ret_pre media: {fmt_ci_bps(trim_pre_mean, trim_pre_lo, trim_pre_hi)}")
    log(f"     OLS slope={trim_ols['slope']:.4f}  t_NW={trim_ols['t_slope']:.2f} (lag={trim_ols['nw_lag']})")
    log(f"     fade cond. media: {fmt_ci_bps(fade_t_mean, fade_t_lo, fade_t_hi)}")

    res["trim_n"] = n_trim
    res["trim_ret_pre_mean_bps"] = bps(trim_pre_mean)
    res["trim_ret_pre_ci95_bps"] = [bps(trim_pre_lo), bps(trim_pre_hi)]
    res["trim_ols_slope"] = trim_ols["slope"]
    res["trim_ols_t_nw"] = trim_ols["t_slope"]
    res["trim_fade_mean_bps"] = bps(fade_t_mean)
    res["trim_fade_ci95_bps"] = [bps(fade_t_lo), bps(fade_t_hi)]

    # --- Item 4: eom subset (descriptive only, no gate) ---
    df_eom = df[df["eom"]]
    n_eom = len(df_eom)
    if n_eom > 0:
        eom_pre_mean = df_eom["ret_pre"].mean()
        eom_post_mean = df_eom["ret_post"].mean()
    else:
        eom_pre_mean = np.nan
        eom_post_mean = np.nan
    log(f"[4] subset eom (n={n_eom}, descritivo, sem gate):")
    log(f"     ret_pre media: {bps(eom_pre_mean):+.2f} bps   ret_post media: {bps(eom_post_mean):+.2f} bps")

    res["eom_n"] = n_eom
    res["eom_ret_pre_mean_bps"] = bps(eom_pre_mean) if n_eom > 0 else None
    res["eom_ret_post_mean_bps"] = bps(eom_post_mean) if n_eom > 0 else None

    # --- M15 grid replication (item 5) ---
    df15 = m15_window_tables[w_lab]
    n15 = len(df15)
    pre15_mean = df15["ret_pre"].mean()
    ols15 = ols_nw(df15["ret_pre"].values, df15["ret_post"].values)
    log(f"[5] grade M15 (n={n15}): ret_pre_mean={bps(pre15_mean):+.2f} bps (sinal={'+' if pre15_mean>=0 else '-'})  "
        f"slope={ols15['slope']:.4f}  t_NW={ols15['t_slope']:.2f}")

    res["m15_n"] = n15
    res["m15_ret_pre_mean_bps"] = bps(pre15_mean)
    res["m15_ols_slope"] = ols15["slope"]
    res["m15_ols_t_nw"] = ols15["t_slope"]

    results[w_lab] = res

    # ---------------- Gates ----------------
    # D1: |mean ret_pre| >= 3bps with CI95 excluding 0
    d1 = (abs(bps(pre_mean)) >= D1_THRESH_BPS) and not (pre_lo <= 0 <= pre_hi)
    # D2: same sign in all years with n>=60
    years_ok = [yb for yb in year_breakdown if yb["n"] >= D2_MIN_N_YEAR]
    if len(years_ok) == 0:
        d2 = False
    else:
        signs = set(np.sign(yb["ret_pre_mean_bps"]) for yb in years_ok)
        d2 = (len(signs) == 1) and (0 not in signs) and (np.sign(bps(pre_mean)) in signs)
    # D3: survives outlier trim (D1-equivalent condition on trimmed data)
    d3 = (abs(bps(trim_pre_mean)) >= D1_THRESH_BPS) and not (trim_pre_lo <= 0 <= trim_pre_hi)
    # D4: same sign (not magnitude) on M15 grid
    d4 = np.sign(bps(pre_mean)) == np.sign(bps(pre15_mean)) and bps(pre_mean) != 0

    h_drift_pass = d1 and d2 and d3 and d4

    # R1: slope < 0 with t_NW <= -2.0
    r1 = (ols_res["slope"] < 0) and (ols_res["t_slope"] <= R1_T_THRESH)
    # R2: |fade mean| >= 4bps with CI95 excluding 0
    r2 = (abs(bps(fade_mean)) >= R2_THRESH_BPS) and not (fade_lo <= 0 <= fade_hi)
    # R3: slope sign negative in all years n>=60
    years_ok_slope = [ys for ys in year_slope if ys["n"] >= D2_MIN_N_YEAR and ys["slope"] is not None]
    if len(years_ok_slope) == 0:
        r3 = False
    else:
        r3 = all(ys["slope"] < 0 for ys in years_ok_slope)
    # R4: survives outlier trim (R1 and R2 on trimmed data)
    r4a = (trim_ols["slope"] < 0) and (trim_ols["t_slope"] <= R1_T_THRESH)
    r4b = (abs(bps(fade_t_mean)) >= R2_THRESH_BPS) and not (fade_t_lo <= 0 <= fade_t_hi)
    r4 = r4a and r4b
    # R5: slope also negative on M15 grid (sign only)
    r5 = ols15["slope"] < 0

    h_rev_pass = r1 and r2 and r3 and r4 and r5

    gates_rows.append({
        "window": w_lab,
        "D1": d1, "D2": d2, "D3": d3, "D4": d4, "H_drift": h_drift_pass,
        "R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5, "H_rev": h_rev_pass,
    })

    results[w_lab]["gates"] = gates_rows[-1]

# ----------------------------------------------------------------------
# Gates summary table + decision
# ----------------------------------------------------------------------

log("\n" + "=" * 78)
log("TABELA DE GATES (PASS/FAIL)")
log("=" * 78)
header = f"{'janela':>7} | {'D1':>5} {'D2':>5} {'D3':>5} {'D4':>5} | {'H_drift':>8} || " \
         f"{'R1':>5} {'R2':>5} {'R3':>5} {'R4':>5} {'R5':>5} | {'H_rev':>6}"
log(header)
log("-" * len(header))


def yn(b):
    return "PASS" if b else "FAIL"


any_pass = False
passing_windows = []
for row in gates_rows:
    log(f"{row['window']:>7} | {yn(row['D1']):>5} {yn(row['D2']):>5} {yn(row['D3']):>5} {yn(row['D4']):>5} | "
        f"{yn(row['H_drift']):>8} || {yn(row['R1']):>5} {yn(row['R2']):>5} {yn(row['R3']):>5} {yn(row['R4']):>5} "
        f"{yn(row['R5']):>5} | {yn(row['H_rev']):>6}")
    if row["H_drift"] or row["H_rev"]:
        any_pass = True
        hyps = []
        if row["H_drift"]:
            hyps.append("H_drift")
        if row["H_rev"]:
            hyps.append("H_rev")
        passing_windows.append((row["window"], hyps))

log("\n--- DECISAO (conforme regra pre-registrada) ---")
if any_pass:
    decision = "advance_to_phase1"
    log(f"decision = {decision}")
    for w, hyps in passing_windows:
        log(f"  -> Fase 1 (mecanica) na janela {w}, hipotese(s): {', '.join(hyps)}")
else:
    decision = "premise_refuted"
    log(f"decision = {decision}")
    log("  Nenhuma janela passou H_drift completo nem H_rev completo.")
    log("  Fechar campanha sem variantes post-hoc.")

# eom side-note (not part of decision)
log("\n--- Nota eom (fora da decisao, n insuficiente p/ gate formal) ---")
for w_lab in WINDOW_LABELS:
    r = results[w_lab]
    if r["eom_n"] > 0:
        eff = abs(r["eom_ret_pre_mean_bps"]) if r["eom_ret_pre_mean_bps"] is not None else 0
        eff_post = abs(r["eom_ret_post_mean_bps"]) if r["eom_ret_post_mean_bps"] is not None else 0
        note = ""
        if max(eff, eff_post) >= 10:
            note = "  >>> |efeito| >= 10bps -- registrar como hipotese futura separada (nao continuar nesta campanha)"
        log(f"  {w_lab}: n={r['eom_n']}  ret_pre={r['eom_ret_pre_mean_bps']:+.2f}bps  "
            f"ret_post={r['eom_ret_post_mean_bps']:+.2f}bps{note}")

# ----------------------------------------------------------------------
# Save outputs
# ----------------------------------------------------------------------

summary = {
    "campaign": "t2_wdo_ptax_v0",
    "phase": "phase0_map",
    "oos_cutoff": str(OOS_CUTOFF.date()),
    "data": {
        "m5_full_range": [str(m5_full_min), str(m5_full_max)],
        "m5_is_range": [str(m5is["datetime_b3"].min()), str(m5is["datetime_b3"].max())],
        "m5_is_n_days": int(n_days_m5),
        "m5_is_n_bars": int(len(m5is)),
        "m15_full_range": [str(m15_full_min), str(m15_full_max)],
        "m15_is_range": [str(m15is["datetime_b3"].min()), str(m15is["datetime_b3"].max())],
        "m15_is_n_days": int(n_days_m15),
        "m15_is_n_bars": int(len(m15is)),
    },
    "bar_inspection_m5": bar_inspection,
    "vol_profile_m5_bps": vol_profile.assign(
        time_of_day=lambda d: d["time_of_day"].astype(str)
    ).to_dict(orient="records"),
    "windows": results,
    "gates_table": gates_rows,
    "decision": decision,
}

def _json_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


with open(OUT_DIR / "t2_map_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, default=_json_default)

log(f"\nSalvo: {OUT_DIR / 't2_map_summary.json'}")

with open(OUT_DIR / "SUMMARY.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(LOG_LINES))
log(f"Salvo: {OUT_DIR / 'SUMMARY.txt'}")

log("\nFase 0 concluida.")
