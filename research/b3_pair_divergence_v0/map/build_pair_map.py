"""
b3_pair_divergence_v0 - Fase A (mapa descritivo)
Par WIN x WDO -- quebras da relacao inversa (mesmo-sinal em ret_am/ret_day),
divergencia agregada z_win+z_wdo, e o que acontece depois (tarde do mesmo dia
e pregao seguinte).

Script MECANICO: apenas estatisticas descritivas sobre o IS (in-sample,
datetime_b3 < 2025-01-01). NAO toca no OOS (2025-01-01 em diante). Zero
backtest, zero trade simulado, zero custo, zero PF.

Auto-suficiente: paths RELATIVOS ao root do repo (resolvidos via
Path(__file__)). Rodar com:
    cd .../research/b3_pair_divergence_v0/map && python3 build_pair_map.py

Reusa (conforme instrucao da campanha) de research/b3_wdo_ptax_v0/map/build_ptax_map.py:
load_raw (filtro IS + exclusao de quartas de Cinzas), compute_win_roll_days/
compute_wdo_roll_days (derivados do calendario proprio de cada instrumento via
M15), get_bar (bar(T) por timestamp com ">=", nunca "=="), safe_corr (spearman
manual via rank+pearson, sem scipy), log de anomalias, formato SUMMARY.txt/JSON,
default_json.

Definicoes (ver PHASE_A_SPEC.md secao "Dados e janelas"):
  bar(T)     = 1a barra com time >= T e time < T+15min; ausente -> log de
               anomalia, quantidade dependente fica NaN nesse dia.
  ret_am     = open(bar(13:00)) / open(1a barra do dia) - 1
  ret_pm     = close(ultima barra) / open(bar(13:00)) - 1
  ret_day    = close(ultima barra) / open(1a barra do dia) - 1
  Tudo intradiario (imune a roll overnight).

  Alinhamento WIN x WDO: inner join por data. Dias sem bar(13:00) em QUALQUER
  ativo saem das condicionais que dependem de ret_am/ret_pm (ficam NaN,
  logados), mas o dia continua no alinhamento se ambos tem sessao (1a/ultima
  barra) nessa data -- ret_day nao depende de bar(13:00).

  Combo de sinal de uma janela: (sign WIN, sign WDO) em
  {(+,+), (+,-), (-,+), (-,-)}; retorno EXATAMENTE 0 em qualquer perna =
  excluido da celula, contado a parte (sem epsilon -- literal ==0).

  Componente de divergencia: z_a(t) = ret_day(t) do ativo a padronizado pela
  sigma TRAILING de 60 pregoes do PROPRIO ativo (calendario proprio de sessao
  do ativo, ANTES do alinhamento), janela t-60..t-1 (EXCLUI t), min 60 obs
  (senao NaN). div(t) = z_win(t) + z_wdo(t). Sem look-ahead: sigma nunca usa
  t nem futuro.

  t+1 (Q3/Q4) = pregao seguinte no CALENDARIO ALINHADO (proxima linha do
  dataframe alinhado por data, nao o calendario bruto de nenhum ativo
  isoladamente).

Proibicoes (identicas as campanhas 1-3): nada >= 2025-01-01; janelas por
timestamp com ">="; scans em janela inteira; ultima barra como fechamento;
nenhum parametro escolhido olhando resultado; nenhuma simulacao de trade;
sigma trailing NUNCA inclui o dia corrente.
"""

import datetime as dt
import json
import warnings
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
]).date)  # reusado de b3_ny_open_v0 / b3_wdo_ptax_v0; so 2022/23/24 no IS

SESSION_OPEN_TIME = dt.time(9, 0, 0)
T1300 = dt.time(13, 0, 0)

FILES = {
    "WIN": DATA_DIR / "WIN_cont_N_M15.parquet",
    "WDO": DATA_DIR / "WDO_cont_N_M15.parquet",
}

SIGMA_WINDOW = 60  # pregoes, trailing, excluindo t

anomalies = []  # lista global de dicts (instrument, tf, date, desc)


def log_anomaly(instrument, tf, date, desc):
    anomalies.append({"instrument": instrument, "tf": tf, "date": str(date), "desc": desc})


# ---------------------------------------------------------------------------
# Loading (reusado verbatim de build_ptax_map.py)
# ---------------------------------------------------------------------------

def load_raw(instrument):
    path = FILES[instrument]
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {path}")
    df = pd.read_parquet(path)
    required_cols = {"datetime_b3", "epoch", "open", "high", "low", "close",
                      "tick_volume", "real_volume", "spread"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"{instrument} M15: colunas faltando: {missing}")

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
# Roll days (logica reusada verbatim de build_ptax_map.py / campanha 1)
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
# bar(T): 1a barra com time >= T e time < T+15min (regra INVIOLAVEL: sem == /
# contagem de barras). Ausencia -> log de anomalia no ponto de chamada.
# ---------------------------------------------------------------------------

def add_minutes_to_time(t, minutes):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t) + dt.timedelta(minutes=minutes)).time()


def get_bar(day_df, T):
    T_end = add_minutes_to_time(T, 15)
    sub = day_df[(day_df["time"] >= T) & (day_df["time"] < T_end)]
    if len(sub) == 0:
        return None
    return sub.iloc[0]


# ---------------------------------------------------------------------------
# safe_corr: spearman manual via rank+pearson (sem scipy) -- reusado verbatim
# ---------------------------------------------------------------------------

def safe_corr(x, y, method):
    x = pd.Series(x).reset_index(drop=True)
    y = pd.Series(y).reset_index(drop=True)
    mask = x.notna() & y.notna()
    n = int(mask.sum())
    if n < 3:
        return np.nan, n
    xm, ym = x[mask], y[mask]
    if method == "spearman":
        xm, ym = xm.rank(), ym.rank()
    return float(xm.corr(ym, method="pearson")), n


def sign_of(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    if x > 0:
        return "+"
    if x < 0:
        return "-"
    return "0"


# ---------------------------------------------------------------------------
# Per-instrument daily session frame: ret_am/ret_pm/ret_day, missing-bar13
# flag, roll flag, sigma60 trailing (exclui t) + z. Calendario PROPRIO do
# ativo (antes do alinhamento).
# ---------------------------------------------------------------------------

def build_instrument_sessions(instrument):
    df, load_stats = load_raw(instrument)
    session_dates = sorted(set(df["date"]))
    if instrument == "WIN":
        roll_days = compute_win_roll_days(session_dates)
    else:
        roll_days = compute_wdo_roll_days(session_dates)

    rows = []
    for date, day_df in df.groupby("date"):
        day_df = day_df.sort_values("datetime_b3").reset_index(drop=True)
        first_bar = day_df.iloc[0]
        last_bar = day_df.iloc[-1]

        if first_bar["time"] != SESSION_OPEN_TIME:
            log_anomaly(instrument, "M15", date,
                        f"sessao nao comeca as 09:00: primeira barra as {first_bar['time']}")

        day_open = first_bar["open"]
        day_close = last_bar["close"]
        ret_day = (day_close / day_open - 1) if day_open else np.nan

        b13 = get_bar(day_df, T1300)
        missing_bar13 = b13 is None
        if missing_bar13:
            log_anomaly(instrument, "M15", date, "bar ausente para T=13:00")
            ret_am = np.nan
            ret_pm = np.nan
        else:
            ret_am = (b13["open"] / day_open - 1) if day_open else np.nan
            ret_pm = (day_close / b13["open"] - 1) if b13["open"] else np.nan

        rows.append({
            "date": date, "year": int(first_bar["year"]), "weekday": first_bar["weekday"],
            "n_bars_day": len(day_df), "is_roll": bool(date in roll_days),
            "missing_bar13": bool(missing_bar13),
            "ret_am": ret_am, "ret_pm": ret_pm, "ret_day": ret_day,
        })

    sess = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)

    # sigma trailing 60 pregoes, EXCLUI t (janela t-60..t-1); primeiros 60 = NaN.
    sess["sigma60_ret_day"] = sess["ret_day"].shift(1).rolling(SIGMA_WINDOW, min_periods=SIGMA_WINDOW).std()
    sess["z_ret_day"] = sess["ret_day"] / sess["sigma60_ret_day"]
    n_sigma_nan = int(sess["sigma60_ret_day"].isna().sum())

    return sess, roll_days, load_stats, n_sigma_nan


# ---------------------------------------------------------------------------
# Alinhamento WIN x WDO por data (inner join)
# ---------------------------------------------------------------------------

def align_pair(sess_win, sess_wdo):
    m = pd.merge(sess_win, sess_wdo, on="date", how="inner", suffixes=("_win", "_wdo"))
    m = m.sort_values("date").reset_index(drop=True)
    # year_win == year_wdo sempre (mesma data) -- mantem uma coluna year
    m["year"] = m["date"].apply(lambda d: d.year)

    m["div"] = m["z_ret_day_win"] + m["z_ret_day_wdo"]

    # combo_am / combo_day com exclusao de ret==0 (literal, sem epsilon) e de NaN
    def combo(row, ra, rb):
        va, vb = row[ra], row[rb]
        sa, sb = sign_of(va), sign_of(vb)
        if sa is None or sb is None:
            return None  # NaN em algum lado (ex.: bar13 ausente)
        if sa == "0" or sb == "0":
            return "zero"  # excluido da celula, contado a parte
        return f"({sa},{sb})"

    m["combo_am"] = m.apply(lambda r: combo(r, "ret_am_win", "ret_am_wdo"), axis=1)
    m["combo_day"] = m.apply(lambda r: combo(r, "ret_day_win", "ret_day_wdo"), axis=1)

    m["excl_am_nan"] = m["combo_am"].isna()
    m["excl_am_zero"] = m["combo_am"] == "zero"
    m["excl_day_nan"] = m["combo_day"].isna()
    m["excl_day_zero"] = m["combo_day"] == "zero"

    return m


CELLS = ["(+,+)", "(+,-)", "(-,+)", "(-,-)"]


# ---------------------------------------------------------------------------
# Q1 -- base rates e estabilidade
# ---------------------------------------------------------------------------

def matrix_counts(m, combo_col):
    out = {"cells": {c: int((m[combo_col] == c).sum()) for c in CELLS},
           "excluded_zero": int((m[combo_col] == "zero").sum()),
           "excluded_nan": int(m[combo_col].isna().sum()),
           "n_valid": int(m[combo_col].isin(CELLS).sum())}
    return out


def same_sign_rate(m, combo_col):
    valid = m[m[combo_col].isin(CELLS)]
    n = len(valid)
    if n == 0:
        return {"p_same_sign": np.nan, "n": 0}
    same = valid[combo_col].isin(["(+,+)", "(-,-)"]).sum()
    return {"p_same_sign": float(same / n), "n": int(n)}


def compute_q1(m):
    out = {}

    # corr rolling 60d (janela alinhada, inclui t -- estatistica de estabilidade,
    # NAO usada para prever nada; distinta da sigma trailing anti-look-ahead do div)
    roll_corr_day = m["ret_day_win"].rolling(SIGMA_WINDOW, min_periods=SIGMA_WINDOW).corr(m["ret_day_wdo"])
    roll_corr_pm = m["ret_pm_win"].rolling(SIGMA_WINDOW, min_periods=SIGMA_WINDOW).corr(m["ret_pm_wdo"])

    def roll_stats_by_year(roll_series):
        tmp = pd.DataFrame({"year": m["year"], "corr": roll_series})
        tmp = tmp.dropna(subset=["corr"])
        out_y = {}
        for yr, g in tmp.groupby("year"):
            out_y[str(yr)] = {"mean": float(g["corr"].mean()), "min": float(g["corr"].min()),
                               "max": float(g["corr"].max()), "n": int(len(g))}
        agg = {"mean": float(tmp["corr"].mean()) if len(tmp) else np.nan,
               "min": float(tmp["corr"].min()) if len(tmp) else np.nan,
               "max": float(tmp["corr"].max()) if len(tmp) else np.nan, "n": int(len(tmp))}
        return {"agregado": agg, "por_ano": out_y}

    out["corr_rolling60_ret_day"] = roll_stats_by_year(roll_corr_day)
    out["corr_rolling60_ret_pm"] = roll_stats_by_year(roll_corr_pm)

    # corr agregada full-sample (sanity check da spec: perto de -0.5)
    p_day, n_day = safe_corr(m["ret_day_win"], m["ret_day_wdo"], "pearson")
    s_day, _ = safe_corr(m["ret_day_win"], m["ret_day_wdo"], "spearman")
    p_pm, n_pm = safe_corr(m["ret_pm_win"], m["ret_pm_wdo"], "pearson")
    s_pm, _ = safe_corr(m["ret_pm_win"], m["ret_pm_wdo"], "spearman")
    p_am, n_am = safe_corr(m["ret_am_win"], m["ret_am_wdo"], "pearson")
    s_am, _ = safe_corr(m["ret_am_win"], m["ret_am_wdo"], "spearman")
    out["corr_full_sample"] = {
        "ret_day": {"pearson": p_day, "spearman": s_day, "n": n_day},
        "ret_pm": {"pearson": p_pm, "spearman": s_pm, "n": n_pm},
        "ret_am": {"pearson": p_am, "spearman": s_am, "n": n_am},
    }

    # P(mesmo sinal) por ano, ret_am e ret_day
    out["p_same_sign_ret_am"] = {"agregado": same_sign_rate(m, "combo_am")}
    out["p_same_sign_ret_day"] = {"agregado": same_sign_rate(m, "combo_day")}
    for yr, g in m.groupby("year"):
        out["p_same_sign_ret_am"][str(yr)] = same_sign_rate(g, "combo_am")
        out["p_same_sign_ret_day"][str(yr)] = same_sign_rate(g, "combo_day")

    # matriz 2x2
    out["matrix_ret_am"] = {"agregado": matrix_counts(m, "combo_am")}
    out["matrix_ret_day"] = {"agregado": matrix_counts(m, "combo_day")}
    for yr, g in m.groupby("year"):
        out["matrix_ret_am"][str(yr)] = matrix_counts(g, "combo_am")
        out["matrix_ret_day"][str(yr)] = matrix_counts(g, "combo_day")

    return out


# ---------------------------------------------------------------------------
# Q2 -- quebra na manha -> tarde do MESMO dia (por combo_am)
# ---------------------------------------------------------------------------

def q2_slice(g):
    out = {}
    for cell in CELLS:
        sub = g[g["combo_am"] == cell]
        row = {}
        for inst in ("win", "wdo"):
            s = sub[f"ret_pm_{inst}"].dropna()
            row[f"ret_pm_{inst}_mean"] = float(s.mean()) if len(s) else np.nan
            row[f"ret_pm_{inst}_median"] = float(s.median()) if len(s) else np.nan
            row[f"n_ret_pm_{inst}"] = int(len(s))

        both = sub.dropna(subset=["ret_pm_win", "ret_pm_wdo"])
        both = both[(both["ret_pm_win"] != 0) & (both["ret_pm_wdo"] != 0)]
        n_both = len(both)
        if n_both:
            recompoe = (np.sign(both["ret_pm_win"]) != np.sign(both["ret_pm_wdo"])).sum()
            row["p_tarde_recompoe"] = float(recompoe / n_both)
        else:
            row["p_tarde_recompoe"] = np.nan
        row["n_p_tarde_recompoe"] = int(n_both)
        row["n_cell"] = int(len(sub))
        out[cell] = row
    return out


def compute_q2(m):
    out = {"agregado": q2_slice(m)}
    for yr, g in m.groupby("year"):
        out[str(yr)] = q2_slice(g)
    return out


# ---------------------------------------------------------------------------
# Q3 -- quebra no dia t -> pregao t+1 (calendario ALINHADO), intraday only
# ---------------------------------------------------------------------------

def build_t1_frame(m):
    """Anexa colunas *_t1 = valores da PROXIMA linha do dataframe alinhado
    (t+1 no calendario alinhado). Ultima linha fica NaN em *_t1."""
    m2 = m.copy().reset_index(drop=True)
    shift_cols = ["ret_day_win", "ret_day_wdo", "ret_am_win", "ret_am_wdo", "div",
                  "date", "is_roll_win", "is_roll_wdo"]
    for c in shift_cols:
        m2[f"{c}_t1"] = m2[c].shift(-1)
    # flag: par (t, t+1) atravessa roll day de QUALQUER ativo (roll flagado em t OU em t+1)
    m2["pair_crosses_roll"] = (
        m2["is_roll_win"].astype(bool) | m2["is_roll_wdo"].astype(bool) |
        m2["is_roll_win_t1"].fillna(False).astype(bool) | m2["is_roll_wdo_t1"].fillna(False).astype(bool)
    )
    m2 = m2.iloc[:-1].copy()  # ultima linha nao tem t+1
    return m2


def q3_cell_stats(g):
    out = {}
    for cell in CELLS:
        sub = g[g["combo_day"] == cell]
        row = {"n_cell": int(len(sub))}
        for inst in ("win", "wdo"):
            for kind in ("ret_day", "ret_am"):
                col = f"{kind}_{inst}_t1"
                s = sub[col].dropna()
                row[f"{kind}_{inst}_t1_mean"] = float(s.mean()) if len(s) else np.nan
                row[f"n_{kind}_{inst}_t1"] = int(len(s))
        out[cell] = row
    return out


def q3_dose_response(g):
    valid = g.dropna(subset=["div", "div_t1"])
    n = len(valid)
    autocorr_p, n_ac = safe_corr(valid["div"], valid["div_t1"], "pearson")
    autocorr_s, _ = safe_corr(valid["div"], valid["div_t1"], "spearman")
    quintiles = {}
    if n >= 10:
        try:
            q = pd.qcut(valid["div"], 5, labels=["Q1_baixo", "Q2", "Q3", "Q4", "Q5_alto"], duplicates="drop")
            for lbl, gg in valid.groupby(q, observed=True):
                quintiles[str(lbl)] = {
                    "div_t_mean": float(gg["div"].mean()) if len(gg) else np.nan,
                    "div_t1_mean": float(gg["div_t1"].mean()) if len(gg) else np.nan,
                    "n": int(len(gg)),
                }
        except ValueError as e:
            quintiles = {"error": str(e)}
    return {
        "autocorr_lag1_div": {"pearson": autocorr_p, "spearman": autocorr_s, "n": n_ac},
        "div_t1_by_quintile_div_t": quintiles,
        "n_total": n,
    }


def compute_q3(m):
    m2 = build_t1_frame(m)
    out = {}

    out["com_roll_crossing"] = {
        "cells": {"agregado": q3_cell_stats(m2)},
        "dose_response": q3_dose_response(m2),
    }
    for yr, g in m2.groupby("year"):
        out["com_roll_crossing"]["cells"][str(yr)] = q3_cell_stats(g)

    no_cross = m2[~m2["pair_crosses_roll"]]
    out["sem_roll_crossing"] = {
        "cells": {"agregado": q3_cell_stats(no_cross)},
        "dose_response": q3_dose_response(no_cross),
    }
    for yr, g in no_cross.groupby("year"):
        out["sem_roll_crossing"]["cells"][str(yr)] = q3_cell_stats(g)

    out["n_pairs_total"] = int(len(m2))
    out["n_pairs_crossing_roll"] = int(m2["pair_crosses_roll"].sum())

    return out, m2


# ---------------------------------------------------------------------------
# Q4 -- dose-resposta na magnitude: forte (|z_win|>1 e |z_wdo|>1) vs fraco,
# dentro de (+,+) e (-,-) em t.
# ---------------------------------------------------------------------------

def compute_q4(m2):
    out = {}
    for cell in ["(+,+)", "(-,-)"]:
        sub = m2[m2["combo_day"] == cell]
        has_z = sub.dropna(subset=["z_ret_day_win", "z_ret_day_wdo"])
        forte = has_z[(has_z["z_ret_day_win"].abs() > 1) & (has_z["z_ret_day_wdo"].abs() > 1)]
        fraco = has_z[~((has_z["z_ret_day_win"].abs() > 1) & (has_z["z_ret_day_wdo"].abs() > 1))]
        cell_out = {"n_cell_total": int(len(sub)), "n_z_nan_excluded": int(len(sub) - len(has_z))}
        for lbl, sub2 in [("forte", forte), ("fraco", fraco)]:
            row = {"n": int(len(sub2))}
            for inst in ("win", "wdo"):
                s = sub2[f"ret_day_{inst}_t1"].dropna()
                row[f"ret_day_{inst}_t1_mean"] = float(s.mean()) if len(s) else np.nan
                row[f"n_ret_day_{inst}_t1"] = int(len(s))
            sdiv = sub2["div_t1"].dropna()
            row["div_t1_mean"] = float(sdiv.mean()) if len(sdiv) else np.nan
            row["n_div_t1"] = int(len(sdiv))
            cell_out[lbl] = row
        out[cell] = cell_out
    return out


# ---------------------------------------------------------------------------
# Q5 -- qual ativo ajusta: |media ret(t+1)| WIN vs WDO nas celulas (+,+)/(-,-),
# t->t+1 (dia) e AM->PM (mesmo dia, via Q2). Sem juizo (juizo = Fase B).
# ---------------------------------------------------------------------------

def compute_q5(q2_out, q3_com_roll_out):
    out = {"am_to_pm": {}, "day_t_to_t1": {}}
    q2_agg = q2_out["agregado"]
    for cell in ["(+,+)", "(-,-)"]:
        c = q2_agg[cell]
        win_mean = c["ret_pm_win_mean"]
        wdo_mean = c["ret_pm_wdo_mean"]
        out["am_to_pm"][cell] = {
            "ret_pm_win_mean": win_mean, "n_win": c["n_ret_pm_win"],
            "ret_pm_wdo_mean": wdo_mean, "n_wdo": c["n_ret_pm_wdo"],
            "abs_maior": ("WIN" if (not np.isnan(win_mean) and not np.isnan(wdo_mean) and abs(win_mean) > abs(wdo_mean))
                          else ("WDO" if (not np.isnan(win_mean) and not np.isnan(wdo_mean)) else None)),
        }
    q3_cells = q3_com_roll_out["cells"]["agregado"]
    for cell in ["(+,+)", "(-,-)"]:
        c = q3_cells[cell]
        win_mean = c["ret_day_win_t1_mean"]
        wdo_mean = c["ret_day_wdo_t1_mean"]
        out["day_t_to_t1"][cell] = {
            "ret_day_win_t1_mean": win_mean, "n_win": c["n_ret_day_win_t1"],
            "ret_day_wdo_t1_mean": wdo_mean, "n_wdo": c["n_ret_day_wdo_t1"],
            "abs_maior": ("WIN" if (not np.isnan(win_mean) and not np.isnan(wdo_mean) and abs(win_mean) > abs(wdo_mean))
                          else ("WDO" if (not np.isnan(win_mean) and not np.isnan(wdo_mean)) else None)),
        }
    return out


# ---------------------------------------------------------------------------
# Q6 -- qualidade
# ---------------------------------------------------------------------------

def compute_q6(m, roll_days_win, roll_days_wdo, n_sigma_nan_win, n_sigma_nan_wdo,
                sess_win_full, sess_wdo_full, n_pairs_total, n_pairs_crossing_roll,
                inst_anomalies):
    n_total = len(m)
    n_by_year = {str(yr): int(n) for yr, n in m.groupby("year").size().items()}

    excl_am_nan = int(m["excl_am_nan"].sum())
    excl_am_zero = int(m["excl_am_zero"].sum())
    excl_day_nan = int(m["excl_day_nan"].sum())
    excl_day_zero = int(m["excl_day_zero"].sum())

    is_roll_win_n = int(m["is_roll_win"].sum())
    is_roll_wdo_n = int(m["is_roll_wdo"].sum())

    n_win_only = len(set(sess_win_full["date"]) - set(m["date"]))
    n_wdo_only = len(set(sess_wdo_full["date"]) - set(m["date"]))

    anomaly_types = {}
    for a in inst_anomalies:
        key = f"[{a['instrument']}] " + (a["desc"].split(":")[0] if ":" in a["desc"] else a["desc"])
        anomaly_types[key] = anomaly_types.get(key, 0) + 1

    return {
        "n_dias_alinhados_total": n_total,
        "n_dias_alinhados_por_ano": n_by_year,
        "n_dias_win_sem_par_wdo": n_win_only,
        "n_dias_wdo_sem_par_win": n_wdo_only,
        "excl_ret_am_nan_bar13_ausente": excl_am_nan,
        "excl_ret_am_zero": excl_am_zero,
        "excl_ret_day_nan": excl_day_nan,
        "excl_ret_day_zero": excl_day_zero,
        "is_roll_win_n_no_alinhado": is_roll_win_n,
        "is_roll_wdo_n_no_alinhado": is_roll_wdo_n,
        "roll_days_win_total_calendario_proprio": len(roll_days_win),
        "roll_days_wdo_total_calendario_proprio": len(roll_days_wdo),
        "n_sigma60_nan_warmup_win_calendario_proprio": n_sigma_nan_win,
        "n_sigma60_nan_warmup_wdo_calendario_proprio": n_sigma_nan_wdo,
        "n_pairs_t_t1_total": n_pairs_total,
        "n_pairs_t_t1_crossing_roll": n_pairs_crossing_roll,
        "n_anomalies_total": len(inst_anomalies),
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


def build_text_report(q1, q2, q3, q4, q5, q6, load_stats_all, anomalies_list):
    lines = []
    lines.append("=" * 100)
    lines.append("b3_pair_divergence_v0 - Fase A: mapa descritivo (WIN x WDO -- quebras da relacao inversa)")
    lines.append("IS: inicio dos dados ate 2024-12-31 (datetime_b3 < 2025-01-01). OOS (2025+) NAO computado.")
    lines.append("Zero backtest / zero trade simulado / zero custo / zero PF -- estatisticas descritivas apenas.")
    lines.append("=" * 100)

    lines.append("\n--- LOAD STATS ---")
    for key, stats in load_stats_all.items():
        lines.append(f"{key}: {stats}")

    lines.append("\n" + "=" * 100)
    lines.append("### Q6 -- Qualidade ###")
    lines.append("=" * 100)
    lines.append(f"n dias alinhados (total): {q6['n_dias_alinhados_total']}")
    lines.append(f"n dias alinhados por ano: {q6['n_dias_alinhados_por_ano']}")
    lines.append(f"n dias WIN sem par WDO: {q6['n_dias_win_sem_par_wdo']}  |  "
                  f"n dias WDO sem par WIN: {q6['n_dias_wdo_sem_par_win']}")
    lines.append(f"excluidos ret_am (bar13 ausente em algum lado, NaN): {q6['excl_ret_am_nan_bar13_ausente']}  |  "
                  f"excluidos ret_am==0: {q6['excl_ret_am_zero']}")
    lines.append(f"excluidos ret_day NaN: {q6['excl_ret_day_nan']}  |  excluidos ret_day==0: {q6['excl_ret_day_zero']}")
    lines.append(f"is_roll_win no alinhado: {q6['is_roll_win_n_no_alinhado']}  |  "
                  f"is_roll_wdo no alinhado: {q6['is_roll_wdo_n_no_alinhado']}")
    lines.append(f"roll days WIN (calendario proprio, total IS): {q6['roll_days_win_total_calendario_proprio']}  |  "
                  f"roll days WDO (calendario proprio, total IS): {q6['roll_days_wdo_total_calendario_proprio']}")
    lines.append(f"NaN sigma60 (warmup, calendario proprio) WIN: {q6['n_sigma60_nan_warmup_win_calendario_proprio']}  |  "
                  f"WDO: {q6['n_sigma60_nan_warmup_wdo_calendario_proprio']}")
    lines.append(f"pares (t,t+1) total: {q6['n_pairs_t_t1_total']}  |  cruzando roll de qualquer ativo: "
                  f"{q6['n_pairs_t_t1_crossing_roll']}")
    lines.append(f"n anomalias total: {q6['n_anomalies_total']}")
    lines.append("anomalias por tipo:")
    for k, v in sorted(q6["anomaly_types"].items(), key=lambda x: -x[1]):
        lines.append(f"  {k}: {v}")

    lines.append("\n" + "=" * 100)
    lines.append("### Q1 -- Base rates e estabilidade ###")
    lines.append("=" * 100)
    cfs = q1["corr_full_sample"]
    lines.append("-- Corr agregada full-sample (sanity: ret_day perto de -0.5 esperado) --")
    for k in ["ret_day", "ret_pm", "ret_am"]:
        c = cfs[k]
        lines.append(f"  {k}: pearson={fmt_num(c['pearson'])} spearman={fmt_num(c['spearman'])} (n={c['n']})")

    lines.append("\n-- Corr rolling 60d ret_day WIN x WDO (media/min/max por ano) --")
    for yr, v in sorted(q1["corr_rolling60_ret_day"]["por_ano"].items()):
        lines.append(f"  {yr}: mean={fmt_num(v['mean'])} min={fmt_num(v['min'])} max={fmt_num(v['max'])} (n={v['n']})")
    agg = q1["corr_rolling60_ret_day"]["agregado"]
    lines.append(f"  AGREGADO: mean={fmt_num(agg['mean'])} min={fmt_num(agg['min'])} max={fmt_num(agg['max'])} (n={agg['n']})")

    lines.append("\n-- Corr rolling 60d ret_pm WIN x WDO (media/min/max por ano) --")
    for yr, v in sorted(q1["corr_rolling60_ret_pm"]["por_ano"].items()):
        lines.append(f"  {yr}: mean={fmt_num(v['mean'])} min={fmt_num(v['min'])} max={fmt_num(v['max'])} (n={v['n']})")
    agg = q1["corr_rolling60_ret_pm"]["agregado"]
    lines.append(f"  AGREGADO: mean={fmt_num(agg['mean'])} min={fmt_num(agg['min'])} max={fmt_num(agg['max'])} (n={agg['n']})")

    lines.append("\n-- P(mesmo sinal) ret_am, por ano --")
    for yr, v in sorted(q1["p_same_sign_ret_am"].items()):
        if yr == "agregado":
            continue
        lines.append(f"  {yr}: p={fmt_pct(v['p_same_sign'])} (n={v['n']})")
    v = q1["p_same_sign_ret_am"]["agregado"]
    lines.append(f"  AGREGADO: p={fmt_pct(v['p_same_sign'])} (n={v['n']})")

    lines.append("\n-- P(mesmo sinal) ret_day, por ano --")
    for yr, v in sorted(q1["p_same_sign_ret_day"].items()):
        if yr == "agregado":
            continue
        lines.append(f"  {yr}: p={fmt_pct(v['p_same_sign'])} (n={v['n']})")
    v = q1["p_same_sign_ret_day"]["agregado"]
    lines.append(f"  AGREGADO: p={fmt_pct(v['p_same_sign'])} (n={v['n']})")

    lines.append("\n-- Matriz 2x2 combos ret_day (agregado) --")
    mat = q1["matrix_ret_day"]["agregado"]
    for cell in CELLS:
        lines.append(f"  {cell}: {mat['cells'][cell]}")
    lines.append(f"  excluidos (ret==0 em algum lado): {mat['excluded_zero']}  |  excluidos NaN: {mat['excluded_nan']}  |  "
                  f"n valido: {mat['n_valid']}")

    lines.append("\n-- Matriz 2x2 combos ret_am (agregado) --")
    mat = q1["matrix_ret_am"]["agregado"]
    for cell in CELLS:
        lines.append(f"  {cell}: {mat['cells'][cell]}")
    lines.append(f"  excluidos (ret==0 em algum lado): {mat['excluded_zero']}  |  excluidos NaN (bar13 ausente): "
                  f"{mat['excluded_nan']}  |  n valido: {mat['n_valid']}")

    lines.append("\n" + "=" * 100)
    lines.append("### Q2 -- Quebra na manha -> tarde do MESMO dia (por combo_am), agregado ###")
    lines.append("=" * 100)
    for cell, row in q2["agregado"].items():
        lines.append(f"  {cell} (n_cell={row['n_cell']}):")
        lines.append(f"    ret_pm WIN: mean={fmt_pct(row['ret_pm_win_mean'])} median={fmt_pct(row['ret_pm_win_median'])} "
                      f"(n={row['n_ret_pm_win']})")
        lines.append(f"    ret_pm WDO: mean={fmt_pct(row['ret_pm_wdo_mean'])} median={fmt_pct(row['ret_pm_wdo_median'])} "
                      f"(n={row['n_ret_pm_wdo']})")
        lines.append(f"    P(tarde recompoe = sinais ret_pm opostos): {fmt_pct(row['p_tarde_recompoe'])} "
                      f"(n={row['n_p_tarde_recompoe']})")

    lines.append("\n" + "=" * 100)
    lines.append("### Q3 -- Quebra dia t -> pregao t+1 (calendario alinhado), COM roll-crossing ###")
    lines.append("=" * 100)
    for cell, row in q3["com_roll_crossing"]["cells"]["agregado"].items():
        lines.append(f"  {cell} (n_cell={row['n_cell']}):")
        for inst in ("win", "wdo"):
            lines.append(f"    ret_day({inst.upper()},t+1): mean={fmt_pct(row[f'ret_day_{inst}_t1_mean'])} "
                          f"(n={row[f'n_ret_day_{inst}_t1']})  |  ret_am({inst.upper()},t+1): "
                          f"mean={fmt_pct(row[f'ret_am_{inst}_t1_mean'])} (n={row[f'n_ret_am_{inst}_t1']})")
    dr = q3["com_roll_crossing"]["dose_response"]
    ac = dr["autocorr_lag1_div"]
    lines.append(f"\n  Autocorr lag-1 div: pearson={fmt_num(ac['pearson'])} spearman={fmt_num(ac['spearman'])} (n={ac['n']})")
    lines.append("  div(t+1) por quintil de div(t):")
    for lbl, v in dr["div_t1_by_quintile_div_t"].items():
        if isinstance(v, dict) and "n" in v:
            lines.append(f"    {lbl}: div(t) mean={fmt_num(v['div_t_mean'])} -> div(t+1) mean={fmt_num(v['div_t1_mean'])} (n={v['n']})")

    lines.append("\n" + "-" * 100)
    lines.append("### Q3 -- MESMO, SEM pares cruzando roll day de qualquer ativo (robustez) ###")
    lines.append("-" * 100)
    for cell, row in q3["sem_roll_crossing"]["cells"]["agregado"].items():
        lines.append(f"  {cell} (n_cell={row['n_cell']}):")
        for inst in ("win", "wdo"):
            lines.append(f"    ret_day({inst.upper()},t+1): mean={fmt_pct(row[f'ret_day_{inst}_t1_mean'])} "
                          f"(n={row[f'n_ret_day_{inst}_t1']})")
    dr2 = q3["sem_roll_crossing"]["dose_response"]
    ac2 = dr2["autocorr_lag1_div"]
    lines.append(f"\n  Autocorr lag-1 div (sem roll-crossing): pearson={fmt_num(ac2['pearson'])} "
                  f"spearman={fmt_num(ac2['spearman'])} (n={ac2['n']})")

    lines.append("\n" + "=" * 100)
    lines.append("### Q4 -- Dose-resposta na magnitude (forte |z|>1 ambos vs fraco), celulas mesmo-sinal ###")
    lines.append("=" * 100)
    for cell, cell_out in q4.items():
        lines.append(f"  {cell} (n_cell_total={cell_out['n_cell_total']}, n_z_nan_excluido={cell_out['n_z_nan_excluded']}):")
        for lbl in ("forte", "fraco"):
            row = cell_out[lbl]
            lines.append(f"    [{lbl}] n={row['n']}  ret_day(WIN,t+1) mean={fmt_pct(row['ret_day_win_t1_mean'])} "
                          f"(n={row['n_ret_day_win_t1']})  ret_day(WDO,t+1) mean={fmt_pct(row['ret_day_wdo_t1_mean'])} "
                          f"(n={row['n_ret_day_wdo_t1']})  div(t+1) mean={fmt_num(row['div_t1_mean'])} (n={row['n_div_t1']})")

    lines.append("\n" + "=" * 100)
    lines.append("### Q5 -- Qual ativo ajusta (|media ret(t+1)| WIN vs WDO, celulas mesmo-sinal), sem juizo ###")
    lines.append("=" * 100)
    lines.append("-- AM -> PM (mesmo dia) --")
    for cell, v in q5["am_to_pm"].items():
        lines.append(f"  {cell}: ret_pm WIN mean={fmt_pct(v['ret_pm_win_mean'])} (n={v['n_win']})  |  "
                      f"ret_pm WDO mean={fmt_pct(v['ret_pm_wdo_mean'])} (n={v['n_wdo']})  |  |maior|={v['abs_maior']}")
    lines.append("-- dia t -> t+1 --")
    for cell, v in q5["day_t_to_t1"].items():
        lines.append(f"  {cell}: ret_day(t+1) WIN mean={fmt_pct(v['ret_day_win_t1_mean'])} (n={v['n_win']})  |  "
                      f"ret_day(t+1) WDO mean={fmt_pct(v['ret_day_wdo_t1_mean'])} (n={v['n_wdo']})  |  |maior|={v['abs_maior']}")

    lines.append("\n" + "=" * 100)
    lines.append(f"ANOMALIAS DE DADOS DETECTADAS: {len(anomalies_list)}")
    lines.append("=" * 100)
    if anomalies_list:
        anomaly_types = {}
        for a in anomalies_list:
            key = f"[{a['instrument']}] " + (a["desc"].split(":")[0] if ":" in a["desc"] else a["desc"])
            anomaly_types[key] = anomaly_types.get(key, 0) + 1
        lines.append("Resumo por tipo:")
        for k, v in sorted(anomaly_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {v} ocorrencias")
        lines.append("\nDetalhe completo (primeiras 200):")
        for a in anomalies_list[:200]:
            lines.append(f"  [{a['instrument']}] {a['date']}: {a['desc']}")
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
    load_stats_all = {}

    sess_win, roll_days_win, load_stats_win, n_sigma_nan_win = build_instrument_sessions("WIN")
    sess_wdo, roll_days_wdo, load_stats_wdo, n_sigma_nan_wdo = build_instrument_sessions("WDO")
    load_stats_all["WIN_M15"] = load_stats_win
    load_stats_all["WDO_M15"] = load_stats_wdo

    # rename is_roll -> keep for post-merge suffixing consistency
    m = align_pair(sess_win, sess_wdo)

    q1 = compute_q1(m)
    q2 = compute_q2(m)
    q3, m2 = compute_q3(m)
    q4 = compute_q4(m2)
    q5 = compute_q5(q2, q3["com_roll_crossing"])
    q6 = compute_q6(m, roll_days_win, roll_days_wdo, n_sigma_nan_win, n_sigma_nan_wdo,
                     sess_win, sess_wdo, q3["n_pairs_total"], q3["n_pairs_crossing_roll"], anomalies)

    # ---- Saidas ----
    out_cols = [
        "date", "year",
        "ret_am_win", "ret_pm_win", "ret_day_win", "z_ret_day_win", "is_roll_win", "missing_bar13_win",
        "ret_am_wdo", "ret_pm_wdo", "ret_day_wdo", "z_ret_day_wdo", "is_roll_wdo", "missing_bar13_wdo",
        "div", "combo_am", "combo_day",
        "excl_am_nan", "excl_am_zero", "excl_day_nan", "excl_day_zero",
    ]
    m_out = m[out_cols].copy()
    m_out.to_csv(OUT_DIR / "sessions_pair.csv", index=False)

    summary = {
        "is_end_exclusive": str(IS_END),
        "ash_wednesdays_excluded": [str(d) for d in sorted(ASH_WEDNESDAYS)],
        "sigma_window_pregoes": SIGMA_WINDOW,
        "load_stats": load_stats_all,
        "roll_days_win": sorted(str(d) for d in roll_days_win),
        "roll_days_wdo": sorted(str(d) for d in roll_days_wdo),
        "Q1_base_rates_estabilidade": q1,
        "Q2_manha_tarde_mesmo_dia": q2,
        "Q3_dia_t_para_t1": q3["com_roll_crossing"] | {"n_pairs_total": q3["n_pairs_total"],
                                                        "n_pairs_crossing_roll": q3["n_pairs_crossing_roll"]},
        "Q3_dia_t_para_t1_sem_roll_crossing": q3["sem_roll_crossing"],
        "Q4_dose_resposta_magnitude": q4,
        "Q5_qual_ativo_ajusta": q5,
        "Q6_qualidade": q6,
        "n_anomalies_total": len(anomalies),
        "anomalies_full": anomalies,
    }
    with open(OUT_DIR / "pair_map_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=default_json, allow_nan=True)

    report = build_text_report(q1, q2, q3, q4, q5, q6, load_stats_all, anomalies)
    print(report)
    with open(OUT_DIR / "SUMMARY.txt", "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
