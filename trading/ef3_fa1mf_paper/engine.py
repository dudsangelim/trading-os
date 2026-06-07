"""EF3 R003 / F-A.1.M.f engine — ported from R003_backtest.py.

30-bar H4 CLOSE breakout + SMA200 Daily regime filter + ATR14×2.0 trailing stop.
Long-only, BTCUSDT, isolated, 1x.

Indicators are computed over closed-bar OHLC frames (H4 + Daily). The engine is
driven bar-by-bar: process_bar(i) is the live analogue of the backtest loop body
for H4 bar index i, using only data through bar i (and i-1 for signals/stop), so
there is no look-ahead.

State machine: flat ↔ long. Entry fills at open of the bar AFTER a close-signal.
Because the live feed only delivers closed bars, we detect the signal on the most
recently closed bar (i-1) and fill at the open of the just-closed current bar (i)
— identical timing to the backtest (signal at close[i-1] → fill at open[i]).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from trading.ef3_fa1mf_paper.config import (
    ATR_LEN, ATR_MULT, FEE_RT_BPS, N_ENTRY, SMA_LEN,
)

log = logging.getLogger("ef3_fa1mf")


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def build_signals(h4: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Return h4 frame augmented with atr14, regime_bull and entry_signal.

    h4/daily indexed by tz-aware UTC timestamp (bar open time), OHLC columns.
    """
    h4 = h4.copy()
    # SMA200 Daily (lag=1 both sides) → forward-fill to H4
    sma = daily["close"].rolling(SMA_LEN).mean().shift(1)
    regime_daily = (daily["close"].shift(1) > sma).astype(float)
    h4["regime_bull"] = regime_daily.reindex(h4.index, method="ffill")

    h4["atr14"] = compute_atr(h4, ATR_LEN)

    roll_max = h4["close"].rolling(N_ENTRY).max().shift(1)
    breakout = (h4["close"] > roll_max).astype(int)
    h4["entry_signal"] = ((breakout == 1) & (h4["regime_bull"] == 1)).astype(int)
    return h4


class FA1MfEngine:
    VERSION = "ef3_fa1mf_v1"

    def __init__(self) -> None:
        self.state: str = "flat"          # flat | long
        self.entry_price: Optional[float] = None
        self.entry_ts: Optional[str] = None
        self.trailing_stop: Optional[float] = None
        self.last_bar_ts: Optional[str] = None   # last processed H4 bar open-time
        self.last_price: Optional[float] = None
        self.trades: List[Dict] = []

    # ---- state (de)serialisation ----
    def to_state_dict(self) -> dict:
        return dict(
            version=self.VERSION, state=self.state,
            entry_price=self.entry_price, entry_ts=self.entry_ts,
            trailing_stop=self.trailing_stop,
            last_bar_ts=self.last_bar_ts, last_price=self.last_price,
            n_trades=len(self.trades),
        )

    def load_state_dict(self, d: dict) -> None:
        self.state = d.get("state", "flat")
        self.entry_price = d.get("entry_price")
        self.entry_ts = d.get("entry_ts")
        self.trailing_stop = d.get("trailing_stop")
        self.last_bar_ts = d.get("last_bar_ts")
        self.last_price = d.get("last_price")

    def process_bar(self, h4: pd.DataFrame, daily: pd.DataFrame) -> List[Dict]:
        """Process every not-yet-seen closed H4 bar. Returns list of decisions."""
        sig = build_signals(h4, daily)
        idx = sig.index
        decisions: List[Dict] = []
        if len(sig):
            self.last_price = float(sig["close"].iloc[-1])

        last_seen = pd.Timestamp(self.last_bar_ts) if self.last_bar_ts else None

        for i in range(N_ENTRY, len(sig)):
            bar_ts = idx[i]
            if last_seen is not None and bar_ts <= last_seen:
                continue

            o = float(sig["open"].iloc[i])
            c = float(sig["close"].iloc[i])
            atr_prev = sig["atr14"].iloc[i - 1]
            close_prev = float(sig["close"].iloc[i - 1])

            if self.state == "flat":
                if int(sig["entry_signal"].iloc[i - 1]) == 1 and not np.isnan(atr_prev):
                    self.entry_price = o
                    self.entry_ts = bar_ts.isoformat()
                    self.trailing_stop = o - ATR_MULT * float(atr_prev)
                    self.state = "long"
                    decisions.append(dict(
                        ts=bar_ts.isoformat(), action="open_long", price=o,
                        state_after=self.state, trailing_stop=self.trailing_stop,
                    ))
            else:  # long
                if not np.isnan(atr_prev):
                    candidate = close_prev - ATR_MULT * float(atr_prev)
                    self.trailing_stop = max(self.trailing_stop, candidate)

                exit_px = None
                exit_t = None
                if o < self.trailing_stop:
                    exit_px, exit_t = o, "gap_h4"
                elif close_prev < self.trailing_stop:
                    exit_px, exit_t = o, "trailing_stop"

                if exit_px is not None:
                    d = self._close(bar_ts, exit_px, exit_t)
                    decisions.append(d)

            self.last_bar_ts = bar_ts.isoformat()

        return decisions

    def _close(self, ts: pd.Timestamp, price: float, exit_type: str) -> Dict:
        ret_gross = (price - self.entry_price) / self.entry_price
        ret_pct_gross = ret_gross * 100
        ret_pct_net = ret_pct_gross - FEE_RT_BPS / 100.0
        self.trades.append(dict(
            entry_ts=self.entry_ts, exit_ts=ts.isoformat(), side="long",
            entry_price=self.entry_price, exit_price=price,
            ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
            exit_type=exit_type,
        ))
        d = dict(ts=ts.isoformat(), action="close_long", price=price,
                 state_after="flat", exit_type=exit_type,
                 ret_pct_net=ret_pct_net)
        self.state = "flat"
        self.entry_price = None
        self.entry_ts = None
        self.trailing_stop = None
        return d
