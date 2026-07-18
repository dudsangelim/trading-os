"""
b3_or_continuation_v0 - Fase A (mapa descritivo)
Opening range + continuacao intraday, WIN e WDO, M5 e M15.

Script MECANICO: apenas computa estatisticas descritivas sobre o IS
(in-sample, ate 2024-12-31). NAO toca no OOS (2025-01-01 em diante).

Auto-suficiente: paths absolutos, sem env vars especiais.
Rodar com: python build_or_map.py
"""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\b3_or_continuation_v0\map")
OUT_DIR.mkdir(parents=True, exist_ok=True)

IS_END = pd.Timestamp("2025-01-01")  # exclusive upper bound; OOS = >= this

ASH_WEDNESDAYS = pd.to_datetime([
    "2022-03-02", "2023-02-22", "2024-02-14", "2025-03-05", "2026-02-18",
]).date
ASH_WEDNESDAYS = set(ASH_WEDNESDAYS)

WINDOWS = {
    "or15": pd.Timedelta(minutes=15),
    "or30": pd.Timedelta(minutes=30),
    "or60": pd.Timedelta(minutes=60),
}
SESSION_OPEN_TIME = pd.Timestamp("2000-01-01 09:00:00").time()

FILES = {
    ("WIN", "M5"): DATA_DIR / "WIN_cont_N_M5.parquet",
    ("WIN", "M15"): DATA_DIR / "WIN_cont_N_M15.parquet",
    ("WDO", "M5"): DATA_DIR / "WDO_cont_N_M5.parquet",
    ("WDO", "M15"): DATA_DIR / "WDO_cont_N_M15.parquet",
}

anomalies = []  # collect (instrument, tf, date, description) tuples


def log_anomaly(instrument, tf, date, desc):
    anomalies.append({"instrument": instrument, "tf": tf, "date": str(date), "desc": desc})


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_raw(instrument, tf):
    path = FILES[(instrument, tf)]
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {path}")
    df = pd.read_parquet(path)
    required_cols = {"datetime_b3", "epoch", "open", "high", "low", "close",
                      "tick_volume", "real_volume", "spread"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"{instrument} {tf}: colunas faltando: {missing}")

    df = df.sort_values("datetime_b3").reset_index(drop=True)
    df["date"] = df["datetime_b3"].dt.date
    df["time"] = df["datetime_b3"].dt.time
    df["year"] = df["datetime_b3"].dt.year
    df["weekday"] = df["datetime_b3"].dt.day_name()

    n_before = len(df)
    df = df[df["datetime_b3"] < IS_END].copy()
    n_after_oos = len(df)

    # exclude Ash Wednesdays
    df = df[~df["date"].isin(ASH_WEDNESDAYS)].copy()
    n_after_ash = len(df)

    return df, {"n_bars_raw": n_before, "n_bars_is": n_after_oos, "n_bars_is_no_ash": n_after_ash}


# ---------------------------------------------------------------------------
# Roll day computation
# ---------------------------------------------------------------------------

def nearest_wednesday(year, month, day=15):
    base = pd.Timestamp(year=year, month=month, day=day)
    best = None
    best_delta = None
    for offset in range(-6, 7):
        cand = base + pd.Timedelta(days=offset)
        if cand.weekday() == 2:  # Wednesday
            delta = abs(offset)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best = cand
    return best.date()


def compute_win_roll_days(session_dates_sorted):
    """WIN: quarta-feira mais proxima do dia 15 dos meses pares.
    Se a data nominal cair em dia sem pregao, usa o pregao seguinte."""
    session_dates_sorted = sorted(session_dates_sorted)
    session_arr = np.array(session_dates_sorted)
    years = sorted(set(d.year for d in session_dates_sorted))
    roll_days = set()
    for y in years:
        for m in (2, 4, 6, 8, 10, 12):
            nominal = nearest_wednesday(y, m, 15)
            candidates = session_arr[session_arr >= nominal]
            if len(candidates) > 0:
                roll_days.add(candidates[0])
    return roll_days


def compute_wdo_roll_days(session_dates_sorted):
    """WDO: ultimo pregao antes do 1o pregao de cada mes (vespera do
    primeiro dia util do mes). Equivale ao ultimo pregao de cada mes
    presente nos dados, exceto o ultimo mes da amostra (sem mes seguinte
    para confirmar)."""
    session_dates_sorted = sorted(session_dates_sorted)
    roll_days = set()
    for i in range(len(session_dates_sorted) - 1):
        cur = session_dates_sorted[i]
        nxt = session_dates_sorted[i + 1]
        if (cur.year, cur.month) != (nxt.year, nxt.month):
            roll_days.add(cur)
    return roll_days


# ---------------------------------------------------------------------------
# Per-session (per-day) metric computation
# ---------------------------------------------------------------------------

def compute_day_metrics(day_df, instrument, tf, date):
    """day_df: bars for a single trading day, sorted by datetime_b3."""
    day_df = day_df.sort_values("datetime_b3").reset_index(drop=True)
    first_bar_time = day_df.iloc[0]["time"]
    if first_bar_time != SESSION_OPEN_TIME:
        log_anomaly(instrument, tf, date,
                    f"primeira barra do dia as {first_bar_time}, esperado 09:00:00")

    day_open = day_df.iloc[0]["open"]
    day_close = day_df.iloc[-1]["close"]
    day_high = day_df["high"].max()
    day_low = day_df["low"].min()
    day_range_pts = day_high - day_low

    row = {
        "date": str(date),
        "weekday": day_df.iloc[0]["weekday"],
        "year": day_df.iloc[0]["year"],
        "n_bars_day": len(day_df),
        "day_open": day_open,
        "day_close": day_close,
        "day_high": day_high,
        "day_low": day_low,
        "day_range_pts": day_range_pts,
        "ret_open_close": (day_close / day_open - 1) if day_open != 0 else np.nan,
        "last_bar_time": str(day_df.iloc[-1]["time"]),
    }

    base_date = pd.Timestamp(date)

    for wname, wdelta in WINDOWS.items():
        window_end = base_date + pd.Timedelta(hours=9) + wdelta
        win_mask = (day_df["datetime_b3"] >= base_date + pd.Timedelta(hours=9)) & \
                   (day_df["datetime_b3"] < window_end)
        win_df = day_df[win_mask]

        if len(win_df) == 0:
            log_anomaly(instrument, tf, date, f"{wname}: nenhuma barra na janela")
            for k in ["high", "low", "width_pts", "width_pct", "open", "close", "ret",
                      "broke", "side", "breakout_time", "false_break", "time_to_return_min",
                      "continuation_pts", "continuation_pct", "typology",
                      "rest_of_day_ret", "day_range_ratio"]:
                row[f"{wname}_{k}"] = np.nan
            continue

        expected_bars = {
            ("M5", "or15"): 3, ("M5", "or30"): 6, ("M5", "or60"): 12,
            ("M15", "or15"): 1, ("M15", "or30"): 2, ("M15", "or60"): 4,
        }.get((tf, wname))
        if expected_bars is not None and len(win_df) < expected_bars:
            log_anomaly(instrument, tf, date,
                        f"{wname}: {len(win_df)} barras na janela, esperado {expected_bars}")

        or_open = win_df.iloc[0]["open"]
        or_high = win_df["high"].max()
        or_low = win_df["low"].min()
        or_close = win_df.iloc[-1]["close"]
        or_width_pts = or_high - or_low
        or_width_pct = or_width_pts / or_open if or_open != 0 else np.nan
        or_ret = (or_close / or_open - 1) if or_open != 0 else np.nan

        row[f"{wname}_high"] = or_high
        row[f"{wname}_low"] = or_low
        row[f"{wname}_open"] = or_open
        row[f"{wname}_close"] = or_close
        row[f"{wname}_width_pts"] = or_width_pts
        row[f"{wname}_width_pct"] = or_width_pct
        row[f"{wname}_ret"] = or_ret
        row[f"{wname}_day_range_ratio"] = (or_width_pts / day_range_pts) if day_range_pts != 0 else np.nan

        # post-window bars
        post_df = day_df[day_df["datetime_b3"] >= window_end].reset_index(drop=True)

        broke = False
        side = None
        breakout_time = None
        breakout_idx = None
        for i, r in post_df.iterrows():
            up = r["high"] > or_high
            down = r["low"] < or_low
            if up and down:
                broke, side, breakout_idx = True, "both", i
                break
            elif up:
                broke, side, breakout_idx = True, "up", i
                break
            elif down:
                broke, side, breakout_idx = True, "down", i
                break

        row[f"{wname}_broke"] = broke
        row[f"{wname}_side"] = side

        false_break = np.nan
        time_to_return_min = np.nan
        continuation_pts = np.nan
        continuation_pct = np.nan
        typology = "range"

        if broke:
            breakout_time = post_df.iloc[breakout_idx]["datetime_b3"]
            row[f"{wname}_breakout_time"] = str(breakout_time.time())

            # false break: from breakout bar onward, first bar whose close
            # falls back inside [or_low, or_high]
            false_break = False
            for j in range(breakout_idx, len(post_df)):
                c = post_df.iloc[j]["close"]
                if or_low <= c <= or_high:
                    false_break = True
                    time_to_return_min = (post_df.iloc[j]["datetime_b3"] - breakout_time).total_seconds() / 60.0
                    break

            if side in ("up", "down"):
                sign = 1 if side == "up" else -1
                level = or_high if side == "up" else or_low
                if side == "up":
                    continuation_pts = day_close - or_high
                else:
                    continuation_pts = or_low - day_close
                continuation_pct = continuation_pts / level if level != 0 else np.nan

                if side == "up":
                    if day_close > or_high + or_width_pts:
                        typology = "trend"
                    elif day_close < or_low:
                        typology = "reversal"
                    elif or_low <= day_close <= or_high:
                        typology = "range"
                    else:
                        typology = "weak_trend"
                else:
                    if day_close < or_low - or_width_pts:
                        typology = "trend"
                    elif day_close > or_high:
                        typology = "reversal"
                    elif or_low <= day_close <= or_high:
                        typology = "range"
                    else:
                        typology = "weak_trend"
            else:
                # both sides breached in same bar -> ambiguous direction
                typology = "ambiguous_both_break"
        else:
            row[f"{wname}_breakout_time"] = None
            typology = "range"

        row[f"{wname}_false_break"] = false_break
        row[f"{wname}_time_to_return_min"] = time_to_return_min
        row[f"{wname}_continuation_pts"] = continuation_pts
        row[f"{wname}_continuation_pct"] = continuation_pct
        row[f"{wname}_typology"] = typology

        # rest of day return: from end of window (or_close) to day close
        row[f"{wname}_rest_of_day_ret"] = (day_close / or_close - 1) if or_close != 0 else np.nan

    return row


def build_sessions_df(instrument, tf, roll_days):
    df, load_stats = load_raw(instrument, tf)
    rows = []
    for date, day_df in df.groupby("date"):
        row = compute_day_metrics(day_df, instrument, tf, date)
        row["instrument"] = instrument
        row["timeframe"] = tf
        row["is_roll"] = date in roll_days
        rows.append(row)
    sess = pd.DataFrame(rows)

    # tercile of relative OR width, per window, computed within this
    # instrument+timeframe sample (IS only)
    for wname in WINDOWS:
        col = f"{wname}_width_pct"
        tercile_col = f"{wname}_width_tercile"
        valid = sess[col].dropna()
        if len(valid) >= 3:
            try:
                sess[tercile_col] = pd.qcut(sess[col], 3, labels=["estreito", "medio", "largo"])
            except ValueError:
                # duplicate bin edges etc.
                sess[tercile_col] = pd.qcut(sess[col].rank(method="first"), 3,
                                             labels=["estreito", "medio", "largo"])
        else:
            sess[tercile_col] = np.nan

    return sess, load_stats


# ---------------------------------------------------------------------------
# Aggregate statistics
# ---------------------------------------------------------------------------

def safe_corr(x, y, method):
    x = pd.Series(x)
    y = pd.Series(y)
    mask = x.notna() & y.notna()
    n = mask.sum()
    if n < 3:
        return np.nan, n
    return x[mask].corr(y[mask], method=method), n


def compute_aggregates(sess, instrument, tf):
    out = {"instrument": instrument, "timeframe": tf, "n_sessions": len(sess)}

    windows_out = {}
    for wname in WINDOWS:
        w = {}

        broke_col = sess[f"{wname}_broke"]
        n_total = len(sess)
        n_broke = int(broke_col.sum())
        w["pct_breakout"] = n_broke / n_total if n_total else np.nan
        w["n_breakout"] = n_broke
        w["n_total"] = n_total

        fb_col = sess[f"{wname}_false_break"]
        n_fb_valid = fb_col.notna().sum()
        n_fb_true = (fb_col == True).sum()
        w["pct_false_break_of_breakouts"] = (n_fb_true / n_fb_valid) if n_fb_valid else np.nan
        w["n_false_break_valid"] = int(n_fb_valid)

        # correlation OR return vs rest-of-day return, geral + por ano
        ret_or = sess[f"{wname}_ret"]
        ret_rest = sess[f"{wname}_rest_of_day_ret"]
        pearson_all, n_all_p = safe_corr(ret_or, ret_rest, "pearson")
        spearman_all, n_all_s = safe_corr(ret_or, ret_rest, "spearman")
        w["corr_or_vs_rest_geral"] = {"pearson": pearson_all, "spearman": spearman_all, "n": int(n_all_p)}

        by_year = {}
        for yr, g in sess.groupby("year"):
            p, n_p = safe_corr(g[f"{wname}_ret"], g[f"{wname}_rest_of_day_ret"], "pearson")
            s, n_s = safe_corr(g[f"{wname}_ret"], g[f"{wname}_rest_of_day_ret"], "spearman")
            by_year[str(yr)] = {"pearson": p, "spearman": s, "n": int(n_p)}
        w["corr_or_vs_rest_por_ano"] = by_year

        # continuation stats by side / by year
        cont_by_side = {}
        for side_val in ["up", "down"]:
            sub = sess[sess[f"{wname}_side"] == side_val]
            cont_by_side[side_val] = {
                "n": len(sub),
                "mean_pts": float(sub[f"{wname}_continuation_pts"].mean()) if len(sub) else np.nan,
                "median_pts": float(sub[f"{wname}_continuation_pts"].median()) if len(sub) else np.nan,
                "mean_pct": float(sub[f"{wname}_continuation_pct"].mean()) if len(sub) else np.nan,
                "median_pct": float(sub[f"{wname}_continuation_pct"].median()) if len(sub) else np.nan,
            }
        w["continuation_by_side"] = cont_by_side

        cont_by_year = {}
        for yr, g in sess.groupby("year"):
            g_broke = g[g[f"{wname}_side"].isin(["up", "down"])]
            cont_by_year[str(yr)] = {
                "n": len(g_broke),
                "mean_pct": float(g_broke[f"{wname}_continuation_pct"].mean()) if len(g_broke) else np.nan,
                "median_pct": float(g_broke[f"{wname}_continuation_pct"].median()) if len(g_broke) else np.nan,
            }
        w["continuation_by_year"] = cont_by_year

        # breakout time histogram (by hour), only for broke==True
        broke_rows = sess[sess[f"{wname}_broke"] == True].copy()
        if len(broke_rows):
            hours = pd.to_datetime(broke_rows[f"{wname}_breakout_time"], format="%H:%M:%S").dt.hour
            hist = hours.value_counts().sort_index().to_dict()
            w["breakout_hour_histogram"] = {str(k): int(v) for k, v in hist.items()}
        else:
            w["breakout_hour_histogram"] = {}

        # typology
        typ_counts = sess[f"{wname}_typology"].value_counts(dropna=False)
        typ_pct = (typ_counts / len(sess) * 100).round(2)
        w["typology_pct"] = {str(k): float(v) for k, v in typ_pct.items()}
        w["typology_n"] = {str(k): int(v) for k, v in typ_counts.items()}

        # conditioned on tercile of OR width
        tercile_col = f"{wname}_width_tercile"
        cond_tercile = {}
        if tercile_col in sess.columns:
            for terc, g in sess.groupby(tercile_col, observed=True):
                if len(g) == 0:
                    continue
                p, n_p = safe_corr(g[f"{wname}_ret"], g[f"{wname}_rest_of_day_ret"], "pearson")
                cond_tercile[str(terc)] = {
                    "n": len(g),
                    "pct_breakout": float(g[f"{wname}_broke"].mean()),
                    "pct_false_break_of_breakouts": float((g[f"{wname}_false_break"] == True).sum() /
                                                            g[f"{wname}_false_break"].notna().sum())
                    if g[f"{wname}_false_break"].notna().sum() else np.nan,
                    "corr_or_vs_rest_pearson": p,
                    "typology_pct": {str(k): float(v) for k, v in
                                     (g[f"{wname}_typology"].value_counts(dropna=False) / len(g) * 100).round(2).items()},
                }
        w["conditioned_on_width_tercile"] = cond_tercile

        # conditioned on roll day vs normal
        cond_roll = {}
        for roll_flag, g in sess.groupby("is_roll"):
            if len(g) == 0:
                continue
            p, n_p = safe_corr(g[f"{wname}_ret"], g[f"{wname}_rest_of_day_ret"], "pearson")
            cond_roll[str(bool(roll_flag))] = {
                "n": len(g),
                "pct_breakout": float(g[f"{wname}_broke"].mean()),
                "pct_false_break_of_breakouts": float((g[f"{wname}_false_break"] == True).sum() /
                                                        g[f"{wname}_false_break"].notna().sum())
                if g[f"{wname}_false_break"].notna().sum() else np.nan,
                "corr_or_vs_rest_pearson": p,
                "typology_pct": {str(k): float(v) for k, v in
                                 (g[f"{wname}_typology"].value_counts(dropna=False) / len(g) * 100).round(2).items()},
            }
        w["conditioned_on_roll_day"] = cond_roll

        windows_out[wname] = w

    out["windows"] = windows_out

    # DOW map (open->close), general not per-window
    dow_map = {}
    for dow, g in sess.groupby("weekday"):
        dow_map[dow] = {
            "n": len(g),
            "mean_ret_open_close": float(g["ret_open_close"].mean()),
            "median_ret_open_close": float(g["ret_open_close"].median()),
            "std_ret_open_close": float(g["ret_open_close"].std()),
        }
    out["dow_map"] = dow_map

    return out


# ---------------------------------------------------------------------------
# Text report builder
# ---------------------------------------------------------------------------

def fmt_pct(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "NA"
    return f"{x*100:.{nd}f}%"


def fmt_num(x, nd=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "NA"
    return f"{x:.{nd}f}"


def build_text_report(all_aggregates, load_stats_all, anomalies):
    lines = []
    lines.append("=" * 90)
    lines.append("b3_or_continuation_v0 - Fase A: mapa descritivo (OR + continuacao)")
    lines.append("IS: inicio dos dados ate 2024-12-31. OOS (2025+) NAO computado.")
    lines.append("=" * 90)

    lines.append("\n--- N SESSIONS / LOAD STATS ---")
    for key, stats in load_stats_all.items():
        lines.append(f"{key}: {stats}")

    for agg in all_aggregates:
        instrument, tf = agg["instrument"], agg["timeframe"]
        lines.append("\n" + "=" * 90)
        lines.append(f"{instrument} {tf}  |  n_sessions IS = {agg['n_sessions']}")
        lines.append("=" * 90)

        for wname, w in agg["windows"].items():
            lines.append(f"\n### Janela {wname.upper()} ###")
            lines.append(f"  % dias com breakout: {fmt_pct(w['pct_breakout'])} (n={w['n_breakout']}/{w['n_total']})")
            lines.append(f"  % false break (entre breakouts): {fmt_pct(w['pct_false_break_of_breakouts'])} (n_valid={w['n_false_break_valid']})")

            c = w["corr_or_vs_rest_geral"]
            lines.append(f"  Corr OR-ret vs resto-do-dia GERAL: pearson={fmt_num(c['pearson'])} spearman={fmt_num(c['spearman'])} (n={c['n']})")
            lines.append("  Corr OR-ret vs resto-do-dia POR ANO:")
            for yr, cy in sorted(w["corr_or_vs_rest_por_ano"].items()):
                lines.append(f"    {yr}: pearson={fmt_num(cy['pearson'])} spearman={fmt_num(cy['spearman'])} (n={cy['n']})")

            lines.append("  Continuacao por lado (pontos e %):")
            for side, cs in w["continuation_by_side"].items():
                lines.append(f"    {side}: n={cs['n']} mean_pts={fmt_num(cs['mean_pts'],2)} median_pts={fmt_num(cs['median_pts'],2)} "
                              f"mean_pct={fmt_pct(cs['mean_pct'],3)} median_pct={fmt_pct(cs['median_pct'],3)}")

            lines.append("  Continuacao por ano (%):")
            for yr, cy in sorted(w["continuation_by_year"].items()):
                lines.append(f"    {yr}: n={cy['n']} mean_pct={fmt_pct(cy['mean_pct'],3)} median_pct={fmt_pct(cy['median_pct'],3)}")

            lines.append(f"  Histograma hora do 1o breakout: {w['breakout_hour_histogram']}")

            lines.append("  Tipologia dos dias (%):")
            for k, v in w["typology_pct"].items():
                lines.append(f"    {k}: {v}% (n={w['typology_n'].get(k)})")

            lines.append("  Condicionado a tercil de OR width relativa:")
            for terc, cd in w["conditioned_on_width_tercile"].items():
                lines.append(f"    {terc}: n={cd['n']} pct_breakout={fmt_pct(cd['pct_breakout'])} "
                              f"pct_false_break={fmt_pct(cd['pct_false_break_of_breakouts'])} "
                              f"corr_pearson={fmt_num(cd['corr_or_vs_rest_pearson'])} typology={cd['typology_pct']}")

            lines.append("  Condicionado a dia de rolagem:")
            for roll, cd in w["conditioned_on_roll_day"].items():
                lines.append(f"    is_roll={roll}: n={cd['n']} pct_breakout={fmt_pct(cd['pct_breakout'])} "
                              f"pct_false_break={fmt_pct(cd['pct_false_break_of_breakouts'])} "
                              f"corr_pearson={fmt_num(cd['corr_or_vs_rest_pearson'])} typology={cd['typology_pct']}")

        lines.append("\n### DOW map (open->close) ###")
        for dow, d in agg["dow_map"].items():
            lines.append(f"  {dow}: n={d['n']} mean={fmt_pct(d['mean_ret_open_close'],3)} "
                          f"median={fmt_pct(d['median_ret_open_close'],3)} std={fmt_pct(d['std_ret_open_close'],3)}")

    lines.append("\n" + "=" * 90)
    lines.append(f"ANOMALIAS DE DADOS DETECTADAS: {len(anomalies)}")
    lines.append("=" * 90)
    if anomalies:
        anomaly_types = {}
        for a in anomalies:
            key = a["desc"].split(":")[0] if ":" in a["desc"] else a["desc"]
            anomaly_types[key] = anomaly_types.get(key, 0) + 1
        lines.append("Resumo por tipo:")
        for k, v in sorted(anomaly_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {v} ocorrencias")
        lines.append("\nDetalhe completo (primeiras 200):")
        for a in anomalies[:200]:
            lines.append(f"  [{a['instrument']} {a['tf']}] {a['date']}: {a['desc']}")
        if len(anomalies) > 200:
            lines.append(f"  ... e mais {len(anomalies) - 200} ocorrencias (ver JSON completo nao gerado, so resumo acima)")
    else:
        lines.append("Nenhuma anomalia detectada.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def default_json(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return str(obj)
    return str(obj)


def main():
    load_stats_all = {}
    all_sessions = {}
    roll_days_by_instrument = {}

    # First pass: need session date lists per instrument (union across TFs
    # isn't required by spec; roll days are calendar-based so compute
    # separately per instrument using the M5 file, which has denser coverage
    # of trading days across the sample).
    for instrument in ["WIN", "WDO"]:
        df_ref, _ = load_raw(instrument, "M5")
        session_dates = sorted(set(df_ref["date"]))
        if instrument == "WIN":
            roll_days_by_instrument[instrument] = compute_win_roll_days(session_dates)
        else:
            roll_days_by_instrument[instrument] = compute_wdo_roll_days(session_dates)

    all_aggregates = []
    combined_by_instrument = {"WIN": [], "WDO": []}

    for (instrument, tf), path in FILES.items():
        sess, load_stats = build_sessions_df(instrument, tf, roll_days_by_instrument[instrument])
        load_stats_all[f"{instrument}_{tf}"] = load_stats
        all_sessions[(instrument, tf)] = sess
        combined_by_instrument[instrument].append(sess)

        agg = compute_aggregates(sess, instrument, tf)
        all_aggregates.append(agg)

    # write per-instrument CSVs (both timeframes combined, one row per
    # pregao+timeframe as instructed: "uma linha por pregao")
    for instrument in ["WIN", "WDO"]:
        combined = pd.concat(combined_by_instrument[instrument], ignore_index=True, sort=False)
        out_csv = OUT_DIR / f"sessions_{instrument.lower()}.csv"
        combined.to_csv(out_csv, index=False)

    # JSON summary
    summary = {
        "is_end_exclusive": str(IS_END),
        "ash_wednesdays_excluded": [str(d) for d in sorted(ASH_WEDNESDAYS)],
        "load_stats": load_stats_all,
        "roll_days": {
            instrument: sorted(str(d) for d in days)
            for instrument, days in roll_days_by_instrument.items()
        },
        "aggregates": all_aggregates,
        "n_anomalies": len(anomalies),
        "anomalies_sample": anomalies[:500],
    }
    with open(OUT_DIR / "or_map_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=default_json, allow_nan=True)

    # Text report
    report = build_text_report(all_aggregates, load_stats_all, anomalies)
    print(report)
    with open(OUT_DIR / "SUMMARY.txt", "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
