"""taker_cap paper engine — live execution state machine.

Verbatim port of swing_wf_paper.engine.Sleeve (proven identical to the research
run_backtest for signal-exit configs in swing_wf_paper/verify_fidelity.py:
100% side-match, corr 1.0000). taker_cap uses only max-hold exits (fixed 4-bar
hold), so the stop/target/trail branches below are inert-except-maxhold.
Non-overlap fidelity: the research event definition blocks a new signal on the
same bar an exit triggers on — main.py masks long_entry when the sleeve was
holding at the start of the bar, reproducing that exactly.

Fills: signal on the just-closed bar -> fill at the OPEN of the next bar
(the in-progress bar's real open), tick-rounded with adverse slippage.
USD accounting (fees + funding) matches the validated backtest.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from trading.taker_cap_paper import config as C
from trading.taker_cap_paper import exchange as X

log = logging.getLogger("taker_cap_paper")


@dataclass
class Sleeve:
    symbol: str
    tick: float
    step: float
    min_notional: float
    equity: float                      # USD
    pos: int = 0                       # +1 long, -1 short, 0 flat
    qty: float = 0.0
    entry_px: float = 0.0
    entry_ts: Optional[str] = None
    entry_ts_ms: Optional[int] = None
    entry_notional: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    trail: float = 0.0
    bars_held: int = 0
    last_bar_ts: Optional[str] = None
    realized_pnl: float = 0.0
    n_trades: int = 0
    n_wins: int = 0
    consec_losses: int = 0
    peak_equity: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    # -- sizing ---------------------------------------------------------------
    def _size(self, fill_px: float) -> float:
        raw = self.equity * C.LEVERAGE / fill_px
        return X.round_qty(raw, self.step)

    # -- the per-bar state machine (port of run_backtest) ---------------------
    def process_bar(self, j, prev, sig_j, atr_j, atr_prev, next_open,
                    next_open_ts_ms, eopt, allow_short, funding_fn):
        """j/prev: dict-like rows with open/high/low/close. sig_j: dict with
        long_entry/long_exit/short_entry/short_exit. funding_fn(entry_ts, close_ms)
        returns the summed funding rate over the holding window.
        Returns a list of event dicts for notification."""
        sl_atr = eopt.get("sl_atr", 0.0)
        tp_atr = eopt.get("tp_atr", 0.0)
        trail_atr = eopt.get("trail_atr", 0.0)
        max_hold = eopt.get("max_hold", 0)
        events = []

        if self.pos != 0:
            self.bars_held += 1
            # trailing update from the previous bar
            if trail_atr > 0:
                if self.pos == 1:
                    t = prev["high"] - trail_atr * atr_prev
                    self.trail = max(self.trail, t) if self.trail > 0 else t
                    self.stop = max(self.stop, self.trail) if self.stop > 0 else self.trail
                else:
                    t = prev["low"] + trail_atr * atr_prev
                    self.trail = min(self.trail, t) if self.trail > 0 else t
                    self.stop = min(self.stop, self.trail) if self.stop > 0 else self.trail

            exit_px = exit_reason = None
            if self.pos == 1:
                if sl_atr > 0 and self.stop > 0 and j["low"] <= self.stop:
                    exit_px, exit_reason = min(j["open"], self.stop), "stop"
                elif tp_atr > 0 and self.target > 0 and j["high"] >= self.target:
                    exit_px, exit_reason = max(j["open"], self.target), "target"
                elif sig_j["long_exit"] or (sig_j["short_entry"] and allow_short):
                    exit_px, exit_reason = next_open, "signal"
                elif max_hold > 0 and self.bars_held >= max_hold:
                    exit_px, exit_reason = next_open, "maxhold"
            else:
                if sl_atr > 0 and self.stop > 0 and j["high"] >= self.stop:
                    exit_px, exit_reason = max(j["open"], self.stop), "stop"
                elif tp_atr > 0 and self.target > 0 and j["low"] <= self.target:
                    exit_px, exit_reason = min(j["open"], self.target), "target"
                elif sig_j["short_exit"] or sig_j["long_entry"]:
                    exit_px, exit_reason = next_open, "signal"
                elif max_hold > 0 and self.bars_held >= max_hold:
                    exit_px, exit_reason = next_open, "maxhold"

            if exit_px is not None:
                events.append(self._close(exit_px, exit_reason, j, funding_fn))

        # entry (only when flat)
        if self.pos == 0 and next_open is not None:
            side = None
            if sig_j["long_entry"]:
                side = 1
            elif allow_short and sig_j["short_entry"]:
                side = -1
            if side is not None:
                ev = self._open(side, next_open, atr_j, sl_atr, tp_atr, trail_atr, next_open_ts_ms)
                if ev:
                    events.append(ev)
        return events

    def _open(self, side, raw_open, atr_j, sl_atr, tp_atr, trail_atr, exec_ts_ms):
        order_side = "buy" if side == 1 else "sell"
        slip = C.SLIP_BPS / 1e4
        fill = raw_open * (1 + slip) if side == 1 else raw_open * (1 - slip)
        fill = X.round_price(fill, self.tick, order_side)
        qty = self._size(fill)
        notional = qty * fill
        if qty <= 0 or notional < self.min_notional:
            log.warning("%s entry skipped: notional %.2f < min %.2f", self.symbol, notional, self.min_notional)
            return None
        fee = notional * C.FEE_BPS / 1e4
        self.equity -= fee
        self.pos = side
        self.qty = qty
        self.entry_px = fill
        self.entry_notional = notional
        self.entry_ts_ms = exec_ts_ms
        self.entry_ts = pd.to_datetime(exec_ts_ms, unit="ms").strftime("%Y-%m-%d %H:%M") if exec_ts_ms else None
        self.bars_held = 0
        a = atr_j
        self.stop = (fill - sl_atr * a) if (side == 1 and sl_atr > 0) else \
                    (fill + sl_atr * a) if (side == -1 and sl_atr > 0) else 0.0
        self.target = (fill + tp_atr * a) if (side == 1 and tp_atr > 0) else \
                      (fill - tp_atr * a) if (side == -1 and tp_atr > 0) else 0.0
        self.trail = (fill - trail_atr * a) if (side == 1 and trail_atr > 0) else \
                     (fill + trail_atr * a) if (side == -1 and trail_atr > 0) else 0.0
        return {"type": "open", "symbol": self.symbol, "side": "long" if side == 1 else "short",
                "px": fill, "qty": qty, "notional": notional, "fee": fee}

    def _close(self, raw_exit, reason, j, funding_fn):
        side = self.pos
        order_side = "sell" if side == 1 else "buy"
        if reason in ("signal", "maxhold"):
            slip = C.SLIP_BPS / 1e4
            raw_exit = raw_exit * (1 - slip) if side == 1 else raw_exit * (1 + slip)
        fill = X.round_price(raw_exit, self.tick, order_side)
        price_pnl = side * (fill - self.entry_px) * self.qty
        fee = self.qty * fill * C.FEE_BPS / 1e4
        funding_cost = 0.0
        if C.FUNDING_ENABLED and funding_fn is not None and self.entry_ts_ms:
            frate = funding_fn(self.entry_ts_ms, j["close_ms"])
            funding_cost = side * frate * self.entry_notional   # long pays positive
        net = price_pnl - fee - funding_cost
        self.equity += net
        self.realized_pnl += net
        self.n_trades += 1
        win = net > 0
        self.n_wins += int(win)
        self.consec_losses = 0 if win else self.consec_losses + 1
        ev = {"type": "close", "symbol": self.symbol, "side": "long" if side == 1 else "short",
              "entry_px": self.entry_px, "exit_px": fill, "qty": self.qty,
              "pnl": net, "pnl_pct": net / self.entry_notional * 100 if self.entry_notional else 0,
              "reason": reason, "fee": fee, "funding": funding_cost,
              "bars_held": self.bars_held, "equity": self.equity}
        # reset position
        self.pos = 0
        self.qty = 0.0
        self.entry_px = 0.0
        self.entry_notional = 0.0
        self.entry_ts = None
        self.entry_ts_ms = None
        self.stop = self.target = self.trail = 0.0
        self.bars_held = 0
        return ev

    def mark_equity(self, mark_px: float) -> float:
        if self.pos == 0:
            return self.equity
        return self.equity + self.pos * (mark_px - self.entry_px) * self.qty
