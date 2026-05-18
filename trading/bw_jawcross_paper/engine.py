"""Alligator BTC 6h order-flip engine.

Entry : long only when Alligator flips into bull order
        (lips > teeth > jaw now, and previous closed bar was not bull ordered).
Exit  : close below Lips on a closed candle.
No hard stop-loss. This is a paper-forward candidate, not production capital.

Fills use an explicit execution price supplied by the caller. In live mode this
is the market price observed after the 6h candle is closed.

Position sizing: equity × STAKE_PCT × LEVERAGE (notional per trade).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading.bw_jawcross_paper.config import (
    FEE_RT, JAW_SHIFT, LEVERAGE, LIPS_SHIFT, SLIPPAGE_PCT,
    SMMA_JAW, SMMA_LIPS, SMMA_TEETH, STAKE_PCT, TEETH_SHIFT,
)

log = logging.getLogger("bw_jawcross_paper")

VERSION = "2.0.0"


# ── Indicators ────────────────────────────────────────────────────────────────

def _smma(series: pd.Series, n: int) -> pd.Series:
    vals = series.values.astype(float)
    out  = np.full(len(vals), np.nan)
    s    = n - 1
    while s < len(vals) and np.isnan(vals[s - n + 1 : s + 1]).any():
        s += 1
    if s >= len(vals):
        return pd.Series(out, index=series.index)
    out[s] = np.nanmean(vals[s - n + 1 : s + 1])
    for i in range(s + 1, len(vals)):
        out[i] = (out[i - 1] * (n - 1) + vals[i]) / n
    return pd.Series(out, index=series.index)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute canonical Alligator. Returns copy with NaN rows dropped."""
    df  = df.copy()
    mid = (df["high"] + df["low"]) / 2.0
    df["jaw"]   = _smma(mid, SMMA_JAW).shift(JAW_SHIFT)
    df["teeth"] = _smma(mid, SMMA_TEETH).shift(TEETH_SHIFT)
    df["lips"]  = _smma(mid, SMMA_LIPS).shift(LIPS_SHIFT)
    df["bull_order"] = (df["lips"] > df["teeth"]) & (df["teeth"] > df["jaw"])
    return df.dropna(subset=["jaw", "teeth", "lips"])


# ── Engine ────────────────────────────────────────────────────────────────────

class BwJawCrossEngine:
    """
    Stateful engine — processes one closed configured-timeframe bar at a time.

    State is serialisable to/from a plain dict for JSON persistence.
    The main loop calls replay_bars() with the full recent dataframe each tick;
    the engine skips already-processed bars and handles any missed ones.
    """

    VERSION = VERSION

    def __init__(self, initial_equity: float) -> None:
        self.equity:              float                = initial_equity
        self.position_side:       int                  = 0    # 1=long, -1=short
        self.position_entry_px:   Optional[float]      = None
        self.position_entry_ts:   Optional[pd.Timestamp] = None
        self.position_signal_ts:  Optional[pd.Timestamp] = None
        self.bars_held:           int                  = 0
        self.last_bar_ts:         Optional[pd.Timestamp] = None
        self.n_trades:            int                  = 0
        self.trades:              List[Dict[str, Any]] = []
        self._prev_bull_order:    Optional[bool]       = None

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "equity":             self.equity,
            "position_side":      self.position_side,
            "position_entry_px":  self.position_entry_px,
            "position_entry_ts":  self.position_entry_ts.isoformat() if self.position_entry_ts else None,
            "position_signal_ts": self.position_signal_ts.isoformat() if self.position_signal_ts else None,
            "bars_held":          self.bars_held,
            "last_bar_ts":        self.last_bar_ts.isoformat() if self.last_bar_ts else None,
            "n_trades":           self.n_trades,
            "prev_bull_order":    self._prev_bull_order,
        }

    def load_state_dict(self, d: Dict[str, Any]) -> None:
        self.equity             = float(d.get("equity", self.equity))
        self.position_side      = int(d.get("position_side", 0))
        self.position_entry_px  = d.get("position_entry_px")
        self.position_entry_ts  = (
            pd.Timestamp(d["position_entry_ts"], tz="UTC")
            if d.get("position_entry_ts") else None
        )
        self.position_signal_ts = (
            pd.Timestamp(d["position_signal_ts"], tz="UTC")
            if d.get("position_signal_ts") else None
        )
        self.bars_held          = int(d.get("bars_held", 0))
        self.last_bar_ts        = (
            pd.Timestamp(d["last_bar_ts"], tz="UTC")
            if d.get("last_bar_ts") else None
        )
        self.n_trades           = int(d.get("n_trades", 0))
        prev_bull = d.get("prev_bull_order")
        self._prev_bull_order   = bool(prev_bull) if prev_bull is not None else None

    # ── main tick ─────────────────────────────────────────────────────────────

    def replay_bars(
        self,
        df: pd.DataFrame,
        *,
        execution_price: Optional[float] = None,
        execution_ts: Optional[pd.Timestamp] = None,
        trade_stale_entries: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Process all CLOSED bars in df that come after last_bar_ts.

        df must already have Alligator indicators (jaw, teeth, lips).
        df should NOT contain the currently-open bar (caller drops it).

        execution_price is the executable market price at execution_ts. Entries
        are allowed only on the latest processed bar unless trade_stale_entries
        is explicitly enabled. Exits are allowed on any newly processed bar: if
        the bot was down and later detects a touch, it closes at the current
        execution_price instead of pretending it filled at the historical Lips.

        Returns list of event dicts (open/close actions).
        """
        events: List[Dict[str, Any]] = []
        pending = df
        if self.last_bar_ts is not None:
            pending = pending[pending.index > self.last_bar_ts]
        if pending.empty:
            return events

        exec_ts = None
        if execution_ts is not None:
            exec_ts = pd.Timestamp(execution_ts)
            exec_ts = exec_ts.tz_localize("UTC") if exec_ts.tzinfo is None else exec_ts.tz_convert("UTC")
        latest_ts = pending.index[-1]
        can_execute = execution_price is not None

        for ts, row in pending.iterrows():
            allow_entry = can_execute and (trade_stale_entries or ts == latest_ts)
            allow_exit = can_execute
            event = self._on_closed_bar(
                ts,
                row,
                execution_price=execution_price,
                execution_ts=exec_ts,
                allow_entry=allow_entry,
                allow_exit=allow_exit,
            )
            if event:
                events.append(event)
            self.last_bar_ts = ts
        return events

    def _on_closed_bar(
        self,
        ts: pd.Timestamp,
        bar: pd.Series,
        *,
        execution_price: Optional[float],
        execution_ts: Optional[pd.Timestamp],
        allow_entry: bool,
        allow_exit: bool,
    ) -> Optional[Dict[str, Any]]:
        """Process one closed bar. Returns event dict or None."""
        jaw   = bar["jaw"]; teeth = bar["teeth"]; lips = bar["lips"]
        close = bar["close"]
        bull_order = bool(lips > teeth > jaw)
        fill_ts = execution_ts if execution_ts is not None else ts

        # ── exit check ────────────────────────────────────────────────────────
        if self.position_side != 0:
            side     = self.position_side
            exit_signal = side == 1 and close < lips
            if exit_signal and allow_exit and execution_price is not None:
                exit_px   = execution_price * (1 - side * SLIPPAGE_PCT)  # adverse: long sells lower
                gross_ret = side * (exit_px - self.position_entry_px) / self.position_entry_px
                net_ret   = gross_ret - FEE_RT
                notional  = self.equity * STAKE_PCT * LEVERAGE
                pnl_usd   = net_ret * notional
                self.equity  += pnl_usd

                trade = {
                    "side":         "long" if side == 1 else "short",
                    "entry_ts":     self.position_entry_ts.isoformat(),
                    "exit_ts":      fill_ts.isoformat(),
                    "signal_entry_ts": self.position_signal_ts.isoformat() if self.position_signal_ts else None,
                    "entry_px":     round(self.position_entry_px, 6),
                    "exit_px":      round(exit_px, 6),
                    "bars_held":    self.bars_held,
                    "gross_ret_pct": round(gross_ret * 100, 4),
                    "net_ret_pct":  round(net_ret * 100, 4),
                    "notional_usd": round(notional, 2),
                    "pnl_usd":      round(pnl_usd, 4),
                    "equity_after": round(self.equity, 4),
                    "leverage":     LEVERAGE,
                    "stake_pct":    STAKE_PCT,
                    "signal_exit_ts": ts.isoformat(),
                    "signal_lips":   round(lips, 6),
                    "signal_close":  round(close, 6),
                }
                self.trades.append(trade)
                self.n_trades += 1
                self.position_side = 0
                self.position_entry_px = None
                self.position_entry_ts = None
                self.position_signal_ts = None
                self.bars_held = 0

                log.info(
                    "[CLOSE %s] entry=%.4f exit=%.4f net=%+.3f%% pnl=$%.2f eq=$%.2f",
                    trade["side"], trade["entry_px"], trade["exit_px"],
                    trade["net_ret_pct"], pnl_usd, self.equity,
                )

                self._prev_bull_order = bull_order
                return {"action": f"close_{trade['side']}", "trade": trade, "ts": ts}
            else:
                self.bars_held += 1

        # ── entry check (only when flat) ──────────────────────────────────────
        if self.position_side == 0 and self._prev_bull_order is not None:
            order_flip_long = bull_order and not self._prev_bull_order

            if order_flip_long and allow_entry and execution_price is not None:
                self.position_side     = 1
                self.position_entry_px = execution_price * (1 + SLIPPAGE_PCT)  # adverse: buy higher than market
                self.position_entry_ts = fill_ts
                self.position_signal_ts = ts
                self.bars_held         = 0
                notional               = self.equity * STAKE_PCT * LEVERAGE
                log.info(
                    "[OPEN LONG] fill=%.4f signal_close=%.4f notional=$%.2f jaw=%.4f teeth=%.4f lips=%.4f eq=$%.2f",
                    self.position_entry_px, close, notional, jaw, teeth, lips, self.equity,
                )
                self._prev_bull_order = bull_order
                return {
                    "action":    "open_long",
                    "ts":        fill_ts,
                    "signal_ts": ts,
                    "entry_px":  self.position_entry_px,
                    "signal_close": close,
                    "notional":  round(notional, 2),
                    "jaw":       round(jaw, 4),
                    "teeth":     round(teeth, 4),
                    "lips":      round(lips, 4),
                    "equity":    round(self.equity, 4),
                }

        self._prev_bull_order = bull_order
        return None

    # ── helpers ───────────────────────────────────────────────────────────────

    @property
    def state_str(self) -> str:
        if self.position_side == 0:
            return "flat"
        return f"long@{self.position_entry_px:.4f}" if self.position_side == 1 \
               else f"short@{self.position_entry_px:.4f}"
