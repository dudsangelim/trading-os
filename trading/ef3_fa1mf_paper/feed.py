"""Binance Futures REST feed for EF3 F-A.1.M.f paper trader.

Fetches H4 and Daily bars. Drops the currently-open bar.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import requests

from trading.ef3_fa1mf_paper.config import (
    BINANCE_FAPI, D1_LOOKBACK, H4_LOOKBACK, N_BREAKOUT, ATR_WIN, SMA_WIN, SYMBOL,
    SLIPPAGE_ONE_SIDE,
)

log = logging.getLogger("ef3_fa1mf_paper")

_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_vol", "n_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def _fetch_klines(interval: str, limit: int) -> pd.DataFrame:
    r = requests.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit},
        timeout=15,
    )
    r.raise_for_status()
    raw = r.json()
    if not raw:
        raise RuntimeError(f"Binance returned empty klines for {interval}")
    df = pd.DataFrame(raw, columns=_KLINE_COLS)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    # drop the currently-open (last) bar
    return df[["open", "high", "low", "close", "volume"]].iloc[:-1]


def get_current_price() -> float:
    r = requests.get(
        f"{BINANCE_FAPI}/fapi/v1/ticker/price",
        params={"symbol": SYMBOL},
        timeout=10,
    )
    r.raise_for_status()
    return float(r.json()["price"])


# ── Indicator helpers ─────────────────────────────────────────────────────────

def wilder_atr(df: pd.DataFrame, n: int) -> pd.Series:
    """ATR via Wilder RMA (seed = SMA of first n bars)."""
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    cp = np.roll(c, 1)
    cp[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - cp), np.abs(l - cp)))
    atr = np.full(len(tr), np.nan)
    if len(tr) >= n:
        atr[n - 1] = tr[:n].mean()
        alpha = 1.0 / n
        for i in range(n, len(tr)):
            atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    return pd.Series(atr, index=df.index)


def add_indicators(df_h4: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators. Returns copy of df_h4 with indicator columns."""
    df = df_h4.copy()

    # ATR14 Wilder for trailing stop
    df["atr14"] = wilder_atr(df, ATR_WIN)

    # Breakout: close > rolling_max(high, 30, lag=1)
    df["roll_max"] = df["high"].rolling(N_BREAKOUT).max().shift(1)
    df["breakout"] = (df["close"] > df["roll_max"]).astype(int)

    # SMA200 Daily regime — double shift
    sma200 = df_daily["close"].rolling(SMA_WIN).mean().shift(1)
    regime_daily = (df_daily["close"].shift(1) > sma200).astype(float)
    df["regime"] = regime_daily.reindex(df.index, method="ffill")

    return df


def fetch_bars() -> pd.DataFrame:
    """Fetch and compute indicators. Returns annotated H4 dataframe."""
    df_h4    = _fetch_klines("4h", H4_LOOKBACK + 1)
    df_daily = _fetch_klines("1d", D1_LOOKBACK + 1)
    return add_indicators(df_h4, df_daily)
