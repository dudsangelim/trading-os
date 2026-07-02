"""Swing-trade strategy library.

Each builder takes an OHLCV df + params and returns a signal DataFrame with
boolean columns long_entry / long_exit / short_entry / short_exit (evaluated at
bar close) plus a float `atr` column for stop sizing. Entries are expressed as
desired-state booleans; the backtest engine handles execution timing, exits and
stops. `STRATS` registers each with a parameter grid and execution options.
"""
import itertools
import numpy as np
import pandas as pd
from trading.swing_wf_paper import indicators as ind


def _base(df):
    a = ind.atr(df["high"], df["low"], df["close"], 14)
    return pd.DataFrame(index=df.index, data={"atr": a})


# ---------------------------------------------------------------- trend
def ema_cross(df, fast=20, slow=50, adx_min=0):
    s = _base(df)
    ef, es = ind.ema(df["close"], fast), ind.ema(df["close"], slow)
    adx_, _, _ = ind.adx(df["high"], df["low"], df["close"], 14)
    trend_ok = adx_ >= adx_min
    bull = (ef > es) & trend_ok
    s["long_entry"] = bull
    s["long_exit"] = ef < es
    s["short_entry"] = (ef < es) & trend_ok
    s["short_exit"] = ef > es
    return s


def supertrend_follow(df, n=10, mult=3.0, adx_min=0):
    s = _base(df)
    d, _ = ind.supertrend(df["high"], df["low"], df["close"], n, mult)
    adx_, _, _ = ind.adx(df["high"], df["low"], df["close"], 14)
    ok = adx_ >= adx_min
    s["long_entry"] = (d > 0) & ok
    s["long_exit"] = d < 0
    s["short_entry"] = (d < 0) & ok
    s["short_exit"] = d > 0
    return s


def macd_rsi(df, fast=12, slow=26, signal=9, rsi_n=14):
    s = _base(df)
    _, _, hist = ind.macd(df["close"], fast, slow, signal)
    r = ind.rsi(df["close"], rsi_n)
    s["long_entry"] = (hist > 0) & (r > 50)
    s["long_exit"] = hist < 0
    s["short_entry"] = (hist < 0) & (r < 50)
    s["short_exit"] = hist > 0
    return s


# ---------------------------------------------------------------- breakout
def donchian_breakout(df, entry_n=20, exit_n=10):
    s = _base(df)
    up, _ = ind.donchian(df["high"], df["low"], entry_n)
    _, lo_exit = ind.donchian(df["high"], df["low"], exit_n)
    up_exit, _ = ind.donchian(df["high"], df["low"], exit_n)
    _, lo = ind.donchian(df["high"], df["low"], entry_n)
    c = df["close"]
    s["long_entry"] = c > up
    s["long_exit"] = c < lo_exit
    s["short_entry"] = c < lo
    s["short_exit"] = c > up_exit
    return s


def keltner_breakout(df, n=20, mult=2.0):
    s = _base(df)
    mid, up, lo = ind.keltner(df["high"], df["low"], df["close"], n, mult)
    c = df["close"]
    s["long_entry"] = c > up
    s["long_exit"] = c < mid
    s["short_entry"] = c < lo
    s["short_exit"] = c > mid
    return s


# ---------------------------------------------------------------- momentum
def roc_momentum(df, roc_n=20, ema_n=100):
    s = _base(df)
    r = ind.roc(df["close"], roc_n)
    e = ind.ema(df["close"], ema_n)
    c = df["close"]
    s["long_entry"] = (r > 0) & (c > e)
    s["long_exit"] = (r < 0) | (c < e)
    s["short_entry"] = (r < 0) & (c < e)
    s["short_exit"] = (r > 0) | (c > e)
    return s


# ---------------------------------------------------------------- mean reversion
def bb_meanrev(df, n=20, k=2.0, adx_max=25):
    """Range-regime mean reversion: fade band extremes only when ADX is low."""
    s = _base(df)
    mid, up, lo = ind.bollinger(df["close"], n, k)
    adx_, _, _ = ind.adx(df["high"], df["low"], df["close"], 14)
    rng = adx_ <= adx_max
    c = df["close"]
    s["long_entry"] = (c < lo) & rng
    s["long_exit"] = c >= mid
    s["short_entry"] = (c > up) & rng
    s["short_exit"] = c <= mid
    return s


def rsi2_pullback(df, rsi_th=10, sma_trend=200, exit_sma=5):
    """Connors RSI(2) long-only pullback inside an uptrend."""
    s = _base(df)
    r = ind.rsi(df["close"], 2)
    trend = ind.sma(df["close"], sma_trend)
    ex = ind.sma(df["close"], exit_sma)
    c = df["close"]
    s["long_entry"] = (c > trend) & (r < rsi_th)
    s["long_exit"] = c > ex
    s["short_entry"] = pd.Series(False, index=df.index)
    s["short_exit"] = pd.Series(False, index=df.index)
    return s


def _grid(d):
    keys = list(d)
    return [dict(zip(keys, combo)) for combo in itertools.product(*[d[k] for k in keys])]


# registry: name -> (builder, param_grid, exec_options_grid)
STRATS = {
    "ema_cross": (ema_cross, _grid({"fast": [10, 20, 30], "slow": [50, 100, 150], "adx_min": [0, 20, 25]}),
                  [{"trail_atr": 0}, {"sl_atr": 3, "trail_atr": 4}]),
    "supertrend": (supertrend_follow, _grid({"n": [7, 10, 14], "mult": [2.0, 3.0, 4.0], "adx_min": [0, 20]}),
                   [{}]),
    "macd_rsi": (macd_rsi, _grid({"fast": [8, 12], "slow": [21, 26], "signal": [9], "rsi_n": [14]}),
                 [{}, {"sl_atr": 2.5, "trail_atr": 3}]),
    "donchian": (donchian_breakout, _grid({"entry_n": [20, 40, 55], "exit_n": [10, 20]}),
                 [{"trail_atr": 0}, {"trail_atr": 3}]),
    "keltner": (keltner_breakout, _grid({"n": [20, 30], "mult": [1.5, 2.0, 2.5]}),
                [{"trail_atr": 0}, {"trail_atr": 3}]),
    "roc_mom": (roc_momentum, _grid({"roc_n": [10, 20, 40], "ema_n": [50, 100, 200]}),
                [{}, {"sl_atr": 3}]),
    "bb_meanrev": (bb_meanrev, _grid({"n": [20, 30], "k": [2.0, 2.5, 3.0], "adx_max": [20, 25]}),
                   [{"sl_atr": 2.5, "max_hold": 20}]),
    "rsi2": (rsi2_pullback, _grid({"rsi_th": [5, 10, 15], "sma_trend": [100, 200], "exit_sma": [5, 10]}),
             [{"sl_atr": 3, "max_hold": 15}]),
}
