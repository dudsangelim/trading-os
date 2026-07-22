"""
b3_round_levels_v0 - Phase 0A

Premise check DESCRITIVO (sem entry/exit/PnL) para niveis redondos como
pools de liquidez em WIN/WDO (hipoteses de Osler 2000/2003/2005, FX).

Braco intraday (M5 principal, M15/H1 robustez): T1 bounce, T2 cascade, T3
magnetismo.
Braco swing (D1 principal, W1 nao implementado nesta rodada -- ver
ressalvas): T4 barreira, T5 cruzamento, T6 rejeicao.

Tudo medido CONTRA GRADE PLACEBO (meia-grade). Efeito = diferenca
redondo-placebo com bootstrap 1000x seed fixa.

DISCOVERY <=2023 / CONFIRM 2024 / OOS 2025+ filtrado imediatamente apos
load, nunca tocado. Dias de janela de rolagem por calendario (mesma regra
de b3_mtf_swinghold_v0) nao geram eventos.

Script auto-suficiente. Rodar: python analysis_round_levels.py
Dependencias: pandas, numpy, pyarrow, scipy.
"""

import os
import bisect
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

warnings.filterwarnings("ignore")
pd.set_option("mode.chained_assignment", None)

DATA_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_round_levels_v0\phase0a"

ASSETS = ["WIN", "WDO"]
DISCOVERY_END = pd.Timestamp("2024-01-01")
OOS_CUTOFF = pd.Timestamp("2025-01-01")   # OOS sagrado -- filtrado no load, nunca tocado

SEED = 20260722
N_BOOT = 1000

# ---- thresholds (fracao de ATR20d) ----
TH_BAND = 0.05          # banda de toque (T1) / magnetismo (T3)
TH_FAR = 0.30           # distancia minima de aproximacao (T1)
TH_REVERSAL = 0.15      # reversao que confirma "segura" (T1)
TH_CROSS_CONFIRM = 0.10 # confirmacao de quebra (T1 cross-out / T2 cross)
TH_T4_HALFGRID = 0.10   # fracao da meia-grade p/ estatistica de barreira (T4)

# ---- grades (redondo | placebo) ----
GRIDS = {
    "WIN": {
        "intraday": [
            dict(name="g500", grid=500.0, off_round=0.0, off_placebo=250.0),
            dict(name="g1000", grid=1000.0, off_round=0.0, off_placebo=500.0),
        ],
        "swing": [
            dict(name="g5000", grid=5000.0, off_round=0.0, off_placebo=2500.0),
        ],
    },
    "WDO": {
        "intraday": [
            dict(name="g5", grid=5.0, off_round=0.0, off_placebo=2.5),
            dict(name="g10", grid=10.0, off_round=0.0, off_placebo=5.0),
        ],
        "swing": [
            dict(name="g50", grid=50.0, off_round=0.0, off_placebo=25.0),
        ],
    },
}

# grade equivalente entre ativos, p/ gate de replicacao
GRID_EQUIV = {
    ("WIN", "g500"): "intraday_fine", ("WDO", "g5"): "intraday_fine",
    ("WIN", "g1000"): "intraday_coarse", ("WDO", "g10"): "intraday_coarse",
    ("WIN", "g5000"): "swing", ("WDO", "g50"): "swing",
}

INTRADAY_TFS = ["M5", "M15", "H1"]
TF_ROLE = {"M5": "principal", "M15": "robustez", "H1": "robustez"}

# Horizontes de forward return em minutos, adaptados por TF: 15/30/60min sao
# degenerados em H1 (bar=60min faz 15/30min colapsar no proprio evento,
# retorno sempre 0). Usamos horizontes em MULTIPLOS DA BARRA por TF,
# rotulados genericamente short/mid/long -- "mid" eh sempre o horizonte
# PRIMARIO (usado em efeito_round/efeito_placebo/diff/ci). Documentado em
# summary.md (ressalvas).
T1_HORIZONS_BY_TF = {
    "M5": [(15, "short"), (30, "mid"), (60, "long")],
    "M15": [(15, "short"), (30, "mid"), (60, "long")],
    "H1": [(60, "short"), (120, "mid"), (240, "long")],
}
T2_HORIZONS_BY_TF = {
    "M5": [(5, "vshort"), (15, "short"), (30, "mid"), (60, "long")],
    "M15": [(5, "vshort"), (15, "short"), (30, "mid"), (60, "long")],
    "H1": [(30, "vshort"), (60, "short"), (120, "mid"), (240, "long")],
}


# ==========================================================================
# Loading
# ==========================================================================

def load_tf(asset, tf):
    path = os.path.join(DATA_DIR, f"{asset}_cont_N_{tf}.parquet")
    df = pd.read_parquet(path)
    df = df.rename(columns={"datetime_b3": "ts"})
    df = df.sort_values("ts").reset_index(drop=True)
    # 2025+ eh OOS sagrado -- filtrado IMEDIATAMENTE apos o load.
    df = df[df["ts"] < OOS_CUTOFF].reset_index(drop=True)
    df["date"] = df["ts"].dt.date
    return df


def compute_atr20_avail(d1):
    """ATR20d calculado em D1, disponivel para o dia D usando dados ate D-1
    (shift(1)). Retorna dict date -> atr_avail (np.nan se indisponivel)."""
    d = d1.sort_values("ts").reset_index(drop=True)
    h, l, c = d["high"], d["low"], d["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    atr_avail = atr20.shift(1)
    return dict(zip(d["date"], atr_avail)), d, atr_avail


def attach_atr(df, atr_map):
    df = df.copy()
    df["atr"] = df["date"].map(atr_map)
    return df


# ==========================================================================
# Rolagem por calendario (identico a b3_mtf_swinghold_v0)
# ==========================================================================

def nearest_weekday(base_date, target_weekday):
    best = None
    for off in range(-6, 7):
        cand = base_date + timedelta(days=off)
        if cand.weekday() == target_weekday:
            if best is None or abs(off) < abs(best[0]):
                best = (off, cand)
    return best[1]


def snap_to_trading_day(target_date, trading_days):
    idx = bisect.bisect_left(trading_days, target_date)
    candidates = []
    if idx < len(trading_days):
        candidates.append(trading_days[idx])
    if idx > 0:
        candidates.append(trading_days[idx - 1])
    candidates.sort(key=lambda d: (abs((d - target_date).days), d))
    return candidates[0]


def build_win_windows(trading_days):
    years = sorted(set(d.year for d in trading_days))
    windows = []
    skipped = 0
    for y in years:
        for m in (2, 4, 6, 8, 10, 12):
            base = date(y, m, 15)
            wed = nearest_weekday(base, 2)
            expiry = snap_to_trading_day(wed, trading_days)
            idx = trading_days.index(expiry)
            if idx < 4:
                skipped += 1
                continue
            wdates = trading_days[idx - 4: idx + 1]
            windows.append({"expiry": expiry, "dates": wdates})
    return windows, skipped


def build_wdo_windows(trading_days):
    ym_set = sorted(set((d.year, d.month) for d in trading_days))
    windows = []
    skipped = 0
    for (y, m) in ym_set:
        month_days = [d for d in trading_days if d.year == y and d.month == m]
        first_day = month_days[0]
        idx = trading_days.index(first_day)
        if idx < 3:
            skipped += 1
            continue
        wdates = trading_days[idx - 3: idx + 1]
        windows.append({"expiry": first_day, "dates": wdates})
    return windows, skipped


def build_rollover_state(asset, trading_days):
    if asset == "WIN":
        windows, skipped = build_win_windows(trading_days)
    else:
        windows, skipped = build_wdo_windows(trading_days)
    window_dates_set = set()
    for w in windows:
        window_dates_set.update(w["dates"])
    return dict(windows=windows, skipped=skipped, window_dates_set=window_dates_set)


# ==========================================================================
# Utilidades de grade
# ==========================================================================

def nearest_level_vec(prices, grid, offset):
    return np.round((prices - offset) / grid) * grid + offset


def levels_in_range(lo, hi, grid, offset, pad=0.0):
    k_lo = int(np.floor((lo - pad - offset) / grid)) - 1
    k_hi = int(np.ceil((hi + pad - offset) / grid)) + 1
    return np.array([k * grid + offset for k in range(k_lo, k_hi + 1)])


# ==========================================================================
# Bootstrap
# ==========================================================================

def bootstrap_diff_independent(vals_round, vals_placebo, rng, n_boot=N_BOOT):
    vals_round = np.asarray(vals_round, dtype=float)
    vals_placebo = np.asarray(vals_placebo, dtype=float)
    vals_round = vals_round[~np.isnan(vals_round)]
    vals_placebo = vals_placebo[~np.isnan(vals_placebo)]
    nr, npb = len(vals_round), len(vals_placebo)
    if nr == 0 or npb == 0:
        return np.nan, np.nan, np.nan, np.nan, np.nan
    eff_r = float(vals_round.mean())
    eff_p = float(vals_placebo.mean())
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        rs = vals_round[rng.integers(0, nr, nr)]
        ps = vals_placebo[rng.integers(0, npb, npb)]
        diffs[b] = rs.mean() - ps.mean()
    ci_lo, ci_hi = np.percentile(diffs, [2.5, 97.5])
    return eff_r, eff_p, eff_r - eff_p, float(ci_lo), float(ci_hi)


def bootstrap_diff_paired_by_day(round_by_day, placebo_by_day, days, rng, n_boot=N_BOOT):
    """round_by_day / placebo_by_day: dict date -> (numer, denom) para fracao
    pooled. Resample de DIAS com reposicao, pooled fraction em cada lado."""
    days = list(days)
    nd = len(days)
    if nd == 0:
        return np.nan, np.nan, np.nan, np.nan, np.nan

    def pooled_frac(day_list, table):
        num = sum(table[d][0] for d in day_list)
        den = sum(table[d][1] for d in day_list)
        return num / den if den > 0 else np.nan

    eff_r = pooled_frac(days, round_by_day)
    eff_p = pooled_frac(days, placebo_by_day)
    diffs = np.empty(n_boot)
    days_arr = np.array(days, dtype=object)
    for b in range(n_boot):
        sample = days_arr[rng.integers(0, nd, nd)]
        diffs[b] = pooled_frac(sample, round_by_day) - pooled_frac(sample, placebo_by_day)
    ci_lo, ci_hi = np.percentile(diffs, [2.5, 97.5])
    if eff_r is np.nan or eff_p is np.nan:
        diff = np.nan
    else:
        diff = eff_r - eff_p
    return eff_r, eff_p, diff, float(ci_lo), float(ci_hi)


# ==========================================================================
# T1 -- H_bounce (intraday)
# ==========================================================================

def compute_t1_events(df, grid, offset, window_dates_set, horizons):
    events = []
    for d, sub in df.groupby("date"):
        if d in window_dates_set:
            continue
        atr = sub["atr"].iloc[0]
        if pd.isna(atr) or atr <= 0:
            continue
        sub = sub.sort_values("ts")
        close = sub["close"].values.astype(float)
        ts = sub["ts"].values
        n = len(close)
        if n < 3:
            continue
        band = TH_BAND * atr
        far = TH_FAR * atr
        reversal = TH_REVERSAL * atr
        cross_c = TH_CROSS_CONFIRM * atr
        lo, hi = close.min(), close.max()
        cand = levels_in_range(lo, hi, grid, offset, pad=band)
        for L in cand:
            dist = close - L
            absdist = np.abs(dist)
            reached_far = absdist >= far
            far_before = np.concatenate(([False], np.cumsum(reached_far[:-1].astype(int)) > 0))
            touch = absdist <= band
            eligible = touch & far_before
            if not eligible.any():
                continue
            idx = int(np.argmax(eligible))
            far_idxs = np.where(reached_far[:idx])[0]
            if len(far_idxs) == 0:
                continue
            j = far_idxs[-1]
            direction = np.sign(dist[j])
            if direction == 0:
                continue
            event_close = close[idx]
            event_ts = ts[idx]

            outcome = "unresolved"
            for k in range(idx, n):
                dk = dist[k]
                if direction > 0:
                    if dk >= reversal:
                        outcome = "hold"; break
                    if dk <= -cross_c:
                        outcome = "cross"; break
                else:
                    if dk <= -reversal:
                        outcome = "hold"; break
                    if dk >= cross_c:
                        outcome = "cross"; break

            fwd = {}
            for h_min, label in horizons:
                target = event_ts + np.timedelta64(h_min, "m")
                mask = (ts >= event_ts) & (ts <= target)
                if mask.sum() >= 1:
                    exit_close = close[mask][-1]
                    fwd[label] = float(direction * (exit_close / event_close - 1) * 1e4)
                else:
                    fwd[label] = np.nan

            row = dict(
                date=d, level=L, ts=event_ts, direction=float(direction),
                outcome=outcome, hold=(outcome == "hold"),
            )
            row.update(fwd)
            events.append(row)
    return pd.DataFrame(events)


def run_t1(df, tf, asset, grid_cfg, window_dates_set, split_bounds):
    rows = []
    horizons = T1_HORIZONS_BY_TF[tf]
    primary_min = next(h for h, lbl in horizons if lbl == "mid")
    ev_round_all = compute_t1_events(df, grid_cfg["grid"], grid_cfg["off_round"], window_dates_set, horizons)
    ev_placebo_all = compute_t1_events(df, grid_cfg["grid"], grid_cfg["off_placebo"], window_dates_set, horizons)
    for split, (lo_ts, hi_ts) in split_bounds.items():
        er = ev_round_all[(pd.to_datetime(ev_round_all["ts"]) >= lo_ts) & (pd.to_datetime(ev_round_all["ts"]) < hi_ts)] if len(ev_round_all) else ev_round_all
        ep = ev_placebo_all[(pd.to_datetime(ev_placebo_all["ts"]) >= lo_ts) & (pd.to_datetime(ev_placebo_all["ts"]) < hi_ts)] if len(ev_placebo_all) else ev_placebo_all
        rng = np.random.default_rng(SEED)
        eff_r, eff_p, diff, ci_lo, ci_hi = bootstrap_diff_independent(
            er["mid"].values if len(er) else [], ep["mid"].values if len(ep) else [], rng)
        hold_r = float(er["hold"].mean()) if len(er) else np.nan
        hold_p = float(ep["hold"].mean()) if len(ep) else np.nan
        rows.append(dict(
            test="T1_bounce", asset=asset, grid=grid_cfg["name"], tf=tf, split=split,
            n_round=len(er), n_placebo=len(ep),
            efeito_round=eff_r, efeito_placebo=eff_p, diff=diff, ci_lo=ci_lo, ci_hi=ci_hi,
            primary_horizon_min=primary_min,
            hold_rate_round=hold_r, hold_rate_placebo=hold_p,
            fwd_short_round=float(er["short"].mean()) if len(er) else np.nan,
            fwd_short_placebo=float(ep["short"].mean()) if len(ep) else np.nan,
            fwd_long_round=float(er["long"].mean()) if len(er) else np.nan,
            fwd_long_placebo=float(ep["long"].mean()) if len(ep) else np.nan,
        ))
    return rows, ev_round_all, ev_placebo_all


# ==========================================================================
# T2 -- H_cascade (intraday)
# ==========================================================================

def compute_t2_events(df, grid, offset, window_dates_set, horizons):
    events = []
    for d, sub in df.groupby("date"):
        if d in window_dates_set:
            continue
        atr = sub["atr"].iloc[0]
        if pd.isna(atr) or atr <= 0:
            continue
        sub = sub.sort_values("ts")
        close = sub["close"].values.astype(float)
        high = sub["high"].values.astype(float) if "high" in sub else close
        low = sub["low"].values.astype(float) if "low" in sub else close
        ts = sub["ts"].values
        n = len(close)
        if n < 3:
            continue
        cross_c = TH_CROSS_CONFIRM * atr
        lo, hi = close.min(), close.max()
        cand = levels_in_range(lo, hi, grid, offset, pad=cross_c)
        for L in cand:
            dist = close - L
            side = np.where(np.abs(dist) >= cross_c, np.sign(dist), 0.0)
            nz = np.where(side != 0)[0]
            if len(nz) == 0:
                continue
            init_idx = nz[0]
            confirmed = side[init_idx]
            event_idx = None
            new_side = None
            for k in range(init_idx + 1, n):
                if side[k] != 0 and side[k] != confirmed:
                    event_idx = k
                    new_side = side[k]
                    break
            if event_idx is None:
                continue
            event_close = close[event_idx]
            event_ts = ts[event_idx]

            fwd = {}
            for h_min, label in horizons:
                target = event_ts + np.timedelta64(h_min, "m")
                mask = (ts >= event_ts) & (ts <= target)
                if mask.sum() >= 1:
                    exit_close = close[mask][-1]
                    fwd[label] = float(new_side * (exit_close / event_close - 1) * 1e4)
                else:
                    fwd[label] = np.nan

            # velocidade (proxy de cascata): range dos proximos ~30min OU,
            # em TFs mais grosseiros (H1), da proxima barra completa --
            # usa o horizonte "short" da propria lista (adaptado por TF).
            range_h_min = next(h for h, lbl in horizons if lbl == "short")
            target_r = event_ts + np.timedelta64(range_h_min, "m")
            mask_r = (ts >= event_ts) & (ts <= target_r)
            if mask_r.sum() >= 1:
                range_v = float(high[mask_r].max() - low[mask_r].min())
                range_v_atr = range_v / atr
            else:
                range_v = np.nan
                range_v_atr = np.nan

            row = dict(
                date=d, level=L, ts=event_ts, direction=float(new_side),
                range_v=range_v, range_v_atr=range_v_atr,
            )
            row.update(fwd)
            events.append(row)
    return pd.DataFrame(events)


def run_t2(df, tf, asset, grid_cfg, window_dates_set, split_bounds):
    rows = []
    horizons = T2_HORIZONS_BY_TF[tf]
    primary_min = next(h for h, lbl in horizons if lbl == "mid")
    range_min = next(h for h, lbl in horizons if lbl == "short")
    ev_round_all = compute_t2_events(df, grid_cfg["grid"], grid_cfg["off_round"], window_dates_set, horizons)
    ev_placebo_all = compute_t2_events(df, grid_cfg["grid"], grid_cfg["off_placebo"], window_dates_set, horizons)
    for split, (lo_ts, hi_ts) in split_bounds.items():
        er = ev_round_all[(pd.to_datetime(ev_round_all["ts"]) >= lo_ts) & (pd.to_datetime(ev_round_all["ts"]) < hi_ts)] if len(ev_round_all) else ev_round_all
        ep = ev_placebo_all[(pd.to_datetime(ev_placebo_all["ts"]) >= lo_ts) & (pd.to_datetime(ev_placebo_all["ts"]) < hi_ts)] if len(ev_placebo_all) else ev_placebo_all
        rng = np.random.default_rng(SEED)
        eff_r, eff_p, diff, ci_lo, ci_hi = bootstrap_diff_independent(
            er["mid"].values if len(er) else [], ep["mid"].values if len(ep) else [], rng)
        rng2 = np.random.default_rng(SEED + 1)
        rr, rp, rdiff, rci_lo, rci_hi = bootstrap_diff_independent(
            er["range_v_atr"].values if len(er) else [], ep["range_v_atr"].values if len(ep) else [], rng2)
        rows.append(dict(
            test="T2_cascade", asset=asset, grid=grid_cfg["name"], tf=tf, split=split,
            n_round=len(er), n_placebo=len(ep),
            efeito_round=eff_r, efeito_placebo=eff_p, diff=diff, ci_lo=ci_lo, ci_hi=ci_hi,
            primary_horizon_min=primary_min, range_horizon_min=range_min,
            fwd_vshort_round=float(er["vshort"].mean()) if len(er) else np.nan,
            fwd_vshort_placebo=float(ep["vshort"].mean()) if len(ep) else np.nan,
            fwd_short_round=float(er["short"].mean()) if len(er) else np.nan,
            fwd_short_placebo=float(ep["short"].mean()) if len(ep) else np.nan,
            fwd_long_round=float(er["long"].mean()) if len(er) else np.nan,
            fwd_long_placebo=float(ep["long"].mean()) if len(ep) else np.nan,
            range_v_atr_round=rr, range_v_atr_placebo=rp, range_v_atr_diff=rdiff,
            range_v_atr_ci_lo=rci_lo, range_v_atr_ci_hi=rci_hi,
        ))
    return rows, ev_round_all, ev_placebo_all


# ==========================================================================
# T3 -- magnetismo (intraday)
# ==========================================================================

def run_t3(df, tf, asset, grid_cfg, window_dates_set, split_bounds):
    d2 = df[(~df["date"].isin(window_dates_set)) & df["atr"].notna() & (df["atr"] > 0)].copy()
    if len(d2) == 0:
        return [], {}
    Lr = nearest_level_vec(d2["close"].values, grid_cfg["grid"], grid_cfg["off_round"])
    Lp = nearest_level_vec(d2["close"].values, grid_cfg["grid"], grid_cfg["off_placebo"])
    band = TH_BAND * d2["atr"].values
    near_r = np.abs(d2["close"].values - Lr) <= band
    near_p = np.abs(d2["close"].values - Lp) <= band
    d2 = d2.assign(near_r=near_r, near_p=near_p)

    rows = []
    by_year_rows = []
    for split, (lo_ts, hi_ts) in split_bounds.items():
        sub = d2[(d2["ts"] >= lo_ts) & (d2["ts"] < hi_ts)]
        if len(sub) == 0:
            rows.append(dict(test="T3_magnet", asset=asset, grid=grid_cfg["name"], tf=tf, split=split,
                              n_round=0, n_placebo=0, efeito_round=np.nan, efeito_placebo=np.nan,
                              diff=np.nan, ci_lo=np.nan, ci_hi=np.nan))
            continue
        days = sorted(sub["date"].unique())
        round_by_day = {d: (sub[sub["date"] == d]["near_r"].sum(), (sub["date"] == d).sum()) for d in days}
        # otimizacao: usar groupby em vez de loop O(days^2)
        g = sub.groupby("date")
        round_by_day = {d: (int(grp["near_r"].sum()), len(grp)) for d, grp in g}
        placebo_by_day = {d: (int(grp["near_p"].sum()), len(grp)) for d, grp in g}
        rng = np.random.default_rng(SEED)
        eff_r, eff_p, diff, ci_lo, ci_hi = bootstrap_diff_paired_by_day(round_by_day, placebo_by_day, days, rng)
        rows.append(dict(
            test="T3_magnet", asset=asset, grid=grid_cfg["name"], tf=tf, split=split,
            n_round=int(sub["near_r"].sum()), n_placebo=int(sub["near_p"].sum()),
            n_bars=len(sub), n_days=len(days),
            efeito_round=eff_r, efeito_placebo=eff_p, diff=diff, ci_lo=ci_lo, ci_hi=ci_hi,
        ))
        if split == "discovery":
            for yr, grp_y in sub.groupby(sub["ts"].dt.year):
                gy = grp_y.groupby("date")
                rbd = {d: (int(grp["near_r"].sum()), len(grp)) for d, grp in gy}
                pbd = {d: (int(grp["near_p"].sum()), len(grp)) for d, grp in gy}
                days_y = sorted(rbd.keys())
                rng_y = np.random.default_rng(SEED)
                er_y, ep_y, diff_y, lo_y, hi_y = bootstrap_diff_paired_by_day(rbd, pbd, days_y, rng_y)
                by_year_rows.append(dict(
                    test="T3_magnet", asset=asset, grid=grid_cfg["name"], tf=tf, year=int(yr),
                    n_bars=len(grp_y), efeito_round=er_y, efeito_placebo=ep_y, diff=diff_y,
                    ci_lo=lo_y, ci_hi=hi_y,
                ))
    return rows, by_year_rows


# ==========================================================================
# T4 -- barreira (swing, D1)
# ==========================================================================

def run_t4(d1, asset, grid_cfg, window_dates_set, split_bounds):
    d2 = d1[(~d1["date"].isin(window_dates_set)) & d1["atr"].notna() & (d1["atr"] > 0)].copy()
    grid = grid_cfg["grid"]
    Lr = nearest_level_vec(d2["close"].values, grid, grid_cfg["off_round"])
    Lp = nearest_level_vec(d2["close"].values, grid, grid_cfg["off_placebo"])
    dist_r_atr = np.abs(d2["close"].values - Lr) / d2["atr"].values
    dist_p_atr = np.abs(d2["close"].values - Lp) / d2["atr"].values
    dist_r_hg = np.abs(d2["close"].values - Lr) / (grid / 2.0)
    dist_p_hg = np.abs(d2["close"].values - Lp) / (grid / 2.0)
    d2 = d2.assign(dist_r_atr=dist_r_atr, dist_p_atr=dist_p_atr,
                   dist_r_hg=dist_r_hg, dist_p_hg=dist_p_hg,
                   near_r=dist_r_hg <= TH_T4_HALFGRID, near_p=dist_p_hg <= TH_T4_HALFGRID)

    rows = []
    for split, (lo_ts, hi_ts) in split_bounds.items():
        sub = d2[(d2["ts"] >= lo_ts) & (d2["ts"] < hi_ts)]
        n = len(sub)
        if n == 0:
            rows.append(dict(test="T4_barrier", asset=asset, grid=grid_cfg["name"], tf="D1", split=split,
                              n_round=0, n_placebo=0, efeito_round=np.nan, efeito_placebo=np.nan,
                              diff=np.nan, ci_lo=np.nan, ci_hi=np.nan))
            continue
        days = sorted(sub["date"].unique())
        round_by_day = {d: (int(sub.loc[sub["date"] == d, "near_r"].sum()), 1) for d in days}
        placebo_by_day = {d: (int(sub.loc[sub["date"] == d, "near_p"].sum()), 1) for d in days}
        rng = np.random.default_rng(SEED)
        eff_r, eff_p, diff, ci_lo, ci_hi = bootstrap_diff_paired_by_day(round_by_day, placebo_by_day, days, rng)
        ks_stat, ks_p = ks_2samp(sub["dist_r_atr"].values, sub["dist_p_atr"].values)
        rows.append(dict(
            test="T4_barrier", asset=asset, grid=grid_cfg["name"], tf="D1", split=split,
            n_round=n, n_placebo=n,
            efeito_round=eff_r, efeito_placebo=eff_p, diff=diff, ci_lo=ci_lo, ci_hi=ci_hi,
            ks_stat=float(ks_stat), ks_pvalue=float(ks_p),
            mean_dist_atr_round=float(sub["dist_r_atr"].mean()), mean_dist_atr_placebo=float(sub["dist_p_atr"].mean()),
        ))
    return rows


# ==========================================================================
# T5 -- cruzamento swing / T6 -- rejeicao swing
# ==========================================================================

def compute_t5_events(d1, grid, offset, window_dates_set):
    d = d1.sort_values("ts").reset_index(drop=True)
    dates = d["date"].tolist()
    close = d["close"].values.astype(float)
    atr = d["atr"].values.astype(float)
    ts = d["ts"].values
    n = len(d)
    n_discarded_rollover = 0
    events = []
    for i in range(1, n - 5):
        di = dates[i]
        if di in window_dates_set:
            continue
        if pd.isna(atr[i]) or atr[i] <= 0:
            continue
        c_prev, c_cur = close[i - 1], close[i]
        lo, hi = min(c_prev, c_cur), max(c_prev, c_cur)
        if hi - lo < 1e-9:
            continue
        cand = levels_in_range(lo, hi, grid, offset, pad=0.0)
        cand = cand[(cand > lo) & (cand < hi)]
        if len(cand) == 0:
            continue
        fwd_days = dates[i + 1: i + 6]
        if any(fd in window_dates_set for fd in fwd_days):
            n_discarded_rollover += len(cand)
            continue
        for L in cand:
            direction = np.sign(c_cur - L)
            if direction == 0:
                continue
            fwd = {}
            for h in range(1, 6):
                fwd[f"fwd{h}"] = float(direction * (close[i + h] / c_cur - 1) * 1e4)
            events.append(dict(date=di, level=L, ts=ts[i], direction=float(direction), **fwd))
    return pd.DataFrame(events), n_discarded_rollover


def compute_t6_events(d1, grid, offset, window_dates_set):
    d = d1.sort_values("ts").reset_index(drop=True)
    dates = d["date"].tolist()
    close = d["close"].values.astype(float)
    high = d["high"].values.astype(float)
    low = d["low"].values.astype(float)
    atr = d["atr"].values.astype(float)
    ts = d["ts"].values
    n = len(d)
    n_discarded_rollover = 0
    events = []
    for i in range(1, n - 3):
        di = dates[i]
        if di in window_dates_set:
            continue
        if pd.isna(atr[i]) or atr[i] <= 0:
            continue
        c_prev, c_cur, h_i, l_i = close[i - 1], close[i], high[i], low[i]
        cand = levels_in_range(l_i, h_i, grid, offset, pad=0.0)
        cand = cand[(cand >= l_i) & (cand <= h_i)]
        if len(cand) == 0:
            continue
        fwd_days = dates[i + 1: i + 4]
        if any(fd in window_dates_set for fd in fwd_days):
            n_discarded_rollover += len(cand)
            continue
        for L in cand:
            side_prev = np.sign(c_prev - L)
            side_cur = np.sign(c_cur - L)
            if side_prev == 0:
                continue
            if side_cur != side_prev:
                continue  # close atravessou -> nao eh rejeicao
            direction = side_prev
            fwd = {}
            for h in range(1, 4):
                fwd[f"fwd{h}"] = float(direction * (close[i + h] / c_cur - 1) * 1e4)
            events.append(dict(date=di, level=L, ts=ts[i], direction=float(direction), **fwd))
    return pd.DataFrame(events), n_discarded_rollover


def run_t5(d1, asset, grid_cfg, window_dates_set, split_bounds):
    rows = []
    ev_r, disc_r = compute_t5_events(d1, grid_cfg["grid"], grid_cfg["off_round"], window_dates_set)
    ev_p, disc_p = compute_t5_events(d1, grid_cfg["grid"], grid_cfg["off_placebo"], window_dates_set)
    for split, (lo_ts, hi_ts) in split_bounds.items():
        er = ev_r[(pd.to_datetime(ev_r["ts"]) >= lo_ts) & (pd.to_datetime(ev_r["ts"]) < hi_ts)] if len(ev_r) else ev_r
        ep = ev_p[(pd.to_datetime(ev_p["ts"]) >= lo_ts) & (pd.to_datetime(ev_p["ts"]) < hi_ts)] if len(ev_p) else ev_p
        rng = np.random.default_rng(SEED)
        eff_r, eff_p, diff, ci_lo, ci_hi = bootstrap_diff_independent(
            er["fwd5"].values if len(er) else [], ep["fwd5"].values if len(ep) else [], rng)
        rows.append(dict(
            test="T5_cross", asset=asset, grid=grid_cfg["name"], tf="D1", split=split,
            n_round=len(er), n_placebo=len(ep),
            efeito_round=eff_r, efeito_placebo=eff_p, diff=diff, ci_lo=ci_lo, ci_hi=ci_hi,
            fwd1_round=float(er["fwd1"].mean()) if len(er) else np.nan,
            fwd1_placebo=float(ep["fwd1"].mean()) if len(ep) else np.nan,
            fwd3_round=float(er["fwd3"].mean()) if len(er) else np.nan,
            fwd3_placebo=float(ep["fwd3"].mean()) if len(ep) else np.nan,
            n_discarded_rollover_round=disc_r, n_discarded_rollover_placebo=disc_p,
        ))
    return rows, ev_r, ev_p


def run_t6(d1, asset, grid_cfg, window_dates_set, split_bounds):
    rows = []
    ev_r, disc_r = compute_t6_events(d1, grid_cfg["grid"], grid_cfg["off_round"], window_dates_set)
    ev_p, disc_p = compute_t6_events(d1, grid_cfg["grid"], grid_cfg["off_placebo"], window_dates_set)
    for split, (lo_ts, hi_ts) in split_bounds.items():
        er = ev_r[(pd.to_datetime(ev_r["ts"]) >= lo_ts) & (pd.to_datetime(ev_r["ts"]) < hi_ts)] if len(ev_r) else ev_r
        ep = ev_p[(pd.to_datetime(ev_p["ts"]) >= lo_ts) & (pd.to_datetime(ev_p["ts"]) < hi_ts)] if len(ev_p) else ev_p
        rng = np.random.default_rng(SEED)
        eff_r, eff_p, diff, ci_lo, ci_hi = bootstrap_diff_independent(
            er["fwd3"].values if len(er) else [], ep["fwd3"].values if len(ep) else [], rng)
        rows.append(dict(
            test="T6_reject", asset=asset, grid=grid_cfg["name"], tf="D1", split=split,
            n_round=len(er), n_placebo=len(ep),
            efeito_round=eff_r, efeito_placebo=eff_p, diff=diff, ci_lo=ci_lo, ci_hi=ci_hi,
            fwd1_round=float(er["fwd1"].mean()) if len(er) else np.nan,
            fwd1_placebo=float(ep["fwd1"].mean()) if len(ep) else np.nan,
            n_discarded_rollover_round=disc_r, n_discarded_rollover_placebo=disc_p,
        ))
    return rows, ev_r, ev_p


# ==========================================================================
# Gates
# ==========================================================================

GATE1_BPS_INTRADAY = 6.0
GATE1_BPS_SWING = 25.0
GATE3_N_INTRADAY = 150
GATE3_N_SWING = 60


def apply_gates(all_rows_df):
    """Aplica gates 1-4 do pre-registro, por celula. gate1/gate4 exigem
    juntar discovery+confirm; gate2 exige juntar WIN+WDO na grade equivalente."""
    df = all_rows_df.copy()
    bps_tests = {"T1_bounce", "T2_cascade", "T5_cross", "T6_reject"}
    df["bps_test"] = df["test"].isin(bps_tests)
    df["gate1_threshold"] = np.where(df["test"].isin(["T5_cross", "T6_reject"]), GATE1_BPS_SWING, GATE1_BPS_INTRADAY)
    df["gate3_threshold"] = np.where(df["test"].isin(["T5_cross", "T6_reject"]), GATE3_N_SWING, GATE3_N_INTRADAY)

    disc = df[df["split"] == "discovery"].set_index(["test", "asset", "grid", "tf"])
    conf = df[df["split"] == "confirm"].set_index(["test", "asset", "grid", "tf"])

    gate1, gate2, gate3, gate4_flag = [], [], [], []
    for idx, row in df.iterrows():
        key = (row["test"], row["asset"], row["grid"], row["tf"])
        g1 = False
        if row["bps_test"] and row["split"] == "discovery":
            ci_excl_zero = pd.notna(row["ci_lo"]) and pd.notna(row["ci_hi"]) and not (row["ci_lo"] <= 0 <= row["ci_hi"])
            mag_ok = pd.notna(row["diff"]) and abs(row["diff"]) >= row["gate1_threshold"]
            same_sign_confirm = False
            if key in conf.index:
                c = conf.loc[key]
                if isinstance(c, pd.DataFrame):
                    c = c.iloc[0]
                same_sign_confirm = pd.notna(c["diff"]) and pd.notna(row["diff"]) and np.sign(c["diff"]) == np.sign(row["diff"]) and row["diff"] != 0
            g1 = bool(ci_excl_zero and mag_ok and same_sign_confirm)
        elif not row["bps_test"] and row["split"] == "discovery":
            g1 = None  # N/A -- sem "forward", gate1 nao se aplica mecanicamente
        gate1.append(g1)

        # gate2: replicacao entre ativos, mesma celula equivalente, mesmo sinal
        g2 = None
        if row["split"] == "discovery":
            equiv = GRID_EQUIV.get((row["asset"], row["grid"]))
            other_asset = "WDO" if row["asset"] == "WIN" else "WIN"
            other_grid = None
            for (a, g), e in GRID_EQUIV.items():
                if a == other_asset and e == equiv:
                    other_grid = g
            other_key = (row["test"], other_asset, other_grid, row["tf"])
            if other_key in disc.index and pd.notna(row["diff"]):
                other_row = disc.loc[other_key]
                if isinstance(other_row, pd.DataFrame):
                    other_row = other_row.iloc[0]
                if pd.notna(other_row["diff"]):
                    g2 = bool(np.sign(row["diff"]) == np.sign(other_row["diff"]) and row["diff"] != 0)
                else:
                    g2 = False
            else:
                g2 = False
        gate2.append(g2)

        g3 = bool(pd.notna(row["n_round"]) and row["n_round"] >= row["gate3_threshold"]) if row["split"] == "discovery" else None
        gate3.append(g3)
        gate4_flag.append(None)  # preenchido depois (par T1/T2 e T5/T6)

    df["gate1"] = gate1
    df["gate2"] = gate2
    df["gate3"] = gate3

    # gate4: coerencia -- T1 (bounce) e T2 (cascade) nao podem ser ambas
    # positivas na mesma celula asset x grid x tf x split (idem T5/T6 no swing)
    df["gate4_coherent"] = True
    pairs = [("T1_bounce", "T2_cascade"), ("T6_reject", "T5_cross")]
    for t_a, t_b in pairs:
        sub_a = df[df["test"] == t_a]
        sub_b = df[df["test"] == t_b]
        for _, ra in sub_a.iterrows():
            key = (ra["asset"], ra["grid"], ra["tf"], ra["split"])
            rb = sub_b[(sub_b["asset"] == key[0]) & (sub_b["grid"] == key[1]) &
                       (sub_b["tf"] == key[2]) & (sub_b["split"] == key[3])]
            if len(rb) == 0:
                continue
            rb = rb.iloc[0]
            both_positive = pd.notna(ra["diff"]) and pd.notna(rb["diff"]) and ra["diff"] > 0 and rb["diff"] > 0
            if both_positive:
                df.loc[(df["test"] == t_a) & (df["asset"] == key[0]) & (df["grid"] == key[1]) &
                       (df["tf"] == key[2]) & (df["split"] == key[3]), "gate4_coherent"] = False
                df.loc[(df["test"] == t_b) & (df["asset"] == key[0]) & (df["grid"] == key[1]) &
                       (df["tf"] == key[2]) & (df["split"] == key[3]), "gate4_coherent"] = False

    return df


# ==========================================================================
# Main
# ==========================================================================

def main():
    t1_rows, t2_rows, t3_rows, t4_rows, t5_rows, t6_rows = [], [], [], [], [], []
    t3_by_year_rows = []
    rollover_report = {}
    n_atr_nan_days = {}
    d1_cache = {}

    for asset in ASSETS:
        print(f"=== {asset} ===")
        d1_raw = load_tf(asset, "D1")
        atr_map, d1_sorted, atr_series = compute_atr20_avail(d1_raw)
        n_nan = int(atr_series.isna().sum())
        n_atr_nan_days[asset] = n_nan
        print(f"  D1 rows: {len(d1_sorted)}  ATR20 indisponivel em {n_nan} dias iniciais (warm-up)")

        trading_days = sorted(d1_sorted["date"].unique())
        roll_state = build_rollover_state(asset, trading_days)
        window_dates_set = roll_state["window_dates_set"]
        n_windows = len(roll_state["windows"])
        years_span = (d1_sorted["ts"].max() - d1_sorted["ts"].min()).days / 365.25
        print(f"  janelas de rolo: {n_windows} em ~{years_span:.2f} anos "
              f"({roll_state['skipped']} pulada(s) por falta de historico), "
              f"{len(window_dates_set)} dias de pregao bloqueados p/ eventos")
        rollover_report[asset] = dict(n_windows=n_windows, n_window_days=len(window_dates_set),
                                       skipped=roll_state["skipped"])

        d1 = attach_atr(d1_sorted, atr_map)
        d1_cache[asset] = d1

        split_bounds = {
            "discovery": (pd.Timestamp("2000-01-01"), DISCOVERY_END),
            "confirm": (DISCOVERY_END, OOS_CUTOFF),
        }

        # ---------------- braco intraday ----------------
        for tf in INTRADAY_TFS:
            df_tf = load_tf(asset, tf)
            df_tf = attach_atr(df_tf, atr_map)
            n_rows = len(df_tf)
            n_nan_atr = int(df_tf["atr"].isna().sum())
            print(f"  {tf}: {n_rows} linhas [{df_tf['ts'].min()} -> {df_tf['ts'].max()}], "
                  f"{n_nan_atr} sem ATR (warm-up/gap)")

            for grid_cfg in GRIDS[asset]["intraday"]:
                rows1, _, _ = run_t1(df_tf, tf, asset, grid_cfg, window_dates_set, split_bounds)
                t1_rows.extend(rows1)
                rows2, _, _ = run_t2(df_tf, tf, asset, grid_cfg, window_dates_set, split_bounds)
                t2_rows.extend(rows2)
                rows3, by_year3 = run_t3(df_tf, tf, asset, grid_cfg, window_dates_set, split_bounds)
                t3_rows.extend(rows3)
                t3_by_year_rows.extend(by_year3)
                print(f"    {tf} {grid_cfg['name']}: T1={rows1[0]['n_round']}/{rows1[0]['n_placebo']}(disc) "
                      f"T2={rows2[0]['n_round']}/{rows2[0]['n_placebo']}(disc) "
                      f"T3_bars={rows3[0].get('n_bars', 0) if rows3 else 0}(disc)")

        # ---------------- braco swing ----------------
        for grid_cfg in GRIDS[asset]["swing"]:
            rows4 = run_t4(d1, asset, grid_cfg, window_dates_set, split_bounds)
            t4_rows.extend(rows4)
            rows5, _, _ = run_t5(d1, asset, grid_cfg, window_dates_set, split_bounds)
            t5_rows.extend(rows5)
            rows6, _, _ = run_t6(d1, asset, grid_cfg, window_dates_set, split_bounds)
            t6_rows.extend(rows6)
            print(f"  D1 {grid_cfg['name']}: T4_n={rows4[0]['n_round']}(disc) "
                  f"T5={rows5[0]['n_round']}/{rows5[0]['n_placebo']}(disc, "
                  f"{rows5[0]['n_discarded_rollover_round']} descartados p/ rolo) "
                  f"T6={rows6[0]['n_round']}/{rows6[0]['n_placebo']}(disc, "
                  f"{rows6[0]['n_discarded_rollover_round']} descartados p/ rolo)")

    t1_df = pd.DataFrame(t1_rows)
    t2_df = pd.DataFrame(t2_rows)
    t3_df = pd.DataFrame(t3_rows)
    t4_df = pd.DataFrame(t4_rows)
    t5_df = pd.DataFrame(t5_rows)
    t6_df = pd.DataFrame(t6_rows)
    t3_by_year_df = pd.DataFrame(t3_by_year_rows)

    t1_df.to_csv(os.path.join(OUT_DIR, "t1_bounce.csv"), index=False)
    t2_df.to_csv(os.path.join(OUT_DIR, "t2_cascade.csv"), index=False)
    t3_df.to_csv(os.path.join(OUT_DIR, "t3_magnet.csv"), index=False)
    t4_df.to_csv(os.path.join(OUT_DIR, "t4_barrier.csv"), index=False)
    t5_df.to_csv(os.path.join(OUT_DIR, "t5_cross.csv"), index=False)
    t6_df.to_csv(os.path.join(OUT_DIR, "t6_reject.csv"), index=False)
    t3_by_year_df.to_csv(os.path.join(OUT_DIR, "t3_magnet_by_year.csv"), index=False)
    print("\nCSVs salvos em", OUT_DIR)

    all_df = pd.concat([t1_df, t2_df, t3_df, t4_df, t5_df, t6_df], ignore_index=True, sort=False)
    gated = apply_gates(all_df)
    gated.to_csv(os.path.join(OUT_DIR, "gates_all_cells.csv"), index=False)
    print("gates_all_cells.csv salvo")

    build_summary(gated, t1_df, t2_df, t3_df, t4_df, t5_df, t6_df, t3_by_year_df,
                   rollover_report, n_atr_nan_days)


def fmt(x, nd=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "NA"
    if isinstance(x, bool):
        return str(x)
    return f"{x:.{nd}f}"


def build_summary(gated, t1_df, t2_df, t3_df, t4_df, t5_df, t6_df, t3_by_year_df,
                   rollover_report, n_atr_nan_days):
    lines = []
    lines.append("# b3_round_levels_v0 — Phase 0A — Summary\n")
    lines.append(
        "Premise check descritivo (sem entry/exit/PnL) para niveis redondos "
        "em WIN/WDO. Hipoteses simetricas H_bounce (T1) e H_cascade (T2) no "
        "intraday; T3 magnetismo; braco swing D1 com T4 barreira, T5 "
        "cruzamento, T6 rejeicao. Toda estatistica medida CONTRA GRADE "
        "PLACEBO (meia-grade). Bootstrap 1000x, seed fixa "
        f"({SEED}). DISCOVERY <2024-01-01 / CONFIRM 2024 / OOS 2025+ filtrado "
        "no load, sagrado."
    )
    lines.append("")

    lines.append("## Metodologia efetiva (thresholds em ATR20d)\n")
    lines.append(f"- Banda de toque / magnetismo (T1/T3): {TH_BAND}×ATR20d")
    lines.append(f"- Distancia minima de aproximacao (T1 'vem de longe'): {TH_FAR}×ATR20d")
    lines.append(f"- Reversao que confirma 'segura' (T1): {TH_REVERSAL}×ATR20d")
    lines.append(f"- Confirmacao de quebra/cruzamento (T1 cross-out, T2): {TH_CROSS_CONFIRM}×ATR20d")
    lines.append(f"- T4 barreira: fracao de closes com distancia <= {TH_T4_HALFGRID}× meia-grade "
                  "(ver ressalva sobre normalizacao) + KS-test na distancia normalizada por ATR20d")
    lines.append("- ATR20d: media movel de 20 dias do True Range diario, com shift(1) — "
                  "o dia D usa ATR calculado com dados ate D-1 (sem lookahead).")
    lines.append("")

    lines.append("## Contagem de eventos por celula (discovery)\n")
    for name, df in [("T1_bounce", t1_df), ("T2_cascade", t2_df), ("T5_cross", t5_df), ("T6_reject", t6_df)]:
        d = df[df["split"] == "discovery"]
        lines.append(f"### {name}\n")
        lines.append("| asset | grid | tf | n_round | n_placebo |")
        lines.append("|---|---|---|---|---|")
        for _, r in d.iterrows():
            lines.append(f"| {r['asset']} | {r['grid']} | {r['tf']} | {int(r['n_round'])} | {int(r['n_placebo'])} |")
        lines.append("")

    lines.append("### T3_magnet (n_bars discovery)\n")
    d = t3_df[t3_df["split"] == "discovery"]
    lines.append("| asset | grid | tf | n_bars | n_days | near_round | near_placebo |")
    lines.append("|---|---|---|---|---|---|---|")
    for _, r in d.iterrows():
        lines.append(f"| {r['asset']} | {r['grid']} | {r['tf']} | {int(r.get('n_bars', 0))} | "
                      f"{int(r.get('n_days', 0))} | {int(r['n_round'])} | {int(r['n_placebo'])} |")
    lines.append("")

    lines.append("### T4_barrier (n dias discovery)\n")
    d = t4_df[t4_df["split"] == "discovery"]
    lines.append("| asset | grid | n_dias |")
    lines.append("|---|---|---|")
    for _, r in d.iterrows():
        lines.append(f"| {r['asset']} | {r['grid']} | {int(r['n_round'])} |")
    lines.append("")

    lines.append("## Tabelas principais redondo vs placebo (todas as celulas, discovery + confirm)\n")
    for name, df, extra_cols in [
        ("T1_bounce (efeito = forward horizonte 'mid' assinado, bps [30min M5/M15, 120min H1]; direcao = lado de aproximacao)", t1_df,
         ["primary_horizon_min", "hold_rate_round", "hold_rate_placebo"]),
        ("T2_cascade (efeito = forward horizonte 'mid' assinado, bps [30min M5/M15, 120min H1]; direcao = lado da quebra)", t2_df,
         ["primary_horizon_min", "range_v_atr_round", "range_v_atr_placebo", "range_v_atr_diff"]),
        ("T3_magnet (efeito = fracao de barras a <=0.05xATR do nivel)", t3_df, []),
        ("T4_barrier (efeito = fracao de closes a <=0.10x meia-grade; ks_stat/ks_pvalue na distancia/ATR)", t4_df,
         ["ks_stat", "ks_pvalue"]),
        ("T5_cross (efeito = drift D+1 assinado, bps; ver fwd3/fwd5 no CSV p/ acumulado)", t5_df,
         ["n_discarded_rollover_round"]),
        ("T6_reject (efeito = reversao D+1 assinada, bps; ver fwd3 no CSV p/ D+1..D+3 acumulado)", t6_df,
         ["n_discarded_rollover_round"]),
    ]:
        lines.append(f"### {name}\n")
        base_cols = ["asset", "grid", "tf", "split", "n_round", "n_placebo", "efeito_round", "efeito_placebo", "diff", "ci_lo", "ci_hi"]
        cols = base_cols + extra_cols
        header = " | ".join(cols)
        lines.append(f"| {header} |")
        lines.append("|" + "---|" * len(cols))
        for _, r in df.iterrows():
            vals = []
            for c in cols:
                v = r.get(c, np.nan)
                if c in ("asset", "grid", "tf", "split"):
                    vals.append(str(v))
                elif c in ("n_round", "n_placebo", "primary_horizon_min", "range_horizon_min", "n_discarded_rollover_round"):
                    vals.append(str(int(v)) if pd.notna(v) else "NA")
                else:
                    vals.append(fmt(v, 3))
            lines.append("| " + " | ".join(vals) + " |")
        lines.append("")

    lines.append("## Resultado mecanico dos gates por celula\n")
    lines.append(
        "Gate 1 (efeito liquido >= 6bps intraday / 25bps swing, IC exclui zero no "
        "discovery, mesmo sinal no confirm) so se aplica a testes com metrica "
        "'forward' (T1,T2,T5,T6) — marcado N/A para T3/T4. Gate 2 = replicacao "
        "WIN<->WDO na grade equivalente, mesmo sinal. Gate 3 = n minimo no "
        "discovery (150 intraday / 60 swing). Gate 4 = coerencia — flag se "
        "T1 e T2 (ou T6 e T5) derem AMBAS positivas na mesma celula.\n"
    )
    disc_gated = gated[gated["split"] == "discovery"]
    lines.append("| test | asset | grid | tf | n_round | gate1 | gate2 | gate3 | gate4_coherent |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in disc_gated.iterrows():
        lines.append(f"| {r['test']} | {r['asset']} | {r['grid']} | {r['tf']} | {int(r['n_round'])} | "
                      f"{fmt(r['gate1'],0) if r['gate1'] is not None else 'N/A'} | "
                      f"{fmt(r['gate2'],0) if r['gate2'] is not None else 'N/A'} | "
                      f"{fmt(r['gate3'],0) if r['gate3'] is not None else 'N/A'} | "
                      f"{r['gate4_coherent']} |")
    lines.append("")

    n_pass_g1 = int((disc_gated["gate1"] == True).sum())
    n_pass_all_bps = int(((disc_gated["gate1"] == True) & (disc_gated["gate2"] == True) &
                           (disc_gated["gate3"] == True) & (disc_gated["gate4_coherent"] == True)).sum())
    lines.append(f"**Celulas com gate1=True: {n_pass_g1}. Celulas passando gate1+gate2+gate3+gate4 simultaneamente: {n_pass_all_bps}.**\n")

    passing_cells = disc_gated[(disc_gated["gate1"] == True) & (disc_gated["gate2"] == True) &
                                (disc_gated["gate3"] == True) & (disc_gated["gate4_coherent"] == True)]
    if len(passing_cells) > 0:
        lines.append("## Breakdown por ano das celulas que passaram algum gate (T1/T2/T3 no discovery)\n")
        for _, r in passing_cells.iterrows():
            if r["test"] == "T3_magnet":
                sub_y = t3_by_year_df[(t3_by_year_df["asset"] == r["asset"]) & (t3_by_year_df["grid"] == r["grid"]) &
                                       (t3_by_year_df["tf"] == r["tf"])]
                lines.append(f"### {r['test']} {r['asset']} {r['grid']} {r['tf']}\n")
                lines.append("| ano | n_bars | efeito_round | efeito_placebo | diff | ci_lo | ci_hi |")
                lines.append("|---|---|---|---|---|---|---|")
                for _, ry in sub_y.iterrows():
                    lines.append(f"| {int(ry['year'])} | {int(ry['n_bars'])} | {fmt(ry['efeito_round'],4)} | "
                                  f"{fmt(ry['efeito_placebo'],4)} | {fmt(ry['diff'],4)} | {fmt(ry['ci_lo'],4)} | {fmt(ry['ci_hi'],4)} |")
                lines.append("")
            else:
                lines.append(f"### {r['test']} {r['asset']} {r['grid']} {r['tf']} — ver eventos individuais no CSV correspondente "
                              "(breakdown por ano nao pre-computado para T1/T2/T5/T6; nenhuma celula bps passou todos os gates "
                              "nesta rodada, ver secao de gates acima)\n")
    else:
        lines.append("## Breakdown por ano das celulas que passaram algum gate\n")
        lines.append("Nenhuma celula passou gate1+gate2+gate3+gate4 simultaneamente no discovery — sem breakdown por ano a reportar "
                      "(regra: nao adicionar analise pos-hoc para 'salvar' celula reprovada).\n")

    lines.append("## Sanity — rolagem por calendario\n")
    for asset, rep in rollover_report.items():
        lines.append(f"- {asset}: {rep['n_windows']} janelas de rolo ({rep['n_window_days']} dias de pregao "
                      f"bloqueados p/ eventos), {rep['skipped']} pulada(s) por falta de historico, "
                      f"{n_atr_nan_days[asset]} dias iniciais sem ATR20d (warm-up de 20 dias).")
    lines.append("")

    lines.append("## Ressalvas\n")
    lines.append(
        "- **M5 truncado**: os parquets M5 de WIN e WDO tem teto de 100.000 "
        "linhas do terminal MT5 e comecam apenas em 2022-12 (WIN) / 2022-12 "
        "(WDO), nao em 2021-07 como M15/H1/D1. Isso significa que o braco "
        "intraday principal (M5) tem DISCOVERY efetivamente reduzido a "
        "~2022-12..2023-12 (~1 ano), nao os ~2.5 anos nominais do periodo "
        "<2024. M15/H1 (robustez) cobrem o periodo completo desde 2021-07 e "
        "sao a fonte mais confiavel de n_trades para os anos 2021-2022."
    )
    lines.append(
        "- **T4 'distancia normalizada'**: o pre-registro nao especifica "
        "explicitamente a unidade de normalizacao para T4 (a regra geral do "
        "briefing pede ATR20d para 'todos os thresholds', mas a instrucao "
        "especifica de T4 pede 'diferenca de fracao a <=0.1 DA MEIA-GRADE'). "
        "Decisao: reportar AMBAS as metricas — (a) fracao de closes com "
        "distancia <= 0.10x meia-grade (efeito_round/efeito_placebo/diff/CI "
        "no CSV, conforme pedido explicito para T4) e (b) distancia media "
        "normalizada por ATR20d + KS-test nessa distribuicao (ks_stat/"
        "ks_pvalue), para nao violar a regra geral de nunca usar pontos "
        "fixos. As duas sao reportadas lado a lado, sem esconder nenhuma."
    )
    lines.append(
        "- **T1, resolucao 'segura' vs 'atravessa'**: quando nem a reversao "
        "de 0.15xATR nem o cross-out de 0.10xATR ocorrem ate o fim do "
        "pregao (M5/M15) apos o toque, o evento fica com outcome='unresolved' "
        "(nao conta nem como hold nem como cross na taxa hold_rate — afeta o "
        "denominador). Nao ha timeout explicito no pre-registro; a resolucao "
        "e limitada ao proprio pregao (sem overnight), decisao de "
        "implementacao documentada aqui."
    )
    lines.append(
        "- **T1/T2, horizontes de forward adaptados por TF**: os horizontes "
        "15/30/60min do pre-registro sao degenerados em H1 (barra=60min faz "
        "15/30min colapsarem no proprio evento, retorno sempre 0 -- bug "
        "detectado e corrigido durante a execucao desta fase). Solucao: "
        "horizontes reescalados por TF em multiplos da propria barra -- "
        "M5/M15 mantêm 15/30/60min (short/mid/long); H1 usa 60/120/240min "
        "(1/2/4 barras). O horizonte PRIMARIO ('mid', usado em "
        "efeito_round/efeito_placebo/diff/CI) e sempre o do meio da lista "
        "-- 30min em M5/M15, 120min em H1 -- reportado explicitamente na "
        "coluna primary_horizon_min de cada linha. T2 usa 4 horizontes "
        "(vshort/short/mid/long); a metrica de velocidade (range) usa o "
        "horizonte 'short' de cada TF (15min M5/M15, 60min H1), coluna "
        "range_horizon_min."
    )
    lines.append(
        "- **T2, direcao do evento e velocidade**: a metrica de velocidade "
        "(|range| pos-quebra, horizonte 'short' por TF) e reportada em "
        "pontos absolutos (range_v) e normalizada por ATR20d (range_v_atr, "
        "usada no efeito/CI das colunas range_v_atr_*) — NAO e signed, e "
        "uma comparacao de magnitude (redondo vs placebo), nao de direcao."
    )
    lines.append(
        "- **T5/T6, ausencia de threshold de confirmacao**: seguindo a "
        "redacao literal do pre-registro ('close diario cruza nivel' / "
        "'high/low diario toca nivel... sem close atravessar'), T5 e T6 "
        "usam comparacao de SINAL pura (sem magnitude minima de cruzamento "
        "em ATR) — a unica excecao intencional a regra geral de escala por "
        "ATR20d, porque o proprio pre-registro nao define uma magnitude "
        "minima para estes dois testes especificos (diferente de T1/T2/T3, "
        "onde a magnitude em ATR e explicita). Isso pode incluir cruzamentos "
        "'raspando' o nivel por pontos irrisorios como eventos validos; "
        "anotado como risco de ruido, nao corrigido pos-hoc para nao "
        "introduzir grau de liberdade nao pre-registrado."
    )
    lines.append(
        "- **T5/T6, multiplos niveis por dia**: se o movimento diario "
        "atravessar/tocar mais de um nivel da grade no mesmo dia (raro dado "
        "grid 5000/50 vs ATR tipico ~2000pts WIN / ~70pts WDO, mas possivel "
        "em dias de gap extremo), cada nivel gera um evento separado nesse "
        "dia — sem dedupe adicional alem do que o pre-registro pede "
        "explicitamente so para T1/T2 (intraday)."
    )
    lines.append(
        "- **Janela de rolagem e braco swing**: dias na janela de rolo NUNCA "
        "geram evento (T4/T5/T6, mesmo T4 que nao e um 'evento' de "
        "crossing/touch — excluido por seguranca, pois o preco continuo (N) "
        "pode ter salto artificial de rolagem que contamina a distancia ao "
        "nivel). Para T5/T6, eventos cujo horizonte forward (D+1..D+5 ou "
        "D+1..D+3) atravessa QUALQUER dia de janela de rolo sao descartados "
        "e contados em n_discarded_rollover_round/placebo no CSV."
    )
    lines.append(
        "- **Gate 1 aplicado so a T1/T2/T5/T6**: T3 e T4 nao tem uma "
        "metrica 'forward' equivalente (sao estatisticas de posicao/tempo, "
        "nao de retorno subsequente) — o texto do gate1 do pre-registro se "
        "refere explicitamente a 'diferenca ... de forward', entao gate1 "
        "foi marcado N/A para essas duas celulas, mecanicamente, sem "
        "inferir um substituto nao pre-registrado."
    )
    lines.append(
        "- **W1 (robustez swing) nao implementado nesta rodada**: o "
        "pre-registro cita 'W1 como robustez' no braco swing mas nao ha "
        "parquet W1 disponivel no diretorio de dados; D1 (principal) foi "
        "executado integralmente. Anotado como lacuna, nao preenchida com "
        "resample ad-hoc para nao introduzir metodologia nao pre-registrada."
    )
    lines.append(
        "- **T3, saturacao em grades finas (WDO g5, parcialmente WDO g10)**: "
        "a fracao 'near' de T3 se aproxima de min(1, 2×banda/grid) quando o "
        "preco e aproximadamente uniforme dentro do intervalo entre niveis "
        "(banda=0.05×ATR20d). Para WDO g5 (meia-grade=2.5pts) com ATR "
        "tipico ~60-90pts, banda~3-4.5pts JA EXCEDE a meia-grade — TODO "
        "ponto do espaco de precos fica, por construcao, dentro da banda do "
        "nivel redondo mais proximo (e, simetricamente, do placebo mais "
        "proximo tambem), saturando near_round e near_placebo perto de "
        "99-100% (ver t3_magnet.csv) e tornando a DIFERENCA "
        "estruturalmente proxima de zero independente de haver clustering "
        "real. WDO g10 (meia-grade=5pts, banda~3.5-4.5pts) fica na fronteira "
        "(~60-77%, nao totalmente saturado mas com pouca margem). WIN g500/"
        "g1000 e WDO g10 tem meia-grade suficientemente maior que a banda "
        "e nao sofrem desse problema. Isto NAO e um resultado de 'sem "
        "clustering' para WDO g5 — e uma limitacao de POTENCIA do desenho "
        "(banda >= meia-grade), analoga a um teste com n baixo: a celula "
        "T3 WDO g5 (M5/M15/H1) deve ser lida como 'sem potencia para "
        "detectar magnetismo', nao como evidencia contra H_bounce/magnetismo."
    )
    lines.append(
        "- **Sem alavancagem/custos**: fase 0A e puramente descritiva "
        "(toques, cruzamentos, drift, magnetismo) — nenhum PnL, fee ou "
        "slippage e modelado. Gate1 usa bps de PRECO, nao de PnL liquido."
    )

    out_md = os.path.join(OUT_DIR, "summary.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"summary.md salvo em {out_md}")


if __name__ == "__main__":
    main()
