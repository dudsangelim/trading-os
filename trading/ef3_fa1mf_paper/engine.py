"""EF3 F-A.1.M.f engine — 30-bar H4 breakout + SMA200 + ATR14×2.0 trailing stop.

States: FLAT (0) → PENDING (2) → IN_POSITION (1) → FLAT (0)

PENDING: signal detected at bar t close; waiting for bar t+4h open to enter.
IN_POSITION: entered at open of bar t+4h; trailing stop managed bar by bar.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from trading.ef3_fa1mf_paper.config import (
    ATR_MULT, FEE_RT_BPS, INITIAL_CAPITAL, SLIPPAGE_ONE_SIDE,
)

log = logging.getLogger("ef3_fa1mf_paper")

VERSION = "1.0.0"

# position_side constants
FLAT     = 0
LONG     = 1
PENDING  = 2


class Fa1mfEngine:
    """
    Stateful paper engine for F-A.1.M.f.

    Processes one closed H4 bar at a time via replay_bars().
    State is serialisable for JSON persistence.
    """

    VERSION = VERSION

    def __init__(self, initial_equity: float = INITIAL_CAPITAL) -> None:
        self.equity:            float                  = initial_equity
        self.position_side:     int                    = FLAT
        self.position_entry_px: Optional[float]        = None
        self.position_entry_ts: Optional[pd.Timestamp] = None
        self.signal_bar_ts:     Optional[pd.Timestamp] = None
        self.pending_atr:       Optional[float]        = None
        self.trailing_stop:     Optional[float]        = None
        self._prev_close:       Optional[float]        = None
        self._prev_atr14:       Optional[float]        = None
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
            "trailing_stop":     self.trailing_stop,
            "prev_close":        self._prev_close,
            "prev_atr14":        self._prev_atr14,
            "last_bar_ts":       self.last_bar_ts.isoformat() if self.last_bar_ts else None,
            "n_trades":          self.n_trades,
        }

    def load_state_dict(self, d: Dict[str, Any]) -> None:
        self.equity            = float(d.get("equity", self.equity))
        self.position_side     = int(d.get("position_side", FLAT))
        self.position_entry_px = d.get("position_entry_px")
        self.position_entry_ts = _parse_ts(d.get("position_entry_ts"))
        self.signal_bar_ts     = _parse_ts(d.get("signal_bar_ts"))
        self.pending_atr   = d.get("pending_atr")
        self.trailing_stop = d.get("trailing_stop")
        self._prev_close   = d.get("prev_close")
        self._prev_atr14   = d.get("prev_atr14")
        self.last_bar_ts   = _parse_ts(d.get("last_bar_ts"))
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
        """
        Process all closed bars in df that come after last_bar_ts.

        df must already have indicator columns (atr14, roll_max, breakout, regime).
        Returns list of event dicts.
        """
        events: List[Dict[str, Any]] = []
        pending = df
        if self.last_bar_ts is not None:
            pending = pending[pending.index > self.last_bar_ts]
        if pending.empty:
            return events

        latest_ts = pending.index[-1]

        for ts, row in pending.iterrows():
            # For PENDING→IN_POSITION transition: always allow (realising a prior decision)
            # For FLAT→PENDING: only on latest bar unless stale entries enabled
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
        """Process one closed bar. Returns event dict or None."""

        # ── PENDING → IN_POSITION ────────────────────────────────────────────
        if self.position_side == PENDING:
            entry_px = float(bar["open"]) * (1.0 + SLIPPAGE_ONE_SIDE)
            self.position_entry_px = entry_px
            self.position_entry_ts = ts
            self.position_side     = LONG
            self.trailing_stop     = entry_px - ATR_MULT * self.pending_atr
            self._prev_close       = float(bar["close"])
            self._prev_atr14       = float(bar["atr14"]) if not _isnan(bar["atr14"]) else self.pending_atr
            log.info(
                "[OPEN LONG] entry=%.4f ts=%s atr=%.4f stop=%.4f eq=$%.2f",
                entry_px, ts, self.pending_atr, self.trailing_stop, self.equity,
            )
            return {
                "action":        "open_long",
                "ts":            ts,
                "signal_bar_ts": self.signal_bar_ts,
                "entry_px":      entry_px,
                "atr_signal":    self.pending_atr,
                "trailing_stop": self.trailing_stop,
                "equity":        round(self.equity, 4),
            }

        # ── IN_POSITION: trailing stop management ────────────────────────────
        if self.position_side == LONG:
            old_stop  = self.trailing_stop
            candidate = self._prev_close - ATR_MULT * self._prev_atr14
            self.trailing_stop = max(self.trailing_stop, candidate)

            curr_open = float(bar["open"])
            exit_px   = None
            exit_type = None

            if self._prev_close < old_stop:
                # Previous bar closed below stop — exit at current open
                exit_px   = curr_open
                exit_type = "trailing_stop"
            elif curr_open < self.trailing_stop:
                # Open gapped below updated stop
                exit_px   = curr_open
                exit_type = "gap_h4"

            if exit_px is not None:
                return self._close_position(ts, bar, exit_px, exit_type)

            # still in position — update prev for next bar
            self._prev_close = float(bar["close"])
            if not _isnan(bar["atr14"]):
                self._prev_atr14 = float(bar["atr14"])
            return None

        # ── FLAT → PENDING: check signal ─────────────────────────────────────
        if self.position_side == FLAT and allow_new_signal:
            atr14 = bar.get("atr14")
            if (
                bar.get("breakout") == 1
                and bar.get("regime") == 1.0
                and not _isnan(atr14)
            ):
                self.position_side = PENDING
                self.pending_atr   = float(atr14)
                self.signal_bar_ts = ts
                log.info(
                    "[SIGNAL] breakout at %s atr14=%.4f — PENDING entry next bar",
                    ts, self.pending_atr,
                )
                return {
                    "action":        "signal",
                    "ts":            ts,
                    "pending_atr":   self.pending_atr,
                }

        return None

    def _close_position(
        self,
        ts: pd.Timestamp,
        bar: pd.Series,
        exit_px: float,
        exit_type: str,
    ) -> Dict[str, Any]:
        gross_ret_bps = (exit_px - self.position_entry_px) / self.position_entry_px * 10_000.0
        net_ret_bps   = gross_ret_bps - FEE_RT_BPS
        pnl_usd       = net_ret_bps / 10_000.0 * self.equity
        self.equity  += pnl_usd

        trade = {
            "entry_ts":       self.position_entry_ts.isoformat(),
            "exit_ts":        ts.isoformat(),
            "signal_bar_ts":  self.signal_bar_ts.isoformat() if self.signal_bar_ts else None,
            "entry_px":       round(self.position_entry_px, 6),
            "exit_px":        round(exit_px, 6),
            "exit_type":      exit_type,
            "gross_ret_bps":  round(gross_ret_bps, 4),
            "net_ret_bps":    round(net_ret_bps, 4),
            "pnl_usd":        round(pnl_usd, 4),
            "equity_after":   round(self.equity, 4),
            "atr_signal":     round(self.pending_atr, 6) if self.pending_atr else None,
            "trailing_stop":  round(self.trailing_stop, 6) if self.trailing_stop else None,
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
        self.trailing_stop     = None
        self._prev_close       = None
        self._prev_atr14       = None

        return {"action": "close_long", "trade": trade, "ts": ts}


def _isnan(v: Any) -> bool:
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def _parse_ts(v: Any) -> Optional[pd.Timestamp]:
    if not v:
        return None
    ts = pd.Timestamp(v)
    return ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
