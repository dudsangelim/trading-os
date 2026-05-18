"""
BinanceFeed — thin wrapper around Binance Futures REST API (public endpoints only).

No API keys required. Paper trade only.
Ported from paper_trade_reference.py (BinanceFeed class).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import requests

from trading.ny_open_paper.config import BINANCE_FAPI, SYMBOL

_TIMEOUT = 15


class BinanceFeed:
    @staticmethod
    def _get(endpoint: str, params: dict) -> object:
        url = f"{BINANCE_FAPI}{endpoint}"
        r = requests.get(url, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    @classmethod
    def klines(cls, interval: str, limit: int = 500,
               end_time_ms: Optional[int] = None) -> pd.DataFrame:
        """Fetch klines from Binance Futures. Returns DataFrame with open_time as Timestamp[UTC]."""
        params: dict = {"symbol": SYMBOL, "interval": interval, "limit": limit}
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        raw = cls._get("/fapi/v1/klines", params)
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        for col in ("open", "high", "low", "close", "volume", "quote_volume"):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        return df

    @classmethod
    def last_closed_5m(cls) -> pd.Series:
        """Return the last fully closed 5m kline (second-to-last in the list)."""
        df = cls.klines("5m", limit=2)
        return df.iloc[-2]

    @classmethod
    def ticker_price(cls) -> float:
        """Return latest trade price from Binance Futures (no auth required)."""
        raw = cls._get("/fapi/v1/ticker/price", {"symbol": SYMBOL})
        return float(raw["price"])

    @classmethod
    def last_funding_rate(cls) -> float:
        """Return most recent funding rate as a fraction (e.g. 0.0001 = 0.01%)."""
        raw = cls._get("/fapi/v1/fundingRate", {"symbol": SYMBOL, "limit": 1})
        return float(raw[0]["fundingRate"]) if raw else 0.0
