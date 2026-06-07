"""EF3 R004 / F-B.2.A.f engine — ported from R004_backtest.py.

20-bar H4 HIGH breakout + range-compression (<12%) + SMA200 Daily filter
+ fixed SL 1×ATR14 + fixed TP 8:1. Long-only, BTCUSDT, isolated, 1x.

Driven bar-by-bar over closed H4/Daily frames; no look-ahead (signals use
shift(1); entry fills at open of the bar after the close-signal).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from trading.ef3_fb2af_paper.config import (
    ATR_LEN, ATR_SL_MULT, COMP_THRESH, FEE_RT_BPS, N_COMP, SMA_LEN, TP_RR,
)

log = logging.getLogger("ef3_fb2af")


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def build_signals(h4: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    h4 = h4.copy()
    # SMA200 Daily (lag=1) → ffill to H4
    sma = daily["close"].rolling(SMA_LEN).mean().shift(1)
    regime_daily = (daily["close"].shift(1) > sma).astype(float)
    h4["regime_bull"] = regime_daily.reindex(h4.index, method="ffill")

    h4["atr14"] = compute_atr(h4, ATR_LEN)

    # Compression filter (lag=1): range_rel over N_COMP bars < threshold
    roll_high = h4["high"].rolling(N_COMP).max()
    roll_low = h4["low"].rolling(N_COMP).min()
    roll_mid = h4["close"].rolling(N_COMP).mean()
    range_rel = (roll_high - roll_low) / roll_mid
    compress = (range_rel.shift(1) < COMP_THRESH).astype(float)

    # HIGH breakout: close > max(high, N_COMP, lag=1)
    roll_max_high = h4["high"].rolling(N_COMP).max().shift(1)
    breakout = (h4["close"] > roll_max_high).astype(int)

    h4["entry_signal"] = (
        (breakout == 1) & (compress == 1) & (h4["regime_bull"] == 1)
    ).astype(int)
    return h4


class FB2AfEngine:
    VERSION = "ef3_fb2af_v1"

    def __init__(self) -> None:
        self.state: str = "flat"          # flat | long
        self.entry_price: Optional[float] = None
        self.entry_ts: Optional[str] = None
        self.sl_price: Optional[float] = None
        self.tp_price: Optional[float] = None
        self.last_bar_ts: Optional[str] = None
        self.last_price: Optional[float] = None
        self.trades: List[Dict] = []

    def to_state_dict(self) -> dict:
        return dict(
            version=self.VERSION, state=self.state,
            entry_price=self.entry_price, entry_ts=self.entry_ts,
            sl_price=self.sl_price, tp_price=self.tp_price,
            last_bar_ts=self.last_bar_ts, last_price=self.last_price,
            n_trades=len(self.trades),
        )

    def load_state_dict(self, d: dict) -> None:
        self.state = d.get("state", "flat")
        self.entry_price = d.get("entry_price")
        self.entry_ts = d.get("entry_ts")
        self.sl_price = d.get("sl_price")
        self.tp_price = d.get("tp_price")
        self.last_bar_ts = d.get("last_bar_ts")
        self.last_price = d.get("last_price")

    def process_bar(self, h4: pd.DataFrame, daily: pd.DataFrame) -> List[Dict]:
        sig = build_signals(h4, daily)
        idx = sig.index
        decisions: List[Dict] = []
        if len(sig):
            self.last_price = float(sig["close"].iloc[-1])
        last_seen = pd.Timestamp(self.last_bar_ts) if self.last_bar_ts else None

        for i in range(N_COMP + ATR_LEN, len(sig)):
            bar_ts = idx[i]
            if last_seen is not None and bar_ts <= last_seen:
                continue

            o = float(sig["open"].iloc[i])
            h = float(sig["high"].iloc[i])
            lo = float(sig["low"].iloc[i])
            c = float(sig["close"].iloc[i])
            atr_prev = sig["atr14"].iloc[i - 1]

            if self.state == "flat":
                if int(sig["entry_signal"].iloc[i - 1]) == 1 and not np.isnan(atr_prev):
                    self.entry_price = o
                    self.entry_ts = bar_ts.isoformat()
                    self.sl_price = o - ATR_SL_MULT * float(atr_prev)
                    self.tp_price = o + TP_RR * float(atr_prev)
                    self.state = "long"
                    decisions.append(dict(
                        ts=bar_ts.isoformat(), action="open_long", price=o,
                        state_after=self.state,
                        sl_price=self.sl_price, tp_price=self.tp_price,
                    ))
            else:  # long — fixed SL/TP, same priority cascade as backtest
                exit_px = None
                exit_t = None
                if o <= self.sl_price:
                    exit_px, exit_t = o, "gap_sl"
                elif o >= self.tp_price:
                    exit_px, exit_t = o, "gap_tp"
                elif lo <= self.sl_price and h >= self.tp_price:
                    exit_px, exit_t = self.sl_price, "both_sl"
                elif lo <= self.sl_price:
                    exit_px, exit_t = self.sl_price, "sl"
                elif h >= self.tp_price:
                    exit_px, exit_t = self.tp_price, "tp"

                if exit_px is not None:
                    decisions.append(self._close(bar_ts, exit_px, exit_t))

            self.last_bar_ts = bar_ts.isoformat()

        return decisions

    def _close(self, ts: pd.Timestamp, price: float, exit_type: str) -> Dict:
        ret_gross = (price - self.entry_price) / self.entry_price
        ret_pct_gross = ret_gross * 100
        ret_pct_net = ret_pct_gross - FEE_RT_BPS / 100.0
        self.trades.append(dict(
            entry_ts=self.entry_ts, exit_ts=ts.isoformat(), side="long",
            entry_price=self.entry_price, exit_price=price,
            sl_price=self.sl_price, tp_price=self.tp_price,
            ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
            exit_type=exit_type,
        ))
        d = dict(ts=ts.isoformat(), action="close_long", price=price,
                 state_after="flat", exit_type=exit_type,
                 ret_pct_net=ret_pct_net)
        self.state = "flat"
        self.entry_price = None
        self.entry_ts = None
        self.sl_price = None
        self.tp_price = None
        return d
