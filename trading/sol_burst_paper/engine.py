"""R012 SOL Extreme Burst Reversal engine — stateful 1m-tick processor.

Mecânica (idêntica ao watcher Phase 0 original em
/home/agent/backtests/edge_factory/sol_extreme_burst_reversal_5m/phase0/):

1. Sinal: bar 5m fechado com |ret| > 2% (close vs open). Fade da direção.
2. Entry: open do primeiro candle 1m em t = sig_ts + 5min (próximo bar 5m).
3. TP: entry * (1 ± 0.005). SL: entry * (1 ∓ 0.0025).
4. Timeout: 30 min após entry → exit a close do bar 1m que cruza o timeout.
5. Sem novas entradas enquanto posição aberta (cooldown == timeout).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from trading.sol_burst_paper.config import (
    BAR_MINUTES, FEE_RT_BPS, GATE_MIN_N, GATE_WR,
    NOTIONAL, SIGNAL_THR, SL_FRAC, TIMEOUT_MINUTES, TP_FRAC,
)

log = logging.getLogger(__name__)


def floor_to_5m(ts: pd.Timestamp) -> pd.Timestamp:
    m = (ts.minute // BAR_MINUTES) * BAR_MINUTES
    return ts.replace(minute=m, second=0, microsecond=0)


def aggregate_5m_closed(df_1m: pd.DataFrame, now_utc: datetime) -> pd.DataFrame:
    """Aggregate 1m OHLCV into closed 5m bars (drop the still-open trailing bar)."""
    if df_1m.empty:
        return df_1m
    df = df_1m.resample(f"{BAR_MINUTES}min", closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["open"])
    cutoff = now_utc - timedelta(minutes=BAR_MINUTES)
    df = df[df.index <= cutoff]
    return df


def _pnl_bps(direction: str, entry: float, exit_: float) -> float:
    raw = (exit_ - entry) / entry * 10_000 if direction == "LONG" \
          else (entry - exit_) / entry * 10_000
    return raw - FEE_RT_BPS


class BurstReversalEngine:
    """
    Stateful engine for R012 SOL Burst Reversal.

    State machine:
      - flat → pending_entry (após sinal 5m, aguarda primeiro candle 1m do próximo bar)
      - pending_entry → open (entry confirmado no open do candle 1m)
      - open → flat (TP/SL/timeout via candles 1m subsequentes)
    """

    def __init__(self) -> None:
        # Pending signal (waiting for entry window to open)
        self.pending_signal: Optional[Dict[str, Any]] = None
        # Open position
        self.position:       Optional[str]   = None   # "LONG" | "SHORT" | None
        self.entry_ts:       Optional[str]   = None
        self.entry_price:    Optional[float] = None
        self.tp:             Optional[float] = None
        self.sl:             Optional[float] = None
        self.signal_anchor:  Optional[float] = None
        self.signal_ts:      Optional[str]   = None
        self.slippage_bps:   Optional[float] = None
        # Accounting (bps-based to match R012 backtest)
        self.pnl_total_bps:  float = 0.0
        self.n_trades:       int   = 0
        self.n_wins:         int   = 0
        self.halted:         bool  = False
        # Tracking
        self.last_5m_bar_ts: Optional[str] = None
        self.last_1m_bar_ts: Optional[str] = None
        self._trade_results: List[bool] = []

    # ── State persistence ───────────────────────────────────────────────────

    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "pending_signal": self.pending_signal,
            "position":       self.position,
            "entry_ts":       self.entry_ts,
            "entry_price":    self.entry_price,
            "tp":             self.tp,
            "sl":             self.sl,
            "signal_anchor":  self.signal_anchor,
            "signal_ts":      self.signal_ts,
            "slippage_bps":   self.slippage_bps,
            "pnl_total_bps":  self.pnl_total_bps,
            "n_trades":       self.n_trades,
            "n_wins":         self.n_wins,
            "halted":         self.halted,
            "last_5m_bar_ts": self.last_5m_bar_ts,
            "last_1m_bar_ts": self.last_1m_bar_ts,
            "trade_results":  self._trade_results,
        }

    def load_state_dict(self, d: Dict[str, Any]) -> None:
        self.pending_signal = d.get("pending_signal")
        self.position       = d.get("position")
        self.entry_ts       = d.get("entry_ts")
        self.entry_price    = d.get("entry_price")
        self.tp             = d.get("tp")
        self.sl             = d.get("sl")
        self.signal_anchor  = d.get("signal_anchor")
        self.signal_ts      = d.get("signal_ts")
        self.slippage_bps   = d.get("slippage_bps")
        self.pnl_total_bps  = float(d.get("pnl_total_bps", 0.0))
        self.n_trades       = int(d.get("n_trades", 0))
        self.n_wins         = int(d.get("n_wins", 0))
        self.halted         = bool(d.get("halted", False))
        self.last_5m_bar_ts = d.get("last_5m_bar_ts")
        self.last_1m_bar_ts = d.get("last_1m_bar_ts")
        self._trade_results = list(d.get("trade_results", []))

    @property
    def state_str(self) -> str:
        if self.position is not None:
            return f"open_{self.position.lower()}"
        if self.pending_signal is not None:
            return f"pending_{self.pending_signal['direction'].lower()}"
        return "flat"

    @property
    def win_rate(self) -> Optional[float]:
        return self.n_wins / self.n_trades if self.n_trades > 0 else None

    # ── Main entry ──────────────────────────────────────────────────────────

    def process(self, df_1m: pd.DataFrame, now_utc: datetime) -> List[Dict[str, Any]]:
        """
        Process new 1m candles and freshly closed 5m bars.
        Returns event dicts in chronological order.

        df_1m index: tz-aware UTC timestamps (open_time).
        """
        if df_1m.empty:
            return []

        events: List[Dict[str, Any]] = []

        # Step 1: handle the open position over each new 1m candle
        if self.position is not None:
            sub = df_1m
            if self.last_1m_bar_ts:
                cutoff = pd.Timestamp(self.last_1m_bar_ts, tz="UTC")
                sub = sub[sub.index > cutoff]
            ev = self._step_open_position(sub)
            if ev:
                events.append(ev)

        # Step 2: confirm pending entry if its window opened
        if self.pending_signal is not None and self.position is None:
            sub = df_1m
            entry_open = pd.Timestamp(self.pending_signal["entry_window_start"], tz="UTC")
            sub = sub[sub.index >= entry_open]
            if not sub.empty:
                ev = self._confirm_entry(sub)
                if ev:
                    events.append(ev)
                    # After entry, immediately step through subsequent 1m bars
                    after = df_1m[df_1m.index > pd.Timestamp(self.entry_ts, tz="UTC")]
                    if not after.empty:
                        ev2 = self._step_open_position(after)
                        if ev2:
                            events.append(ev2)

        # Step 3: detect new 5m signals
        if self.position is None and self.pending_signal is None and not self.halted:
            df_5m = aggregate_5m_closed(df_1m, now_utc)
            if not df_5m.empty:
                cutoff = pd.Timestamp(self.last_5m_bar_ts, tz="UTC") if self.last_5m_bar_ts else None
                new_5m = df_5m if cutoff is None else df_5m[df_5m.index > cutoff]
                for ts, bar in new_5m.iterrows():
                    self.last_5m_bar_ts = ts.isoformat()
                    sig = self._detect_signal(ts, bar)
                    if sig is not None:
                        self.pending_signal = sig
                        events.append({"type": "signal", **sig})
                        # Wait for next 1m candle in window — no double-pending
                        break

        # Update last 1m bar tracker
        self.last_1m_bar_ts = df_1m.index[-1].isoformat()

        return events

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _detect_signal(self, ts: pd.Timestamp, bar: pd.Series) -> Optional[Dict[str, Any]]:
        o = float(bar["open"])
        c = float(bar["close"])
        ret = (c - o) / o if o else 0.0
        if ret > SIGNAL_THR:
            direction = "SHORT"
        elif ret < -SIGNAL_THR:
            direction = "LONG"
        else:
            return None
        entry_window_start = ts + timedelta(minutes=BAR_MINUTES)
        return {
            "ts_signal":          ts.isoformat(),
            "bar_open":           o,
            "bar_close":          c,
            "ret_pct":            round(ret * 100, 3),
            "direction":          direction,
            "anchor":             c,
            "entry_window_start": entry_window_start.isoformat(),
        }

    def _confirm_entry(self, df_1m: pd.DataFrame) -> Optional[Dict[str, Any]]:
        first = df_1m.iloc[0]
        ts    = df_1m.index[0]
        direction = self.pending_signal["direction"]
        anchor    = float(self.pending_signal["anchor"])

        entry_price = float(first["open"])
        if direction == "LONG":
            tp = entry_price * (1 + TP_FRAC)
            sl = entry_price * (1 - SL_FRAC)
            slip_bps = (entry_price - anchor) / anchor * 10_000
        else:
            tp = entry_price * (1 - TP_FRAC)
            sl = entry_price * (1 + SL_FRAC)
            slip_bps = (anchor - entry_price) / anchor * 10_000

        self.position      = direction
        self.entry_ts      = ts.isoformat()
        self.entry_price   = entry_price
        self.tp            = tp
        self.sl            = sl
        self.signal_anchor = anchor
        self.signal_ts     = self.pending_signal["ts_signal"]
        self.slippage_bps  = round(slip_bps, 2)
        ev = {
            "type":         "open",
            "direction":    direction,
            "entry_price":  entry_price,
            "entry_ts":     self.entry_ts,
            "tp":           tp,
            "sl":           sl,
            "slippage_bps": self.slippage_bps,
            "anchor":       anchor,
        }
        self.pending_signal = None
        log.info("[OPEN %s] entry=%.4f tp=%.4f sl=%.4f slip=%+.1fbps",
                 direction, entry_price, tp, sl, slip_bps)
        return ev

    def _step_open_position(self, df_1m: pd.DataFrame) -> Optional[Dict[str, Any]]:
        if df_1m.empty or self.position is None:
            return None

        direction   = self.position
        entry_price = float(self.entry_price)
        tp          = float(self.tp)
        sl          = float(self.sl)
        entry_dt    = pd.Timestamp(self.entry_ts, tz="UTC")
        timeout_dt  = entry_dt + timedelta(minutes=TIMEOUT_MINUTES)

        for ts, bar in df_1m.iterrows():
            high  = float(bar["high"])
            low   = float(bar["low"])
            close = float(bar["close"])

            hit_tp = (direction == "LONG"  and high >= tp) or \
                     (direction == "SHORT" and low  <= tp)
            hit_sl = (direction == "LONG"  and low  <= sl) or \
                     (direction == "SHORT" and high >= sl)

            if hit_tp or hit_sl:
                if hit_sl and hit_tp:
                    outcome, exit_px = "SL", sl   # conservative
                elif hit_tp:
                    outcome, exit_px = "TP", tp
                else:
                    outcome, exit_px = "SL", sl
                return self._do_close(ts, exit_px, outcome)

            if ts >= timeout_dt:
                return self._do_close(ts, close, "TIMEOUT")

        return None

    def _do_close(self, ts: pd.Timestamp, exit_px: float, outcome: str) -> Dict[str, Any]:
        direction   = self.position or "?"
        entry_price = float(self.entry_price or 0.0)
        pnl_bps     = _pnl_bps(direction, entry_price, exit_px)
        won         = pnl_bps > 0

        self.pnl_total_bps += pnl_bps
        self.n_trades      += 1
        if won:
            self.n_wins += 1
        self._trade_results.append(won)

        ev = {
            "type":          "close",
            "direction":     direction,
            "entry_price":   entry_price,
            "exit_price":    exit_px,
            "entry_ts":      self.entry_ts,
            "ts_close":      ts.isoformat(),
            "outcome":       outcome,
            "pnl_bps":       pnl_bps,
            "pnl_total_bps": self.pnl_total_bps,
            "n_trades":      self.n_trades,
            "n_wins":        self.n_wins,
            "slippage_bps":  self.slippage_bps,
            "anchor":        self.signal_anchor,
            "signal_ts":     self.signal_ts,
        }

        log.info(
            "[CLOSE %s] %s entry=%.4f exit=%.4f pnl=%+.1fbps total=%+.1fbps",
            direction, outcome, entry_price, exit_px, pnl_bps, self.pnl_total_bps,
        )

        # Reset position state
        self.position      = None
        self.entry_ts      = None
        self.entry_price   = None
        self.tp            = None
        self.sl            = None
        self.signal_anchor = None
        self.signal_ts     = None
        self.slippage_bps  = None

        # Phase 0 gate
        wr = self.win_rate
        if (self.n_trades >= GATE_MIN_N and wr is not None
                and wr < GATE_WR and not self.halted):
            self.halted = True
            ev["phase0_halted"] = True
            log.warning("[phase0] HALTED — WR=%.1f%% after %d trades", wr * 100, self.n_trades)

        ev["consec_losses"] = self._count_consec_losses()
        return ev

    def _count_consec_losses(self) -> int:
        count = 0
        for won in reversed(self._trade_results):
            if not won:
                count += 1
            else:
                break
        return count
