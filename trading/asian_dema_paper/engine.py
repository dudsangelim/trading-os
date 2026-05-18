"""
R021-B Asian DEMA paper trader — engine.

Strategy:
  - DEMA(13) × DEMA(34) crossover on 1h bars, Asian session only (00:00–07:59 UTC)
  - Slope confirmation: slope_slow = dema_slow[i] - dema_slow[i-3] must confirm direction
  - Long : dema_fast crosses above dema_slow AND slope_slow > 0
  - Short: dema_fast crosses below dema_slow AND slope_slow < 0
  - Exit : reverse crossover (dema_fast crosses back)
  - Sizing Phase 0: $1000 nominal, no leverage
  - Cost: 13 bps round-trip (FEE_RT)

Data source:
  - el_candles_1m table in jarvis postgres (candle_collector)
  - Aggregated to 1h via pandas resample
"""
from __future__ import annotations

import logging
from datetime import timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading.asian_dema_paper.config import (
    ASIAN_HOURS, CONSEC_LOSS_ALERT, DEMA_FAST, DEMA_SLOW, FEE_RT,
    NOTIONAL, PHASE0_MIN_TRADES, PHASE0_MIN_WR, SLOPE_BARS,
)

log = logging.getLogger("asian_dema_paper")

VERSION = "1.0.0"


# ── DEMA indicators ────────────────────────────────────────────────────────────

def _ema_arr(s: np.ndarray, n: int) -> np.ndarray:
    """Compute EMA on a numpy array. Returns array with NaN for warm-up period."""
    r = np.full(len(s), np.nan)
    a = 2.0 / (n + 1)
    valid = np.where(~np.isnan(s))[0]
    if len(valid) < n:
        return r
    st = valid[0]
    if st + n - 1 >= len(s):
        return r
    r[st + n - 1] = np.nanmean(s[st:st + n])
    for i in range(st + n, len(s)):
        r[i] = a * s[i] + (1 - a) * r[i - 1]
    return r


def _dema(s: np.ndarray, n: int) -> np.ndarray:
    """Double EMA = 2*EMA(n) - EMA(EMA(n))."""
    e1 = _ema_arr(s, n)
    e2 = _ema_arr(e1, n)
    return 2 * e1 - e2


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute DEMA(FAST) and DEMA(SLOW) on 1h closes.
    Returns copy with columns: dema_fast, dema_slow.
    Rows with NaN indicators are kept (caller decides how many to use).
    """
    df = df.copy()
    closes = df["close"].values.astype(float)
    df["dema_fast"] = _dema(closes, DEMA_FAST)
    df["dema_slow"] = _dema(closes, DEMA_SLOW)
    return df


def resample_1m_to_1h(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate 1-minute OHLCV DataFrame to 1-hour bars.
    df_1m must have a UTC DatetimeIndex and columns: open, high, low, close.
    Uses closed='left', label='left' — bar labelled at its open time.
    Drops the currently-open (incomplete) bar.
    """
    df_1h = df_1m.resample("1h", closed="left", label="left").agg(
        open=("open",  "first"),
        high=("high",  "max"),
        low=("low",   "min"),
        close=("close", "last"),
        volume=("volume", "sum") if "volume" in df_1m.columns else ("close", "count"),
    ).dropna(subset=["open", "close"])

    # Drop the last bar — it may still be open/incomplete
    if len(df_1h) > 0:
        df_1h = df_1h.iloc[:-1]

    return df_1h


# ── Engine ────────────────────────────────────────────────────────────────────

class AsianDemaEngine:
    """
    Stateful paper engine for R021-B.
    Processes one closed 1h bar at a time (via process_bar).
    State is serialisable to/from a plain dict for JSON persistence.
    """

    VERSION = VERSION

    def __init__(self) -> None:
        # position state
        self.position: Optional[str]   = None      # None, "long", "short"
        self.entry_ts: Optional[str]   = None
        self.entry_price: Optional[float] = None
        self.entry_dema_fast: Optional[float] = None
        self.signal_bar_ts: Optional[str] = None

        # performance
        self.pnl_total: float          = 0.0
        self.trades: List[Dict[str, Any]] = []
        self.n_trades: int             = 0
        self.n_wins: int               = 0

        # internal state for bar sequencing
        self.last_bar_ts: Optional[pd.Timestamp] = None

        # Phase 0 gate
        self.halted: bool              = False

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "position":        self.position,
            "entry_ts":        self.entry_ts,
            "entry_price":     self.entry_price,
            "entry_dema_fast": self.entry_dema_fast,
            "signal_bar_ts":   self.signal_bar_ts,
            "pnl_total":       round(self.pnl_total, 6),
            "trades":          self.trades,
            "n_trades":        self.n_trades,
            "n_wins":          self.n_wins,
            "last_bar_ts":     self.last_bar_ts.isoformat() if self.last_bar_ts else None,
            "halted":          self.halted,
        }

    def load_state_dict(self, d: Dict[str, Any]) -> None:
        self.position        = d.get("position")
        self.entry_ts        = d.get("entry_ts")
        self.entry_price     = d.get("entry_price")
        self.entry_dema_fast = d.get("entry_dema_fast")
        self.signal_bar_ts   = d.get("signal_bar_ts")
        self.pnl_total       = float(d.get("pnl_total", 0.0))
        self.trades          = d.get("trades", [])
        self.n_trades        = int(d.get("n_trades", 0))
        self.n_wins          = int(d.get("n_wins", 0))
        self.halted          = bool(d.get("halted", False))
        last = d.get("last_bar_ts")
        self.last_bar_ts     = pd.Timestamp(last, tz="UTC") if last else None

    # ── main replay ───────────────────────────────────────────────────────────

    def replay_bars(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Process all CLOSED bars in df that come after last_bar_ts.
        df must already have dema_fast and dema_slow columns.
        df index: UTC DatetimeIndex (1h bars, labelled at open time).
        Returns list of event dicts.
        """
        events: List[Dict[str, Any]] = []
        bar_list = list(df.iterrows())

        for idx, (ts, row) in enumerate(bar_list):
            if self.last_bar_ts is not None and ts <= self.last_bar_ts:
                continue
            # Need access to previous bar for crossover detection
            # Use idx to look back within the df
            prev_row = bar_list[idx - 1][1] if idx > 0 else None
            ev = self._on_closed_bar(ts, row, prev_row, df, idx)
            if ev:
                events.append(ev)
            self.last_bar_ts = ts

        return events

    def _on_closed_bar(
        self,
        ts: pd.Timestamp,
        bar: pd.Series,
        prev_bar: Optional[pd.Series],
        df: pd.DataFrame,
        idx: int,
    ) -> Optional[Dict[str, Any]]:
        """Process one closed 1h bar. Returns event dict or None."""
        dema_fast_i = bar.get("dema_fast")
        dema_slow_i = bar.get("dema_slow")

        # Need valid indicators
        if (dema_fast_i is None or dema_slow_i is None
                or np.isnan(dema_fast_i) or np.isnan(dema_slow_i)):
            return None

        if prev_bar is None:
            return None

        dema_fast_prev = prev_bar.get("dema_fast")
        dema_slow_prev = prev_bar.get("dema_slow")
        if (dema_fast_prev is None or dema_slow_prev is None
                or np.isnan(dema_fast_prev) or np.isnan(dema_slow_prev)):
            return None

        # slope_slow: need 3 bars back
        slope_slow = None
        if idx >= SLOPE_BARS:
            ts_3back = df.index[idx - SLOPE_BARS]
            row_3back = df.loc[ts_3back]
            dslow_3back = row_3back.get("dema_slow")
            if dslow_3back is not None and not np.isnan(dslow_3back):
                slope_slow = dema_slow_i - dslow_3back

        close      = float(bar["close"])
        bar_hour   = ts.hour   # bar labelled at open time
        in_session = bar_hour in ASIAN_HOURS

        ts_str = ts.isoformat()

        # ── exit check (always — regardless of session) ─────────────────────
        if self.position is not None:
            side = self.position
            bull_cross_now = dema_fast_i > dema_slow_i and dema_fast_prev <= dema_slow_prev
            bear_cross_now = dema_fast_i < dema_slow_i and dema_fast_prev >= dema_slow_prev

            reverse = (side == "long" and bear_cross_now) or \
                      (side == "short" and bull_cross_now)

            if reverse:
                return self._do_exit(ts_str, close, side)

        # ── entry check (only flat, session, not halted) ────────────────────
        if self.position is None and in_session and not self.halted:
            if slope_slow is None:
                return None  # not enough data for slope

            bull_cross = dema_fast_i > dema_slow_i and dema_fast_prev <= dema_slow_prev
            bear_cross = dema_fast_i < dema_slow_i and dema_fast_prev >= dema_slow_prev

            if bull_cross and slope_slow > 0:
                return self._do_entry("long", ts_str, close, dema_fast_i, dema_slow_i)
            elif bear_cross and slope_slow < 0:
                return self._do_entry("short", ts_str, close, dema_fast_i, dema_slow_i)

        return None

    def _do_entry(
        self, side: str, ts_str: str, close: float,
        dema_fast: float, dema_slow: float,
    ) -> Dict[str, Any]:
        self.position        = side
        self.entry_ts        = ts_str
        self.entry_price     = close
        self.entry_dema_fast = dema_fast
        self.signal_bar_ts   = ts_str

        log.info(
            "[OPEN %s] bar=%s entry=%.4f dema_f=%.4f dema_s=%.4f",
            side.upper(), ts_str[:19], close, dema_fast, dema_slow,
        )

        return {
            "action":     f"open_{side}",
            "ts":         ts_str,
            "side":       side,
            "entry_px":   close,
            "dema_fast":  round(dema_fast, 6),
            "dema_slow":  round(dema_slow, 6),
        }

    def _do_exit(self, ts_str: str, close: float, side: str) -> Dict[str, Any]:
        entry_px  = self.entry_price
        entry_ts  = self.entry_ts

        # signed gross return (before fees)
        if side == "long":
            gross_ret = (close - entry_px) / entry_px
        else:
            gross_ret = (entry_px - close) / entry_px

        net_ret  = gross_ret - FEE_RT
        pnl_usd  = net_ret * NOTIONAL
        pnl_pct  = net_ret * 100.0

        self.pnl_total += pnl_usd
        self.n_trades  += 1
        if pnl_usd > 0:
            self.n_wins += 1

        # Duration in hours
        try:
            entry_dt = pd.Timestamp(entry_ts, tz="UTC")
            exit_dt  = pd.Timestamp(ts_str,   tz="UTC")
            duration_h = max(0, int((exit_dt - entry_dt).total_seconds() / 3600))
        except Exception:
            duration_h = 0

        trade = {
            "side":       side,
            "entry_ts":   entry_ts,
            "exit_ts":    ts_str,
            "entry_px":   round(entry_px, 6),
            "exit_px":    round(close, 6),
            "gross_ret_pct": round(gross_ret * 100.0, 4),
            "net_ret_pct":   round(pnl_pct, 4),
            "pnl_usd":       round(pnl_usd, 4),
            "notional_usd":  NOTIONAL,
            "duration_h":    duration_h,
            "pnl_total_after": round(self.pnl_total, 4),
        }

        self.trades.append(trade)
        self.position        = None
        self.entry_ts        = None
        self.entry_price     = None
        self.entry_dema_fast = None
        self.signal_bar_ts   = None

        log.info(
            "[CLOSE %s] entry=%.4f exit=%.4f net=%+.3f%% pnl=$%+.2f total=$%+.2f",
            side.upper(), trade["entry_px"], trade["exit_px"],
            pnl_pct, pnl_usd, self.pnl_total,
        )

        # ── Phase 0 gate check ─────────────────────────────────────────────
        consec_losses = self._count_consec_losses()
        wr = self.n_wins / self.n_trades if self.n_trades > 0 else 1.0

        halt_triggered = (
            self.n_trades >= PHASE0_MIN_TRADES and wr < PHASE0_MIN_WR
        )
        if halt_triggered and not self.halted:
            self.halted = True

        return {
            "action":         f"close_{side}",
            "ts":             ts_str,
            "side":           side,
            "trade":          trade,
            "pnl_total":      round(self.pnl_total, 4),
            "n_trades":       self.n_trades,
            "n_wins":         self.n_wins,
            "consec_losses":  consec_losses,
            "halt_triggered": halt_triggered,
        }

    def _count_consec_losses(self) -> int:
        """Count trailing consecutive losses in trades list."""
        count = 0
        for tr in reversed(self.trades):
            if tr["pnl_usd"] < 0:
                count += 1
            else:
                break
        return count

    # ── helpers ───────────────────────────────────────────────────────────────

    @property
    def state_str(self) -> str:
        if self.position is None:
            return "flat"
        return f"{self.position}@{self.entry_price:.4f}" if self.entry_price else self.position

    @property
    def win_rate(self) -> float:
        return self.n_wins / self.n_trades if self.n_trades > 0 else 0.0
