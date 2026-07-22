"""
t3_b3_ny_open_v0 -- Fase 0 (mapa probabilistico, zero trades)
Pre-registro: mt5/campaigns/t3_b3_ny_open_v0/PREREGISTRATION.md

Script auto-suficiente. So pandas/numpy/pyarrow. Roda com:
    python build_t3_map.py

Convencao de barra: datetime_b3 = INICIO da barra (naive, wall-clock B3,
sem DST -- B3 nao observa horario de verao desde 2019). preco@T = close da
barra que comeca em (T - timeframe). Ex (M5): preco@10:30 = close da barra
com datetime_b3 == 10:25.

DST US calculada em codigo (2a domingo de marco -> 1o domingo de novembro,
data local, sem lib externa). NY open = 10:30 B3 se EDT, 11:30 se EST.

OOS 2025-01-01+ e' removido logo apos carregar cada parquet -- nao entra em
NENHUMA estatistica deste script.
"""

import json
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

DATA_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
PATHS = {
    ("WIN", "M5"): os.path.join(DATA_DIR, "WIN_cont_N_M5.parquet"),
    ("WDO", "M5"): os.path.join(DATA_DIR, "WDO_cont_N_M5.parquet"),
    ("WIN", "M15"): os.path.join(DATA_DIR, "WIN_cont_N_M15.parquet"),
    ("WDO", "M15"): os.path.join(DATA_DIR, "WDO_cont_N_M15.parquet"),
}

OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\t3_b3_ny_open_v0\phase0"

OOS_CUTOFF = pd.Timestamp("2025-01-01")   # NAO TOCAR em 2025+
M15_MIN_DATE = date(2021, 1, 1)
M15_MAX_DATE = date(2024, 12, 31)

SEED = 42
N_BOOT = 1000

MARKETS = ["WIN", "WDO"]

# ----------------------------------------------------------------------
# DST (US) -- 2o domingo de marco -> 1o domingo de novembro
# ----------------------------------------------------------------------

def nth_sunday(year, month, n):
    d = date(year, month, 1)
    days_ahead = (6 - d.weekday()) % 7  # Monday=0 ... Sunday=6
    first_sunday = d + timedelta(days=days_ahead)
    return first_sunday + timedelta(weeks=n - 1)


def is_edt(d):
    """d: python date (dia de pregao B3). True se EUA em EDT nesse dia."""
    dst_start = nth_sunday(d.year, 3, 2)
    dst_end = nth_sunday(d.year, 11, 1)
    return dst_start <= d < dst_end


# ----------------------------------------------------------------------
# Data loading / price lookup
# ----------------------------------------------------------------------

def load_data(path, cutoff=OOS_CUTOFF):
    df = pd.read_parquet(path)
    n_raw = len(df)
    df = df[df["datetime_b3"] < cutoff].copy()
    n_oos_dropped = n_raw - len(df)
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    # safety: so dias uteis (B3 ja' nao deveria ter fds, mas confirmar)
    wd = df["datetime_b3"].dt.weekday
    n_weekend = int((wd >= 5).sum())
    if n_weekend:
        df = df[wd < 5].copy()
    return df, n_raw, n_oos_dropped, n_weekend


def build_close_index(df):
    s = df.set_index("datetime_b3")["close"]
    s = s[~s.index.duplicated(keep="first")]
    return s


def get_close_at(close_idx, T, freq_min):
    """preco@T = close da barra (T - timeframe)."""
    t = T - pd.Timedelta(minutes=freq_min)
    return close_idx.get(t, np.nan)


def ret_x(close_idx, T, freq_min):
    """retorno de <freq_min> minutos terminando em T."""
    p2 = get_close_at(close_idx, T, freq_min)
    p1 = get_close_at(close_idx, T - pd.Timedelta(minutes=freq_min), freq_min)
    if pd.isna(p1) or pd.isna(p2) or p1 == 0:
        return np.nan
    return p2 / p1 - 1.0


def window_vol(close_idx, T0, freq_min, pre=True):
    """media |ret| das barras na janela de 30min (pre ou pos T0)."""
    n_bars = 30 // freq_min  # 6 (M5) ou 2 (M15)
    vals = []
    for k in range(1, n_bars + 1):
        if pre:
            T = T0 - pd.Timedelta(minutes=freq_min * (n_bars - k))
        else:
            T = T0 + pd.Timedelta(minutes=freq_min * k)
        vals.append(ret_x(close_idx, T, freq_min))
    if any(pd.isna(v) for v in vals):
        return np.nan
    return float(np.mean(np.abs(vals)))


# ----------------------------------------------------------------------
# Tabela diaria
# ----------------------------------------------------------------------

def build_daily_table(df, freq_min, min_date=None, max_date=None):
    close_idx = build_close_index(df)
    all_dates = sorted(set(df["datetime_b3"].dt.date))
    dates = all_dates
    if min_date is not None:
        dates = [d for d in dates if d >= min_date]
    if max_date is not None:
        dates = [d for d in dates if d <= max_date]

    rows = []
    discard_reasons = {
        "vol_pre_missing": 0,
        "vol_post_missing": 0,
        "ret_pre_missing": 0,
        "ret_post_missing": 0,
        "vol_pre_zero": 0,
    }
    discarded = 0

    for d in dates:
        edt = is_edt(d)
        hh, mm = (10, 30) if edt else (11, 30)
        T0 = pd.Timestamp(d) + pd.Timedelta(hours=hh, minutes=mm)

        vol_pre = window_vol(close_idx, T0, freq_min, pre=True)
        vol_post = window_vol(close_idx, T0, freq_min, pre=False)

        t_0930 = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30)
        close_T0 = get_close_at(close_idx, T0, freq_min)
        close_0930 = get_close_at(close_idx, t_0930, freq_min)
        close_T0_60 = get_close_at(close_idx, T0 + pd.Timedelta(minutes=60), freq_min)

        ret_pre = np.nan
        if pd.notna(close_T0) and pd.notna(close_0930) and close_0930 != 0:
            ret_pre = close_T0 / close_0930 - 1.0

        ret_post = np.nan
        if pd.notna(close_T0_60) and pd.notna(close_T0) and close_T0 != 0:
            ret_post = close_T0_60 / close_T0 - 1.0

        # placebo 14:30 fixo (sem DST) -- so' usado em freq M5 (analise V3)
        T0_pb = pd.Timestamp(d) + pd.Timedelta(hours=14, minutes=30)
        vol_pre_pb = window_vol(close_idx, T0_pb, freq_min, pre=True)
        vol_post_pb = window_vol(close_idx, T0_pb, freq_min, pre=False)
        vol_ratio_pb = np.nan
        if pd.notna(vol_pre_pb) and pd.notna(vol_post_pb) and vol_pre_pb != 0:
            vol_ratio_pb = vol_post_pb / vol_pre_pb

        bad = False
        if pd.isna(vol_pre):
            discard_reasons["vol_pre_missing"] += 1
            bad = True
        if pd.isna(vol_post):
            discard_reasons["vol_post_missing"] += 1
            bad = True
        if pd.isna(ret_pre):
            discard_reasons["ret_pre_missing"] += 1
            bad = True
        if pd.isna(ret_post):
            discard_reasons["ret_post_missing"] += 1
            bad = True
        if bad:
            discarded += 1
            continue
        if vol_pre == 0:
            discard_reasons["vol_pre_zero"] += 1
            discarded += 1
            continue

        vol_ratio = vol_post / vol_pre

        rows.append(dict(
            date=d, year=d.year, is_edt=edt, T0=T0,
            vol_pre=vol_pre, vol_post=vol_post, vol_ratio=vol_ratio,
            ret_pre=ret_pre, ret_post=ret_post,
            vol_ratio_pb=vol_ratio_pb,
        ))

    tbl = pd.DataFrame(rows)
    total_days = len(dates)
    kept = len(tbl)
    return tbl, dict(total_days=total_days, kept=kept, discarded=discarded,
                      discard_reasons=discard_reasons)


# ----------------------------------------------------------------------
# Event-time profile (M5 apenas)
# ----------------------------------------------------------------------

def event_time_profile(df, min_offset=-60, max_offset=90, step=5):
    close_idx = build_close_index(df)
    dates = sorted(set(df["datetime_b3"].dt.date))
    offsets = list(range(min_offset, max_offset + 1, step))

    recs = []
    for d in dates:
        edt = is_edt(d)
        hh, mm = (10, 30) if edt else (11, 30)
        T0 = pd.Timestamp(d) + pd.Timedelta(hours=hh, minutes=mm)
        for o in offsets:
            r = ret_x(close_idx, T0 + pd.Timedelta(minutes=o), 5)
            recs.append((o, edt, r))

    prof = pd.DataFrame(recs, columns=["offset", "is_edt", "ret"])
    prof["abs_ret"] = prof["ret"].abs()
    agg = prof.groupby(["offset", "is_edt"])["abs_ret"].agg(["mean", "count"]).reset_index()
    return agg


# ----------------------------------------------------------------------
# Estatistica: bootstrap, OLS + Newey-West
# ----------------------------------------------------------------------

def bootstrap_stat_ci(values, stat_fn, n_boot=N_BOOT, seed=SEED):
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    n = len(values)
    if n == 0:
        return dict(point=np.nan, lo=np.nan, hi=np.nan, n=0)
    point = float(stat_fn(values))
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boots[i] = stat_fn(values[idx])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return dict(point=point, lo=float(lo), hi=float(hi), n=n)


def median_ci(values, n_boot=N_BOOT, seed=SEED):
    return bootstrap_stat_ci(values, np.median, n_boot, seed)


def mean_ci(values, n_boot=N_BOOT, seed=SEED):
    return bootstrap_stat_ci(values, np.mean, n_boot, seed)


def cond_spread_ci(ret_pre, ret_post, n_boot=N_BOOT, seed=SEED):
    """
    Metrica de sinal condicional (definicao explicita deste script, pre-reg
    nao especifica formula exata -- ver SUMMARY.txt):
        spread = ( mean(ret_post | ret_pre>0) - mean(ret_post | ret_pre<0) ) / 2
    em bps. Bootstrap reamostra DIAS (pares ret_pre/ret_post conjuntos).
    """
    ret_pre = np.asarray(ret_pre, dtype=float)
    ret_post = np.asarray(ret_post, dtype=float)
    mask = ~(np.isnan(ret_pre) | np.isnan(ret_post))
    ret_pre = ret_pre[mask]
    ret_post = ret_post[mask]
    n = len(ret_pre)

    def stat(rp, rq):
        pos = rq[rp > 0]
        neg = rq[rp < 0]
        if len(pos) == 0 or len(neg) == 0:
            return np.nan
        return (pos.mean() - neg.mean()) / 2.0 * 10000.0  # bps

    point = stat(ret_pre, ret_post)
    pos_mean = ret_post[ret_pre > 0].mean() * 10000.0 if (ret_pre > 0).sum() else np.nan
    neg_mean = ret_post[ret_pre < 0].mean() * 10000.0 if (ret_pre < 0).sum() else np.nan

    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boots[i] = stat(ret_pre[idx], ret_post[idx])
    boots = boots[~np.isnan(boots)]
    if len(boots) == 0:
        lo = hi = np.nan
    else:
        lo, hi = np.percentile(boots, [2.5, 97.5])
    return dict(point=point, lo=float(lo) if len(boots) else np.nan,
                hi=float(hi) if len(boots) else np.nan, n=n,
                pos_mean_bps=float(pos_mean), neg_mean_bps=float(neg_mean),
                n_pos=int((ret_pre > 0).sum()), n_neg=int((ret_pre < 0).sum()))


def ols_nw(y, x, lag=None):
    """OLS y ~ 1 + x com erro padrao Newey-West (Bartlett kernel)."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    mask = ~(np.isnan(y) | np.isnan(x))
    y = y[mask]
    x = x[mask]
    n = len(y)
    if n < 10:
        return dict(n=n, slope=np.nan, intercept=np.nan, t_nw=np.nan,
                     se_nw=np.nan, lag=np.nan)

    X = np.column_stack([np.ones(n), x])
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta

    if lag is None:
        lag = int(np.floor(4 * (n / 100) ** (2 / 9)))
    lag = max(lag, 0)

    scores = X * resid[:, None]
    S = scores.T @ scores
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        Gamma = scores[l:].T @ scores[:-l]
        S += w * (Gamma + Gamma.T)

    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    t_stats = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)

    return dict(n=n, intercept=float(beta[0]), slope=float(beta[1]),
                se_nw=float(se[1]), t_nw=float(t_stats[1]), lag=int(lag))


def trim_1pct(tbl):
    lo, hi = np.percentile(tbl["ret_pre"].dropna(), [1, 99])
    return tbl[(tbl["ret_pre"] >= lo) & (tbl["ret_pre"] <= hi)].copy()


# ----------------------------------------------------------------------
# Analise por mercado
# ----------------------------------------------------------------------

def analyze_market(market):
    out = {"market": market}
    log_lines = []

    def log(s=""):
        print(s)
        log_lines.append(s)

    log("=" * 78)
    log(f"MERCADO: {market}")
    log("=" * 78)

    # ---- M5 IS ----
    path5 = PATHS[(market, "M5")]
    df5, n_raw5, n_oos5, n_wknd5 = load_data(path5)
    log(f"[M5] arquivo: {path5}")
    log(f"[M5] linhas brutas={n_raw5}, removidas OOS(>=2025-01-01)={n_oos5}, "
        f"removidas fim-de-semana(anomalia)={n_wknd5}")
    log(f"[M5] range apos filtro: {df5['datetime_b3'].min()} -> {df5['datetime_b3'].max()}")

    tbl5, meta5 = build_daily_table(df5, freq_min=5)
    log(f"[M5] dias no calendario de dados={meta5['total_days']}, "
        f"validos={meta5['kept']}, descartados={meta5['discarded']}")
    log(f"[M5] motivos de descarte: {meta5['discard_reasons']}")

    n_edt = int(tbl5["is_edt"].sum())
    n_est = int((~tbl5["is_edt"]).sum())
    log(f"[M5] distribuicao EDT/EST: EDT={n_edt}, EST={n_est}")

    tbl5.to_csv(os.path.join(OUT_DIR, f"daily_{market.lower()}_m5.csv"), index=False)

    out["m5_data_meta"] = dict(n_raw=n_raw5, n_oos_dropped=n_oos5,
                                n_weekend_dropped=n_wknd5,
                                date_min=str(df5["datetime_b3"].min()),
                                date_max=str(df5["datetime_b3"].max()))
    out["m5_daily_meta"] = meta5
    out["m5_edt_est"] = dict(n_edt=n_edt, n_est=n_est)

    # ---- Analise 1: V-gate estrutura ----
    log("\n--- Analise 1: V-gate (estrutura, mediana vol_ratio) ---")
    v_overall = median_ci(tbl5["vol_ratio"])
    log(f"V1 mediana vol_ratio (todo IS M5): {v_overall['point']:.4f} "
        f"IC95=[{v_overall['lo']:.4f}, {v_overall['hi']:.4f}] n={v_overall['n']}")
    V1 = v_overall["point"] >= 1.15

    v_by_year = {}
    for yr, g in tbl5.groupby("year"):
        if len(g) >= 60:
            r = median_ci(g["vol_ratio"])
            v_by_year[int(yr)] = r
            log(f"V2  ano={yr} n={len(g)} mediana={r['point']:.4f} "
                f"IC95=[{r['lo']:.4f}, {r['hi']:.4f}]")
        else:
            log(f"    ano={yr} n={len(g)} (<60, nao entra no V2)")
    V2 = all(r["point"] >= 1.10 for r in v_by_year.values()) if v_by_year else False

    v_by_dst = {}
    for edt_flag, g in tbl5.groupby("is_edt"):
        r = median_ci(g["vol_ratio"])
        label = "EDT" if edt_flag else "EST"
        v_by_dst[label] = r
        log(f"    {label} n={len(g)} mediana vol_ratio={r['point']:.4f} "
            f"IC95=[{r['lo']:.4f}, {r['hi']:.4f}]")

    log("\n--- V3: NY open vs placebo 14:30 ---")
    matched = tbl5.dropna(subset=["vol_ratio_pb"])
    n_pb_missing = len(tbl5) - len(matched)
    log(f"dias com placebo valido: {len(matched)} (sem placebo: {n_pb_missing})")
    ny_med = median_ci(matched["vol_ratio"])
    pb_med = median_ci(matched["vol_ratio_pb"])
    log(f"mediana vol_ratio NY-open  = {ny_med['point']:.4f} "
        f"IC95=[{ny_med['lo']:.4f},{ny_med['hi']:.4f}]")
    log(f"mediana vol_ratio placebo  = {pb_med['point']:.4f} "
        f"IC95=[{pb_med['lo']:.4f},{pb_med['hi']:.4f}]")
    V3 = ny_med["point"] > pb_med["point"]

    log("\n--- V4: perfil event-time (pico pos-T0 em EDT e EST?) ---")
    prof = event_time_profile(df5)
    prof.to_csv(os.path.join(OUT_DIR, f"_event_profile_{market.lower()}_m5.csv"), index=False)
    v4_detail = {}
    for edt_flag in [True, False]:
        label = "EDT" if edt_flag else "EST"
        sub = prof[prof["is_edt"] == edt_flag].sort_values("offset")
        if sub.empty or sub["mean"].isna().all():
            v4_detail[label] = dict(peak_offset=None, peak_val=None)
            continue
        peak_row = sub.loc[sub["mean"].idxmax()]
        v4_detail[label] = dict(peak_offset=int(peak_row["offset"]),
                                 peak_val=float(peak_row["mean"]))
        log(f"    {label}: offset de pico |ret5m| = {int(peak_row['offset'])}min "
            f"(valor={peak_row['mean']:.6f})")
    V4 = all(v.get("peak_offset") is not None and v["peak_offset"] > 0
             for v in v4_detail.values())

    log("\n--- Perfil event-time completo (offset: media|ret5m| EDT / EST / n) ---")
    piv = prof.pivot(index="offset", columns="is_edt", values="mean").sort_index()
    cnt = prof.pivot(index="offset", columns="is_edt", values="count").sort_index()
    for o in piv.index:
        edt_v = piv.loc[o, True] if True in piv.columns else np.nan
        est_v = piv.loc[o, False] if False in piv.columns else np.nan
        edt_n = cnt.loc[o, True] if True in cnt.columns else 0
        est_n = cnt.loc[o, False] if False in cnt.columns else 0
        log(f"    offset={o:+4d}min  EDT={edt_v if pd.notna(edt_v) else float('nan'):.6f}(n={edt_n:.0f})  "
            f"EST={est_v if pd.notna(est_v) else float('nan'):.6f}(n={est_n:.0f})")

    V = V1 and V2 and V3 and V4
    log(f"\nV-GATE [{market}]: V1={V1} V2={V2} V3={V3} V4={V4}  ->  V_PASS={V}")

    out["V"] = dict(V1=V1, V2=V2, V3=V3, V4=V4, V_pass=bool(V),
                     v_overall=v_overall, v_by_year={str(k): v for k, v in v_by_year.items()},
                     v_by_dst=v_by_dst, ny_vs_placebo=dict(ny=ny_med, placebo=pb_med,
                                                            n_matched=len(matched),
                                                            n_placebo_missing=int(n_pb_missing)),
                     v4_detail=v4_detail)

    # ---- P-gate (so' se V passar) ----
    P = None
    if V:
        log("\n--- Analise 2: P-gate (sinal ret_post ~ ret_pre) ---")
        reg = ols_nw(tbl5["ret_post"], tbl5["ret_pre"])
        log(f"OLS full IS: n={reg['n']} slope={reg['slope']:.4f} "
            f"intercept={reg['intercept']:.6f} t_NW={reg['t_nw']:.3f} lag={reg['lag']}")
        sign_label = "momentum" if reg["slope"] > 0 else "reversao"
        log(f"sinal do slope: {sign_label}")
        P1 = abs(reg["t_nw"]) >= 2.0 if pd.notna(reg["t_nw"]) else False

        cond = cond_spread_ci(tbl5["ret_pre"], tbl5["ret_post"])
        log(f"cond. spread (def: (mean(ret_post|pre>0)-mean(ret_post|pre<0))/2, bps) = "
            f"{cond['point']:.3f} IC95=[{cond['lo']:.3f},{cond['hi']:.3f}]")
        log(f"    mean ret_post | ret_pre>0 (n={cond['n_pos']}) = {cond['pos_mean_bps']:.3f} bps")
        log(f"    mean ret_post | ret_pre<0 (n={cond['n_neg']}) = {cond['neg_mean_bps']:.3f} bps")
        P2 = (abs(cond["point"]) >= 4.0 and pd.notna(cond["lo"]) and pd.notna(cond["hi"])
              and not (cond["lo"] <= 0 <= cond["hi"]))

        log("\n--- P3: consistencia de sinal por ano (n>=60) ---")
        p_by_year = {}
        signs = []
        for yr, g in tbl5.groupby("year"):
            if len(g) >= 60:
                r = ols_nw(g["ret_post"], g["ret_pre"])
                p_by_year[int(yr)] = r
                signs.append(np.sign(r["slope"]) if pd.notna(r["slope"]) else 0)
                log(f"    ano={yr} n={r['n']} slope={r['slope']:.4f} t_NW={r['t_nw']:.3f}")
        P3 = len(signs) > 0 and all(s == signs[0] and s != 0 for s in signs) and np.sign(reg["slope"]) == signs[0]

        log("\n--- P4: robustez, exclusao top/bottom 1% ret_pre ---")
        tbl5_trim = trim_1pct(tbl5)
        reg_trim = ols_nw(tbl5_trim["ret_post"], tbl5_trim["ret_pre"])
        cond_trim = cond_spread_ci(tbl5_trim["ret_pre"], tbl5_trim["ret_post"])
        log(f"trimmed n={reg_trim['n']} slope={reg_trim['slope']:.4f} t_NW={reg_trim['t_nw']:.3f}")
        log(f"trimmed cond spread={cond_trim['point']:.3f} bps "
            f"IC95=[{cond_trim['lo']:.3f},{cond_trim['hi']:.3f}]")
        P1_trim = abs(reg_trim["t_nw"]) >= 2.0 if pd.notna(reg_trim["t_nw"]) else False
        P2_trim = (abs(cond_trim["point"]) >= 4.0 and pd.notna(cond_trim["lo"]) and pd.notna(cond_trim["hi"])
                   and not (cond_trim["lo"] <= 0 <= cond_trim["hi"]))
        P4 = P1_trim and P2_trim and (np.sign(reg_trim["slope"]) == np.sign(reg["slope"]))
        log(f"P4 (sobrevive exclusao outliers, mesmo sinal + P1/P2 mantidos) = {P4}")

        log("\n--- P5: grade M15 IS 2021-2024 (sinal do slope) ---")
        path15 = PATHS[(market, "M15")]
        df15, n_raw15, n_oos15, n_wknd15 = load_data(path15)
        log(f"[M15] arquivo: {path15}")
        log(f"[M15] linhas brutas={n_raw15}, removidas OOS={n_oos15}, "
            f"removidas fim-de-semana={n_wknd15}")
        tbl15, meta15 = build_daily_table(df15, freq_min=15,
                                           min_date=M15_MIN_DATE, max_date=M15_MAX_DATE)
        log(f"[M15] janela 2021-2024: dias={meta15['total_days']} validos={meta15['kept']} "
            f"descartados={meta15['discarded']}")
        log(f"[M15] motivos de descarte: {meta15['discard_reasons']}")
        v_m15 = median_ci(tbl15["vol_ratio"])
        log(f"[M15] mediana vol_ratio = {v_m15['point']:.4f} "
            f"IC95=[{v_m15['lo']:.4f},{v_m15['hi']:.4f}] n={v_m15['n']}")
        reg15 = ols_nw(tbl15["ret_post"], tbl15["ret_pre"])
        log(f"[M15] OLS slope={reg15['slope']:.4f} t_NW={reg15['t_nw']:.3f} n={reg15['n']}")
        P5 = (pd.notna(reg15["slope"]) and pd.notna(reg["slope"])
              and np.sign(reg15["slope"]) == np.sign(reg["slope"]) and np.sign(reg["slope"]) != 0)
        log(f"P5 (mesmo sinal M5 IS vs M15 grade) = {P5}")

        Ppass = bool(P1 and P2 and P3 and P4 and P5)
        log(f"\nP-GATE [{market}]: P1={P1} P2={P2} P3={P3} P4={P4} P5={P5}  ->  P_PASS={Ppass}")

        P = dict(P1=bool(P1), P2=bool(P2), P3=bool(P3), P4=bool(P4), P5=bool(P5),
                  P_pass=Ppass, sign=sign_label,
                  reg_full=reg, cond_full=cond,
                  p_by_year={str(k): v for k, v in p_by_year.items()},
                  reg_trimmed=reg_trim, cond_trimmed=cond_trim,
                  m15=dict(meta=meta15, vol_ratio=v_m15, reg=reg15))
    else:
        log("\nV-gate falhou -> P-gate NAO avaliado (regra pre-registrada).")

    if V and P and P["P_pass"]:
        decision = "advance_to_phase1"
    elif V:
        decision = "premise_weak_structure_only"
    else:
        decision = "premise_refuted"

    log(f"\n>>> DECISAO [{market}]: {decision}")

    out["P"] = P
    out["decision"] = decision
    out["log"] = log_lines
    return out


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    summary_lines = []

    def slog(s=""):
        print(s)
        summary_lines.append(s)

    slog("t3_b3_ny_open_v0 -- Fase 0 (mapa probabilistico)")
    slog(f"seed={SEED} n_boot={N_BOOT} OOS_CUTOFF={OOS_CUTOFF.date()}")
    slog("")

    results = {}
    for market in MARKETS:
        r = analyze_market(market)
        results[market] = r
        summary_lines.extend(r["log"])
        summary_lines.append("")

    slog("=" * 78)
    slog("RESUMO FINAL")
    slog("=" * 78)
    any_advance = False
    any_weak = False
    for market in MARKETS:
        d = results[market]["decision"]
        slog(f"{market}: {d}")
        if d == "advance_to_phase1":
            any_advance = True
        elif d == "premise_weak_structure_only":
            any_weak = True

    if any_advance:
        overall = "ADVANCE_TO_PHASE1 (>=1 mercado com V+P completos)"
    elif any_weak:
        overall = "PREMISE_WEAK_STRUCTURE_ONLY (estrutura sem sinal tradeavel em nenhum mercado)"
    else:
        overall = "PREMISE_REFUTED (V-gate falhou em ambos os mercados)"
    slog(f"\nDECISAO GERAL DA CAMPANHA: {overall}")

    # ---- salvar outputs ----
    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, bool):
            return o
        return o

    json_out = {}
    for market in MARKETS:
        r = dict(results[market])
        r.pop("log", None)
        json_out[market] = clean(r)
    json_out["overall_decision"] = overall

    with open(os.path.join(OUT_DIR, "t3_map_summary.json"), "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    with open(os.path.join(OUT_DIR, "SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    print(f"\nOutputs salvos em: {OUT_DIR}")


if __name__ == "__main__":
    main()
