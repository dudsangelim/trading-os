"""R021-A C1 RSI Reversion engine — stateful bar processor."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading.rsi_reversion_paper.config import (
    BLACKLIST_HOURS, BLACKLIST_WEEKDAYS, CONSEC_LOSS_ALERT,
    EXIT_LEVEL, FEE_RT, NOTIONAL, OVERBOUGHT, OVERSOLD,
    PHASE0_MIN_TRADES, PHASE0_MIN_WR, RSI_PERIOD,
)

log = logging.getLogger(__name__)


# ── RSI (Wilder smoothing) ───────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI with Wilder smoothing — matches backtest exactly."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs       = avg_gain / (avg_loss + 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


# ── Resample 1m → 5m ─────────────────────────────────────────────────────────

def resample_1m_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1m OHLCV to 5m bars. Drops the still-open trailing bar."""
    df_5m = df_1m.resample("5min", closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["open"])
    # Drop last bar — may be incomplete
    if len(df_5m) > 1:
        df_5m = df_5m.iloc[:-1]
    return df_5m


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = calc_rsi(df["close"], RSI_PERIOD)
    return df


# ── Engine ────────────────────────────────────────────────────────────────────

class RsiReversionEngine:
    """
    Stateful 5m bar processor for R021-A C1.

    Entry rules (applied at bar close):
      - LONG:  RSI crosses below OVERSOLD (10) — bar i-1 RSI >= 10, bar i RSI < 10
      - SHORT: RSI crosses above OVERBOUGHT (85) — bar i-1 RSI <= 85, bar i RSI > 85
    Blacklist on entry: hour in {13,14,15} or weekday==1 (Tuesday).

    Exit rule (any session, any bar):
      - LONG:  RSI crosses above EXIT_LEVEL (50) — bar i-1 RSI < 50, bar i RSI >= 50
      - SHORT: RSI crosses below EXIT_LEVEL (50) — bar i-1 RSI > 50, bar i RSI <= 50

    Entry price: close of signal bar (same-bar fill — consistent with backtest).
    """

    def __init__(self) -> None:
        self.position:    Optional[str]   = None   # "long" | "short" | None
        self.entry_ts:    Optional[str]   = None
        self.entry_price: Optional[float] = None
        self.entry_rsi:   Optional[float] = None
        self.signal_bar_ts: Optional[str] = None

        self.pnl_total:    float = 0.0
        self.n_trades:     int   = 0
        self.n_wins:       int   = 0
        self.halted:       bool  = False
        self.last_bar_ts:  Optional[str] = None

        self._trade_results: List[bool] = []  # True=win, False=loss

    # ── State persistence ────────────────────────────────────────────────────

    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "position":     self.position,
            "entry_ts":     self.entry_ts,
            "entry_price":  self.entry_price,
            "entry_rsi":    self.entry_rsi,
            "signal_bar_ts": self.signal_bar_ts,
            "pnl_total":    self.pnl_total,
            "n_trades":     self.n_trades,
            "n_wins":       self.n_wins,
            "halted":       self.halted,
            "last_bar_ts":  self.last_bar_ts,
            "trade_results": self._trade_results,
        }

    def load_state_dict(self, d: Dict[str, Any]) -> None:
        self.position     = d.get("position")
        self.entry_ts     = d.get("entry_ts")
        self.entry_price  = d.get("entry_price")
        self.entry_rsi    = d.get("entry_rsi")
        self.signal_bar_ts = d.get("signal_bar_ts")
        self.pnl_total    = float(d.get("pnl_total", 0.0))
        self.n_trades     = int(d.get("n_trades", 0))
        self.n_wins       = int(d.get("n_wins", 0))
        self.halted       = bool(d.get("halted", False))
        self.last_bar_ts  = d.get("last_bar_ts")
        self._trade_results = list(d.get("trade_results", []))

    # ── Main processing loop ─────────────────────────────────────────────────

    def replay_bars(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Process bars newer than last_bar_ts (or all if fresh state).
        Returns list of event dicts (open/close).
        """
        if self.last_bar_ts is not None:
            cutoff = pd.Timestamp(self.last_bar_ts, tz="UTC")
            df = df[df.index > cutoff]
        if df.empty:
            return []

        events: List[Dict[str, Any]] = []
        df_full = df  # we need prev-bar values → work on full df passed in

        for idx in range(len(df)):
            ts  = df.index[idx]
            bar = df.iloc[idx]

            rsi_i = bar.get("rsi")
            if rsi_i is None or (isinstance(rsi_i, float) and np.isnan(rsi_i)):
                self.last_bar_ts = ts.isoformat()
                continue

            # Need previous bar RSI
            if idx == 0:
                # First bar in this batch — check previous bar in df_full context
                # If no prior bar, skip (can't detect crossover)
                self.last_bar_ts = ts.isoformat()
                continue

            rsi_prev = df.iloc[idx - 1].get("rsi")
            if rsi_prev is None or (isinstance(rsi_prev, float) and np.isnan(rsi_prev)):
                self.last_bar_ts = ts.isoformat()
                continue

            event = self._on_closed_bar(ts, bar, float(rsi_i), float(rsi_prev))
            if event:
                events.append(event)

            self.last_bar_ts = ts.isoformat()

        return events

    def _on_closed_bar(
        self,
        ts: pd.Timestamp,
        bar: pd.Series,
        rsi_i: float,
        rsi_prev: float,
    ) -> Optional[Dict[str, Any]]:

        close     = float(bar["close"])
        bar_hour  = ts.hour
        bar_dow   = ts.dayofweek  # 0=Mon, 1=Tue
        ts_str    = ts.isoformat()
        blacklisted = bar_hour in BLACKLIST_HOURS or bar_dow in BLACKLIST_WEEKDAYS

        # ── Exit check (always — any session, any day) ─────────────────────
        if self.position is not None:
            side = self.position
            exit_triggered = False
            if side == "long"  and rsi_prev < EXIT_LEVEL and rsi_i >= EXIT_LEVEL:
                exit_triggered = True
            elif side == "short" and rsi_prev > EXIT_LEVEL and rsi_i <= EXIT_LEVEL:
                exit_triggered = True

            if exit_triggered:
                return self._do_exit(ts_str, close, side, rsi_i)

        # ── Entry check (flat, not halted, not blacklisted) ────────────────
        if self.position is None and not self.halted and not blacklisted:
            long_cross  = rsi_prev >= OVERSOLD   and rsi_i < OVERSOLD    # RSI dips below 10
            short_cross = rsi_prev <= OVERBOUGHT  and rsi_i > OVERBOUGHT  # RSI spikes above 85

            if long_cross:
                return self._do_entry("long", ts_str, close, rsi_i)
            elif short_cross:
                return self._do_entry("short", ts_str, close, rsi_i)

        return None

    def _do_entry(self, side: str, ts_str: str, close: float, rsi: float) -> Dict[str, Any]:
        self.position    = side
        self.entry_ts    = ts_str
        self.entry_price = close
        self.entry_rsi   = rsi
        self.signal_bar_ts = ts_str
        log.info("[OPEN %s] bar=%s entry=%.4f rsi=%.2f", side.upper(), ts_str[:16], close, rsi)
        return {
            "type": "open", "side": side, "ts": ts_str,
            "entry_price": close, "entry_rsi": rsi,
        }

    def _do_exit(self, ts_str: str, close: float, side: str, rsi_exit: float) -> Dict[str, Any]:
        ep       = self.entry_price
        dir_mult = 1 if side == "long" else -1
        gross    = (close - ep) / ep * dir_mult
        net_ret  = gross - FEE_RT
        pnl_usd  = net_ret * NOTIONAL

        self.pnl_total += pnl_usd
        self.n_trades  += 1
        won = pnl_usd > 0
        if won:
            self.n_wins += 1
        self._trade_results.append(won)

        log.info(
            "[CLOSE %s] entry=%.4f exit=%.4f net=%+.3f%% pnl=$%+.2f total=$%+.2f rsi=%.1f",
            side.upper(), ep, close, net_ret * 100, pnl_usd, self.pnl_total, rsi_exit,
        )

        entry_ts    = self.entry_ts
        entry_price = self.entry_price
        entry_rsi   = self.entry_rsi

        self.position    = None
        self.entry_ts    = None
        self.entry_price = None
        self.entry_rsi   = None
        self.signal_bar_ts = None

        # Phase 0 gate
        wr = self.win_rate
        if (self.n_trades >= PHASE0_MIN_TRADES and wr is not None
                and wr < PHASE0_MIN_WR and not self.halted):
            self.halted = True
            log.warning("[phase0] HALTED — WR=%.1f%% after %d trades", wr * 100, self.n_trades)

        consec = self._count_consec_losses()

        return {
            "type": "close", "side": side, "ts": ts_str,
            "entry_ts": entry_ts, "entry_price": entry_price, "entry_rsi": entry_rsi,
            "exit_price": close, "exit_rsi": rsi_exit,
            "net_ret": net_ret, "pnl_usd": pnl_usd, "pnl_total": self.pnl_total,
            "n_trades": self.n_trades, "n_wins": self.n_wins,
            "phase0_halted": self.halted,
            "consec_losses": consec,
        }

    def _count_consec_losses(self) -> int:
        count = 0
        for won in reversed(self._trade_results):
            if not won:
                count += 1
            else:
                break
        return count

    @property
    def win_rate(self) -> Optional[float]:
        return self.n_wins / self.n_trades if self.n_trades > 0 else None

    @property
    def state_str(self) -> str:
        return self.position or "flat"
