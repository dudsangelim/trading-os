"""
win_vwap_v0 -- Fase 0 / Bateria D (mapa VWAP de sessao), script auto-suficiente.

Pre-registro: ..\PREREGISTRATION.md
Protocolo-mae: ..\..\win_intraday_atlas_v0\PROTOCOL.md

Roda com:
    python build_vwap_map.py

Convencoes (identicas as campanhas atlas/T1/T2/T3):
- datetime_b3 = INICIO da barra, naive, wall-clock B3. preco@T = close(barra
  que comeca em T - timeframe).
- OOS 2025-01-01+ e' removido logo apos carregar cada parquet -- NUNCA entra
  em nenhuma estatistica deste script.
- Splits: DISCOVERY = ate 2023-12-31 (inclusive). CONFIRM = ano 2024 inteiro.
  M5 comeca em 2022-12-14 -> DISCOVERY_PARTIAL rotulado explicitamente.
- Dias que nao abrem exatamente 09:00 (Cinzas/sessao curta) sao descartados.
- VWAP de sessao: cumsum(TP*volume_efetivo)/cumsum(volume_efetivo), TP=(H+L+C)/3,
  comecando na barra 09:00. volume_efetivo = real_volume, com fallback para
  tick_volume quando real_volume<=0 (contado e reportado).
- dev(t) = close(t)/VWAP(t) - 1. dev_n = dev / ATR20_frac, ATR20 = media movel
  20 sessoes do range diario (high-low)/open em bps, usando SO dias < t
  (rolling().shift(1), sem lookahead).
- Checkpoint T usa close da barra (T-tf) e VWAP acumulado ate essa barra
  inclusive (mesma convencao preco@T). Forward 30/60min tambem respeitam essa
  convencao (close@(T+30)=close(barra T+15) etc, ver PREREGISTRATION.md).
- Zero trades simulados. So descritivo (medias, %, bootstrap).
- Bootstrap 1000x seed 42, reamostrando observacoes (1 obs = 1 dia dentro de
  cada celula checkpoint x bucket x split, entao reamostrar linhas == reamostrar
  dias).
- CUIDADO dtype: colunas booleanas com NaN viram dtype "object" em pandas e
  quebram sum()/mean() silenciosamente -- todas as colunas booleanas aqui usam
  o dtype nullable "boolean" explicitamente (ver bug conhecido, atlas B2/B3).

Saidas em phase0/ (mesma pasta deste script):
  D1_dev_dist.csv, D2_reversion.csv, D3_magnet.csv, D4_trend.csv, D5_robust.csv,
  SUMMARY.txt
"""

import os
from datetime import date

import numpy as np
import pandas as pd

pd.set_option("display.width", 160)

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

DATA_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
PATH_M15 = os.path.join(DATA_DIR, "WIN_cont_N_M15.parquet")
PATH_M5 = os.path.join(DATA_DIR, "WIN_cont_N_M5.parquet")

OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\win_vwap_v0\phase0"

OOS_CUTOFF = pd.Timestamp("2025-01-01")     # NAO TOCAR em 2025+
DISCOVERY_END = date(2023, 12, 31)
CONFIRM_START = date(2024, 1, 1)
CONFIRM_END = date(2024, 12, 31)

SEED = 42
N_BOOT = 1000

SESSION_OPEN_TIME = pd.Timestamp("09:00:00").time()

CHECKPOINT_HOURS = [10, 11, 12, 13, 14, 15, 16]

BUCKET_ORDER = ["(-inf,-0.15]", "(-0.15,-0.05]", "(-0.05,0.05)", "[0.05,0.15)", "[0.15,+inf)"]
MAGNET_BUCKETS = ["(-inf,-0.15]", "(-0.15,-0.05]", "[0.05,0.15)", "[0.15,+inf)"]  # |dev_n|>=0.05

ATR_WINDOW = 20

PROMO_MIN_ABS_EFFECT_BPS = 6.0
PROMO_MIN_N = 100
CONFIRM_MIN_ABS_EFFECT_BPS = 3.0

# ----------------------------------------------------------------------
# Logger
# ----------------------------------------------------------------------

LOG_LINES = []


def log(s=""):
    print(s)
    LOG_LINES.append(str(s))


# ----------------------------------------------------------------------
# Data loading / calendar (mesmo padrao do atlas build_atlas_A.py)
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
    d = df.copy()
    d["date"] = d["datetime_b3"].dt.date
    g = d.groupby("date")["datetime_b3"].agg(open_dt="min", close_dt="max", n_bars="count")
    g = g.reset_index()
    g["open_time"] = g["open_dt"].dt.time
    g["close_time"] = g["close_dt"].dt.time
    g["year"] = pd.to_datetime(g["date"]).dt.year
    return g


def valid_day_set(calendar):
    ok = calendar[calendar["open_time"] == SESSION_OPEN_TIME]
    bad = calendar[calendar["open_time"] != SESSION_OPEN_TIME]
    return set(ok["date"]), bad


def split_label(d, discovery_end=DISCOVERY_END, confirm_start=CONFIRM_START, confirm_end=CONFIRM_END):
    if d <= discovery_end:
        return "DISCOVERY"
    if confirm_start <= d <= confirm_end:
        return "CONFIRM"
    return None


# ----------------------------------------------------------------------
# Volume efetivo (fallback tick_volume quando real_volume<=0)
# ----------------------------------------------------------------------


def add_effective_volume(df):
    df = df.copy()
    fallback_mask = ~(df["real_volume"] > 0)
    df["eff_volume"] = np.where(df["real_volume"] > 0, df["real_volume"], df["tick_volume"]).astype(float)
    df["vol_fallback"] = fallback_mask.astype("boolean")
    n_fb = int(fallback_mask.sum())
    pct_fb = 100.0 * n_fb / len(df) if len(df) else np.nan
    return df, n_fb, pct_fb


# ----------------------------------------------------------------------
# VWAP de sessao (cumulativo, TP=(H+L+C)/3, reset a cada dia)
# ----------------------------------------------------------------------


def build_vwap_running(df):
    d = df.copy()
    d["date"] = d["datetime_b3"].dt.date
    d["TP"] = (d["high"] + d["low"] + d["close"]) / 3.0
    d = d.sort_values(["date", "datetime_b3"]).reset_index(drop=True)
    d["TPV"] = d["TP"] * d["eff_volume"]
    d["cum_tpv"] = d.groupby("date")["TPV"].cumsum()
    d["cum_vol"] = d.groupby("date")["eff_volume"].cumsum()
    d["vwap_running"] = d["cum_tpv"] / d["cum_vol"]
    return d


# ----------------------------------------------------------------------
# Lookup por convencao preco@T = valor(barra T-tf)
# ----------------------------------------------------------------------


def get_value_at(idx_series, T, freq_min):
    t = T - pd.Timedelta(minutes=freq_min)
    return idx_series.get(t, np.nan)


# ----------------------------------------------------------------------
# ATR20 diario (canonico, sempre calculado a partir do M15)
# ----------------------------------------------------------------------


def compute_daily_range_atr(df_valid_m15, cal_valid_m15):
    d = df_valid_m15.copy()
    d["date"] = d["datetime_b3"].dt.date
    d = d.sort_values("datetime_b3")
    g = d.groupby("date", sort=True).agg(day_open=("open", "first"), day_high=("high", "max"),
                                          day_low=("low", "min"), day_close=("close", "last"))
    g = g.reset_index()
    g["range_bps"] = (g["day_high"] - g["day_low"]) / g["day_open"] * 1e4
    g["date_ts"] = pd.to_datetime(g["date"])
    g = g.sort_values("date_ts").reset_index(drop=True)
    # rolling media 20 sessoes, SO com dias < t (shift(1) apos rolling em dias <= t-1)
    g["ATR20_bps"] = g["range_bps"].rolling(window=ATR_WINDOW, min_periods=ATR_WINDOW).mean().shift(1)
    return g[["date", "range_bps", "ATR20_bps"]]


# ----------------------------------------------------------------------
# Estatistica: bootstrap
# ----------------------------------------------------------------------


def bootstrap_stat_ci(values, stat_fn, n_boot=N_BOOT, seed=SEED):
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    n = len(values)
    if n == 0:
        return dict(point=np.nan, lo=np.nan, hi=np.nan, n=0)
    point = float(stat_fn(values))
    if n == 1:
        return dict(point=point, lo=np.nan, hi=np.nan, n=1)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    sampled = values[idx]  # (n_boot, n)
    if stat_fn is np.mean:
        boots = sampled.mean(axis=1)
    else:
        boots = np.array([stat_fn(row) for row in sampled])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return dict(point=point, lo=float(lo), hi=float(hi), n=n)


def mean_ci(values, n_boot=N_BOOT, seed=SEED):
    return bootstrap_stat_ci(values, np.mean, n_boot, seed)


# ----------------------------------------------------------------------
# Bucket helpers
# ----------------------------------------------------------------------


def dev_bucket(x):
    if pd.isna(x):
        return None
    if x <= -0.15:
        return "(-inf,-0.15]"
    if x <= -0.05:
        return "(-0.15,-0.05]"
    if x < 0.05:
        return "(-0.05,0.05)"
    if x < 0.15:
        return "[0.05,0.15)"
    return "[0.15,+inf)"


# ----------------------------------------------------------------------
# Preparacao de um dataset (M15 ou M5)
# ----------------------------------------------------------------------


def prepare_dataset(path, freq_min, label):
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

    df_valid = df[df["datetime_b3"].dt.date.isin(valid_dates)].copy().reset_index(drop=True)
    cal_valid = cal[cal["date"].isin(valid_dates)].copy().reset_index(drop=True)
    cal_valid["split"] = cal_valid["date"].apply(split_label)

    df_valid, n_fb, pct_fb = add_effective_volume(df_valid)
    log(f"[{label}] barras com fallback tick_volume (real_volume<=0): {n_fb} ({pct_fb:.4f}%)")

    df_valid = build_vwap_running(df_valid)

    close_idx = df_valid.set_index("datetime_b3")["close"]
    close_idx = close_idx[~close_idx.index.duplicated(keep="first")]
    vwap_idx = df_valid.set_index("datetime_b3")["vwap_running"]
    vwap_idx = vwap_idx[~vwap_idx.index.duplicated(keep="first")]

    bars_by_date = {d: g.sort_values("datetime_b3").reset_index(drop=True)
                     for d, g in df_valid.groupby(df_valid["datetime_b3"].dt.date)}

    n_disc = int((cal_valid["split"] == "DISCOVERY").sum())
    n_conf = int((cal_valid["split"] == "CONFIRM").sum())
    n_none = int(cal_valid["split"].isna().sum())
    log(f"[{label}] dias validos por split: DISCOVERY={n_disc}  CONFIRM={n_conf}  fora_dos_splits={n_none}")

    return dict(label=label, freq_min=freq_min, df=df_valid, cal=cal_valid, valid_dates=valid_dates,
                close_idx=close_idx, vwap_idx=vwap_idx, bars_by_date=bars_by_date,
                n_fallback=n_fb, pct_fallback=pct_fb, n_disc=n_disc, n_conf=n_conf)


# ----------------------------------------------------------------------
# Tabela de checkpoints (base para D1, D2, D4, D5)
# ----------------------------------------------------------------------


def build_checkpoint_records(ds, atr_map, discovery_label="DISCOVERY"):
    freq_min = ds["freq_min"]
    close_idx = ds["close_idx"]
    vwap_idx = ds["vwap_idx"]
    cal = ds["cal"]
    date_split = dict(zip(cal["date"], cal["split"]))

    rows = []
    for d in sorted(ds["valid_dates"]):
        split = date_split.get(d)
        if split is None:
            continue
        atr_bps = atr_map.get(d, np.nan)
        atr_frac = atr_bps / 1e4 if pd.notna(atr_bps) else np.nan

        for H in CHECKPOINT_HOURS:
            T = pd.Timestamp(d) + pd.Timedelta(hours=H)
            close_T = get_value_at(close_idx, T, freq_min)
            vwap_T = get_value_at(vwap_idx, T, freq_min)
            if pd.isna(close_T) or pd.isna(vwap_T) or vwap_T == 0:
                continue

            dev = close_T / vwap_T - 1.0
            dev_n = dev / atr_frac if (pd.notna(atr_frac) and atr_frac != 0) else np.nan
            bucket = dev_bucket(dev_n)

            close_T30 = get_value_at(close_idx, T + pd.Timedelta(minutes=30), freq_min)
            close_T60 = get_value_at(close_idx, T + pd.Timedelta(minutes=60), freq_min)
            ret_fwd30 = (close_T30 / close_T - 1.0) if pd.notna(close_T30) else np.nan
            ret_fwd60 = (close_T60 / close_T - 1.0) if pd.notna(close_T60) else np.nan

            vwap_Tm30 = get_value_at(vwap_idx, T - pd.Timedelta(minutes=30), freq_min)
            if pd.notna(vwap_Tm30) and vwap_Tm30 != 0:
                slope = vwap_T / vwap_Tm30 - 1.0
                slope_sign = "pos" if slope > 0 else ("neg" if slope < 0 else "zero")
            else:
                slope = np.nan
                slope_sign = None

            dev_sign = "pos" if dev > 0 else ("neg" if dev < 0 else "zero")

            rows.append(dict(
                date=d, split=split, checkpoint=f"{H:02d}:00", hour=H,
                close_T=close_T, vwap_T=vwap_T, dev=dev, dev_bps=dev * 1e4,
                atr20_bps=atr_bps, dev_n=dev_n, bucket=bucket,
                ret_fwd30_bps=ret_fwd30 * 1e4 if pd.notna(ret_fwd30) else np.nan,
                ret_fwd60_bps=ret_fwd60 * 1e4 if pd.notna(ret_fwd60) else np.nan,
                vwap_slope_30m=slope, vwap_slope_sign=slope_sign, dev_sign=dev_sign,
            ))
    out = pd.DataFrame(rows)
    return out


# ----------------------------------------------------------------------
# D1 -- distribuicao de dev por checkpoint x split
# ----------------------------------------------------------------------


def analyze_D1(ckpt_df):
    log("\n" + "=" * 78)
    log("D1 -- Distribuicao de dev (bps e dev_n) por checkpoint x split")
    log("=" * 78)
    rows = []
    for (split, ckpt), g in ckpt_df.groupby(["split", "checkpoint"]):
        for col, metric in [("dev_bps", "dev_bps"), ("dev_n", "dev_n")]:
            s = g[col].dropna()
            n = len(s)
            rows.append(dict(split=split, checkpoint=ckpt, metric=metric, n=n,
                              mean=float(s.mean()) if n else np.nan,
                              median=float(s.median()) if n else np.nan,
                              q25=float(s.quantile(.25)) if n else np.nan,
                              q75=float(s.quantile(.75)) if n else np.nan,
                              std=float(s.std()) if n else np.nan))
        vc = g["bucket"].value_counts()
        for b in BUCKET_ORDER:
            c = int(vc.get(b, 0))
            rows.append(dict(split=split, checkpoint=ckpt, metric=f"bucket_n:{b}", n=c,
                              mean=np.nan, median=np.nan, q25=np.nan, q75=np.nan, std=np.nan))

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT_DIR, "D1_dev_dist.csv"), index=False)
    log(f"[D1] salvo D1_dev_dist.csv ({len(out)} linhas)")

    for split in ["DISCOVERY", "CONFIRM"]:
        log(f"\n--- split={split} (dev_n mediana / q25 / q75 por checkpoint) ---")
        sub = out[(out["split"] == split) & (out["metric"] == "dev_n")]
        for _, r in sub.sort_values("checkpoint").iterrows():
            log(f"  {r['checkpoint']}  n={r['n']:5.0f}  mediana={r['median']:+.4f}  "
                f"q25/q75=[{r['q25']:+.4f},{r['q75']:+.4f}]  mean={r['mean']:+.4f}")
    return out


# ----------------------------------------------------------------------
# D2 -- H_rev: ret forward condicional ao bucket de dev_n
# ----------------------------------------------------------------------


def analyze_D2(ckpt_df, source_label="M15"):
    rows = []
    registry = []
    sub = ckpt_df.dropna(subset=["bucket"])
    for (split, ckpt, bucket), g in sub.groupby(["split", "checkpoint", "bucket"]):
        for horizon, col in [(30, "ret_fwd30_bps"), (60, "ret_fwd60_bps")]:
            vals = g[col].dropna().values
            stat = mean_ci(vals)
            tid = f"D2_{ckpt}_{bucket}_{horizon}"
            desc = f"D2 [{source_label}] checkpoint={ckpt} bucket={bucket} fwd{horizon}min"
            rows.append(dict(source=source_label, split=split, checkpoint=ckpt, bucket=bucket,
                              horizon_min=horizon, n=stat["n"], mean_bps=stat["point"],
                              ci_lo=stat["lo"], ci_hi=stat["hi"], test_id=tid))
            registry.append(dict(test_id=tid, source=source_label, split=split, n=stat["n"],
                                  effect_bps=stat["point"], ci_lo=stat["lo"], ci_hi=stat["hi"],
                                  description=desc))
    return pd.DataFrame(rows), registry


# ----------------------------------------------------------------------
# D3 -- H_magnet: % toque do VWAP p/ |dev_n|>=0.05
# ----------------------------------------------------------------------


def analyze_D3(ds, ckpt_df):
    log("\n" + "=" * 78)
    log("D3 -- H_magnet: toque do VWAP para |dev_n|>=0.05 (checkpoint x lado x split)")
    log("=" * 78)

    freq_min = ds["freq_min"]
    bars_by_date = ds["bars_by_date"]

    sub = ckpt_df[ckpt_df["bucket"].isin(MAGNET_BUCKETS)].copy()
    sub["side"] = np.where(sub["dev_n"] > 0, "pos", "neg")

    detail_rows = []
    for r in sub.itertuples():
        d = r.date
        H = r.hour
        T = pd.Timestamp(d) + pd.Timedelta(hours=H)
        bars = bars_by_date.get(d)
        if bars is None:
            continue
        fwd = bars[bars["datetime_b3"] >= T]
        if fwd.empty:
            continue

        touch_mask_eod = (fwd["low"] <= fwd["vwap_running"]) & (fwd["high"] >= fwd["vwap_running"])
        touched_eod = bool(touch_mask_eod.any())
        minutes_eod = np.nan
        if touched_eod:
            i0 = int(np.argmax(touch_mask_eod.values))
            minutes_eod = (fwd["datetime_b3"].values[i0] - np.datetime64(T)) / np.timedelta64(1, "m")

        window60 = fwd[fwd["datetime_b3"] < T + pd.Timedelta(minutes=60)]
        touch_mask_60 = (window60["low"] <= window60["vwap_running"]) & (window60["high"] >= window60["vwap_running"])
        touched_60 = bool(touch_mask_60.any())
        minutes_60 = np.nan
        if touched_60:
            i1 = int(np.argmax(touch_mask_60.values))
            minutes_60 = (window60["datetime_b3"].values[i1] - np.datetime64(T)) / np.timedelta64(1, "m")

        detail_rows.append(dict(date=d, split=r.split, checkpoint=r.checkpoint, side=r.side,
                                 touched_60=touched_60, minutes_to_touch_60=minutes_60,
                                 touched_eod=touched_eod, minutes_to_touch_eod=minutes_eod))

    det = pd.DataFrame(detail_rows)
    if det.empty:
        log("  nenhuma observacao em bucket magnet")
        return pd.DataFrame(), det

    det["touched_60"] = det["touched_60"].astype("boolean")
    det["touched_eod"] = det["touched_eod"].astype("boolean")

    out_rows = []
    for (split, ckpt, side), g in det.groupby(["split", "checkpoint", "side"]):
        n = len(g)
        pct60 = float(g["touched_60"].mean()) * 100.0
        pcteod = float(g["touched_eod"].mean()) * 100.0
        med60 = g.loc[g["touched_60"] == True, "minutes_to_touch_60"].median()
        medeod = g.loc[g["touched_eod"] == True, "minutes_to_touch_eod"].median()
        stat60 = mean_ci(g["touched_60"].astype(float).values * 100.0)
        stateod = mean_ci(g["touched_eod"].astype(float).values * 100.0)
        out_rows.append(dict(split=split, checkpoint=ckpt, side=side, n=n,
                              pct_touch_60min=pct60, ci_lo_60=stat60["lo"], ci_hi_60=stat60["hi"],
                              median_min_to_touch_60=med60,
                              pct_touch_eod=pcteod, ci_lo_eod=stateod["lo"], ci_hi_eod=stateod["hi"],
                              median_min_to_touch_eod=medeod))

    out = pd.DataFrame(out_rows).sort_values(["split", "checkpoint", "side"])
    out.to_csv(os.path.join(OUT_DIR, "D3_magnet.csv"), index=False)
    log(f"[D3] salvo D3_magnet.csv ({len(out)} linhas)")
    for _, r in out.iterrows():
        log(f"  [{r['split']:10s}] {r['checkpoint']} side={r['side']:3s} n={r['n']:4.0f}  "
            f"touch60min={r['pct_touch_60min']:.1f}% IC[{r['ci_lo_60']:.1f},{r['ci_hi_60']:.1f}] "
            f"med_min={r['median_min_to_touch_60']}  touch_eod={r['pct_touch_eod']:.1f}% "
            f"med_min_eod={r['median_min_to_touch_eod']}")
    return out, det


# ----------------------------------------------------------------------
# D4 -- H_trend: sinal(dev) x sinal(inclinacao VWAP 30min) -> ret fwd 60min
# ----------------------------------------------------------------------


def analyze_D4(ckpt_df):
    log("\n" + "=" * 78)
    log("D4 -- H_trend: sinal(dev) x sinal(inclinacao VWAP 30min) -> ret fwd60min")
    log("=" * 78)

    rows = []
    registry = []
    sub = ckpt_df[(ckpt_df["dev_sign"].isin(["pos", "neg"])) &
                   (ckpt_df["vwap_slope_sign"].isin(["pos", "neg"]))].dropna(subset=["ret_fwd60_bps"])

    for (split, ckpt, dsign, ssign), g in sub.groupby(["split", "checkpoint", "dev_sign", "vwap_slope_sign"]):
        vals = g["ret_fwd60_bps"].dropna().values
        stat = mean_ci(vals)
        tid = f"D4_{ckpt}_{dsign}_{ssign}"
        desc = f"D4 checkpoint={ckpt} dev={dsign} slope_vwap={ssign} fwd60min"
        rows.append(dict(split=split, checkpoint=ckpt, dev_sign=dsign, vwap_slope_sign=ssign,
                          n=stat["n"], mean_ret_fwd60_bps=stat["point"], ci_lo=stat["lo"], ci_hi=stat["hi"],
                          test_id=tid))
        registry.append(dict(test_id=tid, source="M15", split=split, n=stat["n"], effect_bps=stat["point"],
                              ci_lo=stat["lo"], ci_hi=stat["hi"], description=desc))

    out = pd.DataFrame(rows).sort_values(["checkpoint", "split", "dev_sign", "vwap_slope_sign"])
    out.to_csv(os.path.join(OUT_DIR, "D4_trend.csv"), index=False)
    log(f"[D4] salvo D4_trend.csv ({len(out)} linhas)")
    for _, r in out[out["split"] == "DISCOVERY"].iterrows():
        log(f"  DISCOVERY {r['checkpoint']} dev={r['dev_sign']:3s} slope={r['vwap_slope_sign']:3s} "
            f"n={r['n']:4.0f}  {r['mean_ret_fwd60_bps']:+.2f}bps IC[{r['ci_lo']:+.2f},{r['ci_hi']:+.2f}]")
    return out, registry


# ----------------------------------------------------------------------
# D5 -- Robustez: trim 1% de dev_n (M15) + recomputo em M5
# ----------------------------------------------------------------------


def trim_by_dev_n(ckpt_df):
    """Remove, por split, o 1% superior e 1% inferior da distribuicao de dev_n
    (winsoriza por corte, nao clip) -- pre-registro: 'D2 com trim 1% dos dev_n'."""
    out_parts = []
    for split, g in ckpt_df.groupby("split"):
        s = g["dev_n"].dropna()
        if len(s) < 50:
            out_parts.append(g)
            continue
        lo, hi = s.quantile([0.01, 0.99])
        keep = g["dev_n"].isna() | ((g["dev_n"] >= lo) & (g["dev_n"] <= hi))
        out_parts.append(g[keep])
    return pd.concat(out_parts, ignore_index=True)


def analyze_D5(ckpt_df_m15, ds_m5, atr_map):
    log("\n" + "=" * 78)
    log("D5 -- Robustez: D2 com trim 1% dos dev_n (M15) + D2 recomputado em M5")
    log("=" * 78)

    # --- D5a: trim 1% ---
    trimmed = trim_by_dev_n(ckpt_df_m15)
    n_before = len(ckpt_df_m15.dropna(subset=["dev_n"]))
    n_after = len(trimmed.dropna(subset=["dev_n"]))
    log(f"[D5a] trim 1%/1% em dev_n por split: n antes={n_before}, n depois={n_after} "
        f"({100.0*(n_before-n_after)/n_before:.2f}% removido)")
    d5a_tbl, d5a_registry = analyze_D2(trimmed, source_label="M15_trim1pct")
    d5a_tbl["robust_type"] = "trim1pct_dev_n_M15"

    # --- D5b: recomputo em M5 ---
    ckpt_df_m5 = build_checkpoint_records(ds_m5, atr_map)
    # relabel DISCOVERY como DISCOVERY_PARTIAL (M5 comeca em 2022-12-14)
    ckpt_df_m5_relabel = ckpt_df_m5.copy()
    ckpt_df_m5_relabel["split"] = ckpt_df_m5_relabel["split"].replace({"DISCOVERY": "DISCOVERY_PARTIAL"})
    d5b_tbl, d5b_registry = analyze_D2(ckpt_df_m5_relabel, source_label="M5")
    d5b_tbl["robust_type"] = "M5_recompute"

    out = pd.concat([d5a_tbl, d5b_tbl], ignore_index=True, sort=False)
    out.to_csv(os.path.join(OUT_DIR, "D5_robust.csv"), index=False)
    log(f"[D5] salvo D5_robust.csv ({len(out)} linhas)")

    n_m5_disc = int((ckpt_df_m5["split"] == "DISCOVERY").sum())
    n_m5_conf = int((ckpt_df_m5["split"] == "CONFIRM").sum())
    log(f"[D5b] M5 checkpoints validos: DISCOVERY_PARTIAL={n_m5_disc}  CONFIRM={n_m5_conf}")

    return out, d5a_tbl, d5b_tbl, ckpt_df_m5_relabel


# ----------------------------------------------------------------------
# Candidate scan (criterio de promocao do pre-registro)
# ----------------------------------------------------------------------


def candidate_scan(registry_rows):
    df = pd.DataFrame(registry_rows)
    if df.empty:
        return pd.DataFrame(), df
    disc = df[df["split"] == "DISCOVERY"].copy()
    conf = df[df["split"] == "CONFIRM"].set_index("test_id")

    disc["ci_excludes_zero"] = (disc["ci_lo"] > 0) | (disc["ci_hi"] < 0)
    cand = disc[(disc["effect_bps"].abs() >= PROMO_MIN_ABS_EFFECT_BPS) &
                disc["ci_excludes_zero"] & (disc["n"] >= PROMO_MIN_N)].copy()

    rows = []
    for _, r in cand.iterrows():
        tid = r["test_id"]
        crow = conf.loc[tid] if tid in conf.index else None
        rows.append(dict(
            test_id=tid, description=r["description"],
            discovery_n=r["n"], discovery_effect_bps=r["effect_bps"],
            discovery_ci_lo=r["ci_lo"], discovery_ci_hi=r["ci_hi"],
            confirm_n=crow["n"] if crow is not None else np.nan,
            confirm_effect_bps=crow["effect_bps"] if crow is not None else np.nan,
            confirm_ci_lo=crow["ci_lo"] if crow is not None else np.nan,
            confirm_ci_hi=crow["ci_hi"] if crow is not None else np.nan,
        ))
    out = pd.DataFrame(rows)
    if len(out) > 0:
        out["same_sign_confirm"] = np.sign(out["discovery_effect_bps"]) == np.sign(out["confirm_effect_bps"])
        out["confirm_ge3bps"] = out["confirm_effect_bps"].abs() >= CONFIRM_MIN_ABS_EFFECT_BPS
        out["verdict"] = np.where(out["same_sign_confirm"] & out["confirm_ge3bps"], "REPLICA",
                                   np.where(out["confirm_n"].isna(), "SEM_DADOS_CONFIRM", "NAO_REPLICA"))
        out = out.sort_values("discovery_effect_bps", key=lambda s: s.abs(), ascending=False)
    return out, df


def attach_robustness(cand_df, d5a_tbl, ckpt_df_m5_relabel):
    """Para cada candidato D2 (test_id = D2_{ckpt}_{bucket}_{horizon}), busca o
    valor correspondente no trim1% (M15) e no recomputo M5 (DISCOVERY_PARTIAL),
    quando existir a mesma celula (checkpoint,bucket,horizon)."""
    if cand_df.empty:
        cand_df["trim1pct_discovery_bps"] = []
        cand_df["m5_discovery_partial_bps"] = []
        return cand_df

    trim_idx = d5a_tbl[d5a_tbl["split"] == "DISCOVERY"].set_index("test_id")

    m5_tbl, _ = analyze_D2(ckpt_df_m5_relabel, source_label="M5")
    m5_idx = m5_tbl[m5_tbl["split"] == "DISCOVERY_PARTIAL"].set_index("test_id")

    trim_vals, trim_n, m5_vals, m5_n = [], [], [], []
    for tid in cand_df["test_id"]:
        if tid in trim_idx.index:
            trim_vals.append(trim_idx.loc[tid, "mean_bps"])
            trim_n.append(trim_idx.loc[tid, "n"])
        else:
            trim_vals.append(np.nan)
            trim_n.append(np.nan)
        if tid in m5_idx.index:
            m5_vals.append(m5_idx.loc[tid, "mean_bps"])
            m5_n.append(m5_idx.loc[tid, "n"])
        else:
            m5_vals.append(np.nan)
            m5_n.append(np.nan)

    cand_df = cand_df.copy()
    cand_df["trim1pct_discovery_bps"] = trim_vals
    cand_df["trim1pct_discovery_n"] = trim_n
    cand_df["m5_discovery_partial_bps"] = m5_vals
    cand_df["m5_discovery_partial_n"] = m5_n
    return cand_df


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    log("win_vwap_v0 -- Fase 0 / Bateria D (mapa VWAP de sessao)")
    log(f"seed={SEED} n_boot={N_BOOT} OOS_CUTOFF={OOS_CUTOFF.date()} "
        f"DISCOVERY<=2023-12-31 CONFIRM=2024")
    log(f"checkpoints={CHECKPOINT_HOURS} buckets_dev_n={BUCKET_ORDER}")
    log("")

    ds15 = prepare_dataset(PATH_M15, 15, "M15")
    ds5 = prepare_dataset(PATH_M5, 5, "M5")

    atr_tbl = compute_daily_range_atr(ds15["df"], ds15["cal"])
    atr_map = dict(zip(atr_tbl["date"], atr_tbl["ATR20_bps"]))
    n_atr_valid = int(atr_tbl["ATR20_bps"].notna().sum())
    log(f"\n[ATR20] dias com ATR20 valido (>= {ATR_WINDOW} sessoes anteriores): {n_atr_valid} / {len(atr_tbl)}")

    ckpt15 = build_checkpoint_records(ds15, atr_map)
    log(f"[checkpoints M15] linhas construidas: {len(ckpt15)} (dias x checkpoints validos, com ATR20 e VWAP disponiveis)")

    # ---- D1 ----
    d1_out = analyze_D1(ckpt15)

    # ---- D2 (M15, canonico) ----
    log("\n" + "=" * 78)
    log("D2 -- H_rev: ret forward condicional ao bucket de dev_n (M15)")
    log("=" * 78)
    d2_out, d2_registry = analyze_D2(ckpt15, source_label="M15")
    d2_out.to_csv(os.path.join(OUT_DIR, "D2_reversion.csv"), index=False)
    log(f"[D2] salvo D2_reversion.csv ({len(d2_out)} linhas)")
    for _, r in d2_out[(d2_out["split"] == "DISCOVERY")].sort_values(["checkpoint", "bucket", "horizon_min"]).iterrows():
        log(f"  DISCOVERY {r['checkpoint']} bucket={r['bucket']:15s} fwd{r['horizon_min']:2d}min "
            f"n={r['n']:4.0f}  {r['mean_bps']:+.2f}bps IC[{r['ci_lo']:+.2f},{r['ci_hi']:+.2f}]")

    # ---- D3 (M15) ----
    d3_out, d3_det = analyze_D3(ds15, ckpt15)

    # ---- D4 (M15) ----
    d4_out, d4_registry = analyze_D4(ckpt15)

    # ---- D5 (trim 1% + M5) ----
    d5_out, d5a_tbl, d5b_tbl, ckpt5_relabel = analyze_D5(ckpt15, ds5, atr_map)

    # ---- Candidate scan (D2 + D4, M15 canonico) ----
    all_registry = d2_registry + d4_registry
    cand, all_tests_df = candidate_scan(all_registry)
    cand = attach_robustness(cand, d5a_tbl, ckpt5_relabel)

    total_cells = len(d1_out) + len(d2_out) + len(d3_out) + len(d4_out) + len(d5_out)

    # ---- SUMMARY ----
    log("\n" + "=" * 78)
    log("CANDIDATOS (criterio pre-registro: |efeito|>=6bps DISCOVERY, IC95 excl.0, n>=100)")
    log("=" * 78)
    if cand.empty:
        log("  NENHUMA celula (D2/D4) passou o criterio de candidato no DISCOVERY.")
        log("  -> Se confirmado apos revisao, familia VWAP OHLCV encerrada (registrar, nao revisitar sem tick data).")
    else:
        for _, r in cand.iterrows():
            log(f"\n  {r['description']}  [{r['test_id']}]")
            log(f"      DISCOVERY: n={r['discovery_n']:.0f}  {r['discovery_effect_bps']:+.2f}bps  "
                f"IC95=[{r['discovery_ci_lo']:+.2f},{r['discovery_ci_hi']:+.2f}]")
            conf_str = (f"n={r['confirm_n']:.0f}  {r['confirm_effect_bps']:+.2f}bps  "
                        f"IC95=[{r['confirm_ci_lo']:+.2f},{r['confirm_ci_hi']:+.2f}]"
                        if pd.notna(r['confirm_n']) else "sem dados no CONFIRM")
            log(f"      CONFIRM  : {conf_str}   -> VEREDITO: {r['verdict']}")
            trim_str = f"{r['trim1pct_discovery_bps']:+.2f}bps (n={r['trim1pct_discovery_n']:.0f})" if pd.notna(r.get('trim1pct_discovery_bps', np.nan)) else "N/A"
            m5_str = f"{r['m5_discovery_partial_bps']:+.2f}bps (n={r['m5_discovery_partial_n']:.0f})" if pd.notna(r.get('m5_discovery_partial_bps', np.nan)) else "N/A (celula sem obs em M5)"
            log(f"      robustez: trim1%(M15 DISCOVERY)={trim_str}  |  M5 DISCOVERY_PARTIAL={m5_str}")

    log("\n" + "=" * 78)
    log("CONTAGEM DE TESTES (contexto de comparacoes multiplas)")
    log("=" * 78)
    log(f"  Testes de efeito com bootstrap IC (bps) registrados (D2+D4, M15, ambos splits): {len(all_tests_df)}")
    if not all_tests_df.empty:
        log(f"    DISCOVERY: {(all_tests_df['split']=='DISCOVERY').sum()}   CONFIRM: {(all_tests_df['split']=='CONFIRM').sum()}")
    log(f"  Testes de efeito adicionais em D5 (trim1% M15 + M5 recompute), ambos splits: {len(d5a_tbl)+len(d5b_tbl)}")
    log(f"  Total de CELULAS reportadas em todos os CSVs (D1..D5, todas as linhas de tabela): {total_cells}")
    log(f"  Candidatos que passaram |efeito|>=6bps + IC95 excl.0 + n>=100 no DISCOVERY: {len(cand)}")
    n_replica = int((cand["verdict"] == "REPLICA").sum()) if not cand.empty else 0
    log(f"  Dos candidatos, replicaram em CONFIRM (mesmo sinal, |efeito|>=3bps): {n_replica}")

    log("\n" + "=" * 78)
    log("RESUMO DE DADOS")
    log("=" * 78)
    log(f"  M15: dias validos DISCOVERY={ds15['n_disc']}  CONFIRM={ds15['n_conf']}  "
        f"fallback_volume={ds15['n_fallback']} barras ({ds15['pct_fallback']:.4f}%)")
    log(f"  M5:  dias validos DISCOVERY_PARTIAL={ds5['n_disc']}  CONFIRM={ds5['n_conf']}  "
        f"fallback_volume={ds5['n_fallback']} barras ({ds5['pct_fallback']:.4f}%)  "
        f"(M5 comeca em {ds5['df']['datetime_b3'].min()})")

    with open(os.path.join(OUT_DIR, "SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES))

    print(f"\nOutputs salvos em: {OUT_DIR}")


if __name__ == "__main__":
    main()
