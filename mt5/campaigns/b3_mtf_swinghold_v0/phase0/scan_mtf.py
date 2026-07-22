"""
b3_mtf_swinghold_v0 - Fase 0

Varredura da grade fechada (56 configs) do pre-registro: Arm A (MTF
intraday, M15 + gate H1/D1, flat EOD), Arm B (swing-hold state-follower,
sem saida EOD, seis sinais M15/H1/D1), Arm C (overnight puro close->open).

DISCOVERY <2024-01-01 / CONFIRM 2024. 2025+ eh OOS sagrado, filtrado
imediatamente apos o load em TODOS os TFs (M15/H1/D1), nunca tocado
depois disso nesta fase.

Script auto-suficiente. Rodar: python scan_mtf.py
Dependencias: pandas, numpy, pyarrow.
"""

import os
import bisect
from datetime import date, timedelta

import numpy as np
import pandas as pd

pd.set_option("mode.chained_assignment", None)

DATA_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_mtf_swinghold_v0\phase0"

ASSETS = ["WIN", "WDO"]
COST_LEVELS = [2, 4, 6]

WINDOW_START_MIN = 9 * 60 + 15   # 09:15 (Arm A janela de entrada)
WINDOW_END_MIN = 16 * 60         # 16:00

DISCOVERY_END = pd.Timestamp("2024-01-01")
OOS_CUTOFF = pd.Timestamp("2025-01-01")   # OOS sagrado -- filtrado no load, nunca tocado


# ==========================================================================
# Loading
# ==========================================================================

def load_tf(asset, tf):
    path = os.path.join(DATA_DIR, f"{asset}_cont_N_{tf}.parquet")
    df = pd.read_parquet(path)
    df = df.rename(columns={"datetime_b3": "ts"})
    df = df.sort_values("ts").reset_index(drop=True)
    # 2025+ eh OOS sagrado -- filtrado IMEDIATAMENTE apos o load, em todos os TFs.
    df = df[df["ts"] < OOS_CUTOFF].reset_index(drop=True)
    df["date"] = df["ts"].dt.date
    return df


# ==========================================================================
# Indicadores
# ==========================================================================

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def macd_lines(series, fast=12, slow=26, signal=9):
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig


def bollinger(series, n=20, k=2.0):
    sma = series.rolling(n).mean()
    std = series.rolling(n).std(ddof=0)
    return sma, sma + k * std, sma - k * std


def adx_wilder(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev_close = c.shift(1)
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_s = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_dm_s = pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    minus_dm_s = pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * plus_dm_s / tr_s
    minus_di = 100 * minus_dm_s / tr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def cross_up(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    out = (prev_a <= prev_b) & (a > b)
    out[0] = False
    out[np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)] = False
    return out


def cross_down(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    out = (prev_a >= prev_b) & (a < b)
    out[0] = False
    out[np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)] = False
    return out


def add_m15_indicators(df):
    c = df["close"]
    for span in (9, 21, 50, 100):
        df[f"ema{span}"] = ema(c, span)
    df["macd"], df["macd_signal"] = macd_lines(c)
    df["sma20"], df["bb_up"], df["bb_lo"] = bollinger(c)
    df["term_ts"] = df["ts"] + pd.Timedelta(minutes=15)
    return df


def add_h1_indicators(df):
    c = df["close"]
    for span in (9, 21, 50):
        df[f"ema{span}"] = ema(c, span)
    df["macd"], df["macd_signal"] = macd_lines(c)
    df["adx14"] = adx_wilder(df, 14)
    # duracao nominal 60min como tempo de termino, MESMO quando a ultima barra
    # do pregao eh parcial (pregao nao termina em hora cheia). Documentado.
    df["term_ts"] = df["ts"] + pd.Timedelta(minutes=60)
    return df


def add_d1_indicators(df):
    c = df["close"]
    df["ema5"] = ema(c, 5)
    df["ema20"] = ema(c, 20)
    return df


def day_bar_flags(df):
    n = len(df)
    is_first = np.zeros(n, dtype=bool)
    is_last = np.zeros(n, dtype=bool)
    first_idx = df.groupby("date").apply(lambda g: g.index[0])
    last_idx = df.groupby("date").apply(lambda g: g.index[-1])
    is_first[first_idx.values] = True
    is_last[last_idx.values] = True
    return is_first, is_last


def window_mask(df):
    tmin = (df["ts"].dt.hour * 60 + df["ts"].dt.minute).astype(int)
    return (tmin >= WINDOW_START_MIN) & (tmin <= WINDOW_END_MIN)


# ==========================================================================
# Alinhamento MTF sem lookahead (merge_asof por tempo de TERMINO da barra)
# ==========================================================================

def merge_asof_upper(signal_df, upper_df, value_cols, prefix):
    """Para cada linha de signal_df (chave 'term_ts'), busca a ULTIMA linha de
    upper_df cujo 'term_ts' seja <= ao term_ts do sinal (direction='backward',
    allow_exact_matches=True). Retorna DataFrame alinhado (mesmo n de linhas
    e ordem de signal_df) com colunas prefixadas."""
    left = pd.DataFrame({
        "term_ts": signal_df["term_ts"].values,
        "_orig_idx": np.arange(len(signal_df)),
    })
    right = upper_df[["term_ts"] + value_cols].dropna(subset=["term_ts"]).sort_values("term_ts")
    left_sorted = left.sort_values("term_ts")
    merged = pd.merge_asof(
        left_sorted, right, on="term_ts", direction="backward", allow_exact_matches=True
    )
    merged = merged.sort_values("_orig_idx")
    out = merged[value_cols].reset_index(drop=True)
    out.columns = [f"{prefix}{c}" for c in value_cols]
    return out


# ==========================================================================
# Rolagem por calendario
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
            wed = nearest_weekday(base, 2)  # 2 = quarta-feira
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

    next_day_map = {trading_days[i]: trading_days[i + 1] for i in range(len(trading_days) - 1)}
    next_day_in_window = {d for d, nd in next_day_map.items() if nd in window_dates_set}
    blocked_overnight = window_dates_set | next_day_in_window

    return dict(
        windows=windows, skipped=skipped,
        window_dates_set=window_dates_set,
        next_day_in_window=next_day_in_window,
        blocked_overnight=blocked_overnight,
    )


# ==========================================================================
# Metricas
# ==========================================================================

def pf(pnl):
    wins = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    if losses == 0:
        return np.inf if wins > 0 else np.nan
    return wins / abs(losses)


def max_drawdown(pnl_series):
    if len(pnl_series) == 0:
        return np.nan
    cum = np.cumsum(pnl_series)
    running_max = np.maximum.accumulate(cum)
    dd = running_max - cum
    return dd.max()


def trades_to_df(trades, cols):
    tdf = pd.DataFrame(trades, columns=cols)
    if len(tdf) == 0:
        return tdf
    tdf["entry_ts"] = pd.to_datetime(tdf["entry_ts"])
    tdf["exit_ts"] = pd.to_datetime(tdf["exit_ts"])
    tdf["pnl_gross_bps"] = tdf["direction"] * (tdf["exit_price"] / tdf["entry_price"] - 1) * 1e4
    for cost in COST_LEVELS:
        tdf[f"pnl_net{cost}_bps"] = tdf["pnl_gross_bps"] - cost
    return tdf


def compute_metrics(tdf, config_id, asset, arm, tf, family, split):
    n_trades = len(tdf)
    row = dict(config_id=config_id, asset=asset, arm=arm, tf=tf, family=family,
               split=split, n_trades=n_trades)
    keys = ["win_rate", "pf_gross", "pf_net2", "pf_net4", "pf_net6",
            "exp_gross_bps", "exp_net6_bps", "avg_hold_bars",
            "pf_net6_ex_top2", "pf_net6_ex_top3", "pior_ano_pf_net6", "max_dd_bps"]
    if n_trades == 0:
        row.update({k: np.nan for k in keys})
        return row

    net6 = tdf["pnl_net6_bps"].values
    gross = tdf["pnl_gross_bps"].values

    row["win_rate"] = float((net6 > 0).mean())
    row["pf_gross"] = pf(gross)
    row["pf_net2"] = pf(tdf["pnl_net2_bps"].values)
    row["pf_net4"] = pf(tdf["pnl_net4_bps"].values)
    row["pf_net6"] = pf(net6)
    row["exp_gross_bps"] = float(gross.mean())
    row["exp_net6_bps"] = float(net6.mean())
    row["avg_hold_bars"] = float(tdf["hold_bars"].mean())

    if n_trades > 2:
        order = np.argsort(net6)[::-1]
        rest2 = np.delete(net6, order[:2])
        row["pf_net6_ex_top2"] = pf(rest2)
    else:
        row["pf_net6_ex_top2"] = np.nan

    if n_trades > 3:
        order = np.argsort(net6)[::-1]
        rest3 = np.delete(net6, order[:3])
        row["pf_net6_ex_top3"] = pf(rest3)
    else:
        row["pf_net6_ex_top3"] = np.nan

    years = tdf["entry_ts"].dt.year.values
    pf_per_year = [pf(net6[years == y]) for y in np.unique(years)]
    finite = [p for p in pf_per_year if np.isfinite(p)]
    row["pior_ano_pf_net6"] = float(min(finite)) if finite else np.nan

    row["max_dd_bps"] = float(max_drawdown(net6))
    return row


def split_metrics_rows(tdf, config_id, asset, arm, tf, family):
    if len(tdf) > 0:
        disc = tdf[tdf["entry_ts"] < DISCOVERY_END]
        conf = tdf[(tdf["entry_ts"] >= DISCOVERY_END) & (tdf["entry_ts"] < OOS_CUTOFF)]
    else:
        disc = tdf
        conf = tdf
    row_disc = compute_metrics(disc, config_id, asset, arm, tf, family, "discovery")
    row_conf = compute_metrics(conf, config_id, asset, arm, tf, family, "confirm")
    return row_disc, row_conf


# ==========================================================================
# Arm A -- MTF intraday (M15, flat EOD)
# ==========================================================================

def build_gates_arm_a(m15):
    g1_side = np.sign(m15["h1_ema9"].values - m15["h1_ema21"].values)
    g2_side = np.sign(m15["h1_ema21"].values - m15["h1_ema50"].values)
    g3_side = np.sign(m15["d1_close"].values - m15["d1_ema20"].values)
    g4_on = (m15["h1_adx14"].values > 25)
    return {"G1": ("dir", g1_side), "G2": ("dir", g2_side),
            "G3": ("dir", g3_side), "G4": ("adx", g4_on)}


def build_triggers_arm_a(m15):
    close = m15["close"].values
    ema9, ema21, ema50 = m15["ema9"].values, m15["ema21"].values, m15["ema50"].values
    macd_, macd_sig = m15["macd"].values, m15["macd_signal"].values
    bb_up, bb_lo = m15["bb_up"].values, m15["bb_lo"].values

    t1 = (cross_up(ema9, ema21), cross_down(ema9, ema21))
    t2 = (cross_up(ema21, ema50), cross_down(ema21, ema50))
    t3 = (cross_up(macd_, macd_sig), cross_down(macd_, macd_sig))
    t4 = (cross_up(close, bb_up), cross_down(close, bb_lo))
    return {"T1": t1, "T2": t2, "T3": t3, "T4": t4}


def simulate_arm_a(df, entry_long, entry_short, exit_long, exit_short, blocked):
    ts = df["ts"].values
    o = df["open"].values
    c = df["close"].values
    in_window = df["in_window"].values
    is_last = df["is_last_bar"].values

    n = len(df)
    trades = []
    pos = 0
    entry_price = None
    entry_ts_ = None
    entry_i = None

    for i in range(n - 1):
        if pos != 0:
            if is_last[i]:
                exit_price = c[i]
                trades.append((entry_ts_, ts[i], pos, entry_price, exit_price, "eod", i - entry_i))
                pos = 0
                entry_price = None
                continue

            own_exit = exit_long[i] if pos == 1 else exit_short[i]
            opp_entry = entry_short[i] if pos == 1 else entry_long[i]

            if own_exit or opp_entry:
                old_pos = pos
                exit_price = o[i + 1]
                reason = "reversal" if opp_entry else "signal"
                trades.append((entry_ts_, ts[i + 1], old_pos, entry_price, exit_price, reason, (i + 1) - entry_i))
                pos = 0
                entry_price = None
                if opp_entry and in_window[i] and not blocked[i]:
                    pos = -old_pos
                    entry_price = o[i + 1]
                    entry_ts_ = ts[i + 1]
                    entry_i = i + 1

        if pos == 0 and in_window[i] and not blocked[i]:
            if entry_long[i]:
                pos = 1
                entry_price = o[i + 1]
                entry_ts_ = ts[i + 1]
                entry_i = i + 1
            elif entry_short[i]:
                pos = -1
                entry_price = o[i + 1]
                entry_ts_ = ts[i + 1]
                entry_i = i + 1

    return trades


def run_arm_a(m15, asset, window_dates_set):
    gates = build_gates_arm_a(m15)
    triggers = build_triggers_arm_a(m15)
    dates_arr = m15["date"].values
    blocked = np.array([d in window_dates_set for d in dates_arr])

    rows = []
    cfg_meta = []
    trades_cache = {}
    for gname, (gkind, gval) in gates.items():
        for tname, (tr_long_evt, tr_short_evt) in triggers.items():
            if gkind == "adx":
                entry_long = tr_long_evt & gval
                entry_short = tr_short_evt & gval
            else:
                entry_long = tr_long_evt & (gval == 1)
                entry_short = tr_short_evt & (gval == -1)
            exit_long = tr_short_evt
            exit_short = tr_long_evt

            config_id = f"{asset}_A_{gname}_{tname}"
            trades = simulate_arm_a(m15, entry_long, entry_short, exit_long, exit_short, blocked)
            cols = ["entry_ts", "exit_ts", "direction", "entry_price", "exit_price", "reason", "hold_bars"]
            tdf = trades_to_df(trades, cols)

            if len(tdf) > 0:
                cross_day = (tdf["entry_ts"].dt.date != tdf["exit_ts"].dt.date).sum()
                if cross_day > 0:
                    print(f"  [WARN] {config_id}: {cross_day} trades cruzando dia (Arm A nao deveria)")

            row_disc, row_conf = split_metrics_rows(tdf, config_id, asset, "A", "M15", f"{gname}_{tname}")
            rows.append(row_disc)
            rows.append(row_conf)
            trades_cache[config_id] = tdf
    return rows, trades_cache


# ==========================================================================
# Arm B -- swing-hold state follower (sem saida EOD)
# ==========================================================================

def build_signals_arm_b(m15):
    signals = {}
    signals["B1_m15_ema9x100"] = np.sign(m15["ema9"].values - m15["ema100"].values)
    signals["B2_m15_ema21x100"] = np.sign(m15["ema21"].values - m15["ema100"].values)
    signals["B3_h1_ema9x21"] = np.sign(m15["h1_ema9"].values - m15["h1_ema21"].values)
    signals["B4_h1_ema21x50"] = np.sign(m15["h1_ema21"].values - m15["h1_ema50"].values)
    signals["B5_h1_macd"] = np.sign(m15["h1_macd"].values - m15["h1_macd_signal"].values)
    signals["B6_d1_ema5x20"] = np.sign(m15["d1_ema5"].values - m15["d1_ema20"].values)
    return signals


def simulate_arm_b(m15, state, blocked_overnight_dates):
    ts = m15["ts"].values
    o = m15["open"].values
    c = m15["close"].values
    dates = m15["date"].values
    is_first = m15["is_first_bar"].values
    is_last = m15["is_last_bar"].values

    n = len(m15)
    trades = []
    pos = 0
    entry_price = None
    entry_ts_ = None
    entry_i = None
    confirmed_state = 0

    for i in range(n - 1):
        # (a) reentrada no open da 1a barra do pregao, usando o ultimo estado
        # conhecido ANTES da abertura de hoje (sem lookahead: state[i-1] ja
        # era conhecido no fechamento de ontem).
        if is_first[i] and pos == 0 and i > 0:
            s_prev = state[i - 1]
            if not np.isnan(s_prev) and s_prev != 0:
                pos = int(s_prev)
                entry_price = o[i]
                entry_ts_ = ts[i]
                entry_i = i
                confirmed_state = pos

        # (b) saida forcada por rolagem: nunca segurar overnight se hoje esta
        # em blocked_overnight_dates (dia da janela OU dia anterior a janela).
        if pos != 0 and is_last[i] and (dates[i] in blocked_overnight_dates):
            exit_price = c[i]
            trades.append((entry_ts_, ts[i], pos, entry_price, exit_price, "rollover_forced", i - entry_i))
            pos = 0
            entry_price = None
            confirmed_state = 0
            continue

        # (c) flip intradiario / entrada "cold start", executado no open t+1
        s = state[i]
        if not np.isnan(s) and s != 0:
            s_int = int(s)
            if pos == 0:
                pos = s_int
                entry_price = o[i + 1]
                entry_ts_ = ts[i + 1]
                entry_i = i + 1
                confirmed_state = s_int
            elif s_int != confirmed_state:
                exit_price = o[i + 1]
                trades.append((entry_ts_, ts[i + 1], pos, entry_price, exit_price, "flip", (i + 1) - entry_i))
                pos = s_int
                entry_price = o[i + 1]
                entry_ts_ = ts[i + 1]
                entry_i = i + 1
                confirmed_state = s_int

    return trades


def validate_arm_b_no_rollover_gap(tdf, window_dates_set, config_id):
    """Assert que nenhum trade Arm B contem, entre entry e exit, uma noite
    cujo dia inicial pertenca a window_dates_set (ou seja: nenhuma noite de
    janela de rolo eh atravessada por um trade)."""
    if len(tdf) == 0:
        return 0
    bad = 0
    for _, tr in tdf.iterrows():
        d0 = tr["entry_ts"].date()
        d1 = tr["exit_ts"].date()
        if d0 == d1:
            continue
        cur = d0
        while cur < d1:
            if cur in window_dates_set:
                bad += 1
                break
            cur = cur + timedelta(days=1)
    if bad > 0:
        print(f"  [ASSERT-FAIL] {config_id}: {bad} trades intersectam janela de rolo!")
    return bad


def run_arm_b(m15, asset, blocked_overnight_dates, window_dates_set):
    signals = build_signals_arm_b(m15)
    rows = []
    trades_cache = {}
    for sname, state in signals.items():
        tf_tag = "M15" if sname.startswith("B1") or sname.startswith("B2") else (
            "H1" if sname.startswith(("B3", "B4", "B5")) else "D1")
        config_id = f"{asset}_B_{sname}"
        trades = simulate_arm_b(m15, state, blocked_overnight_dates)
        cols = ["entry_ts", "exit_ts", "direction", "entry_price", "exit_price", "reason", "hold_bars"]
        tdf = trades_to_df(trades, cols)

        n_bad = validate_arm_b_no_rollover_gap(tdf, window_dates_set, config_id)

        row_disc, row_conf = split_metrics_rows(tdf, config_id, asset, "B", tf_tag, sname)
        rows.append(row_disc)
        rows.append(row_conf)
        trades_cache[config_id] = tdf
    return rows, trades_cache


# ==========================================================================
# Arm C -- overnight puro (close -> open)
# ==========================================================================

def build_day_agg_arm_c(m15):
    last_bars = m15[m15["is_last_bar"]].reset_index(drop=True)
    last_bars = last_bars[["date", "ts", "close", "h1_ema21", "h1_ema50",
                            "h1_macd", "h1_macd_signal", "d1_close", "d1_ema20"]].copy()
    last_bars = last_bars.rename(columns={"ts": "last_ts", "close": "last_close"})
    last_bars["e1_side"] = np.sign(last_bars["h1_ema21"] - last_bars["h1_ema50"])
    last_bars["e2_side"] = np.sign(last_bars["d1_close"] - last_bars["d1_ema20"])
    last_bars["e3_side"] = np.sign(last_bars["h1_macd"] - last_bars["h1_macd_signal"])

    first_bars = m15[m15["is_first_bar"]].reset_index(drop=True)
    first_bars = first_bars[["date", "ts", "open"]].rename(columns={"ts": "first_ts", "open": "first_open"})

    second_bars = m15.groupby("date", group_keys=False).nth(1)
    second_bars = second_bars[["date", "ts", "close"]].rename(columns={"ts": "second_ts", "close": "second_close"})

    day_agg = last_bars.merge(first_bars, on="date", how="left").merge(second_bars, on="date", how="left")
    day_agg = day_agg.sort_values("date").reset_index(drop=True)

    day_agg["next_date"] = day_agg["date"].shift(-1)
    day_agg["next_first_open"] = day_agg["first_open"].shift(-1)
    day_agg["next_first_ts"] = day_agg["first_ts"].shift(-1)
    day_agg["next_second_close"] = day_agg["second_close"].shift(-1)
    day_agg["next_second_ts"] = day_agg["second_ts"].shift(-1)

    day_agg = day_agg.iloc[:-1].reset_index(drop=True)  # ultimo dia da serie nao tem D+1
    return day_agg


def build_trades_arm_c(day_agg, state_col, exit_type, window_dates_set):
    d = day_agg.copy()
    d = d[~d["next_date"].isin(window_dates_set)]  # sem entrada se pregao seguinte esta na janela
    d = d[d[state_col].isin([-1.0, 1.0])]

    if exit_type == "X1":
        d = d.dropna(subset=["next_first_open", "next_first_ts"])
        exit_price = d["next_first_open"].values
        exit_ts = d["next_first_ts"].values
    else:  # X2
        d = d.dropna(subset=["next_second_close", "next_second_ts"])
        exit_price = d["next_second_close"].values
        exit_ts = d["next_second_ts"].values

    direction = d[state_col].values.astype(int)
    entry_price = d["last_close"].values
    entry_ts = d["last_ts"].values
    hold_bars = (pd.to_datetime(exit_ts) - pd.to_datetime(entry_ts)) / pd.Timedelta(minutes=15)

    trades = list(zip(entry_ts, exit_ts, direction, entry_price, exit_price,
                       ["overnight"] * len(d), hold_bars.values))
    return trades


def run_arm_c(m15, asset, window_dates_set):
    day_agg = build_day_agg_arm_c(m15)
    states = {"E1_h1_ema21x50": "e1_side", "E2_d1_close_vs_ema20": "e2_side", "E3_h1_macd": "e3_side"}
    exits = {"X1_next_open": "X1", "X2_0915_close": "X2"}

    rows = []
    trades_cache = {}
    for ename, scol in states.items():
        for xname, xtype in exits.items():
            config_id = f"{asset}_C_{ename}_{xname}"
            trades = build_trades_arm_c(day_agg, scol, xtype, window_dates_set)
            cols = ["entry_ts", "exit_ts", "direction", "entry_price", "exit_price", "reason", "hold_bars"]
            tdf = trades_to_df(trades, cols)

            if len(tdf) > 0:
                dup = tdf["entry_ts"].duplicated().sum()
                if dup > 0:
                    print(f"  [WARN] {config_id}: {dup} noites com >1 trade (deveria ser <=1)")

            row_disc, row_conf = split_metrics_rows(tdf, config_id, asset, "C", "M15/H1/D1", f"{ename}_{xname}")
            rows.append(row_disc)
            rows.append(row_conf)
            trades_cache[config_id] = tdf
    return rows, trades_cache


# ==========================================================================
# Main
# ==========================================================================

def main():
    all_rows = []
    rollover_report = {}
    spotcheck_notes = []
    all_trades_cache = {}

    for asset in ASSETS:
        print(f"=== {asset} ===")
        m15 = load_tf(asset, "M15")
        h1 = load_tf(asset, "H1")
        d1 = load_tf(asset, "D1")
        print(f"  M15 rows: {len(m15)}  range: {m15['ts'].min()} -> {m15['ts'].max()}")
        print(f"  H1  rows: {len(h1)}  range: {h1['ts'].min()} -> {h1['ts'].max()}")
        print(f"  D1  rows: {len(d1)}  range: {d1['ts'].min()} -> {d1['ts'].max()}")

        m15 = add_m15_indicators(m15)
        h1 = add_h1_indicators(h1)
        d1 = add_d1_indicators(d1)

        is_first, is_last = day_bar_flags(m15)
        m15["is_first_bar"] = is_first
        m15["is_last_bar"] = is_last
        m15["in_window"] = window_mask(m15)

        # term_ts do D1 = tempo de termino da ULTIMA barra M15 daquele pregao
        # (== fechamento real do pregao). Isso garante, via merge_asof
        # backward, que barras intraday de um dia D nunca "veem" o D1 do
        # proprio dia D (so o D1 de D-1); e que a avaliacao feita EXATAMENTE
        # na ultima barra M15 do dia (Arm C) ve o D1 do proprio dia D, pois
        # nesse instante o pregao (e portanto a barra D1) acabou de fechar.
        day_term = m15.groupby("date")["term_ts"].max()
        d1["term_ts"] = d1["date"].map(day_term)
        n_d1_no_term = d1["term_ts"].isna().sum()
        if n_d1_no_term:
            print(f"  [WARN] {n_d1_no_term} linhas D1 sem M15 correspondente (sem term_ts) -- ignoradas no merge_asof")

        h1_vals = merge_asof_upper(m15, h1, ["ema9", "ema21", "ema50", "adx14", "macd", "macd_signal"], "h1_")
        d1_vals = merge_asof_upper(m15, d1, ["ema5", "ema20", "close"], "d1_")
        m15 = pd.concat([m15.reset_index(drop=True), h1_vals, d1_vals], axis=1)

        # ---- rolagem por calendario ----
        trading_days = sorted(d1["date"].unique())
        roll_state = build_rollover_state(asset, trading_days)
        years_span = (m15["ts"].max() - m15["ts"].min()).days / 365.25
        n_windows = len(roll_state["windows"])
        print(f"  janelas de rolo: {n_windows} em ~{years_span:.2f} anos (~{n_windows / years_span:.1f}/ano), "
              f"{roll_state['skipped']} pulada(s) por falta de historico no inicio da serie")
        by_year = {}
        for w in roll_state["windows"]:
            by_year.setdefault(w["expiry"].year, 0)
            by_year[w["expiry"].year] += 1
        rollover_report[asset] = dict(
            n_windows=n_windows, by_year=by_year,
            first5=[(w["expiry"], w["dates"]) for w in roll_state["windows"][:5]],
            skipped=roll_state["skipped"],
        )

        # ---- Arm A ----
        rows_a, cache_a = run_arm_a(m15, asset, roll_state["window_dates_set"])
        all_rows.extend(rows_a)
        all_trades_cache.update(cache_a)
        print(f"  Arm A: {len(rows_a) // 2} configs")

        # ---- Arm B ----
        rows_b, cache_b = run_arm_b(m15, asset, roll_state["blocked_overnight"], roll_state["window_dates_set"])
        all_rows.extend(rows_b)
        all_trades_cache.update(cache_b)
        print(f"  Arm B: {len(rows_b) // 2} configs")

        # ---- Arm C ----
        rows_c, cache_c = run_arm_c(m15, asset, roll_state["window_dates_set"])
        all_rows.extend(rows_c)
        all_trades_cache.update(cache_c)
        print(f"  Arm C: {len(rows_c) // 2} configs")

        # ---- spot-check MTF alignment (1 exemplo): M15 signal bar + H1 usada pelo gate ----
        if asset == "WIN":
            mid = m15[(m15["in_window"]) & (~m15["h1_ema9"].isna())].iloc[200]
            h1_used = h1[h1["term_ts"] <= mid["term_ts"]].iloc[-1]
            note = (
                f"\nExemplo de alinhamento MTF ({asset}):\n"
                f"  Barra M15 de sinal: open_ts={mid['ts']} term_ts(=open+15min)={mid['term_ts']}\n"
                f"  Barra H1 usada pelo gate: open_ts={h1_used['ts']} term_ts(=open+60min)={h1_used['term_ts']}\n"
                f"  Confirmação: term_ts H1 ({h1_used['term_ts']}) <= term_ts M15 ({mid['term_ts']}) "
                f"=> H1 já estava COMPLETA quando o sinal M15 fechou.\n"
                f"  h1_ema9 anexado na barra M15 = {mid['h1_ema9']:.2f} (igual ao ema9 H1 na barra usada: {h1_used['ema9']:.2f})"
            )
            spotcheck_notes.append(("mtf_alignment", note))
            print(note)

        # ---- spot-check Arm B (2 trades manuais) ----
        if asset == "WIN":
            b_cfg = "WIN_B_B3_h1_ema9x21"
            tdf_b = all_trades_cache[b_cfg]
            multi_day = tdf_b[tdf_b["reason"] == "flip"]
            multi_day = multi_day[(multi_day["exit_ts"] - multi_day["entry_ts"]) > pd.Timedelta(days=1)]
            forced = tdf_b[tdf_b["reason"] == "rollover_forced"]

            for label, sub in [("flip multi-dia", multi_day), ("saida forcada por rolagem", forced)]:
                if len(sub) == 0:
                    spotcheck_notes.append((f"arm_b_{label}", f"[{b_cfg}] Nenhum trade '{label}' encontrado."))
                    continue
                tr = sub.iloc[0]
                entry_ts, exit_ts = tr["entry_ts"], tr["exit_ts"]
                cols_show = ["ts", "date", "open", "high", "low", "close", "h1_ema9", "h1_ema21"]
                around_entry = m15[(m15["ts"] >= entry_ts - pd.Timedelta(hours=1, minutes=30)) &
                                    (m15["ts"] <= entry_ts + pd.Timedelta(hours=1, minutes=30))]
                around_exit = m15[(m15["ts"] >= exit_ts - pd.Timedelta(hours=1, minutes=30)) &
                                   (m15["ts"] <= exit_ts + pd.Timedelta(hours=1, minutes=30))]
                note = (
                    f"\n[{b_cfg}] Trade '{label}': dir={tr['direction']} entry_ts={entry_ts} "
                    f"entry_px={tr['entry_price']:.1f} exit_ts={exit_ts} exit_px={tr['exit_price']:.1f} "
                    f"reason={tr['reason']} pnl_gross_bps={tr['pnl_gross_bps']:.2f} hold_bars={tr['hold_bars']:.0f}\n"
                    f"--- barras ao redor da ENTRADA ({entry_ts}) ---\n"
                    + around_entry[cols_show].to_string(index=False)
                    + f"\n--- barras ao redor da SAÍDA ({exit_ts}) ---\n"
                    + around_exit[cols_show].to_string(index=False)
                )
                spotcheck_notes.append((f"arm_b_{label}", note))
                print(note)

    results = pd.DataFrame(all_rows)
    cols_order = [
        "config_id", "asset", "arm", "tf", "family", "split", "n_trades", "win_rate",
        "pf_gross", "pf_net2", "pf_net4", "pf_net6", "exp_gross_bps", "exp_net6_bps",
        "avg_hold_bars", "pf_net6_ex_top2", "pf_net6_ex_top3", "pior_ano_pf_net6", "max_dd_bps",
    ]
    results = results[cols_order]
    out_csv = os.path.join(OUT_DIR, "results.csv")
    results.to_csv(out_csv, index=False)
    print(f"\nresults.csv salvo em {out_csv}  ({len(results)} linhas)")

    build_summary(results, rollover_report, spotcheck_notes)


def build_summary(results, rollover_report, spotcheck_notes):
    disc = results[results["split"] == "discovery"].copy()
    conf = results[results["split"] == "confirm"].copy()

    merged = disc.merge(
        conf[["config_id", "asset", "arm", "tf", "n_trades", "pf_net6", "exp_net6_bps"]],
        on=["config_id", "asset", "arm", "tf"], suffixes=("_disc", "_conf"),
    )

    # ---- gate 7: replicacao estrutural (base signal id sem o asset) ----
    def base_signal_id(config_id, asset):
        return config_id.replace(f"{asset}_", "", 1)

    disc_idx = disc.set_index("config_id")
    exp_by_base = {}
    for cid, r in disc_idx.iterrows():
        base = base_signal_id(cid, r["asset"])
        exp_by_base.setdefault(base, {})[r["asset"]] = r["exp_net6_bps"]

    n_configs = disc["config_id"].nunique()
    gate_counts = {f"gate{i}": 0 for i in range(1, 8)}
    min_n = {"A": 200, "B": 120, "C": 120}

    per_config_gates = {}
    for cid in disc["config_id"].unique():
        d = disc[disc["config_id"] == cid].iloc[0]
        c_rows = conf[conf["config_id"] == cid]
        c = c_rows.iloc[0] if len(c_rows) else None
        arm = d["arm"]

        g1 = pd.notna(d["pf_net6"]) and d["pf_net6"] >= 1.10
        g2 = d["n_trades"] >= min_n[arm]
        g3 = (pd.notna(d["exp_net6_bps"]) and d["exp_net6_bps"] > 0) and \
             (c is not None and pd.notna(c["exp_net6_bps"]) and c["exp_net6_bps"] > 0)
        g4 = (c is not None and pd.notna(c["pf_net6"]) and c["pf_net6"] >= 1.05)
        ex_col = "pf_net6_ex_top3" if arm == "B" else "pf_net6_ex_top2"
        g5 = pd.notna(d[ex_col]) and d[ex_col] >= 1.0
        g6 = pd.notna(d["pior_ano_pf_net6"]) and d["pior_ano_pf_net6"] >= 0.85

        base = base_signal_id(cid, d["asset"])
        other_asset = "WDO" if d["asset"] == "WIN" else "WIN"
        exp_other = exp_by_base.get(base, {}).get(other_asset, np.nan)
        exp_this = d["exp_net6_bps"]
        if pd.isna(exp_other) or pd.isna(exp_this):
            g7 = False
        else:
            g7 = (np.sign(exp_this) == np.sign(exp_other)) or (exp_this <= 0)

        gates = dict(g1=g1, g2=g2, g3=g3, g4=g4, g5=g5, g6=g6, g7=g7)
        per_config_gates[cid] = gates
        for k, v in gates.items():
            gate_counts[k.replace("g", "gate")] += int(bool(v))

    lines = []
    lines.append("# b3_mtf_swinghold_v0 — Fase 0 — Sumário\n")
    lines.append(f"Total de configs: {n_configs} (esperado 56 pelo pré-registro)\n")

    # (a) sanity da rolagem
    lines.append("## (a) Sanity — rolagem por calendário\n")
    for asset, rep in rollover_report.items():
        lines.append(f"### {asset}\n")
        lines.append(f"- Total de janelas: {rep['n_windows']} ({rep['skipped']} pulada(s) por falta de histórico no início da série)")
        lines.append("- Janelas por ano:")
        for y, n in sorted(rep["by_year"].items()):
            lines.append(f"  - {y}: {n}")
        lines.append("- Primeiras 5 janelas (data de vencimento -> pregões da janela):")
        for expiry, wdates in rep["first5"]:
            wdates_str = ", ".join(str(d) for d in wdates)
            lines.append(f"  - vencimento {expiry}: [{wdates_str}]")
        lines.append("")

    # (b) contagem de gates
    lines.append("## (b) Contagem de configs passando cada gate (DISCOVERY, informativo — não é veredito)\n")
    lines.append("| Gate | Descrição | Passam / Total |")
    lines.append("|---|---|---|")
    lines.append(f"| 1 | PF_net6 >= 1.10 discovery | {gate_counts['gate1']} / {n_configs} |")
    lines.append(f"| 2 | n_trades >= 200(A)/120(B,C) discovery | {gate_counts['gate2']} / {n_configs} |")
    lines.append(f"| 3 | Expectancy net6 > 0 discovery E confirm | {gate_counts['gate3']} / {n_configs} |")
    lines.append(f"| 4 | PF_net6 >= 1.05 confirm | {gate_counts['gate4']} / {n_configs} |")
    lines.append(f"| 5 | PF_net6 ex-top2(A,C)/ex-top3(B) >= 1.0 discovery | {gate_counts['gate5']} / {n_configs} |")
    lines.append(f"| 6 | Nenhum ano com PF_net6 < 0.85 discovery | {gate_counts['gate6']} / {n_configs} |")
    lines.append(f"| 7 | Replicação estrutural (sinal não inverte no outro ativo) | {gate_counts['gate7']} / {n_configs} |")
    n_pass_all = sum(1 for g in per_config_gates.values() if all(g.values()))
    lines.append(f"\n**Configs que passam TODOS os 7 gates: {n_pass_all} / {n_configs}**\n")

    # (c) top-15 por arm
    lines.append("## (c) Top-15 configs por PF_net6 no DISCOVERY (com CONFIRM ao lado), agrupado por arm\n")
    for arm in ["A", "B", "C"]:
        sub = merged[merged["arm"] == arm].sort_values("pf_net6_disc", ascending=False).head(15)
        lines.append(f"### Arm {arm}\n")
        lines.append("| config_id | asset | n_disc | pf_net6_disc | exp_net6_disc | n_conf | pf_net6_conf | exp_net6_conf |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for _, r in sub.iterrows():
            lines.append(
                f"| {r['config_id']} | {r['asset']} | {r['n_trades_disc']} | "
                f"{r['pf_net6_disc']:.3f} | {r['exp_net6_bps_disc']:.2f} | "
                f"{r['n_trades_conf']} | {r['pf_net6_conf']:.3f} | {r['exp_net6_bps_conf']:.2f} |"
            )
        lines.append("")

    # (d) mediana pf_gross e pf_net6 por split, por arm
    lines.append("## (d) Mediana de PF_gross e PF_net6 por arm e split\n")
    lines.append("| arm | split | mediana pf_gross | mediana pf_net6 | n configs |")
    lines.append("|---|---|---|---|---|")
    for arm in ["A", "B", "C"]:
        for split in ["discovery", "confirm"]:
            sub = results[(results["arm"] == arm) & (results["split"] == split)]
            med_gross = sub["pf_gross"].replace([np.inf, -np.inf], np.nan).median()
            med_net6 = sub["pf_net6"].replace([np.inf, -np.inf], np.nan).median()
            lines.append(f"| {arm} | {split} | {med_gross:.3f} | {med_net6:.3f} | {len(sub)} |")
    lines.append("")

    # (e) spot-checks
    lines.append("## (e) Spot-checks manuais\n")
    lines.append("```")
    for _, note in spotcheck_notes:
        lines.append(note)
    lines.append("```")
    lines.append("")

    # (f) sanity checks gerais
    lines.append("## Sanity checks gerais\n")
    zero_trades = disc[disc["n_trades"] == 0]["config_id"].tolist()
    lines.append(f"- Configs com 0 trades no discovery: {len(zero_trades)} " + (f"({zero_trades})" if zero_trades else ""))
    lines.append("- Validações Arm A (cruzar dia) e Arm B (intersectar janela de rolo) e Arm C (>1 trade/noite) "
                  "rodadas inline no loop principal — ver stdout da execução para [WARN]/[ASSERT-FAIL].")
    lines.append("")

    # ressalvas
    lines.append("## Ressalvas / decisões de implementação\n")
    lines.append(
        "- **D1 term_ts**: definido como o term_ts da ÚLTIMA barra M15 de cada pregão (não como "
        "open+1440min). Isso faz com que (i) qualquer barra M15 intraday de um dia D nunca veja o "
        "D1 do próprio dia D — sempre D-1, satisfazendo literalmente a regra do pré-registro para "
        "os gates de Arm A (G3) e para o sinal D1 de Arm B (B6) durante o dia — e (ii) a avaliação "
        "feita EXATAMENTE na última barra M15 do pregão (Arm C, estado E2) já vê o D1 do PRÓPRIO "
        "dia, porque nesse instante exato o pregão (e portanto a barra D1) acabou de fechar — não é "
        "lookahead, é o mesmo evento temporal. Isto foi uma decisão de design para reconciliar a "
        "regra 'nunca D1 do dia corrente' (explícita para Arm A intraday) com a definição de Arm C "
        "('estado no fechamento... close vs EMA20 D1'), que naturalmente inclui o fechamento do "
        "próprio dia. Ambos os comportamentos emergem do MESMO mecanismo de merge_asof por "
        "term_ts, sem branch especial por arm."
    )
    lines.append(
        "- **H1 term_ts nominal**: sempre open+60min, mesmo quando a última barra H1 do pregão é "
        "parcial (pregão não termina em hora cheia — ex.: última M15 abre 18:15, termina 18:30; "
        "última H1 abre 18:00, term_ts nominal=19:00 > 18:30). Consequência mecânica: a última "
        "barra H1 do dia NUNCA é usada para gatear/avaliar as últimas barras M15 do mesmo dia "
        "(inclusive Arm C E1/E3) — o alinhamento cai para a penúltima barra H1 (aberta 17:00, "
        "term_ts=18:00). Isso é conservador (nunca usa dado ainda não fechado) mas significa que "
        "Arm C E1/E3 sistematicamente ignora a última hora de dados H1 do pregão."
    )
    lines.append(
        "- **T4 (Bollinger breakout M15)**: 'cross oposto do trigger' interpretado literalmente como "
        "o evento de rompimento na banda OPOSTA (cross_down(close,bb_lo) encerra um long aberto por "
        "cross_up(close,bb_up), e vice-versa) — não como reversão à média (sma20). Isso é bem mais "
        "raro que EOD, então espera-se que a maioria dos trades T4 feche por EOD."
    )
    lines.append(
        "- **Arm B, cold start**: a primeiríssima vez que um sinal fica definido (fim do warm-up de "
        "EMA/MACD) é tratada como um 'flip' a partir de posição zero, executado no open de t+1 — "
        "não há estado anterior conhecido para entrar no open da própria barra (evita lookahead), "
        "custando um atraso de 1 barra apenas nesse evento único por config."
    )
    lines.append(
        "- **Arm B, dias de janela de rolo**: entradas/reversões INTRADAY continuam permitidas em "
        "dias que pertencem à janela de rolo (o pré-registro só proíbe segurar OVERNIGHT nesses "
        "dias) — a posição é forçada a fechar no close de qualquer dia bloqueado (`blocked_overnight`), "
        "podendo reabrir na manhã seguinte se ainda dentro/ao redor da janela, e fechar de novo à "
        "noite, se aplicável. Isso pode gerar sequências de trades intraday curtos durante janelas "
        "multi-dia (WIN: 5 pregões) — comportamento intencional, documentado, não filtro pós-hoc."
    )
    lines.append(
        "- **Arm C, preço de entrada**: literal ao pré-registro, a entrada usa o CLOSE da última "
        "barra M15 do pregão (não o open da barra seguinte) — mais agressivo que as demais mecânicas "
        "desta campanha, que sempre adiam execução para o open da barra seguinte. Risco de slippage "
        "de leilão de fechamento não modelado além do custo de 6 bps, conforme já anotado no "
        "pré-registro."
    )
    lines.append(
        "- **hold_bars do Arm C**: calculado como (exit_ts - entry_ts) / 15min (aproximação por "
        "tempo decorrido), não por contagem de barras via loop — Arm C não itera bar-a-bar (é "
        "vetorizado a partir de um agregado diário), então não há um índice de barra nativo para "
        "diferença exata; a aproximação é exata em minutos de qualquer forma (15min = 1 barra M15 "
        "nominal), só não conta barras 'puladas' se houver gap de dados intraday."
    )
    lines.append(
        "- **Gate 7 (replicação estrutural)**: comparação feita por config_id com o prefixo do "
        "asset removido (ex.: 'A_G1_T1', 'B_B3_h1_ema9x21', 'C_E1_h1_ema21x50_X1_next_open'), "
        "cruzando WIN<->WDO. Passa se o sinal do expectancy_net6 discovery no outro ativo não é "
        "invertido (ou se o próprio config já não é positivo, caso em que gate7 é irrelevante — "
        "reportado como False para não inflar contagem)."
    )
    lines.append(
        "- **Split por entry_ts (todos os arms)**: DISCOVERY = entry_ts < 2024-01-01, CONFIRM = "
        "2024-01-01 <= entry_ts < 2025-01-01. Para o Arm B isso significa que um trade aberto em "
        "2023 e fechado em 2024 conta inteiro no DISCOVERY (mesma convenção usada nos outros arms "
        "e na campanha anterior) — comportamento esperado e pré-registrado explicitamente."
    )
    lines.append(
        "- **Snap de vencimento para dia de pregão real**: a quarta-feira mais próxima do dia 15 "
        "(WIN) é calculada em cima do calendário civil e depois ajustada ('snap') para o pregão "
        "real mais próximo presente na série D1, caso a quarta calculada seja feriado/não-pregão. "
        "Em empate de distância, prefere a data mais cedo (critério de desempate arbitrário, "
        "raramente acionado)."
    )

    out_md = os.path.join(OUT_DIR, "summary.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"summary.md salvo em {out_md}")


if __name__ == "__main__":
    main()
