"""Thin Binance Options public-API client (no auth, no order endpoints)."""
from __future__ import annotations

import logging
import time
from datetime import datetime

import requests

from trading.vrp_paper import config as C
from trading.vrp_paper import deribit

log = logging.getLogger("vrp_paper")
_S = requests.Session()


def _get(path: str, params: dict | None = None, retries: int = 3):
    for i in range(retries):
        try:
            r = _S.get(f"{C.BINANCE_BASE}{path}", params=params or {}, timeout=15)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, dict) and payload.get("code") not in (None, 0, 200):
                raise RuntimeError(f"binance error: {payload}")
            return payload
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def index_price() -> float:
    return float(_get("/eapi/v1/index", {"underlying": "BTCUSDT"})["indexPrice"])


def dvol_now() -> float:
    """Binance has no DVOL equivalent; retain the audited Deribit DVOL signal."""
    return deribit.dvol_now()


def option_instruments() -> list[dict]:
    payload = _get("/eapi/v1/exchangeInfo")
    return [row for row in payload.get("optionSymbols", [])
            if row.get("underlying") == "BTCUSDT" and row.get("status") == "TRADING"]


def ticker(instrument: str) -> dict:
    rows = _get("/eapi/v1/ticker", {"symbol": instrument})
    if not rows:
        raise RuntimeError(f"empty Binance ticker for {instrument}")
    return rows[0]


def mark(instrument: str) -> dict:
    rows = _get("/eapi/v1/mark", {"symbol": instrument})
    if not rows:
        raise RuntimeError(f"empty Binance mark for {instrument}")
    return rows[0]


def delivery_price(expiry_utc: datetime) -> float | None:
    rows = _get("/eapi/v1/exerciseHistory", {"underlying": "BTCUSDT", "limit": 100})
    want_ms = int(expiry_utc.timestamp() * 1000)
    for row in rows:
        if int(row.get("expiryDate") or 0) == want_ms:
            return float(row["realStrikePrice"])
    return None
