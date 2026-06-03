"""BinanceFeed — thin wrapper around Binance Futures public REST API."""
from __future__ import annotations

from typing import Optional

import pandas as pd
import requests

from trading.ny_open_mom_paper.config import BINANCE_FAPI, SYMBOL

_TIMEOUT = 15


class BinanceFeed:
    @staticmethod
    def _get(endpoint: str, params: dict) -> object:
        r = requests.get(f"{BINANCE_FAPI}{endpoint}", params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    @classmethod
    def klines(cls, interval: str, limit: int = 500,
               end_time_ms: Optional[int] = None) -> pd.DataFrame:
        params: dict = {"symbol": SYMBOL, "interval": interval, "limit": limit}
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        raw = cls._get("/fapi/v1/klines", params)
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    @classmethod
    def last_closed_5m(cls) -> pd.Series:
        df = cls.klines("5m", limit=2)
        return df.iloc[-2]

    @classmethod
    def ticker_price(cls) -> float:
        raw = cls._get("/fapi/v1/ticker/price", {"symbol": SYMBOL})
        return float(raw["price"])
