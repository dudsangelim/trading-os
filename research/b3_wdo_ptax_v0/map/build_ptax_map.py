"""
b3_wdo_ptax_v0 - Fase A (mapa descritivo)
WDO/WIN x janelas de apuracao do PTAX (10:00/11:00/12:00/13:00, horario
local Brasilia, -10min cada) e fixing de fim de mes.

Script MECANICO: apenas estatisticas descritivas sobre o IS (in-sample,
datetime_b3 < 2025-01-01). NAO toca no OOS (2025-01-01 em diante). Zero
backtest, zero trade simulado, zero custo, zero PF.

Auto-suficiente: paths RELATIVOS ao root do repo (resolvidos via
Path(__file__)). Rodar com:
    cd .../research/b3_wdo_ptax_v0/map && python3 build_ptax_map.py

Reusa (conforme instrucao da campanha) de research/b3_ny_open_v0/map/build_ny_map.py:
load com filtro IS no load, exclusao de quartas de Cinzas, compute_win_roll_days
/compute_wdo_roll_days, true_range_series (1a barra do dia = high-low), safe_corr
(spearman manual via rank+pearson, sem scipy), formato de log de anomalias e de
SUMMARY.txt/JSON, default_json.

Definicoes (ver PHASE_A_SPEC.md secao "Definicoes"):
  bar(T)          = 1a barra com time >= T e time < T+15min; ausente -> log de
                     anomalia, quantidade dependente fica NaN nesse dia.
  ret_into_w      = open(bar(w)) / open(bar(w-30min)) - 1
  ret_during_w    = close(bar(w)) / open(bar(w)) - 1
  ret_after_w     = open(bar(w+45min)) / open(bar(w+15min)) - 1
  tr_w, volu_w    = true range / tick_volume de bar(w); idem p/ vizinhas
                     bar(w-15min) e bar(w+15min) (tr_viz_media_w, volu_viz_media_w,
                     e absret_viz_media_w -- extensao local p/ o numerador do P2).
  fixing_eom      = ultima sessao de cada mes do calendario de pregoes do M15.
  eom_offset      = D0 (=fixing_eom) / D-1 / D-2 / D-3 / other, contado em
                     pregoes a partir do fim do mes, calendario proprio do instrumento.
  ret_am_prefix   = open(bar(13:15)) / open(1a barra do dia) - 1
  ret_pos_fix     = close(ultima barra) / open(bar(13:15)) - 1

Nuance de rolagem (spec): o roll do WDO na serie $N e na vespera do 1o dia util
do mes seguinte -> o dia de fixing de fim de mes E o roll day do WDO (overlap
~100% esperado, ver P7). Nenhuma estatistica aqui atravessa o overnight de roll
-- todas as janelas w e ret_am_prefix/ret_pos_fix sao estritamente intradiarias
(open/close/bar do MESMO dia).
"""

import datetime as dt
import json
import warnings
from collections import defaultdict
from pathlib import Path

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
]).date)  # reusado de b3_ny_open_v0 / b3_or_continuation_v0; so 2022/23/24 no IS

SESSION_OPEN_TIME = dt.time(9, 0, 0)
PROFILE_TIME_MAX = dt.time(14, 30, 0)  # P1: perfil horario 09:00-14:30

FILES = {
    ("WIN", "M15"): DATA_DIR / "WIN_cont_N_M15.parquet",
    ("WIN", "M5"): DATA_DIR / "WIN_cont_N_M5.parquet",
    ("WDO", "M15"): DATA_DIR / "WDO_cont_N_M15.parquet",
    ("WDO", "M5"): DATA_DIR / "WDO_cont_N_M5.parquet",
}

# Janelas do PTAX (horario local Brasilia, ja sem DST -- ver MT5_COLLECTION_REPORT.md)
WINDOWS = {
    "10:00": dt.time(10, 0),
    "11:00": dt.time(11, 0),
    "12:00": dt.time(12, 0),
    "13:00": dt.time(13, 0),
}

# Replica M5 2023-2024, so p/ perfis P1/P2 (spec)
M5_PROFILE_START = pd.Timestamp("2023-01-01")
M5_PROFILE_END = pd.Timestamp("2025-01-01")

anomalies = []  # lista global de dicts (instrument, tf, date, desc)


def log_anomaly(instrument, tf, date, desc):
    anomalies.append({"instrument": instrument, "tf": tf, "date": str(date), "desc": desc})


# ---------------------------------------------------------------------------
# Loading (reusado de build_ny_map.py)
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
# Roll days (logica reusada verbatim de build_ny_map.py / campanha 1)
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
    """WIN: quarta-feira mais proxima do dia 15 dos meses pares (dia do vencimento).
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
    """WDO: ultimo pregao antes do 1o pregao de cada mes (vespera do 1o dia util)."""
    session_dates_sorted = sorted(session_dates_sorted)
    roll_days = set()
    for i in range(len(session_dates_sorted) - 1):
        cur = session_dates_sorted[i]
        nxt = session_dates_sorted[i + 1]
        if (cur.year, cur.month) != (nxt.year, nxt.month):
            roll_days.add(cur)
    return roll_days


# ---------------------------------------------------------------------------
# Fim de mes (fixing_eom / eom_offset), calendario proprio de cada instrumento
# ---------------------------------------------------------------------------

def compute_eom_info(session_dates_sorted):
    """Retorna dict date -> (is_fixing_eom: bool, eom_offset: str).
    fixing_eom = ultima sessao de cada mes do calendario de pregoes (M15)."""
    by_month = defaultdict(list)
    for d in session_dates_sorted:
        by_month[(d.year, d.month)].append(d)
    info = {}
    for _, days in by_month.items():
        days_sorted = sorted(days)
        n = len(days_sorted)
        for i, d in enumerate(days_sorted):
            offset_from_end = n - 1 - i  # 0 = ultimo pregao do mes
            if offset_from_end == 0:
                info[d] = (True, "D0")
            elif offset_from_end == 1:
                info[d] = (False, "D-1")
            elif offset_from_end == 2:
                info[d] = (False, "D-2")
            elif offset_from_end == 3:
                info[d] = (False, "D-3")
            else:
                info[d] = (False, "other")
    return info


# ---------------------------------------------------------------------------
# Utilidades de tempo
# ---------------------------------------------------------------------------

def add_minutes_to_time(t, minutes):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t) + dt.timedelta(minutes=minutes)).time()


# ---------------------------------------------------------------------------
# True range intraday (1a barra do dia = high-low; reusado verbatim)
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
# bar(T): 1a barra com time >= T e time < T+15min (regra INVIOLAVEL: sem == /
# contagem de barras). Ausencia -> log de anomalia no ponto de chamada.
# ---------------------------------------------------------------------------

def get_bar(day_df, T):
    T_end = add_minutes_to_time(T, 15)
    sub = day_df[(day_df["time"] >= T) & (day_df["time"] < T_end)]
    if len(sub) == 0:
        return None
    return sub.iloc[0]


# ---------------------------------------------------------------------------
# Per-session (per-day) metrics
# ---------------------------------------------------------------------------

def compute_day_metrics(day_df, instrument, tf, date, is_fixing_eom, eom_offset, is_roll):
    day_df = day_df.sort_values("datetime_b3").reset_index(drop=True)
    n_bars = len(day_df)
    first_bar = day_df.iloc[0]
    last_bar = day_df.iloc[-1]

    if first_bar["time"] != SESSION_OPEN_TIME:
        log_anomaly(instrument, tf, date,
                    f"sessao nao comeca as 09:00: primeira barra as {first_bar['time']}")

    tr = true_range_series(day_df)
    day_df = day_df.copy()
    day_df["tr"] = tr

    day_open = first_bar["open"]
    day_close = last_bar["close"]

    row = {
        "date": str(date), "instrument": instrument, "year": int(first_bar["year"]),
        "weekday": first_bar["weekday"], "n_bars_day": n_bars,
        "is_fixing_eom": bool(is_fixing_eom), "eom_offset": eom_offset, "is_roll": bool(is_roll),
        "ret_day": (day_close / day_open - 1) if day_open else np.nan,
    }

    bar_cache = {}

    def cache_get(T):
        if T not in bar_cache:
            b = get_bar(day_df, T)
            if b is None:
                log_anomaly(instrument, tf, date, f"bar ausente para T={T}")
            bar_cache[T] = b
        return bar_cache[T]

    for wname, wt in WINDOWS.items():
        t_m30 = add_minutes_to_time(wt, -30)
        t_m15 = add_minutes_to_time(wt, -15)
        t_p15 = add_minutes_to_time(wt, 15)
        t_p45 = add_minutes_to_time(wt, 45)

        b_w = cache_get(wt)
        b_m30 = cache_get(t_m30)
        b_m15 = cache_get(t_m15)
        b_p15 = cache_get(t_p15)
        b_p45 = cache_get(t_p45)

        row[f"w{wname.replace(':', '')}_bar_missing"] = bool(b_w is None)

        # ret_into_w
        if b_w is not None and b_m30 is not None and b_m30["open"]:
            row[f"ret_into_{wname}"] = b_w["open"] / b_m30["open"] - 1
        else:
            row[f"ret_into_{wname}"] = np.nan

        # ret_during_w
        if b_w is not None and b_w["open"]:
            row[f"ret_during_{wname}"] = b_w["close"] / b_w["open"] - 1
        else:
            row[f"ret_during_{wname}"] = np.nan

        # ret_after_w
        if b_p15 is not None and b_p45 is not None and b_p15["open"]:
            row[f"ret_after_{wname}"] = b_p45["open"] / b_p15["open"] - 1
        else:
            row[f"ret_after_{wname}"] = np.nan

        # tr_w, volu_w
        row[f"tr_{wname}"] = float(b_w["tr"]) if b_w is not None else np.nan
        row[f"volu_{wname}"] = int(b_w["tick_volume"]) if b_w is not None else np.nan

        # vizinhas: bar(w-15min) e bar(w+15min) -- media exige AMBAS presentes
        if b_m15 is not None and b_p15 is not None:
            row[f"tr_viz_media_{wname}"] = float((b_m15["tr"] + b_p15["tr"]) / 2)
            row[f"volu_viz_media_{wname}"] = float((b_m15["tick_volume"] + b_p15["tick_volume"]) / 2)
            absret_m15 = abs(b_m15["close"] / b_m15["open"] - 1) if b_m15["open"] else np.nan
            absret_p15 = abs(b_p15["close"] / b_p15["open"] - 1) if b_p15["open"] else np.nan
            if not np.isnan(absret_m15) and not np.isnan(absret_p15):
                row[f"absret_viz_media_{wname}"] = (absret_m15 + absret_p15) / 2
            else:
                row[f"absret_viz_media_{wname}"] = np.nan
        else:
            row[f"tr_viz_media_{wname}"] = np.nan
            row[f"volu_viz_media_{wname}"] = np.nan
            row[f"absret_viz_media_{wname}"] = np.nan

    # ret_am_prefix / ret_pos_fix (nucleo do arco do dia de fixing, P4)
    b_1315 = cache_get(dt.time(13, 15))
    if b_1315 is not None and day_open:
        row["ret_am_prefix"] = b_1315["open"] / day_open - 1
    else:
        row["ret_am_prefix"] = np.nan
    if b_1315 is not None and b_1315["open"]:
        row["ret_pos_fix"] = day_close / b_1315["open"] - 1
    else:
        row["ret_pos_fix"] = np.nan

    # bar records p/ perfil P1 (09:00-14:30, TODAS as barras, nao so as janelas)
    prof_mask = (day_df["time"] >= SESSION_OPEN_TIME) & (day_df["time"] <= PROFILE_TIME_MAX)
    bar_records = []
    for _, b in day_df[prof_mask].iterrows():
        abs_ret = abs(b["close"] / b["open"] - 1) if b["open"] else np.nan
        bar_records.append({
            "instrument": instrument, "date": str(date), "year": int(first_bar["year"]),
            "is_fixing_eom": bool(is_fixing_eom), "local_time": str(b["time"]),
            "tr": float(b["tr"]), "abs_ret": abs_ret, "tick_volume": int(b["tick_volume"]),
        })

    return row, bar_records


def build_sessions_df(instrument, tf, roll_days, eom_info, date_filter=None):
    df, load_stats = load_raw(instrument, tf)
    if date_filter is not None:
        df = df[df["datetime_b3"].between(date_filter[0], date_filter[1], inclusive="left")].copy()

    rows = []
    bar_records_all = []
    for date, day_df in df.groupby("date"):
        info = eom_info.get(date)
        if info is None:
            log_anomaly(instrument, tf, date, "data fora do calendario eom_info (M15) -- fallback other/False")
            is_fixing_eom, eom_offset = False, "other"
        else:
            is_fixing_eom, eom_offset = info
        is_roll = date in roll_days
        row, bar_records = compute_day_metrics(day_df, instrument, tf, date, is_fixing_eom, eom_offset, is_roll)
        rows.append(row)
        bar_records_all.extend(bar_records)

    sess = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    bars = pd.DataFrame(bar_records_all)
    return sess, load_stats, bars


# ---------------------------------------------------------------------------
# safe_corr: spearman manual via rank+pearson (sem scipy) -- reusado verbatim
# ---------------------------------------------------------------------------

def safe_corr(x, y, method):
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


# ---------------------------------------------------------------------------
# P1 -- Perfil horario 09:00-14:30, normal vs fixing_eom, por horario de barra
# ---------------------------------------------------------------------------

def compute_p1(bar_df):
    if bar_df is None or len(bar_df) == 0:
        return {}
    out = {}
    for is_fix, g in bar_df.groupby("is_fixing_eom"):
        key = "fixing_eom" if is_fix else "normal"
        agg = g.groupby("local_time").agg(
            tr_mean=("tr", "mean"), tr_median=("tr", "median"),
            abs_ret_mean=("abs_ret", "mean"), abs_ret_median=("abs_ret", "median"),
            vol_mean=("tick_volume", "mean"), vol_median=("tick_volume", "median"),
            n=("tr", "count"),
        ).reset_index()
        out[key] = {
            str(r["local_time"]): {
                "tr_mean": float(r["tr_mean"]), "tr_median": float(r["tr_median"]),
                "abs_ret_mean": float(r["abs_ret_mean"]), "abs_ret_median": float(r["abs_ret_median"]),
                "vol_mean": float(r["vol_mean"]), "vol_median": float(r["vol_median"]),
                "n": int(r["n"]),
            } for _, r in agg.iterrows()
        }
    return out


# ---------------------------------------------------------------------------
# P2 -- Barras-janela vs vizinhas: razao tr_w/tr_viz, |ret_during_w|/absret_viz,
# volu_w/volu_viz, por janela w, agregado e por ano.
# ---------------------------------------------------------------------------

def compute_p2_slice(sess):
    out = {}
    for wname in WINDOWS:
        sub = sess[[f"tr_{wname}", f"tr_viz_media_{wname}", f"volu_{wname}", f"volu_viz_media_{wname}",
                    f"ret_during_{wname}", f"absret_viz_media_{wname}"]].copy()
        sub[f"absret_during_{wname}"] = sub[f"ret_during_{wname}"].abs()

        m_tr = sub[f"tr_{wname}"].notna() & sub[f"tr_viz_media_{wname}"].notna()
        m_vol = sub[f"volu_{wname}"].notna() & sub[f"volu_viz_media_{wname}"].notna()
        m_ret = sub[f"absret_during_{wname}"].notna() & sub[f"absret_viz_media_{wname}"].notna()

        tr_w_mean = float(sub.loc[m_tr, f"tr_{wname}"].mean()) if m_tr.any() else np.nan
        tr_viz_mean = float(sub.loc[m_tr, f"tr_viz_media_{wname}"].mean()) if m_tr.any() else np.nan
        vol_w_mean = float(sub.loc[m_vol, f"volu_{wname}"].mean()) if m_vol.any() else np.nan
        vol_viz_mean = float(sub.loc[m_vol, f"volu_viz_media_{wname}"].mean()) if m_vol.any() else np.nan
        ret_w_mean = float(sub.loc[m_ret, f"absret_during_{wname}"].mean()) if m_ret.any() else np.nan
        ret_viz_mean = float(sub.loc[m_ret, f"absret_viz_media_{wname}"].mean()) if m_ret.any() else np.nan

        out[wname] = {
            "tr_w_mean": tr_w_mean, "tr_viz_mean": tr_viz_mean,
            "ratio_tr": (tr_w_mean / tr_viz_mean) if tr_viz_mean else np.nan, "n_tr": int(m_tr.sum()),
            "absret_w_mean": ret_w_mean, "absret_viz_mean": ret_viz_mean,
            "ratio_absret": (ret_w_mean / ret_viz_mean) if ret_viz_mean else np.nan, "n_absret": int(m_ret.sum()),
            "volu_w_mean": vol_w_mean, "volu_viz_mean": vol_viz_mean,
            "ratio_volu": (vol_w_mean / vol_viz_mean) if vol_viz_mean else np.nan, "n_volu": int(m_vol.sum()),
        }
    return out


def compute_p2(sess):
    out = {"agregado": compute_p2_slice(sess)}
    out["por_ano"] = {str(yr): compute_p2_slice(g) for yr, g in sess.groupby("year")}
    return out


# ---------------------------------------------------------------------------
# P3 -- Into->after por janela: corr(ret_into_w, ret_after_w) + sign-conditioned,
# dias normais vs fixing.
# ---------------------------------------------------------------------------

def compute_p3(sess):
    out = {}
    for wname in WINDOWS:
        into_col, after_col = f"ret_into_{wname}", f"ret_after_{wname}"
        w_out = {}
        for cond, g in [("normal", sess[sess["is_fixing_eom"] == False]),  # noqa: E712
                        ("fixing_eom", sess[sess["is_fixing_eom"] == True])]:  # noqa: E712
            p, n = safe_corr(g[into_col], g[after_col], "pearson")
            s, _ = safe_corr(g[into_col], g[after_col], "spearman")
            pos = g[g[into_col] > 0][after_col]
            neg = g[g[into_col] < 0][after_col]
            w_out[cond] = {
                "corr_into_after": {"pearson": p, "spearman": s, "n": n},
                "ret_after_mean_given_into_pos": float(pos.mean()) if len(pos) else np.nan, "n_pos": int(len(pos)),
                "ret_after_mean_given_into_neg": float(neg.mean()) if len(neg) else np.nan, "n_neg": int(len(neg)),
            }
        out[wname] = w_out
    return out


# ---------------------------------------------------------------------------
# P4 -- Arco do dia de fixing: corr(ret_am_prefix, ret_pos_fix) + sign-conditioned,
# fixing vs normal.
# ---------------------------------------------------------------------------

def compute_p4(sess):
    out = {}
    for cond, g in [("normal", sess[sess["is_fixing_eom"] == False]),  # noqa: E712
                    ("fixing_eom", sess[sess["is_fixing_eom"] == True])]:  # noqa: E712
        p, n = safe_corr(g["ret_am_prefix"], g["ret_pos_fix"], "pearson")
        s, _ = safe_corr(g["ret_am_prefix"], g["ret_pos_fix"], "spearman")
        pos = g[g["ret_am_prefix"] > 0]["ret_pos_fix"]
        neg = g[g["ret_am_prefix"] < 0]["ret_pos_fix"]
        out[cond] = {
            "corr_am_prefix_pos_fix": {"pearson": p, "spearman": s, "n": n},
            "ret_pos_fix_mean_given_am_prefix_pos": float(pos.mean()) if len(pos) else np.nan, "n_pos": int(len(pos)),
            "ret_pos_fix_mean_given_am_prefix_neg": float(neg.mean()) if len(neg) else np.nan, "n_neg": int(len(neg)),
        }
    return out


# ---------------------------------------------------------------------------
# P5 -- Fim de mes: ret_day/ret_am_prefix/ret_pos_fix por eom_offset
# ---------------------------------------------------------------------------

def compute_p5(sess):
    out = {}
    for offset, g in sess.groupby("eom_offset"):
        out[offset] = {}
        for col in ["ret_day", "ret_am_prefix", "ret_pos_fix"]:
            s = g[col].dropna()
            out[offset][col] = {
                "mean": float(s.mean()) if len(s) else np.nan,
                "median": float(s.median()) if len(s) else np.nan,
                "n": int(len(s)),
            }
    return out


# ---------------------------------------------------------------------------
# P7 -- Qualidade
# ---------------------------------------------------------------------------

def compute_p7(sess, roll_days, tf_anomalies):
    n_total = len(sess)
    n_fixing = int(sess["is_fixing_eom"].sum())
    n_roll = int(sess["is_roll"].sum())
    fixing_dates = set(pd.to_datetime(sess.loc[sess["is_fixing_eom"], "date"]).dt.date)
    overlap_n = len(fixing_dates & roll_days)
    overlap_pct = (overlap_n / n_fixing) if n_fixing else np.nan

    missing_windows = {}
    for wname in WINDOWS:
        col = f"w{wname.replace(':', '')}_bar_missing"
        missing_windows[wname] = int(sess[col].sum()) if col in sess.columns else None

    anomaly_types = {}
    for a in tf_anomalies:
        key = a["desc"].split(":")[0]
        anomaly_types[key] = anomaly_types.get(key, 0) + 1

    return {
        "n_sessions_total": n_total,
        "n_sessions_por_ano": {str(yr): int(n) for yr, n in sess.groupby("year").size().items()},
        "n_fixing_eom": n_fixing,
        "n_roll_days": n_roll,
        "overlap_fixing_x_roll_n": overlap_n,
        "overlap_fixing_x_roll_pct": overlap_pct,
        "n_janelas_ausentes_por_w": missing_windows,
        "n_anomalies": len(tf_anomalies),
        "anomaly_types": anomaly_types,
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


def build_text_report(instruments, load_stats_all, roll_days_by_instrument,
                       p1_m15, p1_m5, p2_by_inst, p2_m5_by_inst, p3_by_inst, p4_by_inst,
                       p5_by_inst, p7_by_inst, anomalies_list):
    lines = []
    lines.append("=" * 96)
    lines.append("b3_wdo_ptax_v0 - Fase A: mapa descritivo (WDO/WIN x janelas PTAX 10:00/11:00/12:00/13:00 + fixing EOM)")
    lines.append("IS: inicio dos dados ate 2024-12-31 (datetime_b3 < 2025-01-01). OOS (2025+) NAO computado.")
    lines.append("Zero backtest / zero trade simulado / zero custo / zero PF -- estatisticas descritivas apenas.")
    lines.append("=" * 96)

    lines.append("\n--- LOAD STATS ---")
    for key, stats in load_stats_all.items():
        lines.append(f"{key}: {stats}")

    lines.append("\n--- ROLL DAYS (M15; WIN=vencimento, WDO=vespera do 1o pregao do mes) ---")
    for inst, days in roll_days_by_instrument.items():
        lines.append(f"{inst}: {len(days)} roll days no IS")

    for inst in instruments:
        lines.append("\n" + "=" * 96)
        lines.append(f"{inst}  |  Fonte primaria M15")
        lines.append("=" * 96)

        p7 = p7_by_inst[inst]
        lines.append("\n### P7 -- Qualidade / anomalias ###")
        lines.append(f"  n sessoes totais (IS, sem quartas de Cinzas): {p7['n_sessions_total']}")
        lines.append(f"  n sessoes por ano: {p7['n_sessions_por_ano']}")
        lines.append(f"  n fixing_eom: {p7['n_fixing_eom']}")
        lines.append(f"  n roll days: {p7['n_roll_days']}")
        lines.append(f"  overlap fixing x roll: {p7['overlap_fixing_x_roll_n']}/{p7['n_fixing_eom']} "
                      f"({fmt_pct(p7['overlap_fixing_x_roll_pct'])})")
        lines.append(f"  n janelas ausentes por w (bar(w) nao encontrada): {p7['n_janelas_ausentes_por_w']}")
        lines.append(f"  n anomalias total (M15): {p7['n_anomalies']}")
        lines.append("  anomalias por tipo:")
        for k, v in sorted(p7["anomaly_types"].items(), key=lambda x: -x[1]):
            lines.append(f"    {k}: {v}")

        lines.append("\n### P1 -- Perfil horario 09:00-14:30 (M15), normal vs fixing_eom ###")
        prof = p1_m15[inst]
        for cond in ["normal", "fixing_eom"]:
            if cond not in prof:
                continue
            lines.append(f"  [{cond}]")
            for k in sorted(prof[cond].keys()):
                v = prof[cond][k]
                lines.append(f"    {k}: tr_mean={fmt_num(v['tr_mean'],2)} tr_median={fmt_num(v['tr_median'],2)} "
                              f"absret_mean={fmt_pct(v['abs_ret_mean'])} vol_mean={fmt_num(v['vol_mean'],0)} n={v['n']}")

        prof5 = p1_m5.get(inst, {})
        lines.append("\n### P1 -- Replica M5 (2023-2024) ###")
        for cond in ["normal", "fixing_eom"]:
            if cond not in prof5:
                continue
            lines.append(f"  [M5, {cond}]")
            for k in sorted(prof5[cond].keys()):
                v = prof5[cond][k]
                lines.append(f"    {k}: tr_mean={fmt_num(v['tr_mean'],2)} absret_mean={fmt_pct(v['abs_ret_mean'])} "
                              f"vol_mean={fmt_num(v['vol_mean'],0)} n={v['n']}")

        p2 = p2_by_inst[inst]
        lines.append("\n### P2 -- Barras-janela vs vizinhas (razao janela/vizinhas), agregado ###")
        for wname, v in p2["agregado"].items():
            lines.append(f"  w={wname}: ratio_tr={fmt_num(v['ratio_tr'],3)} (n={v['n_tr']})  "
                          f"ratio_|ret_during|={fmt_num(v['ratio_absret'],3)} (n={v['n_absret']})  "
                          f"ratio_volu={fmt_num(v['ratio_volu'],3)} (n={v['n_volu']})")
        lines.append("  por ano:")
        for yr, slc in sorted(p2["por_ano"].items()):
            for wname, v in slc.items():
                lines.append(f"    {yr} w={wname}: ratio_tr={fmt_num(v['ratio_tr'],3)} "
                              f"ratio_absret={fmt_num(v['ratio_absret'],3)} ratio_volu={fmt_num(v['ratio_volu'],3)} "
                              f"(n_tr={v['n_tr']})")

        p2m5 = p2_m5_by_inst.get(inst)
        if p2m5:
            lines.append("\n### P2 -- Replica M5 (2023-2024), agregado ###")
            for wname, v in p2m5["agregado"].items():
                lines.append(f"  w={wname}: ratio_tr={fmt_num(v['ratio_tr'],3)} (n={v['n_tr']})  "
                              f"ratio_|ret_during|={fmt_num(v['ratio_absret'],3)} (n={v['n_absret']})  "
                              f"ratio_volu={fmt_num(v['ratio_volu'],3)} (n={v['n_volu']})")

        p3 = p3_by_inst[inst]
        lines.append("\n### P3 -- Into->after por janela (corr + sign-conditioned), normal vs fixing ###")
        for wname, w_out in p3.items():
            for cond in ["normal", "fixing_eom"]:
                c = w_out[cond]["corr_into_after"]
                lines.append(f"  w={wname} [{cond}]: corr(into,after) pearson={fmt_num(c['pearson'])} "
                              f"spearman={fmt_num(c['spearman'])} (n={c['n']})  "
                              f"ret_after|into>0={fmt_pct(w_out[cond]['ret_after_mean_given_into_pos'])} "
                              f"(n={w_out[cond]['n_pos']})  ret_after|into<0={fmt_pct(w_out[cond]['ret_after_mean_given_into_neg'])} "
                              f"(n={w_out[cond]['n_neg']})")

        p4 = p4_by_inst[inst]
        lines.append("\n### P4 -- Arco do dia de fixing: corr(ret_am_prefix, ret_pos_fix), fixing vs normal ###")
        for cond in ["normal", "fixing_eom"]:
            c = p4[cond]["corr_am_prefix_pos_fix"]
            lines.append(f"  [{cond}]: pearson={fmt_num(c['pearson'])} spearman={fmt_num(c['spearman'])} (n={c['n']})  "
                          f"ret_pos_fix|am_prefix>0={fmt_pct(p4[cond]['ret_pos_fix_mean_given_am_prefix_pos'])} "
                          f"(n={p4[cond]['n_pos']})  ret_pos_fix|am_prefix<0={fmt_pct(p4[cond]['ret_pos_fix_mean_given_am_prefix_neg'])} "
                          f"(n={p4[cond]['n_neg']})")

        p5 = p5_by_inst[inst]
        lines.append("\n### P5 -- Fim de mes por eom_offset ###")
        for offset in ["D-3", "D-2", "D-1", "D0", "other"]:
            if offset not in p5:
                continue
            v = p5[offset]
            lines.append(f"  {offset}: ret_day mean={fmt_pct(v['ret_day']['mean'])} n={v['ret_day']['n']}  |  "
                          f"ret_am_prefix mean={fmt_pct(v['ret_am_prefix']['mean'])} n={v['ret_am_prefix']['n']}  |  "
                          f"ret_pos_fix mean={fmt_pct(v['ret_pos_fix']['mean'])} n={v['ret_pos_fix']['n']}")

    # P6 -- contraste WDO vs WIN lado a lado
    lines.append("\n" + "=" * 96)
    lines.append("### P6 -- Contraste de identificacao: WDO vs WIN lado a lado (P2/P3/P4), sem juizo ###")
    lines.append("=" * 96)
    lines.append("\n-- P2 (ratio janela/vizinhas, agregado) --")
    lines.append(f"{'w':>6} | {'ratio_tr WDO':>13} {'ratio_tr WIN':>13} | {'ratio_absret WDO':>17} {'ratio_absret WIN':>17} | {'ratio_volu WDO':>15} {'ratio_volu WIN':>15}")
    for wname in WINDOWS:
        d = p2_by_inst["WDO"]["agregado"][wname]
        w = p2_by_inst["WIN"]["agregado"][wname]
        lines.append(f"{wname:>6} | {fmt_num(d['ratio_tr'],3):>13} {fmt_num(w['ratio_tr'],3):>13} | "
                      f"{fmt_num(d['ratio_absret'],3):>17} {fmt_num(w['ratio_absret'],3):>17} | "
                      f"{fmt_num(d['ratio_volu'],3):>15} {fmt_num(w['ratio_volu'],3):>15}")

    lines.append("\n-- P3 (corr into->after, pearson) --")
    lines.append(f"{'w':>6} {'cond':>10} | {'WDO':>10} (n) | {'WIN':>10} (n)")
    for wname in WINDOWS:
        for cond in ["normal", "fixing_eom"]:
            d = p3_by_inst["WDO"][wname][cond]["corr_into_after"]
            w = p3_by_inst["WIN"][wname][cond]["corr_into_after"]
            lines.append(f"{wname:>6} {cond:>10} | {fmt_num(d['pearson']):>10} (n={d['n']}) | "
                          f"{fmt_num(w['pearson']):>10} (n={w['n']})")

    lines.append("\n-- P4 (corr ret_am_prefix vs ret_pos_fix, pearson) --")
    for cond in ["normal", "fixing_eom"]:
        d = p4_by_inst["WDO"][cond]["corr_am_prefix_pos_fix"]
        w = p4_by_inst["WIN"][cond]["corr_am_prefix_pos_fix"]
        lines.append(f"  {cond:>10} | WDO={fmt_num(d['pearson']):>10} (n={d['n']}) | WIN={fmt_num(w['pearson']):>10} (n={w['n']})")

    lines.append("\n" + "=" * 96)
    lines.append(f"ANOMALIAS DE DADOS DETECTADAS (todos tf/instrumentos): {len(anomalies_list)}")
    lines.append("=" * 96)
    if anomalies_list:
        anomaly_types = {}
        for a in anomalies_list:
            key = f"[{a['instrument']} {a['tf']}] " + (a["desc"].split(":")[0] if ":" in a["desc"] else a["desc"])
            anomaly_types[key] = anomaly_types.get(key, 0) + 1
        lines.append("Resumo por tipo:")
        for k, v in sorted(anomaly_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {v} ocorrencias")
        lines.append("\nDetalhe completo (primeiras 200):")
        for a in anomalies_list[:200]:
            lines.append(f"  [{a['instrument']} {a['tf']}] {a['date']}: {a['desc']}")
        if len(anomalies_list) > 200:
            lines.append(f"  ... e mais {len(anomalies_list) - 200} ocorrencias (ver JSON para lista completa)")
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
    eom_info_by_instrument = {}
    sess_m15 = {}
    bars_m15 = {}
    sess_m5 = {}
    bars_m5 = {}

    # Calendario de pregoes (M15) por instrumento -> roll days + eom_info
    for instrument in instruments:
        df_ref, _ = load_raw(instrument, "M15")
        session_dates = sorted(set(df_ref["date"]))
        if instrument == "WIN":
            roll_days_by_instrument[instrument] = compute_win_roll_days(session_dates)
        else:
            roll_days_by_instrument[instrument] = compute_wdo_roll_days(session_dates)
        eom_info_by_instrument[instrument] = compute_eom_info(session_dates)

    # M15 -- fonte primaria: sessoes completas + bar records p/ perfil P1
    for instrument in instruments:
        sess, load_stats, bars = build_sessions_df(
            instrument, "M15", roll_days_by_instrument[instrument], eom_info_by_instrument[instrument])
        load_stats_all[f"{instrument}_M15"] = load_stats
        sess_m15[instrument] = sess
        bars_m15[instrument] = bars

    # M5 -- secundario, so p/ replicar P1/P2 em 2023-2024
    for instrument in instruments:
        sess5, load_stats5, bars5 = build_sessions_df(
            instrument, "M5", roll_days_by_instrument[instrument], eom_info_by_instrument[instrument],
            date_filter=(M5_PROFILE_START, M5_PROFILE_END))
        load_stats_all[f"{instrument}_M5_2023_2024"] = load_stats5
        sess_m5[instrument] = sess5
        bars_m5[instrument] = bars5

    # Estatisticas P1-P7
    p1_m15 = {inst: compute_p1(bars_m15[inst]) for inst in instruments}
    p1_m5 = {inst: compute_p1(bars_m5[inst]) for inst in instruments}
    p2_by_inst = {inst: compute_p2(sess_m15[inst]) for inst in instruments}
    p2_m5_by_inst = {inst: compute_p2(sess_m5[inst]) for inst in instruments}
    p3_by_inst = {inst: compute_p3(sess_m15[inst]) for inst in instruments}
    p4_by_inst = {inst: compute_p4(sess_m15[inst]) for inst in instruments}
    p5_by_inst = {inst: compute_p5(sess_m15[inst]) for inst in instruments}

    anomalies_by_inst_m15 = {
        inst: [a for a in anomalies if a["instrument"] == inst and a["tf"] == "M15"]
        for inst in instruments
    }
    p7_by_inst = {
        inst: compute_p7(sess_m15[inst], roll_days_by_instrument[inst], anomalies_by_inst_m15[inst])
        for inst in instruments
    }

    # ---- Saidas ----
    for instrument in instruments:
        out_csv = OUT_DIR / f"sessions_{instrument.lower()}.csv"
        sess_m15[instrument].to_csv(out_csv, index=False)

    summary = {
        "is_end_exclusive": str(IS_END),
        "ash_wednesdays_excluded": [str(d) for d in sorted(ASH_WEDNESDAYS)],
        "windows_ptax_local_time": {k: str(v) for k, v in WINDOWS.items()},
        "m5_profile_window": [str(M5_PROFILE_START), str(M5_PROFILE_END)],
        "load_stats": load_stats_all,
        "roll_days": {inst: sorted(str(d) for d in days) for inst, days in roll_days_by_instrument.items()},
        "P1_profile_horario_m15": p1_m15,
        "P1_profile_horario_m5_replica_2023_2024": p1_m5,
        "P2_janela_vs_vizinhas": p2_by_inst,
        "P2_janela_vs_vizinhas_m5_replica_2023_2024": p2_m5_by_inst,
        "P3_into_after_por_janela": p3_by_inst,
        "P4_arco_fixing": p4_by_inst,
        "P5_fim_de_mes": p5_by_inst,
        "P7_qualidade": p7_by_inst,
        "n_anomalies_total": len(anomalies),
        "anomalies_full": anomalies,
    }
    with open(OUT_DIR / "ptax_map_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=default_json, allow_nan=True)

    report = build_text_report(instruments, load_stats_all, roll_days_by_instrument,
                                p1_m15, p1_m5, p2_by_inst, p2_m5_by_inst, p3_by_inst, p4_by_inst,
                                p5_by_inst, p7_by_inst, anomalies)
    print(report)
    with open(OUT_DIR / "SUMMARY.txt", "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
