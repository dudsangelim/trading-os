"""
DOW 3-legs skip_low state machine (Dow3LegsSkipLowEngine).

States:
  flat       — no open position
  long_mon   — LONG opened Sun 23:55, closes Mon 23:55 (L_Mon)
  long_wed   — LONG opened Tue 23:55, closes Wed 23:55 (L_Wed)
  short_thu  — SHORT opened Wed 23:55 (after L_Wed close), closes Thu 23:55 (S_Thu)

on_tick receives pass_atr_gate: bool.
  - Opens only if True.
  - Closes always (never leaves trade hanging).

Ported from assets/engine_dow.py — Dow3LegsSkipLowEngine only (DowCaporaleEngine legacy
2-leg variant is NOT included here).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from trading.dow_3legs_paper.config import FEE_RT_PCT

TRIGGER_HOUR = 23
TRIGGER_MIN = 55
TRIGGER_TOLERANCE_MIN = 10  # accepts up to 10 min after 23:55 (i.e. 00:00–00:09)
STALE_CLOSE_HOURS = 30  # force-close any position open longer than this (missed-close guard)

log = logging.getLogger("dow_3legs_paper")


def _is_trigger_time(ts: pd.Timestamp) -> bool:
    t = ts.time()
    if t.hour == TRIGGER_HOUR and t.minute >= TRIGGER_MIN:
        return True
    if t.hour == 0 and t.minute < TRIGGER_TOLERANCE_MIN:
        return True
    return False


def _logical_dow(ts: pd.Timestamp) -> int:
    """Logical day-of-week: if 00:00–00:09 on day X+1, treat as 'end of day X'."""
    t = ts.time()
    if t.hour == 0 and t.minute < TRIGGER_TOLERANCE_MIN:
        return (ts - pd.Timedelta(hours=1)).dayofweek
    return ts.dayofweek


class Dow3LegsSkipLowEngine:
    """
    3-leg calendar strategy + ATR skip_low gate.

    Backtest reference (2023-01-01→2026-04-22, parquet):
      PF 1.62, Sharpe 1.85, weekly +1.02% (lev=1), walk-forward 3/3.
    """

    VERSION = "v2_3legs_skiplow"

    def __init__(self, leverage: float = 1.5):
        self.state: str = "flat"
        self.leverage = leverage
        self.entry_price: Optional[float] = None
        self.entry_ts: Optional[pd.Timestamp] = None
        self.last_action_ts: Optional[pd.Timestamp] = None
        self.trades: List[Dict] = []
        self.decisions: List[Dict] = []

    def to_state_dict(self) -> dict:
        return dict(
            version=self.VERSION,
            state=self.state,
            leverage=self.leverage,
            entry_price=self.entry_price,
            entry_ts=self.entry_ts.isoformat() if self.entry_ts is not None else None,
            last_action_ts=self.last_action_ts.isoformat() if self.last_action_ts is not None else None,
            n_trades=len(self.trades),
        )

    def load_state_dict(self, d: dict) -> None:
        self.state = d.get("state", "flat")
        # leverage intentionally NOT loaded from state — always use env var (DOW_LEVERAGE)
        self.entry_price = d.get("entry_price")
        ets = d.get("entry_ts")
        self.entry_ts = pd.Timestamp(ets) if ets else None
        lts = d.get("last_action_ts")
        self.last_action_ts = pd.Timestamp(lts) if lts else None

    def on_tick(self, ts: pd.Timestamp, price: float,
                pass_atr_gate: bool = True) -> Optional[Dict]:
        # Force-close if a close event was missed (bot was down during close window)
        if self.state != "flat" and self.entry_ts is not None:
            hours_open = (ts - self.entry_ts).total_seconds() / 3600
            if hours_open > STALE_CLOSE_HOURS:
                leg_map = {"long_mon": "L_Mon", "long_wed": "L_Wed", "short_thu": "S_Thu"}
                leg = leg_map.get(self.state, self.state)
                log.warning("[stale] force-closing %s open %.1fh", leg, hours_open)
                d = (self._close_long(ts, price, leg=leg)
                     if self.state in ("long_mon", "long_wed")
                     else self._close_short(ts, price, leg=leg))
                d["stale_close"] = True
                d["hours_open"] = round(hours_open, 1)
                self.last_action_ts = None  # allow normal open on the very next tick
                return d

        if self.last_action_ts is not None:
            if (ts - self.last_action_ts).total_seconds() < 3600:
                return None
        if not _is_trigger_time(ts):
            return None

        ldow = _logical_dow(ts)

        # Closes always (independent of gate) ---

        if ldow == 0 and self.state == "long_mon":
            return self._close_long(ts, price, leg="L_Mon")

        if ldow == 2 and self.state == "long_wed":
            d_close = self._close_long(ts, price, leg="L_Wed")
            if pass_atr_gate:
                d_open = self._open_short(ts, price, leg="S_Thu")
                merged = dict(
                    ts=ts.isoformat(),
                    action="close_long_open_short",
                    price=price,
                    long_ret_net_pct=d_close["long_ret_net_pct"],
                    leg_closed="L_Wed",
                    leg_opened="S_Thu",
                    state_after=self.state,
                    pass_atr_gate=pass_atr_gate,
                )
                self.decisions = self.decisions[:-2] + [merged]
                return merged
            else:
                d_close["pass_atr_gate"] = pass_atr_gate
                d_close["short_skipped_reason"] = "atr_gate_off"
                return d_close

        if ldow == 3 and self.state == "short_thu":
            return self._close_short(ts, price, leg="S_Thu")

        # Opens (gated) ---

        if not pass_atr_gate:
            if self.state == "flat" and ldow in (6, 1):
                d = dict(
                    ts=ts.isoformat(), action="skip_open", price=price,
                    reason="atr_gate_off",
                    intended_leg="L_Mon" if ldow == 6 else "L_Wed",
                    state_after=self.state, pass_atr_gate=False,
                )
                self.decisions.append(d)
                self.last_action_ts = ts
                return d
            return None

        if ldow == 6 and self.state == "flat":
            return self._open_long(ts, price, leg="L_Mon")

        if ldow == 1 and self.state == "flat":
            return self._open_long(ts, price, leg="L_Wed")

        return None

    def _open_long(self, ts: pd.Timestamp, price: float, leg: str) -> Dict:
        self.state = "long_mon" if leg == "L_Mon" else "long_wed"
        self.entry_price = price
        self.entry_ts = ts
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="open_long", price=price, leg=leg,
                 state_after=self.state)
        self.decisions.append(d)
        return d

    def _open_short(self, ts: pd.Timestamp, price: float, leg: str) -> Dict:
        self.state = "short_thu"
        self.entry_price = price
        self.entry_ts = ts
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="open_short", price=price, leg=leg,
                 state_after=self.state)
        self.decisions.append(d)
        return d

    def _close_long(self, ts: pd.Timestamp, price: float, leg: str) -> Dict:
        if self.entry_price is None:
            log.error("[close_long] entry_price is None for %s; resetting to flat", leg)
            self.state = "flat"
            self.entry_ts = None
            self.last_action_ts = ts
            d = dict(ts=ts.isoformat(), action="close_long", price=price, leg=leg,
                     long_ret_net_pct=0.0, state_after="flat", error="entry_price_missing")
            self.decisions.append(d)
            return d
        ret_pct_gross = (price - self.entry_price) / self.entry_price * 100
        ret_pct_net = (ret_pct_gross - FEE_RT_PCT) * self.leverage
        self.trades.append(dict(
            entry_ts=self.entry_ts.isoformat(), exit_ts=ts.isoformat(),
            side="long", leg=leg,
            entry_price=self.entry_price, exit_price=price,
            ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
            fee_pct=FEE_RT_PCT, leverage=self.leverage,
        ))
        self.state = "flat"
        self.entry_price = None
        self.entry_ts = None
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="close_long", price=price, leg=leg,
                 long_ret_net_pct=ret_pct_net, state_after=self.state)
        self.decisions.append(d)
        return d

    def _close_short(self, ts: pd.Timestamp, price: float, leg: str) -> Dict:
        if self.entry_price is None:
            log.error("[close_short] entry_price is None for %s; resetting to flat", leg)
            self.state = "flat"
            self.entry_ts = None
            self.last_action_ts = ts
            d = dict(ts=ts.isoformat(), action="close_short", price=price, leg=leg,
                     short_ret_net_pct=0.0, state_after="flat", error="entry_price_missing")
            self.decisions.append(d)
            return d
        ret_pct_gross = (self.entry_price - price) / self.entry_price * 100
        ret_pct_net = (ret_pct_gross - FEE_RT_PCT) * self.leverage
        self.trades.append(dict(
            entry_ts=self.entry_ts.isoformat(), exit_ts=ts.isoformat(),
            side="short", leg=leg,
            entry_price=self.entry_price, exit_price=price,
            ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
            fee_pct=FEE_RT_PCT, leverage=self.leverage,
        ))
        self.state = "flat"
        self.entry_price = None
        self.entry_ts = None
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="close_short", price=price, leg=leg,
                 short_ret_net_pct=ret_pct_net, state_after=self.state)
        self.decisions.append(d)
        return d
