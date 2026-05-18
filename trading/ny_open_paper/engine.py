"""
StrategyEngine — FSM for the 2C NY Open fade strategy.

Ported faithfully from engine_2C_reference.py (StrategyEngine, ArmedOrder, Position).
Backtest/replay utilities and parquet I/O are NOT ported (paper trader only).

States:
  IDLE           — outside session or already traded today
  COLLECTING     — 13:30–14:00 UTC accumulating first 30m high/low
  DECIDING       — first bar after 14:00: compute features, arm order
  WAITING_BREAK  — armed, waiting for price to break extreme ± buffer
  WAITING_RETEST — broke, waiting retest of extreme within 15 min (3 bars)
  IN_POSITION    — filled, monitoring stop/TP1/TP2
  CLOSED         — exited, waiting for session end to reset
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from typing import Dict, List, Optional

import pandas as pd

from trading.ny_open_paper.model import FrozenModel


@dataclass
class Position:
    date: object
    direction: int           # -1 short (fade high), +1 long (fade low)
    entry_price: float
    stop_price: float
    mid: float               # TP1
    opposite: float          # TP2
    hit_tp1: bool = False
    partial_fill: Optional[float] = None


@dataclass
class ArmedOrder:
    date: object
    direction: int
    entry_price: float       # extreme where LIMIT is placed
    break_level: float
    stop_price: float
    mid: float
    opposite: float
    session_open_price: float
    range_first_pct: float
    first_start_ts: pd.Timestamp   # first bar after 30m (for time_to_break)
    breakouted: bool = False
    break_bar_idx: Optional[int] = None
    bars_since_break: int = 0


class StrategyEngine:
    def __init__(self, model: FrozenModel, threshold: float, verbose: bool = False):
        self.m = model
        self.thr = threshold
        self.verbose = verbose

        self.session_open = self._parse_time(model.strat["session_open_utc"])
        self.session_close = self._parse_time(model.strat["session_close_utc"])
        self.window_min = model.strat["window_min"]
        self.break_buffer = model.strat["break_buffer"]
        # stop_kind="alpha": dynamic stop = alpha × range, clamped [stop_min, stop_max]
        # stop_kind="fixed" (v1 default): stop_pct fixed from model
        self.stop_kind = model.strat.get("stop_kind", "fixed")
        if self.stop_kind == "alpha":
            self.stop_alpha = float(model.strat["stop_alpha"])
            self.stop_min_pct = float(model.strat["stop_min_pct"])
            self.stop_max_pct = float(model.strat["stop_max_pct"])
            self.stop_pct = None
        else:
            self.stop_pct = float(model.strat["stop_pct"])
        self.max_wait_retest_min = model.strat["max_wait_retest_min"]
        self.max_wait_retest_bars_5m = self.max_wait_retest_min // 5
        self.fee_maker = model.fees["maker"]
        self.fee_taker = model.fees["taker"]

        self.state = "IDLE"
        self.current_date = None
        self.armed: Optional[ArmedOrder] = None
        self.pos: Optional[Position] = None

        self.first_bars: List[dict] = []
        self.post_bars: List[dict] = []
        self.trades: List[dict] = []
        self.decisions: List[dict] = []

        # context features set by on_session_context()
        self._ctx: Dict[str, float] = {}
        self._ctx_date = None
        self._feat_partial: Dict[str, float] = {}

    def _stop_pct_for(self, range_first_pct: float) -> float:
        """Return effective stop fraction given the first-30m range (in %)."""
        if self.stop_kind == "alpha":
            raw = self.stop_alpha * (range_first_pct / 100.0)
            return max(self.stop_min_pct, min(self.stop_max_pct, raw))
        return self.stop_pct

    @staticmethod
    def _parse_time(s: str) -> dtime:
        h, m = s.split(":")
        return dtime(int(h), int(m))

    def _end_first_time(self) -> dtime:
        total = self.session_open.hour * 60 + self.session_open.minute + self.window_min
        return dtime(total // 60, total % 60)

    def on_session_context(self, date, ctx: Dict[str, float]) -> None:
        """Call BEFORE session starts. Provides contextual features for the day."""
        self._ctx = ctx
        self._ctx_date = date

    def on_candle_5m(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float) -> None:
        t = ts.time()
        date = ts.date()

        # skip weekends
        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            if self.state != "IDLE":
                self._close_session(force_time_exit=True, ts=ts, price=c)
            return

        # outside session
        if t < self.session_open or t >= self.session_close:
            if self.state != "IDLE":
                self._close_session(force_time_exit=(t >= self.session_close), ts=ts, price=c)
            return

        # new day?
        if self.current_date != date:
            self._reset_for_new_session(date)

        end_first = self._end_first_time()

        if t < end_first:
            self.state = "COLLECTING"
            self.first_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            return

        # t >= end_first
        if self.state == "COLLECTING":
            self._decide(ts, o, h, l, c)
            self.post_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            if self.state == "WAITING_BREAK":
                self._check_break_and_retest(ts, o, h, l, c)
            return

        if self.state in ("WAITING_BREAK", "WAITING_RETEST"):
            self.post_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            self._check_break_and_retest(ts, o, h, l, c)
            return

        if self.state == "IN_POSITION":
            self.post_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            self._check_in_position(ts, o, h, l, c)
            return

        # CLOSED: accumulate silently until session resets

    # --- private ---

    def _reset_for_new_session(self, date) -> None:
        self.current_date = date
        self.armed = None
        self.pos = None
        self.first_bars = []
        self.post_bars = []
        self.state = "IDLE"

    def _decide(self, ts, o, h, l, c) -> None:
        """First bar after 14:00 UTC. Evaluates setup and arms order if valid."""
        if not self._ctx:
            self.state = "IDLE"
            self.decisions.append({"date": self.current_date, "decision": "skip_no_ctx"})
            return

        if len(self.first_bars) < 3:
            self.state = "IDLE"
            self.decisions.append({"date": self.current_date, "decision": "skip_short_first"})
            return

        high_first = max(b["h"] for b in self.first_bars)
        low_first = min(b["l"] for b in self.first_bars)
        sess_open = self.first_bars[0]["o"]

        if high_first - low_first <= 0:
            self.state = "IDLE"
            self.decisions.append({"date": self.current_date, "decision": "skip_zero_range"})
            return

        mid = (high_first + low_first) / 2
        range_first = high_first - low_first
        range_first_pct = range_first / sess_open * 100

        up = (high_first - sess_open) / sess_open
        dn = (sess_open - low_first) / sess_open

        if up >= dn:
            extreme = high_first
            opposite = low_first
            break_level = extreme * (1 + self.break_buffer)
            direction = -1        # short: fade the high
            direction_first = 1.0
        else:
            extreme = low_first
            opposite = high_first
            break_level = extreme * (1 - self.break_buffer)
            direction = 1         # long: fade the low
            direction_first = -1.0

        stop_pct_eff = self._stop_pct_for(range_first_pct)
        self.armed = ArmedOrder(
            date=self.current_date,
            direction=direction,
            entry_price=extreme,
            break_level=break_level,
            stop_price=extreme * (1 + stop_pct_eff) if direction == -1
                       else extreme * (1 - stop_pct_eff),
            mid=mid,
            opposite=opposite,
            session_open_price=sess_open,
            range_first_pct=range_first_pct,
            first_start_ts=ts,
        )
        self._feat_partial = {
            "range_first_30m_pct": range_first_pct,
            "direction_first_30m": direction_first,
            **self._ctx,
        }
        self.state = "WAITING_BREAK"
        self.decisions.append({
            "date": self.current_date, "decision": "armed",
            "direction": direction, "extreme": extreme,
            "range_first_pct": range_first_pct,
        })

    def _check_break_and_retest(self, ts, o, h, l, c) -> None:
        a = self.armed

        if not a.breakouted:
            broke = (
                (a.direction == -1 and h >= a.break_level) or
                (a.direction == 1 and l <= a.break_level)
            )
            if not broke:
                return

            # compute features that depend on the break bar
            if a.direction == -1:
                os_close = (c - a.entry_price) / a.entry_price * 100
            else:
                os_close = (a.entry_price - c) / a.entry_price * 100
            time_to_break_min = (ts - a.first_start_ts).total_seconds() / 60.0

            features = dict(self._feat_partial)
            features["overshoot_close_pct"] = os_close
            features["time_to_break_min"] = time_to_break_min

            p = self.m.predict_proba(features)

            self.decisions.append({
                "date": self.current_date, "decision": "break_p",
                "p_win": float(p), "threshold": self.thr,
                "os_close": os_close, "ttb": time_to_break_min,
            })

            if p < self.thr:
                self.state = "CLOSED"
                self.armed = None
                return

            a.breakouted = True
            a.break_bar_idx = len(self.post_bars) - 1
            a.bars_since_break = 0
            self.state = "WAITING_RETEST"

            # same bar can also be a retest
            retested = (
                (a.direction == -1 and l <= a.entry_price) or
                (a.direction == 1 and h >= a.entry_price)
            )
            if retested:
                self._fill(ts)
            return

        # already broke, waiting for retest
        a.bars_since_break += 1
        if a.bars_since_break > self.max_wait_retest_bars_5m:
            self.state = "CLOSED"
            self.armed = None
            self.decisions.append({"date": self.current_date, "decision": "retest_timeout"})
            return

        retested = (
            (a.direction == -1 and l <= a.entry_price) or
            (a.direction == 1 and h >= a.entry_price)
        )
        if retested:
            self._fill(ts)

    def _fill(self, ts) -> None:
        a = self.armed
        self.pos = Position(
            date=self.current_date,
            direction=a.direction,
            entry_price=a.entry_price,
            stop_price=a.stop_price,
            mid=a.mid,
            opposite=a.opposite,
        )
        self.state = "IN_POSITION"
        self.armed = None
        self.decisions.append({
            "date": self.current_date, "decision": "filled",
            "entry": a.entry_price,
        })
        # the retest bar also gets checked immediately (matches vectorized backtest)
        bar = self.post_bars[-1]
        self._check_in_position(bar["ts"], bar["o"], bar["h"], bar["l"], bar["c"])

    def _check_in_position(self, ts, o, h, l, c) -> None:
        p = self.pos
        # stop first (conservative, matches vectorized backtest)
        stop_hit = (
            (p.direction == -1 and h >= p.stop_price) or
            (p.direction == 1 and l <= p.stop_price)
        )
        if stop_hit:
            if p.hit_tp1:
                exit_price = (p.partial_fill + p.entry_price) / 2
                exit_reason = "trail_stop_be"
            else:
                exit_price = p.stop_price
                exit_reason = "stop"
            self._close_position(ts, exit_price, exit_reason)
            return

        # if/elif prevents TP2 on same bar as TP1 (matches vectorized backtest)
        if not p.hit_tp1:
            if (p.direction == -1 and l <= p.mid) or (p.direction == 1 and h >= p.mid):
                p.hit_tp1 = True
                p.partial_fill = p.mid
                p.stop_price = p.entry_price
        else:
            if (p.direction == -1 and l <= p.opposite) or (p.direction == 1 and h >= p.opposite):
                exit_price = (p.partial_fill + p.opposite) / 2
                self._close_position(ts, exit_price, "tp2")

    def on_tick(self, price: float, ts: pd.Timestamp) -> None:
        """Process a single price tick when IN_POSITION. Caller must hold PaperTrader._lock."""
        if self.state != "IN_POSITION" or self.pos is None:
            return
        p = self.pos

        stop_hit = (
            (p.direction == -1 and price >= p.stop_price) or
            (p.direction == 1 and price <= p.stop_price)
        )
        if stop_hit:
            if p.hit_tp1:
                exit_price = (p.partial_fill + p.entry_price) / 2
                exit_reason = "trail_stop_be"
            else:
                exit_price = p.stop_price
                exit_reason = "stop"
            self._close_position(ts, exit_price, exit_reason)
            return

        if not p.hit_tp1:
            if (p.direction == -1 and price <= p.mid) or (p.direction == 1 and price >= p.mid):
                p.hit_tp1 = True
                p.partial_fill = p.mid
                p.stop_price = p.entry_price
        else:
            if (p.direction == -1 and price <= p.opposite) or (p.direction == 1 and price >= p.opposite):
                exit_price = (p.partial_fill + p.opposite) / 2
                self._close_position(ts, exit_price, "tp2")

    def _close_session(self, force_time_exit: bool, ts, price: float) -> None:
        """Called when session ends. Closes any open position at last price."""
        if self.state == "IN_POSITION" and self.pos is not None:
            p = self.pos
            last_close = self.post_bars[-1]["c"] if self.post_bars else price
            if p.hit_tp1:
                exit_price = (p.partial_fill + last_close) / 2
                exit_reason = "time_partial"
            else:
                exit_price = last_close
                exit_reason = "time"
            self._close_position(ts, exit_price, exit_reason)
        self._reset_for_new_session(None)

    def _close_position(self, ts, exit_price: float, exit_reason: str) -> None:
        p = self.pos
        pnl_gross = p.direction * (exit_price - p.entry_price) / p.entry_price * 100
        m = self.fee_maker * 100
        t = self.fee_taker * 100
        if exit_reason in ("tp", "tp2"):
            exit_fee = m
        elif exit_reason in ("trail_stop_be", "time_partial"):
            exit_fee = 0.5 * m + 0.5 * t
        else:
            exit_fee = t
        fee_total = m + exit_fee
        pnl_net = pnl_gross - fee_total
        self.trades.append({
            "date": pd.Timestamp(p.date),
            "direction": p.direction,
            "entry_price": p.entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pct_net": pnl_net,
        })
        self.state = "CLOSED"
        self.pos = None
