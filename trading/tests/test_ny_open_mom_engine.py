"""
test_ny_open_mom_engine.py

Unit tests for StrategyEngine in ny_open_mom_paper.

Covers:
  A. _check_in_position — candle-level (used as safety net on subsequent bars)
  B. on_tick — tick-level (primary position monitor in live mode)
  C. Fee structure — all limit orders → maker fee; time exit → taker
"""
from __future__ import annotations

import pandas as pd
import pytest

from trading.ny_open_mom_paper.config import FEE_MAKER, FEE_TAKER
from trading.ny_open_mom_paper.engine import Position, StrategyEngine


_DATE = pd.Timestamp("2026-05-28").date()
_BASE_TS = pd.Timestamp("2026-05-28 14:15:00")


def _engine_in_position(
    entry: float = 73136.0,
    stop: float = 72806.89,
    tp1: float = 73334.40,
    tp2: float = 73532.80,
) -> StrategyEngine:
    eng = StrategyEngine()
    eng.current_date = _DATE
    eng.state = "IN_POSITION"
    eng.pos = Position(
        date=_DATE,
        entry_price=entry,
        stop_price=stop,
        tp1=tp1,
        tp2=tp2,
    )
    return eng


# ---------------------------------------------------------------------------
# A. _check_in_position (candle-level, safety net for subsequent bars)
# ---------------------------------------------------------------------------

class TestCheckInPosition:
    def test_tp1_before_stop_same_bar(self):
        """h >= TP1 and l <= original stop → trail_stop_be, NOT original stop."""
        eng = _engine_in_position()
        eng._check_in_position(_BASE_TS, o=73136.0, h=73400.0, l=72739.0, c=73136.0)

        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "trail_stop_be"
        assert trade["exit_price"] == pytest.approx((73334.40 + 73136.0) / 2)

    def test_stop_fires_when_tp1_not_reached(self):
        """h < TP1 and l <= stop → original stop fires at exact stop_price."""
        eng = _engine_in_position()
        eng._check_in_position(_BASE_TS, o=73136.0, h=73200.0, l=72700.0, c=72900.0)

        assert eng.pos is None
        assert eng.trades[-1]["exit_reason"] == "stop"
        assert eng.trades[-1]["exit_price"] == pytest.approx(72806.89)

    def test_tp1_sets_be_no_exit_when_low_above_be(self):
        """h >= TP1 and l > entry → TP1 registered, BE set, position stays open."""
        eng = _engine_in_position()
        eng._check_in_position(_BASE_TS, o=73136.0, h=73400.0, l=73150.0, c=73300.0)

        assert eng.pos is not None
        assert eng.pos.hit_tp1 is True
        assert eng.pos.stop_price == pytest.approx(73136.0)

    def test_tp2_deferred_to_next_bar(self):
        """h >= TP2 on same bar as TP1 → TP1 set, TP2 deferred."""
        eng = _engine_in_position()
        eng._check_in_position(_BASE_TS, o=73136.0, h=73600.0, l=73140.0, c=73500.0)

        assert eng.pos is not None, "TP2 must not fire on same bar as TP1"
        assert eng.pos.hit_tp1 is True
        assert len(eng.trades) == 0

    def test_tp2_fires_on_subsequent_bar(self):
        eng = _engine_in_position()
        # Bar 1: TP1 hit
        eng._check_in_position(_BASE_TS, o=73136.0, h=73400.0, l=73150.0, c=73300.0)
        assert eng.pos.hit_tp1 is True
        # Bar 2: TP2 hit
        eng._check_in_position(_BASE_TS + pd.Timedelta(minutes=5),
                                o=73350.0, h=73600.0, l=73300.0, c=73550.0)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "tp2"
        assert trade["exit_price"] == pytest.approx((73334.40 + 73532.80) / 2)

    def test_trail_stop_be_on_subsequent_bar(self):
        eng = _engine_in_position()
        eng._check_in_position(_BASE_TS, o=73136.0, h=73400.0, l=73150.0, c=73300.0)
        assert eng.pos.hit_tp1 is True
        # Bar 2: low touches entry (BE)
        eng._check_in_position(_BASE_TS + pd.Timedelta(minutes=5),
                                o=73250.0, h=73280.0, l=73100.0, c=73120.0)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "trail_stop_be"
        assert trade["exit_price"] == pytest.approx((73334.40 + 73136.0) / 2)


# ---------------------------------------------------------------------------
# B. on_tick (primary monitor — limit order simulation at exact price levels)
# ---------------------------------------------------------------------------

class TestOnTick:
    def test_stop_fires_at_exact_stop_price(self):
        eng = _engine_in_position()
        eng.on_tick(72806.89, _BASE_TS)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "stop"
        assert trade["exit_price"] == pytest.approx(72806.89)

    def test_stop_does_not_fire_above_stop_price(self):
        eng = _engine_in_position()
        eng.on_tick(72807.0, _BASE_TS)
        assert eng.pos is not None

    def test_tp1_sets_be_no_exit(self):
        eng = _engine_in_position()
        eng.on_tick(73334.40, _BASE_TS)
        assert eng.pos is not None
        assert eng.pos.hit_tp1 is True
        assert eng.pos.stop_price == pytest.approx(73136.0)

    def test_tp2_fires_after_tp1_on_separate_tick(self):
        eng = _engine_in_position()
        eng.on_tick(73334.40, _BASE_TS)                             # TP1
        eng.on_tick(73532.80, _BASE_TS + pd.Timedelta(seconds=1))  # TP2
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "tp2"
        assert trade["exit_price"] == pytest.approx((73334.40 + 73532.80) / 2)

    def test_tp1_and_tp2_same_tick_fires_tp2(self):
        """Price above TP2 on the same tick as TP1 → TP2 executes immediately."""
        eng = _engine_in_position()
        eng.on_tick(73600.0, _BASE_TS)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "tp2"
        assert trade["exit_price"] == pytest.approx((73334.40 + 73532.80) / 2)

    def test_be_stop_fires_after_tp1(self):
        eng = _engine_in_position()
        eng.on_tick(73334.40, _BASE_TS)                             # TP1 → BE
        eng.on_tick(73136.0, _BASE_TS + pd.Timedelta(seconds=1))   # price at BE → trail_stop_be
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "trail_stop_be"
        assert trade["exit_price"] == pytest.approx((73334.40 + 73136.0) / 2)

    def test_original_stop_not_fire_if_tp1_already_hit(self):
        """After TP1, original stop level no longer triggers (BE is higher)."""
        eng = _engine_in_position()
        eng.on_tick(73334.40, _BASE_TS)   # TP1 → stop moves to 73136 (BE)
        # price at 72807 is below original stop (72806.89) but above BE? No — 72807 < 73136
        # So be stop fires (price <= be = 73136)
        eng.on_tick(72807.0, _BASE_TS + pd.Timedelta(seconds=1))
        assert eng.pos is None
        assert eng.trades[-1]["exit_reason"] == "trail_stop_be"

    def test_no_action_below_tp1_above_stop(self):
        eng = _engine_in_position()
        eng.on_tick(73000.0, _BASE_TS)  # between stop and tp1
        assert eng.pos is not None
        assert eng.pos.hit_tp1 is False


# ---------------------------------------------------------------------------
# C. Fee structure — all limit orders use maker; time exit uses taker
# ---------------------------------------------------------------------------

class TestFees:
    def _pnl_gross(self, entry, exit_price):
        return (exit_price - entry) / entry * 100

    def test_stop_uses_maker_fees(self):
        eng = _engine_in_position()
        eng.on_tick(72806.89, _BASE_TS)
        trade = eng.trades[-1]
        gross = self._pnl_gross(73136.0, 72806.89)
        expected_net = gross - (FEE_MAKER + FEE_MAKER) * 100
        assert trade["pnl_pct_net"] == pytest.approx(expected_net, abs=1e-6)

    def test_tp2_uses_maker_fees(self):
        eng = _engine_in_position()
        eng.on_tick(73600.0, _BASE_TS)
        trade = eng.trades[-1]
        exit_price = (73334.40 + 73532.80) / 2
        gross = self._pnl_gross(73136.0, exit_price)
        expected_net = gross - (FEE_MAKER + FEE_MAKER) * 100
        assert trade["pnl_pct_net"] == pytest.approx(expected_net, abs=1e-6)

    def test_trail_stop_be_uses_maker_fees(self):
        eng = _engine_in_position()
        eng.on_tick(73334.40, _BASE_TS)
        eng.on_tick(73136.0, _BASE_TS + pd.Timedelta(seconds=1))
        trade = eng.trades[-1]
        exit_price = (73334.40 + 73136.0) / 2
        gross = self._pnl_gross(73136.0, exit_price)
        expected_net = gross - (FEE_MAKER + FEE_MAKER) * 100
        assert trade["pnl_pct_net"] == pytest.approx(expected_net, abs=1e-6)

    def test_time_exit_uses_taker_for_exit(self):
        """Session end (market close) uses taker fee for the exit leg."""
        eng = _engine_in_position()
        eng._close_session(_BASE_TS, price=73200.0)
        trade = eng.trades[-1]
        gross = self._pnl_gross(73136.0, 73200.0)
        expected_net = gross - (FEE_MAKER + FEE_TAKER) * 100  # entry maker + exit taker
        assert trade["pnl_pct_net"] == pytest.approx(expected_net, abs=1e-6)
