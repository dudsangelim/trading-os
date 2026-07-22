"""Normalized read-only market interface for the venue-neutral VRP engine."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from trading.vrp_paper import config as C
from trading.vrp_paper import deribit

if C.VENUE == "binance":
    from trading.vrp_paper import binance as _M
else:
    _M = deribit


def index_price() -> float:
    return _M.index_price()


def dvol_now() -> float:
    return _M.dvol_now()


def option_instruments() -> list[dict]:
    rows = _M.option_instruments()
    if C.VENUE == "deribit":
        return [{"expiration_timestamp": int(r["expiration_timestamp"]),
                 "strike": float(r["strike"]),
                 "option_type": r["option_type"],
                 "instrument_name": r["instrument_name"],
                 "min_qty": 0.1, "qty_step": 0.1}
                for r in rows]
    return [{"expiration_timestamp": int(r["expiryDate"]),
             "strike": float(r["strikePrice"]),
             "option_type": "call" if r["side"] == "CALL" else "put",
             "instrument_name": r["symbol"],
             "min_qty": float(r.get("minQty") or 0.01),
             "qty_step": _lot_step(r)}
            for r in rows]


def _lot_step(row: dict) -> float:
    for flt in row.get("filters", []):
        if flt.get("filterType") == "LOT_SIZE":
            return float(flt.get("stepSize") or row.get("minQty") or 0.01)
    return float(row.get("minQty") or 0.01)


def ticker(instrument: str) -> dict:
    if C.VENUE == "deribit":
        tk = _M.ticker(instrument)
        S = index_price()
        return {"best_bid_usd": float(tk.get("best_bid_price") or 0.0) * S,
                "mark_usd": float(tk.get("mark_price") or 0.0) * S,
                "mark_iv": float(tk.get("mark_iv") or 0.0) / 100.0,
                "delta": float((tk.get("greeks") or {}).get("delta") or 0.0)}
    tk = _M.ticker(instrument)
    mk = _M.mark(instrument)
    return {"best_bid_usd": float(tk.get("bidPrice") or 0.0),
            "mark_usd": float(mk.get("markPrice") or 0.0),
            "mark_iv": float(mk.get("markIV") or 0.0),
            "delta": float(mk.get("delta") or 0.0)}


def delivery_price(expiry_utc: datetime) -> float | None:
    return _M.delivery_price(expiry_utc)


def entry_fee_usd(premium_usd: float, spot: float) -> float:
    if C.VENUE == "deribit":
        premium_btc = premium_usd / spot
        return deribit.fees_trade_btc(premium_btc) * spot
    return min(C.BINANCE_FEE_TRADE_RATE * spot,
               C.BINANCE_FEE_TRADE_CAP * premium_usd)


def settlement_fee_usd(spot: float, intrinsic_usd: float) -> float:
    if C.VENUE == "deribit":
        return C.FEE_SETTLE_BTC * spot
    if intrinsic_usd <= 0:
        return 0.0
    return C.BINANCE_FEE_EXERCISE_RATE * spot


def quantize_contracts(raw: float, min_qty: float, step: float) -> float:
    """Binance paper mirrors executable quantity; legacy Deribit stays fractional."""
    if C.VENUE == "deribit":
        return raw
    qty = math.floor((raw + 1e-12) / step) * step
    return round(qty, 8) if qty + 1e-12 >= min_qty else 0.0
