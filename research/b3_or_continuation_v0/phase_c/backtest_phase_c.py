"""
b3_or_continuation_v0 - Fase C (backtest mecanico das 4 mecanicas congeladas)

Implementa EXATAMENTE as mecanicas C1 (FADE-RETEST), C2 (FADE-REJECT),
C3 (ORB-CONT) e C4 (ORB-CONT-TGT) especificadas em
DECISION_PHASE_B.md, com as correcoes de code review da Fase A:
  1. Roll days derivados do calendario de sessoes do PROPRIO arquivo em
     analise (nao emprestados de outro timeframe).
  2. M5 e M15 nunca comparados diretamente (universos de datas diferentes).
     M15 e' anexo (C1/C3 apenas), so' pode DERRUBAR uma mecanica.

Executor MECANICO: nenhum parametro foi otimizado, nenhum filtro foi
adicionado. Numeros sao reportados como saem.

Auto-suficiente: paths absolutos, sem env vars especiais.
Rodar com: python backtest_phase_c.py
"""

import json
import warnings
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\b3_or_continuation_v0\phase_c")
OUT_DIR.mkdir(parents=True, exist_ok=True)

IS_END = pd.Timestamp("2025-01-01")  # exclusive upper bound; OOS = >= isto, sagrado

ASH_WEDNESDAYS = set(pd.to_datetime(["2022-03-02", "2023-02-22", "2024-02-14"]).date)

# uniao de dias anomalos da Fase A + dias truncados de inicio de serie
# (WIN M5 2022-12-14, WDO M5 2022-12-26, WIN M5 2023-01-24/2024-03-08,
#  WDO M5 2023-01-24/2023-12-14/2024-12-12) -- aplicada aos dois ativos
# por instrucao explicita do DECISION_PHASE_B.md secao 3.
ANOMALY_UNION = set(pd.to_datetime([
    "2022-12-14", "2022-12-26", "2023-01-24", "2023-12-14", "2024-03-08", "2024-12-12",
]).date)

TICK = {"WIN": 5.0, "WDO": 0.5}          # tamanho do tick em pontos
PT_VALUE_RS = {"WIN": 0.20, "WDO": 10.0}  # R$ por ponto por contrato
COST_SCENARIOS_TICKS = [0.5, 1.0, 2.0]    # ticks por execucao
REF_COST_TICKS = 1.0                      # cenario de referencia para gates
SCENARIO_KEY = {0.5: "05tick", 1.0: "1tick", 2.0: "2tick"}  # sufixo de coluna por cenario

FILES = {
    ("WIN", "M5"): DATA_DIR / "WIN_cont_N_M5.parquet",
    ("WIN", "M15"): DATA_DIR / "WIN_cont_N_M15.parquet",
    ("WDO", "M5"): DATA_DIR / "WDO_cont_N_M5.parquet",
    ("WDO", "M15"): DATA_DIR / "WDO_cont_N_M15.parquet",
}

SESSION_OPEN = time(9, 0, 0)
T_0915 = time(9, 15, 0)
T_1100 = time(11, 0, 0)
T_1130 = time(11, 30, 0)
T_1300 = time(13, 0, 0)

MAIN_RUNS = [(mech, inst, "M5") for mech in ["C1", "C2", "C3", "C4"] for inst in ["WIN", "WDO"]]
ANNEX_RUNS = [(mech, inst, "M15") for mech in ["C1", "C3"] for inst in ["WIN", "WDO"]]
ALL_RUNS = MAIN_RUNS + ANNEX_RUNS

impl_notes = []  # problemas/observacoes de implementacao para o relatorio final


def note(msg):
    impl_notes.append(msg)
    print("[NOTE]", msg)


# ---------------------------------------------------------------------------
# Roll day computation (correcao #1: sempre a partir do PROPRIO arquivo)
# ---------------------------------------------------------------------------

def nearest_wednesday(year, month, day=15):
    base = pd.Timestamp(year=year, month=month, day=day)
    best, best_delta = None, None
    for offset in range(-6, 7):
        cand = base + pd.Timedelta(days=offset)
        if cand.weekday() == 2:  # quarta-feira
            delta = abs(offset)
            if best_delta is None or delta < best_delta:
                best_delta, best = delta, cand
    return best.date()


def compute_win_roll_days(session_dates_sorted):
    """WIN: quarta-feira mais proxima do dia 15 dos meses pares.
    Se a data nominal cair em dia sem pregao, usa o proximo pregao."""
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
    """WDO: ultimo pregao antes do 1o pregao de cada mes. Marca TODOS os
    rolls do periodo do arquivo, inclusive o ultimo (comparando com o
    ultimo dia disponivel mesmo que seja o proprio fim da amostra nao
    seguido de mes novo completo -- aqui usamos apenas transicoes de mes
    observadas dentro da amostra)."""
    session_dates_sorted = sorted(session_dates_sorted)
    roll_days = set()
    for i in range(len(session_dates_sorted) - 1):
        cur = session_dates_sorted[i]
        nxt = session_dates_sorted[i + 1]
        if (cur.year, cur.month) != (nxt.year, nxt.month):
            roll_days.add(cur)
    return roll_days


# ---------------------------------------------------------------------------
# Loading + day-level structure
# ---------------------------------------------------------------------------

def load_and_prepare(instrument, tf):
    path = FILES[(instrument, tf)]
    df = pd.read_parquet(path)
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    df["date"] = df["datetime_b3"].dt.date
    df["time"] = df["datetime_b3"].dt.time
    df["year"] = df["datetime_b3"].dt.year

    n_raw = len(df)
    df = df[df["datetime_b3"] < IS_END].copy()
    n_is = len(df)

    df = df[~df["date"].isin(ASH_WEDNESDAYS) & ~df["date"].isin(ANOMALY_UNION)].copy()
    n_after_excl = len(df)

    # correcao #1: roll days computados do calendario de sessoes DESTE arquivo
    session_dates = sorted(set(df["date"]))
    if instrument == "WIN":
        roll_days = compute_win_roll_days(session_dates)
    else:
        roll_days = compute_wdo_roll_days(session_dates)

    load_stats = {"n_bars_raw": n_raw, "n_bars_is": n_is, "n_bars_is_pos_exclusoes": n_after_excl,
                  "n_roll_days": len(roll_days)}
    return df, roll_days, load_stats


def build_days(df, instrument, tf):
    """Retorna dict date -> {bars, or_high, or_low, or_mid, or_width, year}
    aplicando os guards dinamicos (primeira barra != 09:00, OR_width < 2 ticks)."""
    tick = TICK[instrument]
    days = {}
    excl_counts = {"first_bar_not_0900": 0, "or_window_incomplete": 0,
                   "or_width_lt_2ticks": 0, "no_bars_after_or": 0}

    for date, day_df in df.groupby("date"):
        day_df = day_df.sort_values("datetime_b3").reset_index(drop=True)
        first_time = day_df.iloc[0]["time"]
        if first_time != SESSION_OPEN:
            excl_counts["first_bar_not_0900"] += 1
            continue

        if tf == "M5":
            n_or_bars = 3
        else:  # M15
            n_or_bars = 1

        if len(day_df) <= n_or_bars:
            excl_counts["or_window_incomplete"] += 1
            continue

        or_bars = day_df.iloc[0:n_or_bars]
        if len(or_bars) < n_or_bars:
            excl_counts["or_window_incomplete"] += 1
            continue

        or_high = float(or_bars["high"].max())
        or_low = float(or_bars["low"].min())
        or_width = or_high - or_low

        if or_width < 2 * tick:
            excl_counts["or_width_lt_2ticks"] += 1
            continue

        if len(day_df) <= n_or_bars:
            excl_counts["no_bars_after_or"] += 1
            continue

        days[date] = {
            "date": date,
            "bars": day_df,
            "or_high": or_high,
            "or_low": or_low,
            "or_mid": (or_high + or_low) / 2.0,
            "or_width": or_width,
            "year": int(day_df.iloc[0]["year"]),
        }

    return days, excl_counts


# ---------------------------------------------------------------------------
# Simulador de execucao (comum as 4 mecanicas)
# ---------------------------------------------------------------------------

def simulate_trade(day_df, entry_idx, direction, stop_price, target_price, time_exit_time):
    """direction: +1 long, -1 short.
    Convencao (conservadora, unica interpretacao possivel sem overfitting):
      - Stop e' ordem stop (vira mercado ao ser tocada): se a barra abre
        ALEM do nivel, executa no OPEN (pior preco / slippage de gap).
      - Alvo e' ordem limite: executa exatamente no nivel do alvo, mesmo
        que a barra abra alem dele (nao persegue o gap a favor).
      - Se stop E alvo sao atingiveis na MESMA barra: assume stop primeiro.
      - Saida por tempo (13:00, so' C1/C2) e saida de fim-de-sessao (EOD,
        todas) tem prioridade sobre stop/alvo na barra em que ocorrem —
        sao saidas obrigatorias no OPEN da barra.
    Retorna (exit_time, exit_price, exit_reason).
    """
    n = len(day_df)
    last_idx = n - 1
    for i in range(entry_idx, n):
        bar = day_df.iloc[i]
        bar_time = bar["time"]

        if time_exit_time is not None and bar_time == time_exit_time:
            return bar["datetime_b3"], float(bar["open"]), "time"

        if i == last_idx:
            return bar["datetime_b3"], float(bar["open"]), "eod"

        hi, lo, op = float(bar["high"]), float(bar["low"]), float(bar["open"])

        if direction == 1:
            stop_hit = lo <= stop_price
            tp_hit = (target_price is not None) and (hi >= target_price)
        else:
            stop_hit = hi >= stop_price
            tp_hit = (target_price is not None) and (lo <= target_price)

        if stop_hit:
            if direction == 1:
                exec_p = stop_price if op > stop_price else op
            else:
                exec_p = stop_price if op < stop_price else op
            return bar["datetime_b3"], exec_p, "stop"
        elif tp_hit:
            return bar["datetime_b3"], float(target_price), "tp"
        # senao continua para a proxima barra

    bar = day_df.iloc[last_idx]
    return bar["datetime_b3"], float(bar["open"]), "eod"  # fallback, nao deveria ser alcancado


# ---------------------------------------------------------------------------
# Geradores de sinal por mecanica
# ---------------------------------------------------------------------------

def find_c1_c3_breakout(day):
    """Comum a C1 e C3: primeira barra do dia (inicio in [09:15,11:00]) com
    CLOSE fora do OR. Retorna (idx, side) ou (None, None). side: 'up'/'down'."""
    df = day["bars"]
    or_high, or_low = day["or_high"], day["or_low"]
    n = len(df)
    for i in range(n):
        t = df.iloc[i]["time"]
        if t < T_0915:
            continue
        if t > T_1100:
            break
        c = float(df.iloc[i]["close"])
        if c > or_high:
            return i, "up"
        elif c < or_low:
            return i, "down"
    return None, None


def mech_c1(day):
    df = day["bars"]
    or_high, or_low, or_mid = day["or_high"], day["or_low"], day["or_mid"]
    n = len(df)

    breakout_idx, side = find_c1_c3_breakout(day)
    if breakout_idx is None:
        return None

    signal_idx = None
    for j in range(breakout_idx + 1, n):
        t = df.iloc[j]["time"]
        if t > T_1130:
            break
        c = float(df.iloc[j]["close"])
        if or_low <= c <= or_high:
            signal_idx = j
            break
    if signal_idx is None:
        return None

    entry_idx = signal_idx + 1
    if entry_idx >= n:
        return None

    direction = -1 if side == "up" else 1  # contra o breakout
    seg = df.iloc[breakout_idx:signal_idx + 1]
    stop = float(seg["high"].max()) if direction == -1 else float(seg["low"].min())
    target = or_mid

    return {"entry_idx": entry_idx, "direction": direction, "stop": stop,
            "target": target, "time_exit": T_1300,
            "meta": {"breakout_idx": breakout_idx, "signal_idx": signal_idx, "side": side}}


def mech_c2(day):
    df = day["bars"]
    or_high, or_low, or_mid = day["or_high"], day["or_low"], day["or_mid"]
    n = len(df)

    sig_idx, direction = None, None
    for i in range(n):
        t = df.iloc[i]["time"]
        if t < T_0915:
            continue
        if t > T_1100:
            break
        hi, lo, c = float(df.iloc[i]["high"]), float(df.iloc[i]["low"]), float(df.iloc[i]["close"])
        pen_up = hi > or_high
        pen_down = lo < or_low
        if pen_up or pen_down:
            if pen_up and pen_down:
                return None  # penetracao ambigua dos dois lados na mesma barra: sem trade C2
            inside_close = or_low <= c <= or_high
            if not inside_close:
                return None  # fecha fora do range -> morfologia C1/C3, nao C2
            sig_idx = i
            direction = -1 if pen_up else 1
            break

    if sig_idx is None:
        return None

    entry_idx = sig_idx + 1
    if entry_idx >= n:
        return None

    sig_bar = df.iloc[sig_idx]
    stop = float(sig_bar["high"]) if direction == -1 else float(sig_bar["low"])
    target = or_mid

    return {"entry_idx": entry_idx, "direction": direction, "stop": stop,
            "target": target, "time_exit": T_1300, "meta": {"signal_idx": sig_idx}}


def mech_c3(day):
    df = day["bars"]
    or_high, or_low = day["or_high"], day["or_low"]
    n = len(df)

    breakout_idx, side = find_c1_c3_breakout(day)
    if breakout_idx is None:
        return None

    entry_idx = breakout_idx + 1
    if entry_idx >= n:
        return None

    direction = 1 if side == "up" else -1
    stop = or_low if direction == 1 else or_high

    return {"entry_idx": entry_idx, "direction": direction, "stop": float(stop),
            "target": None, "time_exit": None, "meta": {"signal_idx": breakout_idx, "side": side}}


# C4 reusa o sinal/entrada do C3 (identico por especificacao); stop e alvo
# sao recalculados no runner porque o alvo depende do preco de entrada.


# ---------------------------------------------------------------------------
# Runner: gera trades para 1 (mecanica, instrumento, tf)
# ---------------------------------------------------------------------------

def run_mechanic(mech_name, instrument, tf, days):
    tick = TICK[instrument]
    trades = []

    for date in sorted(days.keys()):
        day = days[date]
        df = day["bars"]

        if mech_name in ("C1", "C2", "C3"):
            sig_fn = {"C1": mech_c1, "C2": mech_c2, "C3": mech_c3}[mech_name]
            sig = sig_fn(day)
            if sig is None:
                continue
            entry_idx = sig["entry_idx"]
            entry_bar = df.iloc[entry_idx]
            entry_price = float(entry_bar["open"])
            direction = sig["direction"]
            stop = sig["stop"]
            target = sig["target"]
            time_exit = sig["time_exit"]
        elif mech_name == "C4":
            sig = mech_c3(day)
            if sig is None:
                continue
            entry_idx = sig["entry_idx"]
            entry_bar = df.iloc[entry_idx]
            entry_price = float(entry_bar["open"])
            direction = sig["direction"]
            stop = day["or_mid"]
            target = entry_price + direction * day["or_width"]
            time_exit = None
        else:
            raise ValueError(mech_name)

        exit_time, exit_price, exit_reason = simulate_trade(
            df, entry_idx, direction, stop, target, time_exit)

        pnl_pts = direction * (exit_price - entry_price)
        pnl_pct = pnl_pts / entry_price if entry_price else np.nan

        row = {
            "date": str(date),
            "instrument": instrument,
            "tf": tf,
            "mechanic": mech_name,
            "direction": "long" if direction == 1 else "short",
            "entry_time": str(entry_bar["datetime_b3"]),
            "entry_price": entry_price,
            "exit_time": str(exit_time),
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "stop_price": stop,
            "target_price": target,
            "or_high": day["or_high"],
            "or_low": day["or_low"],
            "or_width": day["or_width"],
            "year": day["year"],
            "pnl_pts": pnl_pts,
            "pnl_pct": pnl_pct,
        }
        for c_ticks in COST_SCENARIOS_TICKS:
            cost_pts = 2 * c_ticks * tick  # 2 execucoes (entrada + saida)
            key = SCENARIO_KEY[c_ticks]
            row[f"pnl_liq_pts_{key}"] = pnl_pts - cost_pts
            row[f"pnl_liq_pct_{key}"] = (pnl_pts - cost_pts) / entry_price if entry_price else np.nan
            row[f"pnl_liq_rs_{key}"] = (pnl_pts - cost_pts) * PT_VALUE_RS[instrument]
        row["pnl_rs_gross"] = pnl_pts * PT_VALUE_RS[instrument]
        trades.append(row)

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------

def profit_factor(pnl_series):
    wins = pnl_series[pnl_series > 0].sum()
    losses = pnl_series[pnl_series < 0].sum()
    if losses == 0:
        return np.inf if wins > 0 else np.nan
    return float(wins / abs(losses))


def win_rate(pnl_series):
    n = len(pnl_series)
    if n == 0:
        return np.nan
    return float((pnl_series > 0).sum() / n)


def payoff_ratio(pnl_series):
    wins = pnl_series[pnl_series > 0]
    losses = pnl_series[pnl_series < 0]
    if len(losses) == 0 or losses.mean() == 0 or len(wins) == 0:
        return np.nan
    return float(wins.mean() / abs(losses.mean()))


def max_drawdown(equity_curve):
    if len(equity_curve) == 0:
        return np.nan
    running_max = np.maximum.accumulate(equity_curve)
    dd = (equity_curve - running_max) / running_max
    return float(dd.min())


def sharpe_annualized(pct_returns, trades_per_year):
    if len(pct_returns) < 2 or pct_returns.std(ddof=1) == 0:
        return np.nan
    return float(pct_returns.mean() / pct_returns.std(ddof=1) * np.sqrt(trades_per_year))


def build_tercile_map(days):
    """Divide as sessoes validas (universo do instrumento/tf) em 3 grupos
    cronologicos de tamanho igual (tercis do IS do proprio arquivo)."""
    dates_sorted = sorted(days.keys())
    n = len(dates_sorted)
    tercile_map = {}
    for i, d in enumerate(dates_sorted):
        if i < n / 3:
            tercile_map[d] = 1
        elif i < 2 * n / 3:
            tercile_map[d] = 2
        else:
            tercile_map[d] = 3
    return tercile_map


def compute_metrics(trades_df, instrument, tf, days, universe_years):
    ref_key = f"pnl_liq_pts_{SCENARIO_KEY[REF_COST_TICKS]}"
    ref_pct_key = f"pnl_liq_pct_{SCENARIO_KEY[REF_COST_TICKS]}"
    ref_rs_key = f"pnl_liq_rs_{SCENARIO_KEY[REF_COST_TICKS]}"

    n_trades = len(trades_df)
    m = {"n_trades": n_trades, "trades_por_ano": n_trades / universe_years if universe_years else np.nan}

    if n_trades == 0:
        m["_empty"] = True
        return m

    m["win_rate_net_1tick"] = win_rate(trades_df[ref_key])
    m["payoff_net_1tick"] = payoff_ratio(trades_df[ref_key])

    m["pf_bruto"] = profit_factor(trades_df["pnl_pts"])
    for c_ticks in COST_SCENARIOS_TICKS:
        key = SCENARIO_KEY[c_ticks]
        m[f"pf_liq_{key}"] = profit_factor(trades_df[f"pnl_liq_pts_{key}"])
        m[f"expectancy_pts_liq_{key}"] = float(trades_df[f"pnl_liq_pts_{key}"].mean())
        m[f"expectancy_pct_liq_{key}"] = float(trades_df[f"pnl_liq_pct_{key}"].mean())
        m[f"expectancy_rs_liq_{key}"] = float(trades_df[f"pnl_liq_rs_{key}"].mean())

    # equity simulada partindo de $100, cenario referencia 1 tick
    eq = 100.0 * (1.0 + trades_df[ref_pct_key]).cumprod()
    m["equity_final_1tick"] = float(eq.iloc[-1])
    m["max_drawdown_1tick"] = max_drawdown(eq.values)
    m["sharpe_annualized_1tick"] = sharpe_annualized(trades_df[ref_pct_key], m["trades_por_ano"])

    # equity nos outros cenarios (so' final, para reporte)
    for c_ticks in COST_SCENARIOS_TICKS:
        key = SCENARIO_KEY[c_ticks]
        eq_c = 100.0 * (1.0 + trades_df[f"pnl_liq_pct_{key}"]).cumprod()
        m[f"equity_final_{key}"] = float(eq_c.iloc[-1])

    # breakdown exit_reason
    exit_bd = {}
    for reason, g in trades_df.groupby("exit_reason"):
        exit_bd[reason] = {"n": len(g), "pct": len(g) / n_trades,
                            "expectancy_pts_liq_1tick": float(g[ref_key].mean())}
    m["breakdown_exit_reason"] = exit_bd

    # breakdown direcao
    dir_bd = {}
    for direction, g in trades_df.groupby("direction"):
        dir_bd[direction] = {"n": len(g), "pf_liq_1tick": profit_factor(g[ref_key]),
                              "win_rate": win_rate(g[ref_key]),
                              "expectancy_pts_liq_1tick": float(g[ref_key].mean())}
    m["breakdown_direction"] = dir_bd

    # breakdown por ano
    year_bd = {}
    for yr, g in trades_df.groupby("year"):
        year_bd[str(yr)] = {"n": len(g), "pf_liq_1tick": profit_factor(g[ref_key]),
                             "expectancy_pts_liq_1tick": float(g[ref_key].mean())}
    m["breakdown_year"] = year_bd

    # com/sem dias de rolagem -- precisa do flag is_roll por data (calculado no caller)
    if "is_roll" in trades_df.columns:
        roll_bd = {}
        for flag, g in trades_df.groupby("is_roll"):
            roll_bd[str(bool(flag))] = {"n": len(g), "pf_liq_1tick": profit_factor(g[ref_key]),
                                         "expectancy_pts_liq_1tick": float(g[ref_key].mean()) if len(g) else np.nan}
        m["breakdown_roll_day"] = roll_bd

    # tercis do IS (cronologico, por sessao do proprio arquivo)
    if "tercile" in trades_df.columns:
        terc_bd = {}
        for terc, g in trades_df.groupby("tercile"):
            terc_bd[str(terc)] = {"n": len(g), "pf_liq_1tick": profit_factor(g[ref_key]),
                                   "expectancy_pts_liq_1tick": float(g[ref_key].mean()) if len(g) else np.nan}
        m["breakdown_tercile"] = terc_bd

    # dependencia de outliers: PF liq 1 tick removendo os 2 melhores trades
    sorted_desc = trades_df.sort_values(ref_key, ascending=False)
    if n_trades > 2:
        trimmed = sorted_desc.iloc[2:]
        m["pf_liq_1tick_sem_top2"] = profit_factor(trimmed[ref_key])
    else:
        m["pf_liq_1tick_sem_top2"] = np.nan

    return m


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    all_trades = {}
    all_metrics = {}
    load_stats_all = {}
    days_by_instrument_tf = {}
    excl_counts_all = {}
    universe_years = {}

    for (instrument, tf) in [("WIN", "M5"), ("WDO", "M5"), ("WIN", "M15"), ("WDO", "M15")]:
        df, roll_days, load_stats = load_and_prepare(instrument, tf)
        days, excl_counts = build_days(df, instrument, tf)
        load_stats_all[f"{instrument}_{tf}"] = load_stats
        excl_counts_all[f"{instrument}_{tf}"] = excl_counts
        days_by_instrument_tf[(instrument, tf)] = (days, roll_days)

        if len(days) == 0:
            note(f"{instrument} {tf}: ZERO dias validos apos exclusoes/guards.")
            universe_years[(instrument, tf)] = np.nan
            continue

        dmin, dmax = min(days.keys()), max(days.keys())
        span_years = (dmax - dmin).days / 365.25
        universe_years[(instrument, tf)] = span_years if span_years > 0 else np.nan

        print(f"{instrument} {tf}: {len(days)} dias validos ({dmin} -> {dmax}, "
              f"{span_years:.2f} anos). Exclusoes dinamicas: {excl_counts}")

    for (mech, instrument, tf) in ALL_RUNS:
        days, roll_days = days_by_instrument_tf[(instrument, tf)]
        if len(days) == 0:
            continue
        trades_df = run_mechanic(mech, instrument, tf, days)

        run_key = f"{mech}_{instrument}_{tf}"

        if len(trades_df) == 0:
            note(f"{run_key}: ZERO trades gerados.")
            all_trades[run_key] = trades_df
            all_metrics[run_key] = {"n_trades": 0, "_empty": True}
            continue

        # anexar is_roll e tercile por data
        trades_df["is_roll"] = trades_df["date"].apply(lambda d: pd.Timestamp(d).date() in roll_days)
        tercile_map = build_tercile_map(days)
        trades_df["tercile"] = trades_df["date"].apply(lambda d: tercile_map.get(pd.Timestamp(d).date()))

        all_trades[run_key] = trades_df

        uy = universe_years[(instrument, tf)]
        metrics = compute_metrics(trades_df, instrument, tf, days, uy)
        all_metrics[run_key] = metrics

        # salvar CSV
        csv_cols = ["date", "instrument", "tf", "mechanic", "direction",
                    "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason",
                    "stop_price", "target_price", "or_high", "or_low", "or_width",
                    "is_roll", "tercile", "year",
                    "pnl_pts", "pnl_pct", "pnl_rs_gross",
                    "pnl_liq_pts_05tick", "pnl_liq_pct_05tick", "pnl_liq_rs_05tick",
                    "pnl_liq_pts_1tick", "pnl_liq_pct_1tick", "pnl_liq_rs_1tick",
                    "pnl_liq_pts_2tick", "pnl_liq_pct_2tick", "pnl_liq_rs_2tick"]
        csv_path = OUT_DIR / f"trades_{mech}_{instrument}_{tf}.csv"
        trades_df[csv_cols].to_csv(csv_path, index=False)
        print(f"{run_key}: {len(trades_df)} trades -> {csv_path.name}")

    # ---- checar consistencia cross-asset / cross-mecanica: gate da campanha ----
    gate_check = evaluate_abandonment_gate(all_metrics)

    # ---- metrics_all.json ----
    output = {
        "generated": pd.Timestamp.now().isoformat(),
        "is_end_exclusive": str(IS_END),
        "ash_wednesdays_excluded": [str(d) for d in sorted(ASH_WEDNESDAYS)],
        "anomaly_union_excluded": [str(d) for d in sorted(ANOMALY_UNION)],
        "load_stats": load_stats_all,
        "exclusion_counts_dynamic_guards": excl_counts_all,
        "universe_years": {f"{k[0]}_{k[1]}": v for k, v in universe_years.items()},
        "metrics": all_metrics,
        "gate_check": gate_check,
        "implementation_notes": impl_notes,
    }
    with open(OUT_DIR / "metrics_all.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=lambda o: str(o), allow_nan=True)

    # ---- SUMMARY.txt ----
    summary_text = build_summary_text(all_metrics, load_stats_all, excl_counts_all, gate_check)
    with open(OUT_DIR / "SUMMARY.txt", "w", encoding="utf-8") as f:
        f.write(summary_text)
    print("\n" + summary_text)


def evaluate_abandonment_gate(all_metrics):
    """Criterio de abandono (secao 7 do DECISION_PHASE_B.md), cenario 1 tick."""
    primary_results = []
    for (mech, instrument, tf) in MAIN_RUNS:
        key = f"{mech}_{instrument}_{tf}"
        m = all_metrics.get(key, {})
        if m.get("_empty"):
            continue
        pf = m.get("pf_liq_1tick", np.nan)
        n = m.get("n_trades", 0)
        exp1 = m.get("expectancy_pts_liq_1tick", np.nan)
        terc = m.get("breakdown_tercile", {})
        terc_positive = sum(1 for t in terc.values() if (t.get("expectancy_pts_liq_1tick") or 0) > 0)
        passes_gate1 = (isinstance(pf, (int, float)) and pf > 1.10 and n >= 200 and
                         isinstance(exp1, (int, float)) and exp1 > 0 and terc_positive >= 2)
        primary_results.append({
            "config": key, "pf_liq_1tick": pf, "n_trades": n,
            "expectancy_pts_liq_1tick": exp1, "terc_positivos": terc_positive,
            "passes_gate1": bool(passes_gate1),
        })

    any_pass = any(r["passes_gate1"] for r in primary_results)
    return {"primary_results": primary_results, "any_config_passes_gate1_pf1.10_n200_exp_pos_2of3_tercis": any_pass}


def fmt(x, nd=3):
    if x is None:
        return "NA"
    if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
        return "inf" if np.isinf(x) else "NA"
    if isinstance(x, (int, float)):
        return f"{x:.{nd}f}"
    return str(x)


def build_summary_text(all_metrics, load_stats_all, excl_counts_all, gate_check):
    lines = []
    lines.append("=" * 110)
    lines.append("b3_or_continuation_v0 - Fase C: backtests das 4 mecanicas (C1/C2/C3/C4)")
    lines.append("IS: inicio dos dados ate 2024-12-31. OOS (2025+) SAGRADO, nao tocado.")
    lines.append("=" * 110)

    lines.append("\n--- LOAD STATS ---")
    for k, v in load_stats_all.items():
        lines.append(f"{k}: {v}")
    lines.append("\n--- EXCLUSOES DINAMICAS (guards) ---")
    for k, v in excl_counts_all.items():
        lines.append(f"{k}: {v}")

    lines.append("\n" + "=" * 110)
    lines.append("TABELA COMPARATIVA (cenario referencia: custo 1 tick/execucao)")
    lines.append("=" * 110)
    header = (f"{'run':22s} {'n':>5s} {'trd/ano':>8s} {'win%':>6s} {'PF_br':>7s} "
              f"{'PF_0.5t':>8s} {'PF_1t':>7s} {'PF_2t':>7s} {'exp_pts':>8s} "
              f"{'exp_R$':>8s} {'maxDD%':>7s} {'sharpe':>7s} {'PF_semTop2':>10s}")
    lines.append(header)
    lines.append("-" * len(header))

    for (mech, instrument, tf) in ALL_RUNS:
        key = f"{mech}_{instrument}_{tf}"
        m = all_metrics.get(key)
        if m is None:
            continue
        if m.get("_empty"):
            lines.append(f"{key:22s} {'0':>5s}  (zero trades)")
            continue
        row = (f"{key:22s} {m['n_trades']:5d} {fmt(m['trades_por_ano'],1):>8s} "
               f"{fmt(m.get('win_rate_net_1tick',np.nan)*100,1):>6s} "
               f"{fmt(m.get('pf_bruto'),2):>7s} {fmt(m.get('pf_liq_05tick'),2):>8s} "
               f"{fmt(m.get('pf_liq_1tick'),2):>7s} {fmt(m.get('pf_liq_2tick'),2):>7s} "
               f"{fmt(m.get('expectancy_pts_liq_1tick'),2):>8s} "
               f"{fmt(m.get('expectancy_rs_liq_1tick'),2):>8s} "
               f"{fmt(m.get('max_drawdown_1tick',np.nan)*100,1):>7s} "
               f"{fmt(m.get('sharpe_annualized_1tick'),2):>7s} "
               f"{fmt(m.get('pf_liq_1tick_sem_top2'),2):>10s}")
        lines.append(row)

    lines.append("\n" + "=" * 110)
    lines.append("BREAKDOWN POR TERCIL DO IS (PF liq 1 tick, cronologico por sessao do proprio arquivo)")
    lines.append("=" * 110)
    for (mech, instrument, tf) in ALL_RUNS:
        key = f"{mech}_{instrument}_{tf}"
        m = all_metrics.get(key)
        if m is None or m.get("_empty"):
            continue
        terc = m.get("breakdown_tercile", {})
        parts = [f"T{t}: n={d['n']} PF={fmt(d['pf_liq_1tick'],2)} exp={fmt(d['expectancy_pts_liq_1tick'],2)}pts"
                 for t, d in sorted(terc.items())]
        lines.append(f"{key:22s} " + " | ".join(parts))

    lines.append("\n" + "=" * 110)
    lines.append("BREAKDOWN POR DIRECAO (PF liq 1 tick)")
    lines.append("=" * 110)
    for (mech, instrument, tf) in ALL_RUNS:
        key = f"{mech}_{instrument}_{tf}"
        m = all_metrics.get(key)
        if m is None or m.get("_empty"):
            continue
        dirb = m.get("breakdown_direction", {})
        parts = [f"{d}: n={v['n']} PF={fmt(v['pf_liq_1tick'],2)} win%={fmt(v['win_rate']*100,1)}"
                 for d, v in dirb.items()]
        lines.append(f"{key:22s} " + " | ".join(parts))

    lines.append("\n" + "=" * 110)
    lines.append("BREAKDOWN COM/SEM DIA DE ROLAGEM (PF liq 1 tick)")
    lines.append("=" * 110)
    for (mech, instrument, tf) in ALL_RUNS:
        key = f"{mech}_{instrument}_{tf}"
        m = all_metrics.get(key)
        if m is None or m.get("_empty"):
            continue
        rb = m.get("breakdown_roll_day", {})
        parts = [f"is_roll={flag}: n={v['n']} PF={fmt(v['pf_liq_1tick'],2)}" for flag, v in rb.items()]
        lines.append(f"{key:22s} " + " | ".join(parts))

    lines.append("\n" + "=" * 110)
    lines.append("BREAKDOWN POR EXIT_REASON")
    lines.append("=" * 110)
    for (mech, instrument, tf) in ALL_RUNS:
        key = f"{mech}_{instrument}_{tf}"
        m = all_metrics.get(key)
        if m is None or m.get("_empty"):
            continue
        eb = m.get("breakdown_exit_reason", {})
        parts = [f"{reason}: n={v['n']} ({v['pct']*100:.1f}%) exp={fmt(v['expectancy_pts_liq_1tick'],2)}pts"
                 for reason, v in eb.items()]
        lines.append(f"{key:22s} " + " | ".join(parts))

    lines.append("\n" + "=" * 110)
    lines.append("CRITERIO DE ABANDONO (secao 7 DECISION_PHASE_B.md) -- cenario 1 tick")
    lines.append("=" * 110)
    for r in gate_check["primary_results"]:
        lines.append(f"  {r['config']:22s} PF_liq_1t={fmt(r['pf_liq_1tick'],2):>6s} n={r['n_trades']:4d} "
                      f"exp={fmt(r['expectancy_pts_liq_1tick'],2):>7s}pts tercis_positivos={r['terc_positivos']}/3 "
                      f"passa_gate1={r['passes_gate1']}")
    lines.append(f"\n  ALGUMA config primaria passa gate 1 (PF>1.10, n>=200, exp>0, >=2/3 tercis positivos)? "
                  f"{gate_check['any_config_passes_gate1_pf1.10_n200_exp_pos_2of3_tercis']}")

    if impl_notes:
        lines.append("\n" + "=" * 110)
        lines.append("NOTAS DE IMPLEMENTACAO")
        lines.append("=" * 110)
        for n in impl_notes:
            lines.append(f"  - {n}")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
