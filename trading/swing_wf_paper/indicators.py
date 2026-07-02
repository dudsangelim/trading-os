"""Vectorized technical indicators (no TA-Lib dependency, no look-ahead).

Every function returns a Series/DataFrame aligned to the input index. All use
only past+current bar data, so they are safe to shift by 1 for signals.
"""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / n, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def true_range(h, l, c) -> pd.Series:
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr


def atr(h, l, c, n: int = 14) -> pd.Series:
    return true_range(h, l, c).ewm(alpha=1 / n, adjust=False).mean()


def adx(h, l, c, n: int = 14):
    """Returns (adx, plus_di, minus_di)."""
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(h, l, c)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / n, adjust=False).mean()
    return adx_.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    macd_line = ema(close, fast) - ema(close, slow)
    sig = ema(macd_line, signal)
    hist = macd_line - sig
    return macd_line, sig, hist


def bollinger(close: pd.Series, n=20, k=2.0):
    mid = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def keltner(h, l, c, n=20, mult=2.0):
    mid = ema(c, n)
    rng = atr(h, l, c, n)
    return mid, mid + mult * rng, mid - mult * rng


def donchian(h, l, n=20):
    """Channel using only completed prior bars (shifted) to avoid look-ahead on
    the current bar's own extreme."""
    upper = h.rolling(n).max().shift(1)
    lower = l.rolling(n).min().shift(1)
    return upper, lower


def stoch(h, l, c, n=14, d=3):
    ll = l.rolling(n).min()
    hh = h.rolling(n).max()
    k = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    return k.fillna(50), k.rolling(d).mean().fillna(50)


def roc(close: pd.Series, n: int) -> pd.Series:
    return close.pct_change(n)


def supertrend(h, l, c, n=10, mult=3.0):
    """Classic Supertrend. Returns (trend_dir, line) where trend_dir is +1/-1."""
    hl2 = (h + l) / 2
    a = atr(h, l, c, n)
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    fu = upper.copy()
    fl = lower.copy()
    dir_ = pd.Series(1, index=c.index)
    cv = c.values
    uv, lv = upper.values, lower.values
    fuv, flv = fu.values, fl.values
    dv = dir_.values
    for i in range(1, len(c)):
        fuv[i] = uv[i] if (uv[i] < fuv[i - 1] or cv[i - 1] > fuv[i - 1]) else fuv[i - 1]
        flv[i] = lv[i] if (lv[i] > flv[i - 1] or cv[i - 1] < flv[i - 1]) else flv[i - 1]
        if cv[i] > fuv[i - 1]:
            dv[i] = 1
        elif cv[i] < flv[i - 1]:
            dv[i] = -1
        else:
            dv[i] = dv[i - 1]
    line = np.where(dv == 1, flv, fuv)
    return pd.Series(dv, index=c.index), pd.Series(line, index=c.index)


def obv(close, volume):
    sign = np.sign(close.diff()).fillna(0)
    return (sign * volume).cumsum()
