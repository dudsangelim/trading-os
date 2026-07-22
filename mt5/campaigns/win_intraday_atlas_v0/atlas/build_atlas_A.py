"""
win_intraday_atlas_v0 -- Bateria A (estrutura temporal), script auto-suficiente.
Protocolo: mt5/campaigns/win_intraday_atlas_v0/PROTOCOL.md

Roda com:
    python build_atlas_A.py

Convencoes (identicas as campanhas T1/T2/T3):
- datetime_b3 = INICIO da barra, naive, wall-clock B3 (B3 nao observa DST
  desde 2019). preco@T = close(barra que comeca em T - timeframe).
- DST US calculada em codigo (2o domingo de marco -> 1o domingo de novembro).
- OOS 2025-01-01+ e' removido logo apos carregar cada parquet -- NUNCA entra
  em nenhuma estatistica deste script.
- Splits: DISCOVERY = ate 2023-12-31 (inclusive). CONFIRM = ano 2024
  inteiro. Tudo calculado e rotulado separadamente nos dois splits.
- Fim de sessao variavel ate 2023 (DST US): usado o fim REAL por dia,
  lido diretamente da grade de dados (nao inferido por regra fixa).
- Quarta de Cinzas (sessao abre 13:00) descartada inteira -- dias que nao
  abrem 09:00 sao removidos do calendario valido.
- Zero trades simulados. So descritivo (medias, correlacoes, OLS, dist).
- Bootstrap 1000x seed 42 quando reportado IC. t_NW (Newey-West, Bartlett
  kernel) com lag = floor(4*(n/100)**(2/9)).
"""

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

pd.set_option("display.width", 160)

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

DATA_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
PATH_M15 = os.path.join(DATA_DIR, "WIN_cont_N_M15.parquet")
PATH_M5 = os.path.join(DATA_DIR, "WIN_cont_N_M5.parquet")

OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\win_intraday_atlas_v0\atlas"

OOS_CUTOFF = pd.Timestamp("2025-01-01")     # NAO TOCAR em 2025+
DISCOVERY_END = date(2023, 12, 31)
CONFIRM_START = date(2024, 1, 1)
CONFIRM_END = date(2024, 12, 31)

SEED = 42
N_BOOT = 1000

SESSION_OPEN_TIME = pd.Timestamp("09:00:00").time()

# ----------------------------------------------------------------------
# DST (US) -- 2o domingo de marco -> 1o domingo de novembro
# ----------------------------------------------------------------------


def nth_sunday(year, month, n):
    d = date(year, month, 1)
    days_ahead = (6 - d.weekday()) % 7
    first_sunday = d + timedelta(days=days_ahead)
    return first_sunday + timedelta(weeks=n - 1)


def is_edt(d):
    dst_start = nth_sunday(d.year, 3, 2)
    dst_end = nth_sunday(d.year, 11, 1)
    return dst_start <= d < dst_end


# ----------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------


def load_data(path, cutoff=OOS_CUTOFF):
    df = pd.read_parquet(path)
    n_raw = len(df)
    df = df[df["datetime_b3"] < cutoff].copy()
    n_oos_dropped = n_raw - len(df)
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    wd = df["datetime_b3"].dt.weekday
    n_weekend = int((wd >= 5).sum())
    if n_weekend:
        df = df[wd < 5].copy().reset_index(drop=True)
    return df, dict(n_raw=n_raw, n_oos_dropped=n_oos_dropped, n_weekend_dropped=n_weekend)


def build_session_calendar(df):
    """Um registro por dia: hora de abertura/fechamento REAIS (lidas da
    grade), n_bars. Usado pra achar dias de Cinzas (abre 13:00) e o fim
    real de sessao por dia (variavel ate 2023 por causa da DST US)."""
    d = df.copy()
    d["date"] = d["datetime_b3"].dt.date
    g = d.groupby("date")["datetime_b3"].agg(open_dt="min", close_dt="max", n_bars="count")
    g = g.reset_index()
    g["open_time"] = g["open_dt"].dt.time
    g["close_time"] = g["close_dt"].dt.time
    g["year"] = pd.to_datetime(g["date"]).dt.year
    g["is_edt"] = g["date"].apply(is_edt)
    return g


def valid_day_set(calendar):
    """Dias validos = abrem exatamente as 09:00 (descarta Cinzas)."""
    ok = calendar[calendar["open_time"] == SESSION_OPEN_TIME]
    bad = calendar[calendar["open_time"] != SESSION_OPEN_TIME]
    return set(ok["date"]), bad


def split_label(d, discovery_end=DISCOVERY_END, confirm_start=CONFIRM_START, confirm_end=CONFIRM_END):
    if d <= discovery_end:
        return "DISCOVERY"
    if confirm_start <= d <= confirm_end:
        return "CONFIRM"
    return None  # nao deveria ocorrer pos-filtro OOS


# ----------------------------------------------------------------------
# Price index helpers (preco@T = close(barra T-tf))
# ----------------------------------------------------------------------


def build_close_index(df):
    s = df.set_index("datetime_b3")["close"]
    s = s[~s.index.duplicated(keep="first")]
    return s


def get_close_at(close_idx, T, freq_min):
    t = T - pd.Timedelta(minutes=freq_min)
    return close_idx.get(t, np.nan)


def ret_period(close_idx, t0, t1, freq_min):
    """retorno entre preco@t0 e preco@t1 (t1 > t0), usando a convencao
    preco@T = close(barra T-tf)."""
    p0 = get_close_at(close_idx, t0, freq_min)
    p1 = get_close_at(close_idx, t1, freq_min)
    if pd.isna(p0) or pd.isna(p1) or p0 == 0:
        return np.nan
    return p1 / p0 - 1.0


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


def mean_ci(values, n_boot=N_BOOT, seed=SEED):
    return bootstrap_stat_ci(values, np.mean, n_boot, seed)


def median_ci(values, n_boot=N_BOOT, seed=SEED):
    return bootstrap_stat_ci(values, np.median, n_boot, seed)


def cond_spread_ci(x, y, n_boot=N_BOOT, seed=SEED):
    """spread = (mean(y|x>0) - mean(y|x<0))/2 em bps. Bootstrap reamostra
    pares (x,y) conjuntos (dias)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x = x[mask]
    y = y[mask]
    n = len(x)

    def stat(xx, yy):
        pos = yy[xx > 0]
        neg = yy[xx < 0]
        if len(pos) == 0 or len(neg) == 0:
            return np.nan
        return (pos.mean() - neg.mean()) / 2.0 * 10000.0

    point = stat(x, y)
    pos_mean = y[x > 0].mean() * 10000.0 if (x > 0).sum() else np.nan
    neg_mean = y[x < 0].mean() * 10000.0 if (x < 0).sum() else np.nan
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boots[i] = stat(x[idx], y[idx])
    boots = boots[~np.isnan(boots)]
    lo, hi = (np.percentile(boots, [2.5, 97.5]) if len(boots) else (np.nan, np.nan))
    return dict(point=point, lo=float(lo) if len(boots) else np.nan,
                hi=float(hi) if len(boots) else np.nan, n=n,
                pos_mean_bps=float(pos_mean), neg_mean_bps=float(neg_mean),
                n_pos=int((x > 0).sum()), n_neg=int((x < 0).sum()))


def ols_nw(y, x, lag=None):
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    mask = ~(np.isnan(y) | np.isnan(x))
    y = y[mask]
    x = x[mask]
    n = len(y)
    if n < 10:
        return dict(n=n, slope=np.nan, intercept=np.nan, t_nw=np.nan, se_nw=np.nan, lag=np.nan)

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


def pearson_r(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 10:
        return dict(n=n, r=np.nan)
    r = float(np.corrcoef(x, y)[0, 1])
    return dict(n=n, r=r)


# ----------------------------------------------------------------------
# Logger
# ----------------------------------------------------------------------

LOG_LINES = []


def log(s=""):
    print(s)
    LOG_LINES.append(s)


# ========================================================================
# Preparacao geral: calendario, dias validos, splits
# ========================================================================


def prepare(path, freq_min, label):
    df, meta = load_data(path)
    log(f"[{label}] arquivo: {path}")
    log(f"[{label}] linhas brutas={meta['n_raw']}, removidas OOS(>=2025-01-01)="
        f"{meta['n_oos_dropped']}, removidas fim-de-semana(anomalia)={meta['n_weekend_dropped']}")
    log(f"[{label}] range apos filtro: {df['datetime_b3'].min()} -> {df['datetime_b3'].max()}")

    cal = build_session_calendar(df)
    valid_dates, bad_cal = valid_day_set(cal)
    log(f"[{label}] dias no calendario={len(cal)}, validos(abrem 09:00)={len(valid_dates)}, "
        f"descartados(Cinzas/anomalia abertura)={len(bad_cal)}")
    if len(bad_cal):
        for _, r in bad_cal.iterrows():
            log(f"    descartado: {r['date']} abre {r['open_time']} fecha {r['close_time']} n_bars={r['n_bars']}")

    close_time_counts = cal[cal["date"].isin(valid_dates)]["close_time"].value_counts()
    log(f"[{label}] distribuicao de horario de FECHAMENTO real por dia (dias validos): "
        f"{dict(close_time_counts)}")

    df_valid = df[df["datetime_b3"].dt.date.isin(valid_dates)].copy().reset_index(drop=True)
    cal_valid = cal[cal["date"].isin(valid_dates)].copy().reset_index(drop=True)
    cal_valid["split"] = cal_valid["date"].apply(split_label)

    return df_valid, cal_valid, freq_min


# ========================================================================
# A1 -- Perfil de vol por horario
# ========================================================================


def analyze_A1(df15, cal15, df5, cal5):
    log("\n" + "=" * 78)
    log("A1 -- Perfil de volatilidade por horario (media |ret| por barra)")
    log("=" * 78)

    records = []

    def bar_returns(df, cal, freq_min):
        d = df.copy()
        d["date"] = d["datetime_b3"].dt.date
        d = d.sort_values(["date", "datetime_b3"])
        d["ret"] = d.groupby("date")["close"].pct_change()
        d["time_slot"] = d["datetime_b3"].dt.strftime("%H:%M")
        d = d.merge(cal[["date", "split", "is_edt"]], on="date", how="left")
        return d.dropna(subset=["ret"])

    d15 = bar_returns(df15, cal15, 15)
    d5 = bar_returns(df5, cal5, 5)

    def emit(d, tf_label, split_name, sub_label, sub_mask):
        sub = d[d["split"] == split_name]
        if sub_mask is not None:
            sub = sub[sub_mask(sub)]
        if sub.empty:
            return
        g = sub.groupby("time_slot")["ret"].agg(mean_abs=lambda s: float(np.mean(np.abs(s))), n="count")
        n_days = sub["date"].nunique() if "date" in sub.columns else np.nan
        log(f"\n--- {tf_label} | split={split_name} | subset={sub_label} | dias={n_days} ---")
        for slot, row in g.iterrows():
            records.append(dict(timeframe=tf_label, split=split_name, subset=sub_label,
                                 time_slot=slot, mean_abs_ret_bps=row["mean_abs"] * 10000.0,
                                 n_bars=int(row["n"])))
        top5 = g.sort_values("mean_abs", ascending=False).head(5)
        log(f"    top-5 horarios por vol: " +
            ", ".join(f"{s}={v*10000:.2f}bps(n={int(n)})" for s, v, n in
                      zip(top5.index, top5["mean_abs"], top5["n"])))
        bot5 = g.sort_values("mean_abs", ascending=True).head(5)
        log(f"    bottom-5 horarios por vol: " +
            ", ".join(f"{s}={v*10000:.2f}bps(n={int(n)})" for s, v, n in
                      zip(bot5.index, bot5["mean_abs"], bot5["n"])))

    # M15 -- todo o periodo por split, e subset EDT/EST
    for split_name in ["DISCOVERY", "CONFIRM"]:
        emit(d15, "M15", split_name, "ALL", None)
        emit(d15, "M15", split_name, "EDT", lambda s: s["is_edt"])
        emit(d15, "M15", split_name, "EST", lambda s: ~s["is_edt"])

    # M5 -- 2023 discovery-parcial (dados comecam 2022-12-14) e 2024 confirm
    d5_disc_partial = d5[(d5["date"] >= date(2022, 12, 14)) & (d5["date"] <= DISCOVERY_END)]
    log(f"\n[M5] janela DISCOVERY-PARCIAL usada: 2022-12-14 -> {DISCOVERY_END} "
        f"(M5 nao cobre o periodo 2021-2022 inteiro; ver PROTOCOL.md)")
    for split_name, sub_df in [("DISCOVERY_PARTIAL", d5_disc_partial),
                               ("CONFIRM", d5[d5["split"] == "CONFIRM"])]:
        if sub_df.empty:
            continue
        g = sub_df.groupby("time_slot")["ret"].agg(mean_abs=lambda s: float(np.mean(np.abs(s))), n="count")
        n_days = sub_df["date"].nunique()
        log(f"\n--- M5 | split={split_name} | subset=ALL | dias={n_days} ---")
        for slot, row in g.iterrows():
            records.append(dict(timeframe="M5", split=split_name, subset="ALL",
                                 time_slot=slot, mean_abs_ret_bps=row["mean_abs"] * 10000.0,
                                 n_bars=int(row["n"])))
        top5 = g.sort_values("mean_abs", ascending=False).head(5)
        log(f"    top-5 horarios por vol: " +
            ", ".join(f"{s}={v*10000:.2f}bps(n={int(n)})" for s, v, n in
                      zip(top5.index, top5["mean_abs"], top5["n"])))

        for edt_flag, lbl in [(True, "EDT"), (False, "EST")]:
            sub2 = sub_df[sub_df["is_edt"] == edt_flag]
            if sub2.empty:
                continue
            g2 = sub2.groupby("time_slot")["ret"].agg(mean_abs=lambda s: float(np.mean(np.abs(s))), n="count")
            n_days2 = sub2["date"].nunique()
            log(f"--- M5 | split={split_name} | subset={lbl} | dias={n_days2} ---")
            for slot, row in g2.iterrows():
                records.append(dict(timeframe="M5", split=split_name, subset=lbl,
                                     time_slot=slot, mean_abs_ret_bps=row["mean_abs"] * 10000.0,
                                     n_bars=int(row["n"])))

    out = pd.DataFrame(records)
    out.to_csv(os.path.join(OUT_DIR, "A_vol_profile.csv"), index=False)
    log(f"\n[A1] salvo A_vol_profile.csv ({len(out)} linhas)")
    return out


# ========================================================================
# A2 -- Perfil de volume por horario, por ano
# ========================================================================


def analyze_A2(df15, cal15):
    log("\n" + "=" * 78)
    log("A2 -- Perfil de real_volume por horario, por ano (M15)")
    log("=" * 78)

    d = df15.copy()
    d["date"] = d["datetime_b3"].dt.date
    d["time_slot"] = d["datetime_b3"].dt.strftime("%H:%M")
    d = d.merge(cal15[["date", "split", "year"]], on="date", how="left")

    records = []
    for yr, sub in d.groupby("year"):
        g = sub.groupby("time_slot")["real_volume"].agg(["mean", "count"])
        n_days = sub["date"].nunique()
        log(f"\n--- ano={yr} (dias={n_days}) ---")
        top5 = g.sort_values("mean", ascending=False).head(5)
        log(f"    top-5 horarios por volume medio: " +
            ", ".join(f"{s}={v:,.0f}(n={int(n)})" for s, v, n in
                      zip(top5.index, top5["mean"], top5["count"])))
        for slot, row in g.iterrows():
            records.append(dict(year=int(yr), time_slot=slot,
                                 mean_real_volume=float(row["mean"]), n_bars=int(row["count"])))

    out = pd.DataFrame(records)
    out.to_csv(os.path.join(OUT_DIR, "A_volume_profile.csv"), index=False)
    log(f"\n[A2] salvo A_volume_profile.csv ({len(out)} linhas)")
    return out


# ========================================================================
# A3 -- Autocorrelacao local (ret pre-30m vs ret pos-30m), por hora cheia
# ========================================================================


def analyze_A3(df15, cal15):
    log("\n" + "=" * 78)
    log("A3 -- Autocorrelacao local por hora cheia (10h-17h), M15, t_NW")
    log("=" * 78)

    close_idx = build_close_index(df15)
    valid_dates = set(cal15["date"])
    date_split = dict(zip(cal15["date"], cal15["split"]))

    records_raw = []
    for d in sorted(valid_dates):
        split = date_split[d]
        for H in range(10, 18):
            T = pd.Timestamp(d) + pd.Timedelta(hours=H)
            pre = ret_period(close_idx, T - pd.Timedelta(minutes=30), T, 15)
            post = ret_period(close_idx, T, T + pd.Timedelta(minutes=30), 15)
            records_raw.append(dict(date=d, split=split, hour=H, pre=pre, post=post))

    raw = pd.DataFrame(records_raw)

    out_records = []
    for split_name in ["DISCOVERY", "CONFIRM"]:
        log(f"\n--- split={split_name} ---")
        sub = raw[raw["split"] == split_name]
        for H, g in sub.groupby("hour"):
            reg = ols_nw(g["post"], g["pre"])
            cond = cond_spread_ci(g["pre"], g["post"])
            sign_lbl = "momentum" if pd.notna(reg["slope"]) and reg["slope"] > 0 else (
                "reversao" if pd.notna(reg["slope"]) and reg["slope"] < 0 else "?")
            log(f"    H={H:02d}:00  n={reg['n']:4d}  slope={reg['slope']:+.4f}  "
                f"t_NW={reg['t_nw']:+.3f} (lag={reg['lag']})  [{sign_lbl}]  "
                f"cond_spread={cond['point']:+.3f}bps IC95=[{cond['lo']:+.3f},{cond['hi']:+.3f}]  "
                f"mean(post|pre>0)={cond['pos_mean_bps']:+.3f}bps(n={cond['n_pos']})  "
                f"mean(post|pre<0)={cond['neg_mean_bps']:+.3f}bps(n={cond['n_neg']})")
            out_records.append(dict(
                split=split_name, hour=H, n=reg["n"], slope=reg["slope"], t_nw=reg["t_nw"],
                lag=reg["lag"], sign=sign_lbl,
                cond_spread_bps=cond["point"], cond_lo_bps=cond["lo"], cond_hi_bps=cond["hi"],
                pos_mean_bps=cond["pos_mean_bps"], neg_mean_bps=cond["neg_mean_bps"],
                n_pos=cond["n_pos"], n_neg=cond["n_neg"],
            ))

    out = pd.DataFrame(out_records)
    out.to_csv(os.path.join(OUT_DIR, "A_autocorr.csv"), index=False)
    log(f"\n[A3] salvo A_autocorr.csv ({len(out)} linhas)")

    # flag de anomalia: |t_NW|>=2 com IC95 excluindo 0
    log("\n--- A3 candidatos (|t_NW|>=2 E IC95 cond_spread exclui 0) ---")
    cand = out[(out["t_nw"].abs() >= 2.0) & ~((out["cond_lo_bps"] <= 0) & (out["cond_hi_bps"] >= 0))]
    if cand.empty:
        log("    nenhum")
    else:
        for _, r in cand.iterrows():
            log(f"    split={r['split']} H={r['hour']:02d}:00 t_NW={r['t_nw']:+.3f} "
                f"cond_spread={r['cond_spread_bps']:+.3f}bps IC95=[{r['cond_lo_bps']:+.3f},{r['cond_hi_bps']:+.3f}] n={r['n']}")

    return out


# ========================================================================
# A4 -- Horario de HOD/LOD
# ========================================================================


def analyze_A4(df15, cal15):
    log("\n" + "=" * 78)
    log("A4 -- Horario da maxima (HOD) e minima (LOD) do dia, M15")
    log("=" * 78)

    d = df15.copy()
    d["date"] = d["datetime_b3"].dt.date
    date_split = dict(zip(cal15["date"], cal15["split"]))

    rows = []
    for dt, g in d.groupby("date"):
        g = g.sort_values("datetime_b3")
        hod_row = g.loc[g["high"].idxmax()]
        lod_row = g.loc[g["low"].idxmin()]
        rows.append(dict(date=dt, split=date_split.get(dt),
                          hod_time=hod_row["datetime_b3"].time(), hod_hour=hod_row["datetime_b3"].hour,
                          lod_time=lod_row["datetime_b3"].time(), lod_hour=lod_row["datetime_b3"].hour))
    tbl = pd.DataFrame(rows)

    out_records = []
    for split_name in ["DISCOVERY", "CONFIRM"]:
        sub = tbl[tbl["split"] == split_name]
        n = len(sub)
        log(f"\n--- split={split_name} (n_dias={n}) ---")

        hod_dist = sub["hod_hour"].value_counts().sort_index()
        lod_dist = sub["lod_hour"].value_counts().sort_index()
        log(f"    distribuicao HOD por hora: {dict(hod_dist)}")
        log(f"    distribuicao LOD por hora: {dict(lod_dist)}")
        for h in sorted(set(hod_dist.index) | set(lod_dist.index)):
            hc = int(hod_dist.get(h, 0))
            lc = int(lod_dist.get(h, 0))
            out_records.append(dict(split=split_name, hour=int(h),
                                     hod_count=hc, hod_pct=100.0 * hc / n if n else np.nan,
                                     lod_count=lc, lod_pct=100.0 * lc / n if n else np.nan, n_days=n))

        first_hour_hod = int((sub["hod_hour"] == 9).sum())
        first_hour_lod = int((sub["lod_hour"] == 9).sum())
        both_first_hour = int(((sub["hod_hour"] == 9) & (sub["lod_hour"] == 9)).sum())
        log(f"    % HOD na 1a hora (09h): {100.0*first_hour_hod/n:.1f}% (n={first_hour_hod}/{n})")
        log(f"    % LOD na 1a hora (09h): {100.0*first_hour_lod/n:.1f}% (n={first_hour_lod}/{n})")
        log(f"    % AMBOS (HOD e LOD) na 1a hora: {100.0*both_first_hour/n:.1f}% (n={both_first_hour}/{n})")
        out_records.append(dict(split=split_name, hour=-1,
                                 hod_count=first_hour_hod, hod_pct=100.0*first_hour_hod/n if n else np.nan,
                                 lod_count=first_hour_lod, lod_pct=100.0*first_hour_lod/n if n else np.nan,
                                 n_days=n, note="SUMMARY_1a_hora"))
        out_records.append(dict(split=split_name, hour=-2,
                                 hod_count=both_first_hour, hod_pct=100.0*both_first_hour/n if n else np.nan,
                                 lod_count=np.nan, lod_pct=np.nan, n_days=n, note="AMBOS_1a_hora"))

    out = pd.DataFrame(out_records)
    out.to_csv(os.path.join(OUT_DIR, "A_hod_lod.csv"), index=False)
    log(f"\n[A4] salvo A_hod_lod.csv ({len(out)} linhas)")
    return out


# ========================================================================
# A5 -- Tipologia diaria
# ========================================================================


def compute_daily_ohlc(df15, cal15):
    d = df15.copy()
    d["date"] = d["datetime_b3"].dt.date
    close_idx = build_close_index(df15)

    rows = []
    for dt, g in d.groupby("date"):
        g = g.sort_values("datetime_b3")
        day_open = float(g.iloc[0]["open"])
        day_high = float(g["high"].max())
        day_low = float(g["low"].min())
        day_close = float(g.iloc[-1]["close"])
        day_range = day_high - day_low
        close_pos = (day_close - day_low) / day_range if day_range > 0 else np.nan

        T_1000 = pd.Timestamp(dt) + pd.Timedelta(hours=10)
        price_1000 = get_close_at(close_idx, T_1000, 15)
        first_hour_ret = (price_1000 / day_open - 1.0) if (pd.notna(price_1000) and day_open != 0) else np.nan

        first_hour_bars = g[g["datetime_b3"].dt.hour == 9]
        first_hour_range = (float(first_hour_bars["high"].max()) - float(first_hour_bars["low"].min())
                             if not first_hour_bars.empty else np.nan)

        rest_of_day_ret = (day_close / price_1000 - 1.0) if (pd.notna(price_1000) and price_1000 != 0) else np.nan

        rows.append(dict(date=dt, open=day_open, high=day_high, low=day_low, close=day_close,
                          range=day_range, close_pos=close_pos, first_hour_ret=first_hour_ret,
                          first_hour_range=first_hour_range, price_1000=price_1000,
                          rest_of_day_ret=rest_of_day_ret))
    tbl = pd.DataFrame(rows)
    tbl = tbl.merge(cal15[["date", "split", "year"]], on="date", how="left")
    return tbl


def classify_typology(tbl):
    tbl = tbl.copy()
    tbl["type"] = "mixed"
    for split_name in tbl["split"].dropna().unique():
        mask_split = tbl["split"] == split_name
        med_range = tbl.loc[mask_split, "range"].median()
        cp = tbl["close_pos"]
        trend_up = mask_split & (cp >= 0.9) & (tbl["range"] >= med_range)
        trend_down = mask_split & (cp <= 0.1) & (tbl["range"] >= med_range)
        range_day = mask_split & (cp >= 0.3) & (cp <= 0.7)
        tbl.loc[range_day, "type"] = "range"
        tbl.loc[trend_down, "type"] = "trend_down"
        tbl.loc[trend_up, "type"] = "trend_up"
        tbl.loc[mask_split, "median_range_split"] = med_range
    return tbl


def analyze_A5(df15, cal15):
    log("\n" + "=" * 78)
    log("A5 -- Tipologia diaria (trend_up / trend_down / range / mixed)")
    log("=" * 78)

    tbl = compute_daily_ohlc(df15, cal15)
    tbl = classify_typology(tbl)

    out_rows = []

    log("\n--- Frequencias por split ---")
    for split_name in ["DISCOVERY", "CONFIRM"]:
        sub = tbl[tbl["split"] == split_name]
        n = len(sub)
        vc = sub["type"].value_counts()
        log(f"  split={split_name} n={n} median_range={sub['range'].median():.1f}")
        for t, c in vc.items():
            log(f"      {t}: n={c} ({100.0*c/n:.1f}%)")
            out_rows.append(dict(section="freq_by_split", split=split_name, year=None, type=t,
                                  n=int(c), pct=100.0 * c / n))

    log("\n--- Frequencias por ano ---")
    for yr, sub in tbl.groupby("year"):
        n = len(sub)
        vc = sub["type"].value_counts()
        split_name = sub["split"].iloc[0]
        log(f"  ano={yr} (split={split_name}) n={n}: " +
            ", ".join(f"{t}={c}({100.0*c/n:.1f}%)" for t, c in vc.items()))
        for t, c in vc.items():
            out_rows.append(dict(section="freq_by_year", split=split_name, year=int(yr), type=t,
                                  n=int(c), pct=100.0 * c / n))

    log("\n--- Cross: sinal da 1a hora (ret 09:00->10:00) x tipo do dia ---")
    tbl["fh_sign"] = np.where(tbl["first_hour_ret"] > 0, "up",
                        np.where(tbl["first_hour_ret"] < 0, "down", "flat"))
    for split_name in ["DISCOVERY", "CONFIRM"]:
        sub = tbl[tbl["split"] == split_name].dropna(subset=["first_hour_ret"])
        n = len(sub)
        log(f"\n  split={split_name} (n={n})")
        cross = pd.crosstab(sub["fh_sign"], sub["type"])
        cross_pct = pd.crosstab(sub["fh_sign"], sub["type"], normalize="index") * 100.0
        log(cross.to_string())
        for sign_val in cross.index:
            for type_val in cross.columns:
                c = int(cross.loc[sign_val, type_val])
                p = float(cross_pct.loc[sign_val, type_val])
                out_rows.append(dict(section="cross_fhsign_type", split=split_name, year=None,
                                      type=f"{sign_val}|{type_val}", n=c, pct=p))

    log("\n--- E: ret restante do dia (10:00->close) condicional ao tipo-parcial da 1a hora ---")
    for split_name in ["DISCOVERY", "CONFIRM"]:
        sub = tbl[tbl["split"] == split_name].dropna(subset=["first_hour_ret", "first_hour_range", "rest_of_day_ret"])
        if sub.empty:
            continue
        med_fh_range = sub["first_hour_range"].median()
        sub = sub.copy()
        sub["fh_vol_bucket"] = np.where(sub["first_hour_range"] >= med_fh_range, "high_vol", "low_vol")
        log(f"\n  split={split_name} n={len(sub)} mediana_range_1a_hora={med_fh_range:.1f}")
        for (sign_val, vol_bucket), g in sub.groupby(["fh_sign", "fh_vol_bucket"]):
            if sign_val == "flat" or len(g) < 5:
                continue
            r = mean_ci(g["rest_of_day_ret"] * 10000.0)
            log(f"      1a_hora={sign_val}/{vol_bucket} n={r['n']:4d}  "
                f"ret_restante_medio={r['point']:+.2f}bps IC95=[{r['lo']:+.2f},{r['hi']+0:.2f}]")
            out_rows.append(dict(section="cond_rest_of_day", split=split_name, year=None,
                                  type=f"{sign_val}|{vol_bucket}", n=r["n"],
                                  mean_bps=r["point"], lo_bps=r["lo"], hi_bps=r["hi"]))

    out = pd.DataFrame(out_rows)
    out.to_csv(os.path.join(OUT_DIR, "A_typology.csv"), index=False)
    log(f"\n[A5] salvo A_typology.csv ({len(out)} linhas)")
    return out, tbl


# ========================================================================
# A6 -- Range diario
# ========================================================================


def analyze_A6(df15, cal15, daily_tbl):
    log("\n" + "=" * 78)
    log("A6 -- Range diario: distribuicao, correlacao D vs D-1, inside days")
    log("=" * 78)

    tbl = daily_tbl.sort_values("date").reset_index(drop=True)

    out_rows = []

    log("\n--- Distribuicao do range diario por ano ---")
    for yr, sub in tbl.groupby("year"):
        q = sub["range"].quantile([0.25, 0.5, 0.75])
        n = len(sub)
        log(f"  ano={yr} n={n}  q25={q[0.25]:.1f}  mediana={q[0.5]:.1f}  q75={q[0.75]:.1f}")
        out_rows.append(dict(section="range_dist_by_year", split=None, year=int(yr), n=n,
                              q25=float(q[0.25]), median=float(q[0.5]), q75=float(q[0.75])))

    log("\n--- Correlacao range_D vs range_D-1, e % inside days, por split ---")
    for split_name in ["DISCOVERY", "CONFIRM"]:
        sub = tbl[tbl["split"] == split_name].sort_values("date").reset_index(drop=True)
        sub["range_prev"] = sub["range"].shift(1)
        sub["high_prev"] = sub["high"].shift(1)
        sub["low_prev"] = sub["low"].shift(1)
        sub["is_inside"] = (sub["high"] <= sub["high_prev"]) & (sub["low"] >= sub["low_prev"])

        r = pearson_r(sub["range_prev"], sub["range"])
        log(f"\n  split={split_name}: corr(range_D, range_D-1) = {r['r']:.4f} (n={r['n']})")
        out_rows.append(dict(section="corr_range_lag1", split=split_name, year=None,
                              n=r["n"], r=r["r"]))

        n_pairs = sub["is_inside"].notna().sum()
        n_inside = int(sub["is_inside"].sum())
        pct_inside = 100.0 * n_inside / n_pairs if n_pairs else np.nan
        log(f"  split={split_name}: % inside days = {pct_inside:.1f}% (n={n_inside}/{n_pairs})")
        out_rows.append(dict(section="pct_inside_days", split=split_name, year=None,
                              n=n_pairs, n_inside=n_inside, pct_inside=pct_inside))

        # comportamento pos-inside-day: olhar D+1 apos um inside day D
        sub["ret_open_close"] = (sub["close"] / sub["open"] - 1.0) * 10000.0
        median_range_split = sub["range"].median()
        sub["range_ratio_to_median"] = sub["range"] / median_range_split

        inside_idx = sub.index[sub["is_inside"] == True]
        next_idx = inside_idx + 1
        next_idx = next_idx[next_idx < len(sub)]
        post = sub.loc[next_idx]
        if len(post) >= 5:
            r_ret = mean_ci(post["ret_open_close"])
            r_absret = mean_ci(post["ret_open_close"].abs())
            r_rangeratio = mean_ci(post["range_ratio_to_median"])
            log(f"  split={split_name}: pos-inside-day (D+1), n={r_ret['n']}")
            log(f"      ret(open->close) D+1 medio = {r_ret['point']:+.2f}bps "
                f"IC95=[{r_ret['lo']:+.2f},{r_ret['hi']:+.2f}]")
            log(f"      |ret(open->close)| D+1 medio = {r_absret['point']:.2f}bps "
                f"IC95=[{r_absret['lo']:.2f},{r_absret['hi']:.2f}]")
            log(f"      range D+1 / mediana_range_split = {r_rangeratio['point']:.3f} "
                f"IC95=[{r_rangeratio['lo']:.3f},{r_rangeratio['hi']:.3f}]  "
                f"(>1 = expande, <1 = contrai vs tipico)")
            out_rows.append(dict(section="post_inside_day", split=split_name, year=None,
                                  n=r_ret["n"], ret_open_close_bps=r_ret["point"],
                                  ret_lo_bps=r_ret["lo"], ret_hi_bps=r_ret["hi"],
                                  abs_ret_bps=r_absret["point"], abs_ret_lo_bps=r_absret["lo"],
                                  abs_ret_hi_bps=r_absret["hi"], range_ratio=r_rangeratio["point"],
                                  range_ratio_lo=r_rangeratio["lo"], range_ratio_hi=r_rangeratio["hi"]))
        else:
            log(f"  split={split_name}: pos-inside-day n insuficiente ({len(post)}), pulando")

    out = pd.DataFrame(out_rows)
    out.to_csv(os.path.join(OUT_DIR, "A_range.csv"), index=False)
    log(f"\n[A6] salvo A_range.csv ({len(out)} linhas)")
    return out


# ========================================================================
# Main
# ========================================================================


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    log("win_intraday_atlas_v0 -- Bateria A (estrutura temporal)")
    log(f"seed={SEED} n_boot={N_BOOT} OOS_CUTOFF={OOS_CUTOFF.date()} "
        f"DISCOVERY<=2023-12-31 CONFIRM=2024")
    log("")

    df15, cal15, _ = prepare(PATH_M15, 15, "M15")
    df5, cal5, _ = prepare(PATH_M5, 5, "M5")

    n_disc15 = (cal15["split"] == "DISCOVERY").sum()
    n_conf15 = (cal15["split"] == "CONFIRM").sum()
    log(f"\n[M15] dias validos DISCOVERY={n_disc15}  CONFIRM={n_conf15}")
    n_disc5 = (cal5["split"] == "DISCOVERY").sum()
    n_conf5 = (cal5["split"] == "CONFIRM").sum()
    log(f"[M5]  dias validos DISCOVERY(parcial, so' 2022-12-14+)={n_disc5}  CONFIRM={n_conf5}")

    analyze_A1(df15, cal15, df5, cal5)
    analyze_A2(df15, cal15)
    analyze_A3(df15, cal15)
    analyze_A4(df15, cal15)
    _, daily_tbl = analyze_A5(df15, cal15)
    analyze_A6(df15, cal15, daily_tbl)

    with open(os.path.join(OUT_DIR, "A_SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES))

    print(f"\nOutputs salvos em: {OUT_DIR}")


if __name__ == "__main__":
    main()
