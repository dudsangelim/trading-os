"""
t1_intraday_momentum_v0 -- Fase 0 (mapa descritivo)
Segue EXATAMENTE o pre-registro em:
  C:\\Users\\Notebook\\Documents\\Claude\\Projects\\Finanças\\mt5\\campaigns\\t1_intraday_momentum_v0\\PREREGISTRATION.md

NAO simula trades. Apenas estatistica descritiva (OLS + NW t-stat, hit rate
bootstrap, media condicional bootstrap, breakdowns, robustez a outliers,
gates G1-G5).

Regra inegociavel: dados de 2025-01-01 em diante (OOS) sao removidos
IMEDIATAMENTE apos carregar o parquet, antes de qualquer estatistica.

Convencao de bar timestamp (MT5 M15): datetime_b3 = INICIO da barra.
Bar @T cobre [T, T+15min). Logo "preco no instante T" (close de mercado em T)
= close da barra rotulada (T - 15min).
  close(09:30) = close da barra @09:15   -> exige barras @09:00 e @09:15
  close(18:15) = close da barra @18:00   -> exige barra @18:00
  close(17:45) = close da barra @17:30   -> exige barra @17:30
Isso e consistente com a instrucao do prompt: "dias validos = tem barras
09:00 E 09:15 E as duas barras finais usadas em ret_last".

Autor: executor mecanico (Claude), rodar com `python build_t1_map.py`.
Dependencias: pandas, numpy, pyarrow. Sem statsmodels.
"""

import json
import os
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

DATA_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
FILES = {
    "WIN": os.path.join(DATA_DIR, "WIN_cont_N_M15.parquet"),
    "WDO": os.path.join(DATA_DIR, "WDO_cont_N_M15.parquet"),
}

OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\t1_intraday_momentum_v0\phase0"
OUT_JSON = os.path.join(OUT_DIR, "t1_map_summary.json")
OUT_SUMMARY_TXT = os.path.join(OUT_DIR, "SUMMARY.txt")
OUT_DAILY = {
    "WIN": os.path.join(OUT_DIR, "daily_win.csv"),
    "WDO": os.path.join(OUT_DIR, "daily_wdo.csv"),
}

IS_START = pd.Timestamp("2021-01-01")
IS_END_EXCL = pd.Timestamp("2025-01-01")  # OOS sagrado: >= isto e removido logo apos load

BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42
FEE_BPS_LO, FEE_BPS_HI = 2, 6  # cenarios RT registrados no pre-registro (contexto, nao gate)

VARIANTS = ["ret_first", "ret_on_first", "ret_on"]
VARIANT_LABELS = {
    "ret_first": "A: ret_first (close_0930/open_0900 - 1, intraday puro)",
    "ret_on_first": "B: ret_on_first (close_0930/close_prev_day - 1, Gao original)",
    "ret_on": "C: ret_on (open_0900/close_prev_day - 1, Liu & Tse)",
}

lines_out = []  # buffer p/ SUMMARY.txt (espelha os prints)


def log(msg=""):
    print(msg)
    lines_out.append(str(msg))


# ----------------------------------------------------------------------
# NEWEY-WEST OLS (manual, sem statsmodels)
# ----------------------------------------------------------------------

def nw_lag(n):
    return int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))


def ols_nw(x, y):
    """OLS univariado y = a + b*x + e, com erro-padrao Newey-West (Bartlett).
    Retorna dict com a, b, se_b (NW), t_b (NW), n, lag.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 10:
        return dict(a=np.nan, b=np.nan, se_b=np.nan, t_b=np.nan, n=n, lag=0)

    X = np.column_stack([np.ones(n), x])
    XtX = X.T @ X
    XtX_inv = np.linalg.inv(XtX)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta

    L = nw_lag(n)
    # meat matrix S = sum_t u_t^2 x_t x_t' + sum_l w_l sum_t u_t u_{t-l} (x_t x_{t-l}' + x_{t-l} x_t')
    u = resid
    S = np.zeros((2, 2))
    for t in range(n):
        xt = X[t : t + 1].T  # (2,1)
        S += (u[t] ** 2) * (xt @ xt.T)
    for l in range(1, L + 1):
        w_l = 1.0 - l / (L + 1.0)
        Gamma = np.zeros((2, 2))
        for t in range(l, n):
            xt = X[t : t + 1].T
            xtl = X[t - l : t - l + 1].T
            Gamma += u[t] * u[t - l] * (xt @ xtl.T + xtl @ xt.T)
        S += w_l * Gamma

    var_beta = XtX_inv @ S @ XtX_inv
    se_b = np.sqrt(var_beta[1, 1]) if var_beta[1, 1] > 0 else np.nan
    t_b = beta[1] / se_b if se_b and se_b > 0 else np.nan

    return dict(a=beta[0], b=beta[1], se_b=se_b, t_b=t_b, n=n, lag=L)


# ----------------------------------------------------------------------
# BOOTSTRAP (reamostragem de DIAS com reposicao, seed fixa)
# ----------------------------------------------------------------------

def bootstrap_hit_rate(sign_pred, sign_actual, n_boot=BOOTSTRAP_N, seed=BOOTSTRAP_SEED):
    rng = np.random.default_rng(seed)
    mask = np.isfinite(sign_pred) & np.isfinite(sign_actual)
    sp = np.asarray(sign_pred[mask])
    sa = np.asarray(sign_actual[mask])
    n = len(sp)
    hits = (sp == sa).astype(float)
    point = hits.mean() if n > 0 else np.nan
    boot = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boot[i] = hits[idx].mean()
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(point=point, ci_lo=lo, ci_hi=hi, n=n)


def bootstrap_mean_bps(values_bps, n_boot=BOOTSTRAP_N, seed=BOOTSTRAP_SEED):
    rng = np.random.default_rng(seed)
    v = np.asarray(values_bps)
    v = v[np.isfinite(v)]
    n = len(v)
    if n == 0:
        return dict(point=np.nan, ci_lo=np.nan, ci_hi=np.nan, n=0)
    point = v.mean()
    boot = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boot[i] = v[idx].mean()
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(point=point, ci_lo=lo, ci_hi=hi, n=n)


# ----------------------------------------------------------------------
# CARGA + FILTRO OOS (imediato)
# ----------------------------------------------------------------------

def load_is(symbol):
    path = FILES[symbol]
    df = pd.read_parquet(path, columns=[
        "datetime_b3", "open", "high", "low", "close", "tick_volume", "real_volume",
    ])
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    n_total = len(df)
    full_min, full_max = df["datetime_b3"].min(), df["datetime_b3"].max()

    # FILTRO OOS SAGRADO: remove >= 2025-01-01 imediatamente
    df = df[(df["datetime_b3"] >= IS_START) & (df["datetime_b3"] < IS_END_EXCL)].copy()
    df = df.reset_index(drop=True)

    return df, dict(n_total_raw=n_total, full_min=str(full_min), full_max=str(full_max),
                     n_is_rows=len(df))


# ----------------------------------------------------------------------
# INSPECAO DA GRADE HORARIA (passo 2)
# ----------------------------------------------------------------------

def inspect_grid(df, symbol):
    df = df.copy()
    df["date"] = df["datetime_b3"].dt.date
    df["time"] = df["datetime_b3"].dt.time
    g = df.groupby("date")
    first_times = g["time"].first()
    last_times = g["time"].last()
    n_days = g.ngroups

    first_counts = first_times.value_counts()
    last_counts = last_times.value_counts()

    log(f"\n--- Grade horaria real ({symbol}, IS 2021-2024) ---")
    log(f"n dias no IS: {n_days}")
    log("Primeira barra do dia (top 10):")
    for t, c in first_counts.head(10).items():
        log(f"    {t}  {c} dias ({100*c/n_days:.1f}%)")
    log("Ultima barra do dia (top 10):")
    for t, c in last_counts.head(10).items():
        log(f"    {t}  {c} dias ({100*c/n_days:.1f}%)")

    # padrao sazonal (por ano-mes) da ultima barra, pra documentar a anomalia DST-like
    tmp = last_times.reset_index()
    tmp.columns = ["date", "last_time"]
    tmp["date"] = pd.to_datetime(tmp["date"])
    tmp["ym"] = tmp["date"].dt.to_period("M")
    pivot = tmp.groupby(["ym", "last_time"]).size().unstack(fill_value=0)
    # reporta só resumo: quantos meses sao "puros" 17:45 vs "puros" 18:15 vs mistos
    n_bars_1745 = int((pivot.get(pd.to_datetime("17:45:00").time(), pd.Series(dtype=int))).sum()) if False else None

    return dict(
        n_days=n_days,
        first_time_counts={str(k): int(v) for k, v in first_counts.items()},
        last_time_counts={str(k): int(v) for k, v in last_counts.items()},
    )


# ----------------------------------------------------------------------
# CONSTRUCAO DA TABELA DIARIA
# ----------------------------------------------------------------------

def build_daily(df, symbol):
    df = df.copy()
    df["date"] = df["datetime_b3"].dt.date
    df["time"] = df["datetime_b3"].dt.strftime("%H:%M:%S")

    by_date = {d: sub.set_index("time") for d, sub in df.groupby("date")}
    dates_sorted = sorted(by_date.keys())

    daily_open = {}
    daily_high = {}
    daily_low = {}
    daily_close = {}  # close da ULTIMA barra do dia (serve p/ close_prev_day)
    for d, sub in by_date.items():
        daily_open[d] = sub["open"].iloc[0]
        daily_high[d] = sub["high"].max()
        daily_low[d] = sub["low"].min()
        daily_close[d] = sub["close"].iloc[-1]

    rows = []
    n_short_session = 0
    n_no_prevday = 0
    n_missing_0900_0915 = 0
    n_ret_last_fallback = 0
    n_ret_last_missing = 0

    for i, d in enumerate(dates_sorted):
        sub = by_date[d]
        has_0900 = "09:00:00" in sub.index
        has_0915 = "09:15:00" in sub.index
        if not (has_0900 and has_0915):
            n_missing_0900_0915 += 1
            n_short_session += 1
            continue

        open_0900 = sub.loc["09:00:00", "open"]
        close_0915 = sub.loc["09:15:00", "close"]  # = "close as 09:30"
        ret_first = close_0915 / open_0900 - 1.0

        # close do dia anterior (ultima barra disponivel do dia anterior no IS)
        if i == 0:
            n_no_prevday += 1
            continue
        d_prev = dates_sorted[i - 1]
        close_prev_day = daily_close[d_prev]

        ret_on_first = close_0915 / close_prev_day - 1.0
        ret_on = open_0900 / close_prev_day - 1.0

        # ret_last: preferencial close(18:15)/close(17:45) via bar-start convention
        # = close(bar@18:00) / close(bar@17:30). Exige barras @17:30 e @18:00.
        has_1730 = "17:30:00" in sub.index
        has_1800 = "18:00:00" in sub.index
        ret_last = np.nan
        ret_last_method = None
        if has_1730 and has_1800:
            ret_last = sub.loc["18:00:00", "close"] / sub.loc["17:30:00", "close"] - 1.0
            ret_last_method = "standard_1815_1745"
        else:
            # fallback: ultima meia hora real da sessao. Janela de 30min exige
            # closes com 2 barras M15 de distancia (close(T)/close(T-30min)),
            # igual ao metodo padrao (17:30->18:00). Barras adjacentes dariam
            # so 15min (bug corrigido no code review da Fase 0).
            times_before_1820 = [t for t in sub.index if t < "18:20:00"]
            times_before_1820_sorted = sorted(times_before_1820)
            if len(times_before_1820_sorted) >= 3:
                t_last = times_before_1820_sorted[-1]
                t_prev = times_before_1820_sorted[-3]
                ret_last = sub.loc[t_last, "close"] / sub.loc[t_prev, "close"] - 1.0
                ret_last_method = f"fallback_{t_prev}_{t_last}"
                n_ret_last_fallback += 1
            else:
                n_ret_last_missing += 1
                n_short_session += 1
                continue

        # vol regime: range (high-low)/open do dia D-1
        vol_prev = (daily_high[d_prev] - daily_low[d_prev]) / daily_open[d_prev]

        weekday = pd.Timestamp(d).day_name()
        year = pd.Timestamp(d).year

        rows.append(dict(
            date=str(d),
            year=year,
            weekday=weekday,
            open_0900=open_0900,
            close_0915=close_0915,
            close_prev_day=close_prev_day,
            ret_first=ret_first,
            ret_on_first=ret_on_first,
            ret_on=ret_on,
            ret_last=ret_last,
            ret_last_method=ret_last_method,
            vol_prev_range_pct=vol_prev,
        ))

    daily = pd.DataFrame(rows)
    # tercis de vol (sobre a propria amostra IS resultante)
    if len(daily) > 0:
        daily["vol_tercile"] = pd.qcut(daily["vol_prev_range_pct"], 3, labels=["low", "mid", "high"])
    else:
        daily["vol_tercile"] = pd.Series(dtype=str)

    stats = dict(
        n_days_total_is=len(dates_sorted),
        n_days_valid=len(daily),
        n_days_short_session_discarded=n_short_session,
        n_days_missing_0900_or_0915=n_missing_0900_0915,
        n_days_no_prevday=n_no_prevday,
        n_days_ret_last_fallback=n_ret_last_fallback,
        n_days_ret_last_missing=n_ret_last_missing,
    )
    return daily, stats


# ----------------------------------------------------------------------
# ANALISES DA FASE 0 (por variante x mercado)
# ----------------------------------------------------------------------

def analyze_variant(daily, variant, symbol):
    pred = daily[variant].values
    ret_last = daily["ret_last"].values
    mask = np.isfinite(pred) & np.isfinite(ret_last)
    pred_m = pred[mask]
    ret_last_m = ret_last[mask]
    n = len(pred_m)

    # 1. OLS + NW
    ols = ols_nw(pred_m, ret_last_m)

    # 2. hit rate + IC bootstrap
    sign_pred = np.sign(pred_m)
    sign_actual = np.sign(ret_last_m)
    hr = bootstrap_hit_rate(sign_pred, sign_actual)

    # 3. media condicional ao sinal do predictor, em bps, com bootstrap
    cond_ret_bps = np.where(sign_pred >= 0, ret_last_m, -ret_last_m) * 10000.0
    cond = bootstrap_mean_bps(cond_ret_bps)

    # 4a. breakdown por ano
    by_year = {}
    for yr in sorted(daily["year"].unique()):
        sub = daily[(daily["year"] == yr)]
        p = sub[variant].values
        rl = sub["ret_last"].values
        mk = np.isfinite(p) & np.isfinite(rl)
        p, rl = p[mk], rl[mk]
        if len(p) < 5:
            by_year[int(yr)] = dict(n=len(p), slope=np.nan, t_nw=np.nan, hit_rate=np.nan)
            continue
        o = ols_nw(p, rl)
        sgp, sga = np.sign(p), np.sign(rl)
        hrp = (sgp == sga).mean()
        by_year[int(yr)] = dict(n=len(p), slope=o["b"], t_nw=o["t_b"], hit_rate=hrp)

    # 4b. breakdown por tercil de vol
    by_vol = {}
    for terc in ["low", "mid", "high"]:
        sub = daily[daily["vol_tercile"] == terc]
        p = sub[variant].values
        rl = sub["ret_last"].values
        mk = np.isfinite(p) & np.isfinite(rl)
        p, rl = p[mk], rl[mk]
        if len(p) < 5:
            by_vol[terc] = dict(n=len(p), slope=np.nan, t_nw=np.nan, hit_rate=np.nan)
            continue
        o = ols_nw(p, rl)
        sgp, sga = np.sign(p), np.sign(rl)
        hrp = (sgp == sga).mean()
        by_vol[terc] = dict(n=len(p), slope=o["b"], t_nw=o["t_b"], hit_rate=hrp)

    # 4c. breakdown por dia da semana
    by_wd = {}
    for wd in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        sub = daily[daily["weekday"] == wd]
        p = sub[variant].values
        rl = sub["ret_last"].values
        mk = np.isfinite(p) & np.isfinite(rl)
        p, rl = p[mk], rl[mk]
        if len(p) < 5:
            by_wd[wd] = dict(n=len(p), slope=np.nan, t_nw=np.nan, hit_rate=np.nan)
            continue
        o = ols_nw(p, rl)
        sgp, sga = np.sign(p), np.sign(rl)
        hrp = (sgp == sga).mean()
        by_wd[wd] = dict(n=len(p), slope=o["b"], t_nw=o["t_b"], hit_rate=hrp)

    # 5. robustez: excluir top/bottom 1% do predictor
    lo_q, hi_q = np.percentile(pred_m, [1, 99])
    keep = (pred_m >= lo_q) & (pred_m <= hi_q)
    pred_r = pred_m[keep]
    ret_last_r = ret_last_m[keep]
    ols_r = ols_nw(pred_r, ret_last_r)
    sign_pred_r = np.sign(pred_r)
    sign_actual_r = np.sign(ret_last_r)
    hr_r = bootstrap_hit_rate(sign_pred_r, sign_actual_r)
    cond_ret_bps_r = np.where(sign_pred_r >= 0, ret_last_r, -ret_last_r) * 10000.0
    cond_r = bootstrap_mean_bps(cond_ret_bps_r)

    return dict(
        symbol=symbol,
        variant=variant,
        n=n,
        ols=ols,
        hit_rate=hr,
        cond_mean_bps=cond,
        by_year=by_year,
        by_vol_tercile=by_vol,
        by_weekday=by_wd,
        robust_ex_outliers=dict(
            n=len(pred_r),
            ols=ols_r,
            hit_rate=hr_r,
            cond_mean_bps=cond_r,
        ),
    )


# ----------------------------------------------------------------------
# GATES G1-G5
# ----------------------------------------------------------------------

def eval_gates(res):
    ols = res["ols"]
    hr = res["hit_rate"]
    cond = res["cond_mean_bps"]
    by_year = res["by_year"]
    robust = res["robust_ex_outliers"]

    g1 = bool(np.isfinite(ols["t_b"]) and ols["t_b"] >= 2.0 and ols["b"] > 0)

    g2 = bool(np.isfinite(hr["point"]) and hr["point"] >= 0.52 and hr["ci_lo"] > 0.50)

    g3 = bool(np.isfinite(cond["point"]) and abs(cond["point"]) >= 4.0)

    years_positive = sum(1 for y, v in by_year.items() if np.isfinite(v.get("slope", np.nan)) and v["slope"] > 0)
    years_total = sum(1 for y, v in by_year.items() if v.get("n", 0) >= 5)
    g4 = bool(years_total > 0 and years_positive >= 3 and years_total >= 4)

    r_ols = robust["ols"]
    r_hr = robust["hit_rate"]
    r_cond = robust["cond_mean_bps"]
    g5_g1 = bool(np.isfinite(r_ols["t_b"]) and r_ols["t_b"] >= 2.0 and r_ols["b"] > 0)
    g5_g2 = bool(np.isfinite(r_hr["point"]) and r_hr["point"] >= 0.52 and r_hr["ci_lo"] > 0.50)
    g5_g3 = bool(np.isfinite(r_cond["point"]) and abs(r_cond["point"]) >= 4.0)
    g5 = bool(g5_g1 and g5_g2 and g5_g3)

    all_pass = bool(g1 and g2 and g3 and g4 and g5)

    return dict(
        G1_t_nw_ge_2_slope_pos=g1,
        G2_hitrate_ge_52_ci_lo_gt_50=g2,
        G3_abs_cond_mean_ge_4bps=g3,
        G4_slope_pos_in_ge3_of_4_years=g4,
        G5_survives_outlier_exclusion=g5,
        years_positive=years_positive,
        years_total=years_total,
        premise_status="premise_confirmed" if all_pass else "premise_refuted_or_weak",
    )


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def fmt(x, nd=4):
    if x is None:
        return "nan"
    try:
        if isinstance(x, (int, np.integer)):
            return str(x)
        if not np.isfinite(x):
            return "nan"
        return f"{x:.{nd}f}"
    except Exception:
        return str(x)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    log("=" * 78)
    log("t1_intraday_momentum_v0 -- Fase 0 (mapa descritivo)")
    log(f"IS: {IS_START.date()} -> {(IS_END_EXCL - pd.Timedelta(days=1)).date()} (OOS >= {IS_END_EXCL.date()} REMOVIDO logo apos load)")
    log("=" * 78)

    all_results = {"meta": {}, "grid": {}, "daily_stats": {}, "variants": {}, "gates": {}}

    daily_frames = {}

    for symbol in ["WIN", "WDO"]:
        df, meta = load_is(symbol)
        log(f"\n### {symbol} ###")
        log(f"Arquivo bruto: {FILES[symbol]}")
        log(f"Range bruto completo: {meta['full_min']} -> {meta['full_max']} ({meta['n_total_raw']} linhas)")
        log(f"Apos filtro OOS sagrado (IS only): {meta['n_is_rows']} linhas")
        all_results["meta"][symbol] = meta

        grid_info = inspect_grid(df, symbol)
        all_results["grid"][symbol] = grid_info

        daily, dstats = build_daily(df, symbol)
        daily_frames[symbol] = daily
        all_results["daily_stats"][symbol] = dstats

        log(f"\n--- Construcao tabela diaria ({symbol}) ---")
        log(f"dias totais no IS (grupos de data): {dstats['n_days_total_is']}")
        log(f"dias validos (usados nas estatisticas): {dstats['n_days_valid']}")
        log(f"dias descartados (sessao curta / dados faltantes): {dstats['n_days_short_session_discarded']}")
        log(f"  -- faltando barra 09:00 ou 09:15: {dstats['n_days_missing_0900_or_0915']}")
        log(f"  -- ret_last impossivel de calcular mesmo com fallback: {dstats['n_days_ret_last_missing']}")
        log(f"dias sem dia anterior no IS (primeiro dia da serie): {dstats['n_days_no_prevday']}")
        log(f"dias em que ret_last usou fallback (nao 18:15/17:45 padrao): {dstats['n_days_ret_last_fallback']}")

        daily.to_csv(OUT_DAILY[symbol], index=False)
        log(f"Salvo: {OUT_DAILY[symbol]} ({len(daily)} linhas)")

    # ---------------- analises por variante x mercado ----------------
    gate_table_rows = []
    for symbol in ["WIN", "WDO"]:
        daily = daily_frames[symbol]
        all_results["variants"][symbol] = {}
        all_results["gates"][symbol] = {}
        for variant in VARIANTS:
            res = analyze_variant(daily, variant, symbol)
            gates = eval_gates(res)
            all_results["variants"][symbol][variant] = res
            all_results["gates"][symbol][variant] = gates

            log(f"\n{'-'*78}")
            log(f"{symbol} / {VARIANT_LABELS[variant]}")
            log(f"{'-'*78}")
            log(f"n = {res['n']} dias validos")
            o = res["ols"]
            log(f"OLS: slope={fmt(o['b'],6)}  intercept={fmt(o['a'],6)}  t_NW={fmt(o['t_b'],3)}  lag_NW={o['lag']}")
            h = res["hit_rate"]
            log(f"Hit rate sign(pred)==sign(ret_last): {fmt(h['point']*100,2)}%  IC95=[{fmt(h['ci_lo']*100,2)}%, {fmt(h['ci_hi']*100,2)}%]  (n={h['n']})")
            c = res["cond_mean_bps"]
            log(f"Media condicional ret_last (bps, sinal alinhado ao predictor): {fmt(c['point'],2)}  IC95=[{fmt(c['ci_lo'],2)}, {fmt(c['ci_hi'],2)}]")

            log("Breakdown por ano:")
            for yr, v in sorted(res["by_year"].items()):
                log(f"    {yr}: n={v['n']}  slope={fmt(v['slope'],6)}  t_NW={fmt(v['t_nw'],3)}  hit_rate={fmt(v['hit_rate']*100 if np.isfinite(v['hit_rate']) else np.nan,2)}%")

            log("Breakdown por tercil de vol (D-1 range/open):")
            for terc, v in res["by_vol_tercile"].items():
                log(f"    {terc}: n={v['n']}  slope={fmt(v['slope'],6)}  t_NW={fmt(v['t_nw'],3)}  hit_rate={fmt(v['hit_rate']*100 if np.isfinite(v['hit_rate']) else np.nan,2)}%")

            log("Breakdown por dia da semana:")
            for wd, v in res["by_weekday"].items():
                log(f"    {wd}: n={v['n']}  slope={fmt(v['slope'],6)}  t_NW={fmt(v['t_nw'],3)}  hit_rate={fmt(v['hit_rate']*100 if np.isfinite(v['hit_rate']) else np.nan,2)}%")

            r = res["robust_ex_outliers"]
            ro = r["ols"]
            rh = r["hit_rate"]
            rc = r["cond_mean_bps"]
            log(f"Robustez (excluindo top/bottom 1% do predictor, n={r['n']}):")
            log(f"    slope={fmt(ro['b'],6)}  t_NW={fmt(ro['t_b'],3)}  hit_rate={fmt(rh['point']*100,2)}% IC95=[{fmt(rh['ci_lo']*100,2)}%,{fmt(rh['ci_hi']*100,2)}%]  cond_mean_bps={fmt(rc['point'],2)} IC95=[{fmt(rc['ci_lo'],2)},{fmt(rc['ci_hi'],2)}]")

            log("Gates:")
            log(f"    G1 (t_NW>=2.0 & slope>0): {'PASS' if gates['G1_t_nw_ge_2_slope_pos'] else 'FAIL'}")
            log(f"    G2 (hit_rate>=52% & IC_lo>50%): {'PASS' if gates['G2_hitrate_ge_52_ci_lo_gt_50'] else 'FAIL'}")
            log(f"    G3 (|cond_mean|>=4bps): {'PASS' if gates['G3_abs_cond_mean_ge_4bps'] else 'FAIL'}")
            log(f"    G4 (slope>0 em >=3/4 anos): {'PASS' if gates['G4_slope_pos_in_ge3_of_4_years'] else 'FAIL'} ({gates['years_positive']}/{gates['years_total']})")
            log(f"    G5 (sobrevive exclusao outliers): {'PASS' if gates['G5_survives_outlier_exclusion'] else 'FAIL'}")
            log(f"    >>> {gates['premise_status'].upper()} <<<")

            gate_table_rows.append((symbol, variant, gates))

    # ---------------- tabela final ----------------
    log(f"\n{'='*78}")
    log("TABELA FINAL DE GATES (G1-G5) POR VARIANTE x MERCADO")
    log(f"{'='*78}")
    header = f"{'Mercado':<8}{'Variante':<16}{'G1':<6}{'G2':<6}{'G3':<6}{'G4':<6}{'G5':<6}{'STATUS':<24}"
    log(header)
    log("-" * len(header))
    any_confirmed = False
    for symbol, variant, gates in gate_table_rows:
        status = gates["premise_status"]
        if status == "premise_confirmed":
            any_confirmed = True
        row = (f"{symbol:<8}{variant:<16}"
               f"{'PASS' if gates['G1_t_nw_ge_2_slope_pos'] else 'FAIL':<6}"
               f"{'PASS' if gates['G2_hitrate_ge_52_ci_lo_gt_50'] else 'FAIL':<6}"
               f"{'PASS' if gates['G3_abs_cond_mean_ge_4bps'] else 'FAIL':<6}"
               f"{'PASS' if gates['G4_slope_pos_in_ge3_of_4_years'] else 'FAIL':<6}"
               f"{'PASS' if gates['G5_survives_outlier_exclusion'] else 'FAIL':<6}"
               f"{status:<24}")
        log(row)

    log(f"\nRegra de decisao: >=1 variante passa G1-G5 em WIN -> Fase 1 (mecanica) so nessa(s) variante(s).")
    win_confirmed = [v for s, v, g in gate_table_rows if s == "WIN" and g["premise_status"] == "premise_confirmed"]
    wdo_confirmed = [v for s, v, g in gate_table_rows if s == "WDO" and g["premise_status"] == "premise_confirmed"]
    if win_confirmed:
        log(f"RESULTADO: WIN confirma premissa em: {win_confirmed}. WDO confirma em: {wdo_confirmed if wdo_confirmed else 'nenhuma'}.")
        log("-> Avancar para Fase 1 (mecanica) SOMENTE nessas variantes.")
    else:
        log(f"RESULTADO: Nenhuma variante passou G1-G5 em WIN (WDO confirma em: {wdo_confirmed if wdo_confirmed else 'nenhuma'}).")
        log("-> Campanha fecha como premise_refuted. NAO tentar variantes novas post-hoc.")

    all_results["decision"] = dict(
        win_confirmed_variants=win_confirmed,
        wdo_confirmed_variants=wdo_confirmed,
        campaign_status="advance_to_phase1" if win_confirmed else "premise_refuted",
    )

    # ---------------- salvar outputs ----------------
    def _default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o) if np.isfinite(o) else None
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return str(o)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=_default, ensure_ascii=False)
    log(f"\nSalvo: {OUT_JSON}")

    with open(OUT_SUMMARY_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))
    print(f"\nSalvo: {OUT_SUMMARY_TXT}")


if __name__ == "__main__":
    main()
