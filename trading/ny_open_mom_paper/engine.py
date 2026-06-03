"""
StrategyEngine — FSM for the NY Open Momentum Long strategy.

Signal: upside break of 30m NY range with strong close (os_close >= OS_THR_PCT).
Entry: LIMIT LONG at range high on retest.
Exit: TP1 = entry + range/2, TP2 = entry + range (TP_MULT=1.0).
      Stop = entry × (1 - stop_pct × SL_MULT) where stop_pct = alpha × range_pct (clamped).
      TP1 hit → partial fill, stop moves to entry (breakeven).

States:
  IDLE           — outside session or already traded today
  COLLECTING     — 13:30–14:00 UTC accumulating first 30m high/low
  WAITING_BREAK  — armed, watching for upside break
  WAITING_RETEST — break accepted (os_close >= thr), waiting for price to retest range high
  IN_POSITION    — filled, monitoring stop/TP1/TP2
  CLOSED         — position exited, waiting for session end to reset
  HALTED         — Phase 0 gate triggered (WR < floor after min_trades)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time as dtime
from typing import List, Optional

import pandas as pd

from trading.ny_open_mom_paper.config import (
    BREAK_BUFFER, FEE_MAKER, FEE_TAKER, MAX_RETEST_MIN, OS_THR_PCT,
    PHASE0_MIN_TRADES, PHASE0_WR_FLOOR, SESSION_CLOSE_UTC, SESSION_OPEN_UTC,
    SL_MULT, STOP_ALPHA, STOP_MAX_PCT, STOP_MIN_PCT, TP_MULT, WINDOW_MIN,
)


@dataclass
class Position:
    date: object
    entry_price: float
    stop_price: float
    tp1: float
    tp2: float
    hit_tp1: bool = False
    partial_fill: Optional[float] = None


@dataclass
class ArmedOrder:
    date: object
    high_first: float       # 30m high = LIMIT entry price
    low_first: float        # 30m low
    range_first: float      # high - low
    range_pct: float        # range / session_open * 100
    break_level: float      # high × (1 + break_buffer)
    stop_price: float       # entry × (1 - stop_pct × sl_mult)
    tp1: float              # entry + range × tp_mult / 2
    tp2: float              # entry + range × tp_mult
    first_start_ts: pd.Timestamp
    breakouted: bool = False
    bars_since_break: int = 0


class StrategyEngine:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

        h, m = SESSION_OPEN_UTC.split(":")
        self._session_open = dtime(int(h), int(m))
        h, m = SESSION_CLOSE_UTC.split(":")
        self._session_close = dtime(int(h), int(m))

        self.state = "IDLE"
        self.current_date = None
        self.armed: Optional[ArmedOrder] = None
        self.pos: Optional[Position] = None
        self.first_bars: list = []

        self.trades: List[dict] = []
        self.decisions: List[dict] = []

        # Phase 0 counters (restored from state.json on startup)
        self.total_trades: int = 0
        self.total_wins: int = 0
        self._halted: bool = False

    # ------------------------------------------------------------------
    # State restoration
    # ------------------------------------------------------------------

    def restore_phase0(self, total_trades: int, total_wins: int, halted: bool) -> None:
        self.total_trades = total_trades
        self.total_wins = total_wins
        self._halted = halted
        if halted:
            self.state = "HALTED"

    def to_runtime_state(self) -> dict:
        return {
            "state": self.state,
            "current_date": str(self.current_date) if self.current_date else None,
            "armed": self._serialize_armed(),
            "position": self._serialize_position(),
            "first_bars": [
                {**bar, "ts": bar["ts"].isoformat()} for bar in self.first_bars
            ],
        }

    def load_runtime_state(self, state: dict) -> None:
        self.state = state.get("state", self.state)
        current_date = state.get("current_date")
        self.current_date = pd.Timestamp(current_date).date() if current_date else None

        armed = state.get("armed")
        self.armed = self._deserialize_armed(armed) if armed else None

        position = state.get("position")
        self.pos = self._deserialize_position(position) if position else None

        self.first_bars = []
        for bar in state.get("first_bars", []):
            self.first_bars.append({
                **bar,
                "ts": pd.Timestamp(bar["ts"]),
            })

        if self._halted:
            self.state = "HALTED"

    def _serialize_armed(self) -> Optional[dict]:
        if self.armed is None:
            return None
        data = asdict(self.armed)
        data["date"] = str(self.armed.date)
        data["first_start_ts"] = self.armed.first_start_ts.isoformat()
        return data

    def _serialize_position(self) -> Optional[dict]:
        if self.pos is None:
            return None
        data = asdict(self.pos)
        data["date"] = str(self.pos.date)
        return data

    @staticmethod
    def _deserialize_armed(data: dict) -> ArmedOrder:
        return ArmedOrder(
            date=pd.Timestamp(data["date"]).date(),
            high_first=float(data["high_first"]),
            low_first=float(data["low_first"]),
            range_first=float(data["range_first"]),
            range_pct=float(data["range_pct"]),
            break_level=float(data["break_level"]),
            stop_price=float(data["stop_price"]),
            tp1=float(data["tp1"]),
            tp2=float(data["tp2"]),
            first_start_ts=pd.Timestamp(data["first_start_ts"]),
            breakouted=bool(data.get("breakouted", False)),
            bars_since_break=int(data.get("bars_since_break", 0)),
        )

    @staticmethod
    def _deserialize_position(data: dict) -> Position:
        return Position(
            date=pd.Timestamp(data["date"]).date(),
            entry_price=float(data["entry_price"]),
            stop_price=float(data["stop_price"]),
            tp1=float(data["tp1"]),
            tp2=float(data["tp2"]),
            hit_tp1=bool(data.get("hit_tp1", False)),
            partial_fill=(
                float(data["partial_fill"])
                if data.get("partial_fill") is not None else None
            ),
        )

    # ------------------------------------------------------------------
    # Main bar handler
    # ------------------------------------------------------------------

    def on_candle_5m(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float) -> None:
        if self._halted:
            return

        t = ts.time()
        date = ts.date()

        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            if self.state not in ("IDLE", "CLOSED", "HALTED"):
                self._close_session(ts, c)
            return

        if t < self._session_open or t >= self._session_close:
            if self.state not in ("IDLE", "CLOSED", "HALTED"):
                self._close_session(ts, c)
            return

        if self.current_date != date:
            self._reset_for_new_session(date)

        end_first = self._end_first_time()

        if t < end_first:
            self.state = "COLLECTING"
            self.first_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            return

        if self.state == "COLLECTING":
            self._decide(ts, o, h, l, c)
            if self.state == "WAITING_BREAK":
                self._check_break_and_retest(ts, o, h, l, c)
            return

        if self.state in ("WAITING_BREAK", "WAITING_RETEST"):
            self._check_break_and_retest(ts, o, h, l, c)
            return

        if self.state == "IN_POSITION":
            self._check_in_position(ts, o, h, l, c)
            return

        # CLOSED: accumulate silently until session resets

    # ------------------------------------------------------------------
    # Tick handler (called from tick loop when IN_POSITION)
    # ------------------------------------------------------------------

    def on_tick(self, price: float, ts: pd.Timestamp) -> None:
        if self.state != "IN_POSITION" or self.pos is None:
            return
        p = self.pos

        # Stop-limit: executes at exact stop_price (BE after TP1)
        if price <= p.stop_price:
            if p.hit_tp1:
                exit_price = (p.partial_fill + p.entry_price) / 2
                exit_reason = "trail_stop_be"
            else:
                exit_price = p.stop_price
                exit_reason = "stop"
            self._close_position(ts, exit_price, exit_reason)
            return

        if not p.hit_tp1:
            if price >= p.tp1:
                p.hit_tp1 = True
                p.partial_fill = p.tp1
                p.stop_price = p.entry_price  # move stop to BE
                # if price already above TP2 on the same tick, both limits filled
                if price >= p.tp2:
                    exit_price = (p.partial_fill + p.tp2) / 2
                    self._close_position(ts, exit_price, "tp2")
        else:
            if price >= p.tp2:
                exit_price = (p.partial_fill + p.tp2) / 2
                self._close_position(ts, exit_price, "tp2")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _end_first_time(self) -> dtime:
        total = self._session_open.hour * 60 + self._session_open.minute + WINDOW_MIN
        return dtime(total // 60, total % 60)

    def _stop_pct(self, range_pct: float) -> float:
        raw = STOP_ALPHA * (range_pct / 100.0)
        return max(STOP_MIN_PCT, min(STOP_MAX_PCT, raw))

    def _reset_for_new_session(self, date) -> None:
        self.current_date = date
        self.armed = None
        self.pos = None
        self.first_bars = []
        if not self._halted:
            self.state = "IDLE"

    def _decide(self, ts, o, h, l, c) -> None:
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

        range_first = high_first - low_first
        range_pct = range_first / sess_open * 100

        stop_pct_eff = self._stop_pct(range_pct) * SL_MULT
        break_level = high_first * (1 + BREAK_BUFFER)
        tp1 = high_first + range_first * TP_MULT / 2
        tp2 = high_first + range_first * TP_MULT

        self.armed = ArmedOrder(
            date=self.current_date,
            high_first=high_first,
            low_first=low_first,
            range_first=range_first,
            range_pct=range_pct,
            break_level=break_level,
            stop_price=high_first * (1 - stop_pct_eff),
            tp1=tp1,
            tp2=tp2,
            first_start_ts=ts,
        )
        self.state = "WAITING_BREAK"
        self.decisions.append({
            "date": self.current_date,
            "decision": "armed",
            "high_first": round(high_first, 4),
            "low_first": round(low_first, 4),
            "range_pct": round(range_pct, 4),
            "break_level": round(break_level, 4),
            "stop_price": round(high_first * (1 - stop_pct_eff), 4),
            "tp1": round(tp1, 4),
            "tp2": round(tp2, 4),
        })

    def _check_break_and_retest(self, ts, o, h, l, c) -> None:
        a = self.armed

        if not a.breakouted:
            if h < a.break_level:
                return

            os_close = (c - a.high_first) / a.high_first * 100
            ttb = (ts - a.first_start_ts).total_seconds() / 60.0

            self.decisions.append({
                "date": self.current_date,
                "decision": "break_detected",
                "os_close": round(os_close, 4),
                "os_thr": OS_THR_PCT,
                "ttb_min": round(ttb, 1),
                "accept": os_close >= OS_THR_PCT,
            })

            if os_close < OS_THR_PCT:
                self.state = "CLOSED"
                self.armed = None
                return

            a.breakouted = True
            a.bars_since_break = 0
            self.state = "WAITING_RETEST"

            # same bar can also be the retest (price came back to high_first)
            if l <= a.high_first:
                self._fill(ts, o, h, l, c)
            return

        # already broke — waiting for retest
        a.bars_since_break += 1
        max_bars = MAX_RETEST_MIN // 5
        if a.bars_since_break > max_bars:
            self.state = "CLOSED"
            self.armed = None
            self.decisions.append({"date": self.current_date, "decision": "retest_timeout"})
            return

        if l <= a.high_first:
            self._fill(ts, o, h, l, c)

    def _fill(self, ts, o, h, l, c) -> None:
        a = self.armed
        self.pos = Position(
            date=self.current_date,
            entry_price=a.high_first,
            stop_price=a.stop_price,
            tp1=a.tp1,
            tp2=a.tp2,
        )
        self.state = "IN_POSITION"
        self.armed = None
        self.decisions.append({
            "date": self.current_date,
            "decision": "filled",
            "entry": a.high_first,
            "stop": round(a.stop_price, 4),
            "tp1": round(a.tp1, 4),
            "tp2": round(a.tp2, 4),
        })
        # Do NOT check the fill bar here. In live mode the tick loop takes over
        # immediately. Checking the closed bar's OHLC would fire stop/TP on prices
        # that occurred BEFORE the limit order was placed.

    def _check_in_position(self, ts, o, h, l, c) -> None:
        p = self.pos

        # TP1 before stop: for a momentum long the bar breaks upward first, so
        # the high is established before any pullback that could hit the stop.
        # Checking TP1 first ensures BE is set before the stop is evaluated,
        # preventing a same-bar "stop" exit when price actually reached TP1.
        tp1_just_hit = False
        if not p.hit_tp1:
            if h >= p.tp1:
                p.hit_tp1 = True
                p.partial_fill = p.tp1
                p.stop_price = p.entry_price  # move stop to breakeven
                tp1_just_hit = True

        # Stop check uses the updated stop_price (BE if TP1 was just set)
        if l <= p.stop_price:
            if p.hit_tp1:
                exit_price = (p.partial_fill + p.entry_price) / 2
                exit_reason = "trail_stop_be"
            else:
                exit_price = p.stop_price
                exit_reason = "stop"
            self._close_position(ts, exit_price, exit_reason)
            return

        # TP2 deferred to the next bar after TP1 (prevents same-bar TP1+TP2)
        if p.hit_tp1 and not tp1_just_hit:
            if h >= p.tp2:
                exit_price = (p.partial_fill + p.tp2) / 2
                self._close_position(ts, exit_price, "tp2")

    def _close_session(self, ts, price: float) -> None:
        if self.state == "IN_POSITION" and self.pos is not None:
            p = self.pos
            if p.hit_tp1:
                exit_price = (p.partial_fill + price) / 2
                exit_reason = "time_partial"
            else:
                exit_price = price
                exit_reason = "time"
            self._close_position(ts, exit_price, exit_reason)
        self._reset_for_new_session(None)

    def _close_position(self, ts, exit_price: float, exit_reason: str) -> None:
        p = self.pos
        pnl_gross = (exit_price - p.entry_price) / p.entry_price * 100

        # Entry: LIMIT order (passive retest) → maker fee
        # Exits: LIMIT TP orders and stop-limit → maker fee
        # Exception: time/time_partial = forced market close at session end → taker
        entry_fee_pct = FEE_MAKER * 100
        exit_fee_pct = FEE_TAKER * 100 if exit_reason in ("time", "time_partial") else FEE_MAKER * 100
        pnl_net = pnl_gross - (entry_fee_pct + exit_fee_pct)

        self.total_trades += 1
        if pnl_net > 0:
            self.total_wins += 1

        self.trades.append({
            "date": pd.Timestamp(p.date),
            "entry_price": p.entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pct_net": pnl_net,
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
        })
        self.state = "CLOSED"
        self.pos = None

        # Phase 0 gate
        if self.total_trades >= PHASE0_MIN_TRADES:
            wr = self.total_wins / self.total_trades
            if wr < PHASE0_WR_FLOOR:
                self._halted = True
                self.state = "HALTED"
