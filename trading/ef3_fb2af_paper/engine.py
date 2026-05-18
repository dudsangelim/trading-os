"""EF3 F-B.2.A.f engine — 20-bar H4 breakout + compress <12% + SMA200 + SL 1×ATR14 + TP 8:1.

States: FLAT (0) → PENDING (2) → IN_POSITION (1) → FLAT (0)

PENDING: signal detected at bar t close; waiting for bar t+4h open to enter.
IN_POSITION: entered at open of bar t+4h with fixed SL and TP prices.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import pandas as pd

from trading.ef3_fb2af_paper.config import (
    ATR_SL_MULT, FEE_RT_BPS, INITIAL_CAPITAL, SLIPPAGE_ONE_SIDE, TP_RR,
)

log = logging.getLogger("ef3_fb2af_paper")

VERSION = "1.0.0"

FLAT    = 0
LONG    = 1
PENDING = 2


class Fb2afEngine:
    """
    Stateful paper engine for F-B.2.A.f.
    """

    VERSION = VERSION

    def __init__(self, initial_equity: float = INITIAL_CAPITAL) -> None:
        self.equity:            float                  = initial_equity
        self.position_side:     int                    = FLAT
        self.position_entry_px: Optional[float]        = None
        self.position_entry_ts: Optional[pd.Timestamp] = None
        self.signal_bar_ts:     Optional[pd.Timestamp] = None
        self.pending_atr:       Optional[float]        = None
        self.sl_price:          Optional[float]        = None
        self.tp_price:          Optional[float]        = None
        self.last_bar_ts:       Optional[pd.Timestamp] = None
        self.n_trades:          int                    = 0
        self.trades:            List[Dict[str, Any]]   = []

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "equity":            self.equity,
            "position_side":     self.position_side,
            "position_entry_px": self.position_entry_px,
            "position_entry_ts": self.position_entry_ts.isoformat() if self.position_entry_ts else None,
            "signal_bar_ts":     self.signal_bar_ts.isoformat() if self.signal_bar_ts else None,
            "pending_atr":       self.pending_atr,
            "sl_price":          self.sl_price,
            "tp_price":          self.tp_price,
            "last_bar_ts":       self.last_bar_ts.isoformat() if self.last_bar_ts else None,
            "n_trades":          self.n_trades,
        }

    def load_state_dict(self, d: Dict[str, Any]) -> None:
        self.equity            = float(d.get("equity", self.equity))
        self.position_side     = int(d.get("position_side", FLAT))
        self.position_entry_px = d.get("position_entry_px")
        self.position_entry_ts = _parse_ts(d.get("position_entry_ts"))
        self.signal_bar_ts     = _parse_ts(d.get("signal_bar_ts"))
        self.pending_atr = d.get("pending_atr")
        self.sl_price    = d.get("sl_price")
        self.tp_price    = d.get("tp_price")
        self.last_bar_ts = _parse_ts(d.get("last_bar_ts"))
        self.n_trades = int(d.get("n_trades", 0))

    # ── state string ──────────────────────────────────────────────────────────

    @property
    def state_str(self) -> str:
        if self.position_side == FLAT:
            return "flat"
        if self.position_side == PENDING:
            return "pending"
        return f"long@{self.position_entry_px:.4f}"

    # ── main tick ─────────────────────────────────────────────────────────────

    def replay_bars(
        self,
        df: pd.DataFrame,
        *,
        trade_stale_entries: bool = False,
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        pending = df
        if self.last_bar_ts is not None:
            pending = pending[pending.index > self.last_bar_ts]
        if pending.empty:
            return events

        latest_ts = pending.index[-1]

        for ts, row in pending.iterrows():
            allow_new_signal = trade_stale_entries or (ts == latest_ts)
            event = self._on_closed_bar(ts, row, allow_new_signal=allow_new_signal)
            if event:
                events.append(event)
            self.last_bar_ts = ts
        return events

    def _on_closed_bar(
        self,
        ts: pd.Timestamp,
        bar: pd.Series,
        *,
        allow_new_signal: bool,
    ) -> Optional[Dict[str, Any]]:

        # ── PENDING → IN_POSITION ────────────────────────────────────────────
        if self.position_side == PENDING:
            entry_px = float(bar["open"]) * (1.0 + SLIPPAGE_ONE_SIDE)
            self.position_entry_px = entry_px
            self.position_entry_ts = ts
            self.position_side     = LONG
            self.sl_price = entry_px - ATR_SL_MULT * self.pending_atr
            self.tp_price = entry_px + TP_RR * self.pending_atr
            log.info(
                "[OPEN LONG] entry=%.4f sl=%.4f tp=%.4f ts=%s atr=%.4f eq=$%.2f",
                entry_px, self.sl_price, self.tp_price, ts, self.pending_atr, self.equity,
            )
            return {
                "action":        "open_long",
                "ts":            ts,
                "signal_bar_ts": self.signal_bar_ts,
                "entry_px":      entry_px,
                "sl_price":      self.sl_price,
                "tp_price":      self.tp_price,
                "atr_signal":    self.pending_atr,
                "equity":        round(self.equity, 4),
            }

        # ── IN_POSITION: fixed SL/TP check ───────────────────────────────────
        if self.position_side == LONG:
            curr_open = float(bar["open"])
            curr_high = float(bar["high"])
            curr_low  = float(bar["low"])

            exit_px   = None
            exit_type = None

            if curr_open <= self.sl_price:
                exit_px   = curr_open
                exit_type = "gap_sl"
            elif curr_open >= self.tp_price:
                exit_px   = curr_open
                exit_type = "gap_tp"
            elif curr_low <= self.sl_price and curr_high >= self.tp_price:
                exit_px   = self.sl_price
                exit_type = "both_sl"
            elif curr_low <= self.sl_price:
                exit_px   = self.sl_price
                exit_type = "sl"
            elif curr_high >= self.tp_price:
                exit_px   = self.tp_price
                exit_type = "tp"

            if exit_px is not None:
                return self._close_position(ts, exit_px, exit_type)
            return None

        # ── FLAT → PENDING: check signal ─────────────────────────────────────
        if self.position_side == FLAT and allow_new_signal:
            atr14 = bar.get("atr14")
            if (
                bar.get("breakout") == 1
                and bar.get("compress") == 1
                and bar.get("regime") == 1.0
                and not _isnan(atr14)
            ):
                self.position_side = PENDING
                self.pending_atr   = float(atr14)
                self.signal_bar_ts = ts
                log.info(
                    "[SIGNAL] breakout+compress at %s atr14=%.4f — PENDING",
                    ts, self.pending_atr,
                )
                return {
                    "action":      "signal",
                    "ts":          ts,
                    "pending_atr": self.pending_atr,
                }

        return None

    def _close_position(
        self,
        ts: pd.Timestamp,
        exit_px: float,
        exit_type: str,
    ) -> Dict[str, Any]:
        gross_ret_bps = (exit_px - self.position_entry_px) / self.position_entry_px * 10_000.0
        net_ret_bps   = gross_ret_bps - FEE_RT_BPS
        pnl_usd       = net_ret_bps / 10_000.0 * self.equity
        self.equity  += pnl_usd

        trade = {
            "entry_ts":      self.position_entry_ts.isoformat(),
            "exit_ts":       ts.isoformat(),
            "signal_bar_ts": self.signal_bar_ts.isoformat() if self.signal_bar_ts else None,
            "entry_px":      round(self.position_entry_px, 6),
            "exit_px":       round(exit_px, 6),
            "exit_type":     exit_type,
            "gross_ret_bps": round(gross_ret_bps, 4),
            "net_ret_bps":   round(net_ret_bps, 4),
            "pnl_usd":       round(pnl_usd, 4),
            "equity_after":  round(self.equity, 4),
            "atr_signal":    round(self.pending_atr, 6) if self.pending_atr else None,
            "sl_price":      round(self.sl_price, 6) if self.sl_price else None,
            "tp_price":      round(self.tp_price, 6) if self.tp_price else None,
        }
        self.trades.append(trade)
        self.n_trades += 1

        log.info(
            "[CLOSE LONG] entry=%.4f exit=%.4f type=%s net=%+.2fbps pnl=$%+.2f eq=$%.2f",
            trade["entry_px"], trade["exit_px"], exit_type,
            net_ret_bps, pnl_usd, self.equity,
        )

        self.position_side     = FLAT
        self.position_entry_px = None
        self.position_entry_ts = None
        self.signal_bar_ts     = None
        self.pending_atr       = None
        self.sl_price          = None
        self.tp_price          = None

        return {"action": "close_long", "trade": trade, "ts": ts}


def _isnan(v: Any) -> bool:
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def _parse_ts(v: Any) -> "Optional[pd.Timestamp]":
    if not v:
        return None
    ts = pd.Timestamp(v)
    return ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
