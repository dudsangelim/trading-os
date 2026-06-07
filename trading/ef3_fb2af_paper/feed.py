"""REST feed — Binance Futures public klines (H4 + Daily) and spot price."""
from __future__ import annotations

import requests

from trading.ef3_fb2af_paper.config import BINANCE_FAPI, SYMBOL

_TIMEOUT = 15


def _klines(interval: str, limit: int, symbol: str = SYMBOL) -> list[dict]:
    r = requests.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    raw = r.json()
    bars = []
    for k in raw[:-1]:  # drop still-forming last bar
        bars.append({
            "open_time": int(k[0]),
            "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]),
            "close_time": int(k[6]),
        })
    return bars


class Feed:
    @staticmethod
    def h4(limit: int = 60, symbol: str = SYMBOL) -> list[dict]:
        return _klines("4h", limit, symbol)

    @staticmethod
    def daily(limit: int = 300, symbol: str = SYMBOL) -> list[dict]:
        return _klines("1d", limit, symbol)

    @staticmethod
    def get_price(symbol: str = SYMBOL) -> float:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return float(r.json()["price"])
