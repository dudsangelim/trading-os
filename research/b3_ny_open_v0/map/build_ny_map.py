"""
b3_ny_open_v0 - Fase A (mapa descritivo)
Sessao B3 x abertura NY (09:30 America/New_York -> horario local B3).

Script MECANICO: apenas computa estatisticas descritivas sobre o IS
(in-sample, datetime_b3 < 2025-01-01). NAO toca no OOS (2025-01-01 em
diante). Zero backtest, zero trade simulado, zero custo, zero PF.

Auto-suficiente: paths RELATIVOS ao root do repo (resolvidos via
Path(__file__)). Rodar com: cd .../research/b3_ny_open_v0/map && python3 build_ny_map.py

Reusa (conforme spec) as convencoes de research/b3_or_continuation_v0/map/build_or_map.py:
estrutura de load, filtro IS no load, exclusao de quartas de Cinzas, logica
de roll days WIN/WDO, log de anomalias, formato de SUMMARY.txt/JSON.

Desvio deliberado de build_or_map.py: a lista de datas de pregao usada para
computar roll days e derivada do M15 (nao do M5). Nesta coleta o M15 tem
cobertura completa 2021-07-19+ (ver MT5_COLLECTION_REPORT.md) enquanto o M5
esta truncado ao teto de 100k barras (~2022-12+); usar M5 aqui perderia ~1.5
ano de pregoes na lista de dias. O ALGORITMO de roll day (nearest_wednesday /
compute_win_roll_days / compute_wdo_roll_days) e identico ao da campanha 1.
"""

import datetime as dt
import json
import warnings
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Config / paths (relativos ao repo; script roda a partir deste diretorio)
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / ".." / ".." / "b3_win_wdo_data_audit" / "mt5_history"
OUT_DIR = HERE
OUT_DIR.mkdir(parents=True, exist_ok=True)

IS_END = pd.Timestamp("2025-01-01")  # exclusive upper bound; OOS = >= this, NUNCA lido

ASH_WEDNESDAYS = set(pd.to_datetime([
    "2022-03-02", "2023-02-22", "2024-02-14", "2025-03-05", "2026-02-18",
]).date)  # reusado de b3_or_continuation_v0; so 2022/23/24 caem dentro do IS

NY_TZ = ZoneInfo("America/New_York")
SP_TZ = ZoneInfo("America/Sao_Paulo")

SESSION_OPEN_TIME = dt.time(9, 0, 0)

FILES = {
    ("WIN", "M15"): DATA_DIR / "WIN_cont_N_M15.parquet",
    ("WIN", "M5"): DATA_DIR / "WIN_cont_N_M5.parquet",
    ("WDO", "M15"): DATA_DIR / "WDO_cont_N_M15.parquet",
    ("WDO", "M5"): DATA_DIR / "WDO_cont_N_M5.parquet",
}

# M5 profile replica window (spec: "Replicar em M5 (2023-2024) só p/ o
# formato do pico"), still fully dentro do IS.
M5_PROFILE_START = pd.Timestamp("2023-01-01")
M5_PROFILE_END = pd.Timestamp("2025-01-01")

PROFILE_T_MIN, PROFILE_T_MAX = -8, 12

GAP_BINS = [-np.inf, -0.005, -0.001, 0.001, 0.005, np.inf]
GAP_LABELS = ["<=-0.5%", "(-0.5%,-0.1%]", "(-0.1%,+0.1%]", "(+0.1%,+0.5%]", ">+0.5%"]

anomalies = []  # lista global de dicts (instrument, tf, date, desc)


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

    df = df[~df["date"].isin(ASH_WEDNESDAYS)].copy()
    n_after_ash = len(df)

    df = df.reset_index(drop=True)
    return df, {"n_bars_raw": n_before, "n_bars_is": n_after_oos, "n_bars_is_no_ash": n_after_ash}


# ---------------------------------------------------------------------------
# Roll days (logica reusada de b3_or_continuation_v0/map/build_or_map.py)
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
    primeiro dia util do mes)."""
    session_dates_sorted = sorted(session_dates_sorted)
    roll_days = set()
    for i in range(len(session_dates_sorted) - 1):
        cur = session_dates_sorted[i]
        nxt = session_dates_sorted[i + 1]
        if (cur.year, cur.month) != (nxt.year, nxt.month):
            roll_days.add(cur)
    return roll_days


# ---------------------------------------------------------------------------
# Anchor: 09:30 America/New_York -> horario local B3, por data, via zoneinfo
# ---------------------------------------------------------------------------

def anchor_info(date):
    """Retorna (anchor_time [datetime.time], dst_regime str) p/ uma data."""
    ny_dt = dt.datetime(date.year, date.month, date.day, 9, 30, tzinfo=NY_TZ)
    sp_dt = ny_dt.astimezone(SP_TZ)
    offset = ny_dt.utcoffset()
    regime = "EDT" if offset == dt.timedelta(hours=-4) else "EST"
    return sp_dt.time(), regime


def add_minutes_to_time(t, minutes):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t) + dt.timedelta(minutes=minutes)).time()


def minutes_between(t1, t0):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t1) -
            dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# True range intraday (primeira barra do dia = high-low; sem referencia ao
# close do dia anterior, p/ nao contaminar tr_sum_morning/post com o gap
# overnight, que ja e medido separadamente em gap_on)
# ---------------------------------------------------------------------------

def true_range_series(day_df):
    highs = day_df["high"].to_numpy()
    lows = day_df["low"].to_numpy()
    closes = day_df["close"].to_numpy()
    tr = np.empty(len(day_df))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(day_df)):
        prev_close = closes[i - 1]
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
    return tr


# ---------------------------------------------------------------------------
# Per-session (per-day) metrics -- "Grandezas por sessao" da spec
# ---------------------------------------------------------------------------

def compute_day_metrics(day_df, instrument, tf, date, prev_close, is_roll_today, is_roll_prev,
                         collect_bar_records=True):
    day_df = day_df.sort_values("datetime_b3").reset_index(drop=True)
    n_bars = len(day_df)
    first_bar = day_df.iloc[0]
    last_bar = day_df.iloc[-1]

    if first_bar["time"] != SESSION_OPEN_TIME:
        log_anomaly(instrument, tf, date,
                    f"sessao nao comeca as 09:00: primeira barra as {first_bar['time']}")

    anchor_time, dst_regime = anchor_info(date)

    tr = true_range_series(day_df)
    day_df = day_df.copy()
    day_df["tr"] = tr

    anchor_mask = day_df["time"] >= anchor_time
    anchor_found = bool(anchor_mask.any())

    day_open = first_bar["open"]
    day_close = last_bar["close"]

    row = {
        "date": str(date), "instrument": instrument, "weekday": first_bar["weekday"],
        "year": int(first_bar["year"]), "dst_regime": dst_regime,
        "anchor_time_local": str(anchor_time), "n_bars_day": n_bars,
        "first_bar_time": str(first_bar["time"]), "last_bar_time": str(last_bar["time"]),
        "is_roll": bool(is_roll_today),
        "day_open": day_open, "day_close": day_close,
        "ret_day": (day_close / day_open - 1) if day_open else np.nan,
    }

    if prev_close is None or is_roll_today or is_roll_prev:
        row["gap_on"] = np.nan
    else:
        row["gap_on"] = (day_open / prev_close - 1) if prev_close else np.nan

    if not anchor_found:
        log_anomaly(instrument, tf, date, "sem barra ancora: sessao termina antes do anchor")
        row["anchor_found"] = False
        row["anchor_late"] = np.nan
        row["valid_for_conditional"] = False
        for k in ["ret_morning", "ret_ny30", "ret_ny60", "ret_post", "ret_post_after30",
                  "morning_high", "morning_low", "range_morning_pts", "range_morning_pct",
                  "tr_sum_morning", "tr_sum_post", "vol_par", "volu_morning", "volu_post",
                  "broke_high", "broke_low", "t_first_break", "false_break_high", "false_break_low"]:
            row[k] = np.nan
        return row, []

    anchor_idx = int(day_df.index[anchor_mask][0])
    anchor_bar = day_df.iloc[anchor_idx]
    anchor_open = anchor_bar["open"]

    late = minutes_between(anchor_bar["time"], anchor_time) > 15
    row["anchor_found"] = True
    row["anchor_late"] = bool(late)
    if late:
        log_anomaly(instrument, tf, date,
                    f"abertura tardia: barra ancora as {anchor_bar['time']}, esperado {anchor_time}")
    row["valid_for_conditional"] = not late

    morning_mask = day_df["time"] < anchor_time
    post_mask = ~morning_mask  # time >= anchor_time (mesma janela usada p/ ret_post)
    morning_df = day_df[morning_mask]
    post_df = day_df[post_mask]

    row["ret_morning"] = (anchor_open / day_open - 1) if day_open else np.nan
    row["ret_post"] = (day_close / anchor_open - 1) if anchor_open else np.nan

    anchor_plus_30 = add_minutes_to_time(anchor_time, 30)
    anchor_plus_60 = add_minutes_to_time(anchor_time, 60)

    def bar_at_or_after(target_time):
        m = day_df["time"] >= target_time
        if not m.any():
            return None
        return day_df.iloc[int(day_df.index[m][0])]

    bar30 = bar_at_or_after(anchor_plus_30)
    bar60 = bar_at_or_after(anchor_plus_60)

    if bar30 is None:
        log_anomaly(instrument, tf, date, "sem barra ny+30: sessao termina antes de anchor+30min")
        row["ret_ny30"] = np.nan
        row["ret_post_after30"] = np.nan
    else:
        row["ret_ny30"] = (bar30["open"] / anchor_open - 1) if anchor_open else np.nan
        row["ret_post_after30"] = (day_close / bar30["open"] - 1) if bar30["open"] else np.nan

    if bar60 is None:
        log_anomaly(instrument, tf, date, "sem barra ny+60: sessao termina antes de anchor+60min")
        row["ret_ny60"] = np.nan
    else:
        row["ret_ny60"] = (bar60["open"] / anchor_open - 1) if anchor_open else np.nan

    if len(morning_df) > 0:
        morning_high = float(morning_df["high"].max())
        morning_low = float(morning_df["low"].min())
        row["morning_high"] = morning_high
        row["morning_low"] = morning_low
        row["range_morning_pts"] = morning_high - morning_low
        row["range_morning_pct"] = (morning_high - morning_low) / day_open if day_open else np.nan
        row["tr_sum_morning"] = float(morning_df["tr"].sum())
        row["volu_morning"] = int(morning_df["tick_volume"].sum())
        n_morning = len(morning_df)
    else:
        log_anomaly(instrument, tf, date, "sem barras na manha (antes do anchor)")
        morning_high = morning_low = np.nan
        row["morning_high"] = row["morning_low"] = np.nan
        row["range_morning_pts"] = row["range_morning_pct"] = np.nan
        row["tr_sum_morning"] = np.nan
        row["volu_morning"] = np.nan
        n_morning = 0

    row["tr_sum_post"] = float(post_df["tr"].sum())
    row["volu_post"] = int(post_df["tick_volume"].sum())
    n_post = len(post_df)

    tr_avg_morning = (row["tr_sum_morning"] / n_morning) if n_morning else np.nan
    tr_avg_post = (row["tr_sum_post"] / n_post) if n_post else np.nan
    if n_morning and not np.isnan(tr_avg_morning) and tr_avg_morning != 0:
        row["vol_par"] = tr_avg_post / tr_avg_morning
    else:
        row["vol_par"] = np.nan

    # quebra de extremos da manha, pos-anchor
    # (fix code review Fase A: broke_high/low = quebrou EM QUALQUER MOMENTO
    # pos-anchor, nao apenas na primeira quebra -- o scan antigo parava na
    # primeira quebra e perdia a quebra do outro extremo em dias ida-e-volta)
    broke_high = False
    broke_low = False
    t_first_break = np.nan
    if n_morning > 0 and n_post > 0 and not np.isnan(morning_high):
        broke_high = bool((post_df["high"] > morning_high).any())
        broke_low = bool((post_df["low"] < morning_low).any())
        if broke_high or broke_low:
            first_mask = (post_df["high"] > morning_high) | (post_df["low"] < morning_low)
            first_bar_break = post_df[first_mask].iloc[0]
            t_first_break = minutes_between(first_bar_break["time"], anchor_time)
    row["broke_high"] = broke_high
    row["broke_low"] = broke_low
    row["t_first_break"] = t_first_break

    if n_morning > 0 and not np.isnan(morning_high):
        in_range_close = morning_low <= day_close <= morning_high
        row["false_break_high"] = bool(broke_high and in_range_close)
        row["false_break_low"] = bool(broke_low and in_range_close)
    else:
        row["false_break_high"] = np.nan
        row["false_break_low"] = np.nan

    bar_records = []
    if collect_bar_records:
        for pos in range(n_bars):
            offset = pos - anchor_idx
            if PROFILE_T_MIN <= offset <= PROFILE_T_MAX:
                b = day_df.iloc[pos]
                bar_records.append({
                    "instrument": instrument, "date": str(date), "dst_regime": dst_regime,
                    "offset": offset, "local_time": str(b["time"]), "tr": float(b["tr"]),
                    "abs_ret": abs(b["close"] / b["open"] - 1) if b["open"] else np.nan,
                    "tick_volume": int(b["tick_volume"]),
                })

    return row, bar_records


def build_sessions_df(instrument, tf, roll_days, collect_bar_records=True):
    df, load_stats = load_raw(instrument, tf)
    session_dates_sorted = sorted(set(df["date"]))
    prev_date_map = {session_dates_sorted[i]: session_dates_sorted[i - 1]
                      for i in range(1, len(session_dates_sorted))}

    day_close_map = {}
    for date, day_df in df.groupby("date"):
        day_close_map[date] = day_df.sort_values("datetime_b3").iloc[-1]["close"]

    rows = []
    bar_records_all = []
    for date, day_df in df.groupby("date"):
        prev_date = prev_date_map.get(date)
        prev_close = day_close_map.get(prev_date) if prev_date is not None else None
        is_roll_today = date in roll_days
        is_roll_prev = (prev_date in roll_days) if prev_date is not None else False
        row, bar_records = compute_day_metrics(day_df, instrument, tf, date, prev_close,
                                                 is_roll_today, is_roll_prev,
                                                 collect_bar_records=collect_bar_records)
        rows.append(row)
        bar_records_all.extend(bar_records)

    sess = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return sess, load_stats, bar_records_all


# ---------------------------------------------------------------------------
# Estatisticas do mapa: A1-A7
# ---------------------------------------------------------------------------

def safe_corr(x, y, method):
    # scipy nao esta disponivel no ambiente -> spearman calculado manualmente
    # via rank + pearson (equivalente, sem depender de scipy.stats.spearmanr).
    x = pd.Series(x)
    y = pd.Series(y)
    mask = x.notna() & y.notna()
    n = int(mask.sum())
    if n < 3:
        return np.nan, n
    xm, ym = x[mask], y[mask]
    if method == "spearman":
        xm, ym = xm.rank(), ym.rank()
    return float(xm.corr(ym, method="pearson")), n


def compute_a1_profile(bar_df):
    if bar_df is None or len(bar_df) == 0:
        return {"by_anchor_offset": {}, "by_local_time": {}}

    by_anchor = {}
    for regime, g in bar_df.groupby("dst_regime"):
        agg = g.groupby("offset").agg(
            tr_mean=("tr", "mean"), tr_median=("tr", "median"),
            abs_ret_mean=("abs_ret", "mean"), abs_ret_median=("abs_ret", "median"),
            vol_mean=("tick_volume", "mean"), vol_median=("tick_volume", "median"),
            n=("tr", "count"),
        ).reset_index()
        by_anchor[regime] = {
            str(int(r["offset"])): {
                "tr_mean": float(r["tr_mean"]), "tr_median": float(r["tr_median"]),
                "abs_ret_mean": float(r["abs_ret_mean"]), "abs_ret_median": float(r["abs_ret_median"]),
                "vol_mean": float(r["vol_mean"]), "vol_median": float(r["vol_median"]),
                "n": int(r["n"]),
            } for _, r in agg.iterrows()
        }

    by_local = {}
    for regime, g in bar_df.groupby("dst_regime"):
        agg = g.groupby("local_time").agg(
            tr_mean=("tr", "mean"), tr_median=("tr", "median"),
            abs_ret_mean=("abs_ret", "mean"), abs_ret_median=("abs_ret", "median"),
            vol_mean=("tick_volume", "mean"), vol_median=("tick_volume", "median"),
            n=("tr", "count"),
        ).reset_index()
        by_local[regime] = {
            str(r["local_time"]): {
                "tr_mean": float(r["tr_mean"]), "tr_median": float(r["tr_median"]),
                "abs_ret_mean": float(r["abs_ret_mean"]), "abs_ret_median": float(r["abs_ret_median"]),
                "vol_mean": float(r["vol_mean"]), "vol_median": float(r["vol_median"]),
                "n": int(r["n"]),
            } for _, r in agg.iterrows()
        }

    return {"by_anchor_offset": by_anchor, "by_local_time": by_local}


def compute_a2(sess):
    valid = sess[sess["valid_for_conditional"] == True]  # noqa: E712

    def sign_cond(g):
        pos = g[g["ret_morning"] > 0]["ret_post"]
        neg = g[g["ret_morning"] < 0]["ret_post"]
        zero = g[g["ret_morning"] == 0]["ret_post"]
        return {
            "ret_post_mean_given_morning_pos": float(pos.mean()) if len(pos) else np.nan,
            "n_pos": int(len(pos)),
            "ret_post_mean_given_morning_neg": float(neg.mean()) if len(neg) else np.nan,
            "n_neg": int(len(neg)),
            "ret_post_mean_given_morning_zero": float(zero.mean()) if len(zero) else np.nan,
            "n_zero": int(len(zero)),
        }

    p_all, n_all = safe_corr(valid["ret_morning"], valid["ret_post"], "pearson")
    s_all, _ = safe_corr(valid["ret_morning"], valid["ret_post"], "spearman")
    p_ny30, n_ny30 = safe_corr(valid["ret_morning"], valid["ret_ny30"], "pearson")
    s_ny30, _ = safe_corr(valid["ret_morning"], valid["ret_ny30"], "spearman")

    out = {
        "corr_morning_vs_post_geral": {"pearson": p_all, "spearman": s_all, "n": n_all},
        "corr_morning_vs_ny30_geral": {"pearson": p_ny30, "spearman": s_ny30, "n": n_ny30},
        "sign_conditioned_geral": sign_cond(valid),
    }

    by_year = {}
    for yr, g in valid.groupby("year"):
        p, n_p = safe_corr(g["ret_morning"], g["ret_post"], "pearson")
        s, _ = safe_corr(g["ret_morning"], g["ret_post"], "spearman")
        p2, n_p2 = safe_corr(g["ret_morning"], g["ret_ny30"], "pearson")
        s2, _ = safe_corr(g["ret_morning"], g["ret_ny30"], "spearman")
        by_year[str(yr)] = {
            "corr_morning_vs_post": {"pearson": p, "spearman": s, "n": n_p},
            "corr_morning_vs_ny30": {"pearson": p2, "spearman": s2, "n": n_p2},
            "sign_conditioned": sign_cond(g),
        }
    out["por_ano"] = by_year
    return out


def compute_a3(sess):
    valid = sess[sess["valid_for_conditional"] == True].copy()  # noqa: E712
    valid["gap_bucket"] = pd.cut(valid["gap_on"], bins=GAP_BINS, labels=GAP_LABELS)

    def bucket_stats(part):
        agg = part.groupby("gap_bucket", observed=True)["ret_post"].agg(["mean", "median", "count"])
        return {str(k): {"mean": float(v["mean"]), "median": float(v["median"]), "n": int(v["count"])}
                for k, v in agg.iterrows()}

    out = {"agregado_todos_anos": bucket_stats(valid)}

    dates_sorted = sorted(valid["date"].unique())
    if len(dates_sorted) >= 2:
        mid_date = dates_sorted[len(dates_sorted) // 2]
        out["primeira_metade_is"] = bucket_stats(valid[valid["date"] < mid_date])
        out["segunda_metade_is"] = bucket_stats(valid[valid["date"] >= mid_date])
        out["_split_date"] = mid_date
    return out


def compute_a4(sess):
    valid = sess[sess["valid_for_conditional"] == True]  # noqa: E712

    def sign_cond(g):
        pos = g[g["ret_ny30"] > 0]["ret_post_after30"]
        neg = g[g["ret_ny30"] < 0]["ret_post_after30"]
        return {
            "ret_post_after30_mean_given_ny30_pos": float(pos.mean()) if len(pos) else np.nan,
            "n_pos": int(len(pos)),
            "ret_post_after30_mean_given_ny30_neg": float(neg.mean()) if len(neg) else np.nan,
            "n_neg": int(len(neg)),
        }

    p_all, n_all = safe_corr(valid["ret_ny30"], valid["ret_post_after30"], "pearson")
    s_all, _ = safe_corr(valid["ret_ny30"], valid["ret_post_after30"], "spearman")

    out = {
        "geral": {"pearson": p_all, "spearman": s_all, "n": n_all},
        "sign_conditioned_geral": sign_cond(valid),
    }

    by_year = {}
    for yr, g in valid.groupby("year"):
        p, n_p = safe_corr(g["ret_ny30"], g["ret_post_after30"], "pearson")
        s, _ = safe_corr(g["ret_ny30"], g["ret_post_after30"], "spearman")
        by_year[str(yr)] = {"pearson": p, "spearman": s, "n": n_p, "sign_conditioned": sign_cond(g)}
    out["por_ano"] = by_year
    return out


def compute_a5(sess):
    valid = sess[sess["valid_for_conditional"] == True].copy()  # noqa: E712

    def stats_for(g):
        n = len(g)
        if n == 0:
            return {"n": 0}
        p_high = float(g["broke_high"].mean())
        p_low = float(g["broke_low"].mean())
        p_any = float((g["broke_high"] | g["broke_low"]).mean())
        t = g["t_first_break"].dropna()
        quart = {str(k): float(v) for k, v in t.quantile([0.25, 0.5, 0.75]).items()} if len(t) else {}
        broke_high_n = int(g["broke_high"].sum())
        broke_low_n = int(g["broke_low"].sum())
        fbh_rate = float(g.loc[g["broke_high"] == True, "false_break_high"].mean()) if broke_high_n else np.nan  # noqa: E712
        fbl_rate = float(g.loc[g["broke_low"] == True, "false_break_low"].mean()) if broke_low_n else np.nan  # noqa: E712
        return {
            "n": n, "p_broke_high": p_high, "p_broke_low": p_low, "p_broke_any": p_any,
            "n_broke_high": broke_high_n, "n_broke_low": broke_low_n,
            "t_first_break_quartiles_min": quart,
            "false_break_rate_high": fbh_rate, "false_break_rate_low": fbl_rate,
        }

    out = {"geral": stats_for(valid)}
    out["por_ano"] = {str(yr): stats_for(g) for yr, g in valid.groupby("year")}
    out["por_dst_regime"] = {str(reg): stats_for(g) for reg, g in valid.groupby("dst_regime")}
    return out


def compute_a6(sess_win, sess_wdo):
    w = sess_win[sess_win["valid_for_conditional"] == True][["date", "ret_post", "ret_ny30"]].rename(  # noqa: E712
        columns={"ret_post": "ret_post_win", "ret_ny30": "ret_ny30_win"})
    d = sess_wdo[sess_wdo["valid_for_conditional"] == True][["date", "ret_post", "ret_ny30"]].rename(  # noqa: E712
        columns={"ret_post": "ret_post_wdo", "ret_ny30": "ret_ny30_wdo"})
    merged = pd.merge(w, d, on="date", how="inner")

    p1, n1 = safe_corr(merged["ret_post_win"], merged["ret_post_wdo"], "pearson")
    s1, _ = safe_corr(merged["ret_post_win"], merged["ret_post_wdo"], "spearman")
    p2, n2 = safe_corr(merged["ret_ny30_win"], merged["ret_ny30_wdo"], "pearson")
    s2, _ = safe_corr(merged["ret_ny30_win"], merged["ret_ny30_wdo"], "spearman")

    out = {
        "agregado_is": {
            "corr_ret_post_win_wdo": {"pearson": p1, "spearman": s1, "n": n1},
            "corr_ret_ny30_win_wdo": {"pearson": p2, "spearman": s2, "n": n2},
        }
    }

    if len(merged):
        merged = merged.copy()
        merged["year"] = merged["date"].str.slice(0, 4)
        by_year = {}
        for yr, g in merged.groupby("year"):
            p1y, n1y = safe_corr(g["ret_post_win"], g["ret_post_wdo"], "pearson")
            p2y, n2y = safe_corr(g["ret_ny30_win"], g["ret_ny30_wdo"], "pearson")
            by_year[yr] = {
                "corr_ret_post": {"pearson": p1y, "n": n1y},
                "corr_ret_ny30": {"pearson": p2y, "n": n2y},
            }
        out["por_ano"] = by_year
    return out


def compute_a7(sess, tf_anomalies):
    n_total = len(sess)
    n_no_anchor = int((sess["anchor_found"] == False).sum())  # noqa: E712
    n_late = int((sess["anchor_late"] == True).sum())  # noqa: E712
    n_roll = int(sess["is_roll"].sum())
    n_valid_conditional = int(sess["valid_for_conditional"].sum())

    anomaly_types = {}
    for a in tf_anomalies:
        key = a["desc"].split(":")[0]
        anomaly_types[key] = anomaly_types.get(key, 0) + 1

    return {
        "n_sessions_total": n_total,
        "n_sem_barra_ancora": n_no_anchor,
        "n_abertura_tardia": n_late,
        "n_roll_days": n_roll,
        "n_valid_for_conditional_stats": n_valid_conditional,
        "n_anomalies": len(tf_anomalies),
        "anomaly_types": anomaly_types,
        "n_sessions_por_ano": {str(yr): int(n) for yr, n in sess.groupby("year").size().items()},
    }


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def fmt_pct(x, nd=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "NA"
    return f"{x * 100:.{nd}f}%"


def fmt_num(x, nd=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "NA"
    return f"{x:.{nd}f}"


def build_text_report(instruments, load_stats_all, roll_days_by_instrument, a1_m15, a1_m5,
                       a2_by_inst, a3_by_inst, a4_by_inst, a5_by_inst, a6, a7_by_inst, anomalies):
    lines = []
    lines.append("=" * 92)
    lines.append("b3_ny_open_v0 - Fase A: mapa descritivo (sessao B3 x abertura NY 09:30 America/New_York)")
    lines.append("IS: inicio dos dados ate 2024-12-31 (datetime_b3 < 2025-01-01). OOS (2025+) NAO computado.")
    lines.append("Zero backtest / zero trade simulado / zero custo / zero PF -- estatisticas descritivas apenas.")
    lines.append("=" * 92)

    lines.append("\n--- LOAD STATS ---")
    for key, stats in load_stats_all.items():
        lines.append(f"{key}: {stats}")

    lines.append("\n--- ROLL DAYS (calculados a partir do M15, algoritmo reusado de b3_or_continuation_v0) ---")
    for inst, days in roll_days_by_instrument.items():
        lines.append(f"{inst}: {len(days)} roll days no IS")

    for inst in instruments:
        lines.append("\n" + "=" * 92)
        lines.append(f"{inst}  |  Fonte primaria M15")
        lines.append("=" * 92)

        a7 = a7_by_inst[inst]
        lines.append("\n### A7 -- Qualidade / anomalias ###")
        lines.append(f"  n sessoes totais (IS, sem quartas de Cinzas): {a7['n_sessions_total']}")
        lines.append(f"  n sessoes por ano: {a7['n_sessions_por_ano']}")
        lines.append(f"  n sem barra-ancora: {a7['n_sem_barra_ancora']}")
        lines.append(f"  n abertura tardia (>anchor+15min): {a7['n_abertura_tardia']}")
        lines.append(f"  n roll days: {a7['n_roll_days']}")
        lines.append(f"  n validas p/ estatisticas condicionais (A2-A6): {a7['n_valid_for_conditional_stats']}")
        lines.append(f"  n anomalias total (M15): {a7['n_anomalies']}")
        lines.append("  anomalias por tipo:")
        for k, v in sorted(a7["anomaly_types"].items(), key=lambda x: -x[1]):
            lines.append(f"    {k}: {v}")

        lines.append("\n### A1 -- Perfil de vol em event-time (M15, offset t-8..t+12 vs barra-ancora) ###")
        prof = a1_m15[inst]
        for regime in sorted(prof["by_anchor_offset"].keys()):
            lines.append(f"  [alinhado por ANCHOR, regime={regime}]")
            off = prof["by_anchor_offset"][regime]
            for k in sorted(off.keys(), key=lambda x: int(x)):
                v = off[k]
                lines.append(f"    t{k:>3}: tr_mean={fmt_num(v['tr_mean'],2)} tr_median={fmt_num(v['tr_median'],2)} "
                              f"absret_mean={fmt_pct(v['abs_ret_mean'])} vol_mean={fmt_num(v['vol_mean'],0)} n={v['n']}")
        for regime in sorted(prof["by_local_time"].keys()):
            lines.append(f"  [alinhado por HORA LOCAL FIXA, regime={regime}]")
            loc = prof["by_local_time"][regime]
            for k in sorted(loc.keys()):
                v = loc[k]
                lines.append(f"    {k}: tr_mean={fmt_num(v['tr_mean'],2)} tr_median={fmt_num(v['tr_median'],2)} "
                              f"absret_mean={fmt_pct(v['abs_ret_mean'])} vol_mean={fmt_num(v['vol_mean'],0)} n={v['n']}")

        prof5 = a1_m5.get(inst, {"by_anchor_offset": {}, "by_local_time": {}})
        lines.append("\n### A1 -- Replica M5 (2023-2024), so alinhamento por ANCHOR ###")
        for regime in sorted(prof5["by_anchor_offset"].keys()):
            lines.append(f"  [M5, regime={regime}]")
            off = prof5["by_anchor_offset"][regime]
            for k in sorted(off.keys(), key=lambda x: int(x)):
                v = off[k]
                lines.append(f"    t{k:>3}: tr_mean={fmt_num(v['tr_mean'],2)} absret_mean={fmt_pct(v['abs_ret_mean'])} "
                              f"vol_mean={fmt_num(v['vol_mean'],0)} n={v['n']}")

        a2 = a2_by_inst[inst]
        lines.append("\n### A2 -- Manha -> pos-NY ###")
        c = a2["corr_morning_vs_post_geral"]
        lines.append(f"  corr(ret_morning, ret_post) GERAL: pearson={fmt_num(c['pearson'])} spearman={fmt_num(c['spearman'])} (n={c['n']})")
        c2 = a2["corr_morning_vs_ny30_geral"]
        lines.append(f"  corr(ret_morning, ret_ny30) GERAL: pearson={fmt_num(c2['pearson'])} spearman={fmt_num(c2['spearman'])} (n={c2['n']})")
        sc = a2["sign_conditioned_geral"]
        lines.append(f"  ret_post | morning>0: {fmt_pct(sc['ret_post_mean_given_morning_pos'])} (n={sc['n_pos']})  "
                      f"| morning<0: {fmt_pct(sc['ret_post_mean_given_morning_neg'])} (n={sc['n_neg']})")
        lines.append("  por ano:")
        for yr, cy in sorted(a2["por_ano"].items()):
            cc = cy["corr_morning_vs_post"]
            lines.append(f"    {yr}: corr(morning,post) pearson={fmt_num(cc['pearson'])} (n={cc['n']})")

        a3 = a3_by_inst[inst]
        lines.append("\n### A3 -- Gap overnight -> pos-NY (buckets de gap_on) ###")
        lines.append("  agregado todos os anos:")
        for bucket, s in a3["agregado_todos_anos"].items():
            lines.append(f"    {bucket}: mean_ret_post={fmt_pct(s['mean'])} median={fmt_pct(s['median'])} n={s['n']}")
        if "primeira_metade_is" in a3:
            lines.append(f"  primeira metade do IS (split em {a3.get('_split_date')}):")
            for bucket, s in a3["primeira_metade_is"].items():
                lines.append(f"    {bucket}: mean_ret_post={fmt_pct(s['mean'])} median={fmt_pct(s['median'])} n={s['n']}")
            lines.append("  segunda metade do IS:")
            for bucket, s in a3["segunda_metade_is"].items():
                lines.append(f"    {bucket}: mean_ret_post={fmt_pct(s['mean'])} median={fmt_pct(s['median'])} n={s['n']}")

        a4 = a4_by_inst[inst]
        lines.append("\n### A4 -- NY-30min -> resto (continuacao vs reversao) ###")
        g = a4["geral"]
        lines.append(f"  corr(ret_ny30, ret_post_after30) GERAL: pearson={fmt_num(g['pearson'])} spearman={fmt_num(g['spearman'])} (n={g['n']})")
        sc4 = a4["sign_conditioned_geral"]
        lines.append(f"  ret_post_after30 | ny30>0: {fmt_pct(sc4['ret_post_after30_mean_given_ny30_pos'])} (n={sc4['n_pos']})  "
                      f"| ny30<0: {fmt_pct(sc4['ret_post_after30_mean_given_ny30_neg'])} (n={sc4['n_neg']})")
        lines.append("  por ano:")
        for yr, cy in sorted(a4["por_ano"].items()):
            lines.append(f"    {yr}: pearson={fmt_num(cy['pearson'])} (n={cy['n']})")

        a5 = a5_by_inst[inst]
        lines.append("\n### A5 -- Quebras dos extremos da manha ###")
        gg = a5["geral"]
        lines.append(f"  GERAL: P(broke_high)={fmt_pct(gg.get('p_broke_high',np.nan))} P(broke_low)={fmt_pct(gg.get('p_broke_low',np.nan))} "
                      f"P(qualquer)={fmt_pct(gg.get('p_broke_any',np.nan))} n={gg.get('n')}")
        lines.append(f"    t_first_break quartis (min): {gg.get('t_first_break_quartiles_min')}")
        lines.append(f"    false break rate high={fmt_pct(gg.get('false_break_rate_high',np.nan))} low={fmt_pct(gg.get('false_break_rate_low',np.nan))}")
        lines.append("  por dst_regime:")
        for reg, s in a5["por_dst_regime"].items():
            lines.append(f"    {reg}: P(qualquer)={fmt_pct(s.get('p_broke_any',np.nan))} n={s.get('n')} "
                          f"t_first_break_median={fmt_num(s.get('t_first_break_quartiles_min',{}).get('0.5',np.nan),1)}")
        lines.append("  por ano:")
        for yr, s in sorted(a5["por_ano"].items()):
            lines.append(f"    {yr}: P(qualquer)={fmt_pct(s.get('p_broke_any',np.nan))} n={s.get('n')}")

    lines.append("\n" + "=" * 92)
    lines.append("### A6 -- Cross-asset WIN x WDO (alinhado por data) ###")
    ag = a6["agregado_is"]
    lines.append(f"  corr(ret_post_WIN, ret_post_WDO): pearson={fmt_num(ag['corr_ret_post_win_wdo']['pearson'])} "
                 f"spearman={fmt_num(ag['corr_ret_post_win_wdo']['spearman'])} (n={ag['corr_ret_post_win_wdo']['n']})")
    lines.append(f"  corr(ret_ny30_WIN, ret_ny30_WDO): pearson={fmt_num(ag['corr_ret_ny30_win_wdo']['pearson'])} "
                 f"spearman={fmt_num(ag['corr_ret_ny30_win_wdo']['spearman'])} (n={ag['corr_ret_ny30_win_wdo']['n']})")
    if "por_ano" in a6:
        lines.append("  por ano:")
        for yr, cy in sorted(a6["por_ano"].items()):
            lines.append(f"    {yr}: corr_ret_post={fmt_num(cy['corr_ret_post']['pearson'])} (n={cy['corr_ret_post']['n']}) "
                          f"corr_ret_ny30={fmt_num(cy['corr_ret_ny30']['pearson'])} (n={cy['corr_ret_ny30']['n']})")

    lines.append("\n" + "=" * 92)
    lines.append(f"ANOMALIAS DE DADOS DETECTADAS (todas tf/instrumentos): {len(anomalies)}")
    lines.append("=" * 92)
    if anomalies:
        anomaly_types = {}
        for a in anomalies:
            key = f"[{a['instrument']} {a['tf']}] " + (a["desc"].split(":")[0] if ":" in a["desc"] else a["desc"])
            anomaly_types[key] = anomaly_types.get(key, 0) + 1
        lines.append("Resumo por tipo:")
        for k, v in sorted(anomaly_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {v} ocorrencias")
        lines.append("\nDetalhe completo (primeiras 200):")
        for a in anomalies[:200]:
            lines.append(f"  [{a['instrument']} {a['tf']}] {a['date']}: {a['desc']}")
        if len(anomalies) > 200:
            lines.append(f"  ... e mais {len(anomalies) - 200} ocorrencias (ver JSON para lista completa)")
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
    if isinstance(obj, (dt.date, dt.time)):
        return str(obj)
    return str(obj)


def main():
    instruments = ["WIN", "WDO"]
    load_stats_all = {}
    roll_days_by_instrument = {}
    sess_m15 = {}
    bars_m15 = {}
    bars_m5 = {}

    # Roll days: derivados da lista de datas de pregao do M15 (ver docstring
    # do modulo p/ justificativa do desvio de build_or_map.py).
    for instrument in instruments:
        df_ref, _ = load_raw(instrument, "M15")
        session_dates = sorted(set(df_ref["date"]))
        if instrument == "WIN":
            roll_days_by_instrument[instrument] = compute_win_roll_days(session_dates)
        else:
            roll_days_by_instrument[instrument] = compute_wdo_roll_days(session_dates)

    # M15 -- fonte primaria: sessoes completas + bar records p/ perfil A1
    for instrument in instruments:
        sess, load_stats, bar_records = build_sessions_df(
            instrument, "M15", roll_days_by_instrument[instrument], collect_bar_records=True)
        load_stats_all[f"{instrument}_M15"] = load_stats
        sess_m15[instrument] = sess
        bars_m15[instrument] = pd.DataFrame(bar_records)

    # M5 -- secundario, so p/ replicar o perfil A1 em 2023-2024 (grao fino)
    for instrument in instruments:
        df5, load_stats5 = load_raw(instrument, "M5")
        load_stats_all[f"{instrument}_M5"] = load_stats5
        df5_profile = df5[(df5["datetime_b3"] >= M5_PROFILE_START) & (df5["datetime_b3"] < M5_PROFILE_END)].copy()
        bar_records5 = []
        for date, day_df in df5_profile.groupby("date"):
            _, br = compute_day_metrics(day_df, instrument, "M5", date, None, False, False,
                                          collect_bar_records=True)
            bar_records5.extend(br)
        bars_m5[instrument] = pd.DataFrame(bar_records5)

    # Estatisticas A1-A7 por instrumento
    a1_m15 = {inst: compute_a1_profile(bars_m15[inst]) for inst in instruments}
    a1_m5 = {inst: compute_a1_profile(bars_m5[inst]) for inst in instruments}
    a2_by_inst = {inst: compute_a2(sess_m15[inst]) for inst in instruments}
    a3_by_inst = {inst: compute_a3(sess_m15[inst]) for inst in instruments}
    a4_by_inst = {inst: compute_a4(sess_m15[inst]) for inst in instruments}
    a5_by_inst = {inst: compute_a5(sess_m15[inst]) for inst in instruments}
    a6 = compute_a6(sess_m15["WIN"], sess_m15["WDO"])

    anomalies_by_inst_m15 = {
        inst: [a for a in anomalies if a["instrument"] == inst and a["tf"] == "M15"]
        for inst in instruments
    }
    a7_by_inst = {inst: compute_a7(sess_m15[inst], anomalies_by_inst_m15[inst]) for inst in instruments}

    # ---- Saidas ----
    for instrument in instruments:
        out_csv = OUT_DIR / f"sessions_{instrument.lower()}.csv"
        sess_m15[instrument].to_csv(out_csv, index=False)

    summary = {
        "is_end_exclusive": str(IS_END),
        "ash_wednesdays_excluded": [str(d) for d in sorted(ASH_WEDNESDAYS)],
        "anchor_definition": "09:30 America/New_York convertido para America/Sao_Paulo via zoneinfo, por data",
        "load_stats": load_stats_all,
        "roll_days": {inst: sorted(str(d) for d in days) for inst, days in roll_days_by_instrument.items()},
        "A1_profile_m15": a1_m15,
        "A1_profile_m5_replica_2023_2024": a1_m5,
        "A2_morning_to_post": a2_by_inst,
        "A3_gap_to_post": a3_by_inst,
        "A4_ny30_to_rest": a4_by_inst,
        "A5_morning_extreme_breaks": a5_by_inst,
        "A6_cross_asset": a6,
        "A7_quality": a7_by_inst,
        "n_anomalies_total": len(anomalies),
        "anomalies_full": anomalies,
    }
    with open(OUT_DIR / "ny_map_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=default_json, allow_nan=True)

    report = build_text_report(instruments, load_stats_all, roll_days_by_instrument, a1_m15, a1_m5,
                                a2_by_inst, a3_by_inst, a4_by_inst, a5_by_inst, a6, a7_by_inst, anomalies)
    print(report)
    with open(OUT_DIR / "SUMMARY.txt", "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
