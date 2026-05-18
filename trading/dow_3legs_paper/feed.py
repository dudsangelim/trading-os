"""REST feed — Binance Futures public endpoints (no auth required)."""
from __future__ import annotations

import requests

from trading.dow_3legs_paper.config import BINANCE_FAPI, SYMBOL

_TIMEOUT = 10


class Feed:
    @staticmethod
    def get_price(symbol: str = SYMBOL) -> float:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return float(r.json()["price"])
