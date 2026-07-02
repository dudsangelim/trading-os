"""Binance REST access + tick/lot rounding for the btc_lead paper trader.

Only CLOSED candles are used for signals. The in-progress candle is returned
separately so executions can fill at its real OPEN price (= the validated
"execute at next-bar open"). Prices are rounded to the instrument tick and
quantities floored to the lot step. Verbatim port of swing_wf_paper/exchange.py.
"""
from __future__ import annotations

import math
import time
import logging

import requests
import pandas as pd

from trading.btc_lead_paper import config as C

log = logging.getLogger("btc_lead_paper")


# ── instrument filters ───────────────────────────────────────────────────────
def load_filters() -> dict:
    """Return {symbol: {tick, step, min_notional}} from fapi exchangeInfo,
    falling back to hard-coded values on failure."""
    out = {}
    symbols = [C.TRADE_SYMBOL]
    try:
        info = requests.get(f"{C.FAPI_BASE}/fapi/v1/exchangeInfo", timeout=15).json()
        by_sym = {s["symbol"]: s for s in info["symbols"]}
        for sym in symbols:
            s = by_sym[sym]
            f = {x["filterType"]: x for x in s["filters"]}
            out[sym] = {
                "tick": float(f["PRICE_FILTER"]["tickSize"]),
                "step": float(f["LOT_SIZE"]["stepSize"]),
                "min_notional": float(f.get("MIN_NOTIONAL", {}).get("notional", 0.0)),
            }
        log.info("loaded instrument filters from exchangeInfo")
    except Exception as e:  # pragma: no cover - network fallback
        log.warning("exchangeInfo failed (%s); using fallback filters", e)
        for sym in symbols:
            out[sym] = {
                "tick": C.TICK_FALLBACK[sym], "step": C.STEP_FALLBACK[sym],
                "min_notional": C.MIN_NOTIONAL_FALLBACK[sym],
            }
    return out


# ── tick / lot helpers ───────────────────────────────────────────────────────
def _round_to(value: float, increment: float, mode: str) -> float:
    if increment <= 0:
        return value
    n = value / increment
    if mode == "down":
        n = math.floor(n)
    elif mode == "up":
        n = math.ceil(n)
    else:
        n = round(n)
    # avoid binary float dust (e.g. 0.001*3)
    decimals = max(0, -int(math.floor(math.log10(increment)))) if increment < 1 else 0
    return round(n * increment, decimals + 2)


def round_price(px: float, tick: float, side: str) -> float:
    """Round a fill price to tick, adverse to us: buys up, sells down."""
    return _round_to(px, tick, "up" if side == "buy" else "down")


def round_qty(qty: float, step: float) -> float:
    return _round_to(qty, step, "down")


# ── klines ───────────────────────────────────────────────────────────────────
def fetch_klines(symbol: str, limit: int, interval: str = "4h") -> pd.DataFrame:
    """Fetch up to `limit` klines (paginated). Returns ALL candles incl. the
    in-progress one, indexed by open time (UTC), columns o/h/l/c/v + `closed`."""
    rows = []
    end = None
    remaining = limit
    while remaining > 0:
        params = {"symbol": symbol, "interval": interval, "limit": min(1000, remaining)}
        if end is not None:
            params["endTime"] = end
        r = requests.get(C.KLINES_URL, params=params, timeout=15)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows = batch + rows
        remaining -= len(batch)
        end = batch[0][0] - 1
        if len(batch) < min(1000, params["limit"]):
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qv", "trades", "tb", "tq", "ig"])
    df = df.drop_duplicates("open_time").sort_values("open_time")
    df.index = pd.to_datetime(df["open_time"], unit="ms")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    now_ms = int(time.time() * 1000)
    df["closed"] = df["close_time"] < now_ms
    return df[["open", "high", "low", "close", "volume", "closed"]]


def closed_and_inprogress(df: pd.DataFrame):
    """Split into (closed_bars_df, in_progress_open_price_or_None)."""
    closed = df[df["closed"]]
    inprog = df[~df["closed"]]
    nxt_open = float(inprog["open"].iloc[0]) if len(inprog) else None
    return closed[["open", "high", "low", "close", "volume"]], nxt_open


# ── funding ──────────────────────────────────────────────────────────────────
def fetch_funding_between(symbol: str, start_ms: int, end_ms: int) -> float:
    """Sum of funding rates charged in (start_ms, end_ms]. Long pays positive."""
    try:
        rows = requests.get(
            f"{C.FAPI_BASE}/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            timeout=15,
        ).json()
        return float(sum(float(x["fundingRate"]) for x in rows))
    except Exception as e:  # pragma: no cover
        log.warning("funding fetch failed for %s (%s); assuming 0", symbol, e)
        return 0.0
