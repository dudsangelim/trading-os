"""Binance Futures REST feed for the Alligator paper trader."""
from __future__ import annotations

import logging
import pandas as pd
import requests

from trading.bw_jawcross_paper.config import BINANCE_FAPI, LOOKBACK_BARS, SYMBOL, TIMEFRAME

log = logging.getLogger("bw_jawcross_paper")

_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_vol", "n_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def fetch_closed_bars(limit: int = LOOKBACK_BARS + 1) -> pd.DataFrame:
    """
    Fetch the last `limit` klines, drop the currently-open bar,
    and return a DataFrame with a UTC DatetimeIndex.
    """
    url = f"{BINANCE_FAPI}/fapi/v1/klines"
    r   = requests.get(
        url,
        params={"symbol": SYMBOL, "interval": TIMEFRAME, "limit": limit},
        timeout=15,
    )
    r.raise_for_status()
    raw = r.json()
    if not raw:
        raise RuntimeError("Binance returned empty klines")

    df = pd.DataFrame(raw, columns=_KLINE_COLS)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    # drop the last entry — it is the currently-open (unclosed) bar
    df = df.iloc[:-1]
    return df[["open", "high", "low", "close", "volume"]]


def get_current_price() -> float:
    """Fetch latest mark price for quick health/status checks."""
    r = requests.get(
        f"{BINANCE_FAPI}/fapi/v1/ticker/price",
        params={"symbol": SYMBOL},
        timeout=10,
    )
    r.raise_for_status()
    return float(r.json()["price"])
