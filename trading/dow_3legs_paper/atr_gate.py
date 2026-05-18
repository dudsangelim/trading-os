"""
AtrGate — computes ATR(14d) > P20(60d) regime gate via daily Binance klines.

Auto-refreshes once per day (when >12h since last refresh).
If DOW_DISABLE_GATE=true, always returns pass=True.

Ported from assets/paper_trade_dow.py (AtrGate class).
"""
from __future__ import annotations

import logging
import traceback
from typing import Optional

import numpy as np
import pandas as pd
import requests

from trading.dow_3legs_paper.config import (
    ATR_LEN, BINANCE_FAPI, DISABLE_GATE, Q_LOW, REGIME_WINDOW, SYMBOL,
)

log = logging.getLogger("dow_3legs_paper")


class AtrGate:
    def __init__(self) -> None:
        self.last_refresh: Optional[pd.Timestamp] = None
        self.atr_pct_today: Optional[float] = None
        self.p20_today: Optional[float] = None
        self.disabled = DISABLE_GATE

    def _fetch_daily_klines(self, days: int = 90) -> pd.DataFrame:
        end_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
        start_ms = end_ms - days * 24 * 60 * 60 * 1000
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": SYMBOL, "interval": "1d",
                    "startTime": start_ms, "endTime": end_ms, "limit": 200},
            timeout=15,
        )
        r.raise_for_status()
        df = pd.DataFrame(r.json(), columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        return df.set_index("open_time").sort_index()[["open", "high", "low", "close"]]

    def refresh(self, ts_now: pd.Timestamp) -> dict:
        """Recompute ATR gate from daily klines. Call once per day."""
        df = self._fetch_daily_klines(days=90)
        if len(df) < ATR_LEN + REGIME_WINDOW:
            log.warning("atr_gate: insufficient data (%d candles); gate OFF", len(df))
            self.atr_pct_today = None
            self.p20_today = None
            return {"status": "insufficient_data", "n": len(df)}

        df["tr"] = np.maximum.reduce([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ])
        df["atr"] = df["tr"].rolling(ATR_LEN).mean()
        df["atr_pct"] = df["atr"] / df["close"] * 100
        df["p20"] = df["atr_pct"].rolling(REGIME_WINDOW).quantile(Q_LOW)

        # use previous day to avoid look-ahead (current day candle is incomplete)
        prev = df.iloc[-2] if len(df) >= 2 else None
        if prev is None or pd.isna(prev["atr_pct"]) or pd.isna(prev["p20"]):
            self.atr_pct_today = None
            self.p20_today = None
            return {"status": "nan_in_gate"}

        self.atr_pct_today = float(prev["atr_pct"])
        self.p20_today = float(prev["p20"])
        self.last_refresh = ts_now
        based_on = prev.name.isoformat()[:10]
        log.info(
            "atr_gate refresh: atr_pct=%.3f, p20=%.3f, based_on_day=%s",
            self.atr_pct_today, self.p20_today, based_on,
        )
        return {"status": "ok", "atr_pct": self.atr_pct_today,
                "p20": self.p20_today, "based_on_day": based_on}

    def is_pass(self, ts_now: pd.Timestamp) -> tuple[bool, dict]:
        """Returns (pass: bool, info: dict). Auto-refreshes if stale."""
        if self.disabled:
            return True, {"gate": "disabled"}
        if self.last_refresh is None or (ts_now - self.last_refresh).total_seconds() > 12 * 3600:
            try:
                self.refresh(ts_now)
            except Exception:
                log.error("atr_gate refresh error:\n%s", traceback.format_exc())
                return False, {"gate": "error_refresh"}
        if self.atr_pct_today is None or self.p20_today is None:
            return False, {"gate": "no_data"}
        passed = self.atr_pct_today > self.p20_today
        return passed, {
            "gate": "pass" if passed else "fail",
            "atr_pct": round(self.atr_pct_today, 3),
            "p20": round(self.p20_today, 3),
        }

    def to_dict(self) -> dict:
        return {
            "disabled": self.disabled,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "atr_pct": self.atr_pct_today,
            "p20": self.p20_today,
        }
