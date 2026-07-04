"""Thin Deribit public-API client (no auth) for the VRP paper trader."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from trading.vrp_paper import config as C

log = logging.getLogger("vrp_paper")
_S = requests.Session()


def _get(path: str, params: dict, retries: int = 3):
    for i in range(retries):
        try:
            r = _S.get(f"{C.DERIBIT_BASE}/public/{path}", params=params, timeout=15)
            r.raise_for_status()
            j = r.json()
            if "result" in j:
                return j["result"]
            raise RuntimeError(f"deribit error: {j.get('error')}")
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def index_price() -> float:
    return float(_get("get_index_price", {"index_name": "btc_usd"})["index_price"])


def dvol_now() -> float:
    """Latest closed DVOL value (fraction, e.g. 0.55)."""
    now_ms = int(time.time() * 1000)
    r = _get("get_volatility_index_data",
             {"currency": "BTC", "resolution": "3600",
              "start_timestamp": now_ms - 6 * 3600 * 1000, "end_timestamp": now_ms})
    data = r.get("data") or []
    if not data:
        raise RuntimeError("DVOL data empty")
    return float(data[-1][4]) / 100.0        # [ts, o, h, l, c]


def option_instruments() -> list[dict]:
    return _get("get_instruments", {"currency": "BTC", "kind": "option", "expired": "false"})


def ticker(instrument: str) -> dict:
    return _get("ticker", {"instrument_name": instrument})


def delivery_price(expiry_utc: datetime) -> float | None:
    """Official settlement (delivery) price for the given expiry date, or None
    if not published yet."""
    r = _get("get_delivery_prices", {"index_name": "btc_usd", "offset": 0, "count": 5})
    want = expiry_utc.strftime("%Y-%m-%d")
    for row in r.get("data", []):
        if row["date"] == want:
            return float(row["delivery_price"])
    return None


def fees_trade_btc(premium_btc: float) -> float:
    return min(C.FEE_TRADE_BTC, C.FEE_TRADE_CAP * premium_btc)
