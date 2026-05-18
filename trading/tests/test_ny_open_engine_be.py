"""
test_ny_open_engine_be.py

Unit tests for the breakeven-stop logic in StrategyEngine._check_in_position().

Scenarios:
  1. TP1 hit → price returns to entry → exit at trail_stop_be
  2. No TP1 → price hits original stop → exit at stop
  3. TP1 hit → TP2 hit → exit at tp2 (BE stop never triggered)
"""
from __future__ import annotations

import types

import pandas as pd
import pytest

from trading.ny_open_paper.engine import Position, StrategyEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_MODEL = types.SimpleNamespace(
    strat={
        "session_open_utc": "13:30",
        "session_close_utc": "20:00",
        "window_min": 30,
        "break_buffer": 0.001,
        "stop_pct": 0.005,
        "max_wait_retest_min": 15,
    },
    fees={"maker": 0.0002, "taker": 0.0005},
    predict_proba=lambda f: 0.9,
)

_DATE = pd.Timestamp("2026-04-24").date()
_BASE_TS = pd.Timestamp("2026-04-24 14:00:00")


def _engine_in_position(
    direction: int = 1,
    entry: float = 100.0,
    stop: float = 99.5,
    mid: float = 101.0,
    opposite: float = 102.0,
) -> StrategyEngine:
    eng = StrategyEngine(_FAKE_MODEL, threshold=0.5)
    eng.current_date = _DATE
    eng.state = "IN_POSITION"
    eng.post_bars = []
    eng.pos = Position(
        date=_DATE,
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        mid=mid,
        opposite=opposite,
    )
    return eng


# ---------------------------------------------------------------------------
# Test 1 — TP1 hit, then price returns to entry → trail_stop_be
# ---------------------------------------------------------------------------

class TestBreakevenAfterTP1:
    def test_stop_moves_to_entry_on_tp1(self):
        """After TP1 is hit, p.stop_price must equal p.entry_price."""
        eng = _engine_in_position()
        ts = _BASE_TS + pd.Timedelta(hours=1, minutes=45)
        # candle whose high (101.5) reaches mid (101.0)
        eng._check_in_position(ts, o=100.5, h=101.5, l=100.3, c=100.9)
        assert eng.pos is not None, "position must still be open after TP1"
        assert eng.pos.hit_tp1 is True
        assert eng.pos.stop_price == pytest.approx(100.0), \
            "stop must move to entry_price (breakeven) after TP1"

    def test_trail_stop_be_fires_when_price_returns_to_entry(self):
        """Price between original stop and entry → only fires after fix."""
        eng = _engine_in_position(
            direction=1, entry=100.0, stop=99.5, mid=101.0, opposite=102.0
        )
        ts1 = _BASE_TS + pd.Timedelta(hours=1, minutes=45)
        eng._check_in_position(ts1, o=100.5, h=101.5, l=100.3, c=100.9)
        assert eng.pos.hit_tp1 is True

        # low=99.8 is ABOVE original stop (99.5) but BELOW entry (100.0)
        # without the fix this would NOT exit; with the fix it must
        ts2 = _BASE_TS + pd.Timedelta(hours=3, minutes=30)
        eng._check_in_position(ts2, o=100.2, h=100.4, l=99.8, c=99.9)

        assert eng.pos is None, "position must be closed at breakeven"
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "trail_stop_be"
        assert trade["exit_price"] == pytest.approx((101.0 + 100.0) / 2)

    def test_trail_stop_be_reason_only_when_be_was_set(self):
        """trail_stop_be must NOT appear without prior TP1 hit."""
        eng = _engine_in_position()
        ts = _BASE_TS + pd.Timedelta(hours=1)
        # low (99.3) hits original stop (99.5) before TP1
        eng._check_in_position(ts, o=99.8, h=100.2, l=99.3, c=99.4)
        assert eng.pos is None
        assert eng.trades[-1]["exit_reason"] == "stop"

    def test_real_trade_2026_04_24_scenario(self):
        """Reproduce 2026-04-24: TP1 at 15:45, stop should move to 77870."""
        eng = _engine_in_position(
            direction=1,
            entry=77870.00,
            stop=77480.35,
            mid=78062.60,
            opposite=78450.00,
        )
        # 15:45 candle — high 78199 hits TP1/mid 78062.6
        ts_tp1 = pd.Timestamp("2026-04-24 15:45:00")
        eng._check_in_position(ts_tp1, o=77944.30, h=78199.00, l=77875.00, c=77908.90)
        assert eng.pos is not None
        assert eng.pos.hit_tp1 is True
        assert eng.pos.stop_price == pytest.approx(77870.00), \
            "stop must be at entry (breakeven), not at original 77480.35"

        # 17:30 candle — low 77414 breaks both original stop AND entry
        ts_stop = pd.Timestamp("2026-04-24 17:30:00")
        eng._check_in_position(ts_stop, o=77637.90, h=77678.00, l=77414.40, c=77455.70)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "trail_stop_be"
        expected_exit = (78062.60 + 77870.00) / 2
        assert trade["exit_price"] == pytest.approx(expected_exit)


# ---------------------------------------------------------------------------
# Test 2 — No TP1 → original stop
# ---------------------------------------------------------------------------

class TestOriginalStopWithoutTP1:
    def test_stop_hit_before_tp1_uses_original(self):
        eng = _engine_in_position(
            direction=1, entry=100.0, stop=99.5, mid=101.0, opposite=102.0
        )
        ts = _BASE_TS + pd.Timedelta(hours=1)
        eng._check_in_position(ts, o=99.8, h=100.2, l=99.3, c=99.4)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "stop"
        assert trade["exit_price"] == pytest.approx(99.5)

    def test_short_stop_hit_before_tp1(self):
        # SHORT: entry at high, stop above entry
        eng = _engine_in_position(
            direction=-1, entry=100.0, stop=100.5, mid=99.0, opposite=98.0
        )
        ts = _BASE_TS + pd.Timedelta(hours=1)
        eng._check_in_position(ts, o=100.2, h=100.6, l=99.8, c=100.1)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "stop"
        assert trade["exit_price"] == pytest.approx(100.5)

    def test_stop_price_unchanged_without_tp1(self):
        """stop_price must not change if TP1 is never hit."""
        eng = _engine_in_position(
            direction=1, entry=100.0, stop=99.5, mid=101.0, opposite=102.0
        )
        ts = _BASE_TS + pd.Timedelta(hours=1)
        # candle that does NOT hit mid (101.0)
        eng._check_in_position(ts, o=100.0, h=100.8, l=99.7, c=100.3)
        assert eng.pos is not None
        assert eng.pos.stop_price == pytest.approx(99.5), \
            "stop_price must remain at original if TP1 was not hit"


# ---------------------------------------------------------------------------
# Test 3 — TP1 hit, then TP2 hit → tp2
# ---------------------------------------------------------------------------

class TestTP2AfterTP1:
    def test_tp2_fires_after_tp1(self):
        eng = _engine_in_position(
            direction=1, entry=100.0, stop=99.5, mid=101.0, opposite=102.0
        )
        ts1 = _BASE_TS + pd.Timedelta(hours=1)
        # TP1 bar
        eng._check_in_position(ts1, o=100.5, h=101.5, l=100.3, c=101.1)
        assert eng.pos is not None
        assert eng.pos.hit_tp1 is True
        assert eng.pos.stop_price == pytest.approx(100.0)

        # TP2 bar — high reaches opposite (102.0); low stays above BE stop (100.0)
        ts2 = _BASE_TS + pd.Timedelta(hours=2)
        eng._check_in_position(ts2, o=101.5, h=102.5, l=101.2, c=102.2)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "tp2"
        assert trade["exit_price"] == pytest.approx((101.0 + 102.0) / 2)

    def test_tp1_and_tp2_not_on_same_bar(self):
        """TP2 must not fire on the same bar as TP1 (if/elif guard)."""
        eng = _engine_in_position(
            direction=1, entry=100.0, stop=99.5, mid=101.0, opposite=102.0
        )
        ts = _BASE_TS + pd.Timedelta(hours=1)
        # single candle with both h>=mid and h>=opposite
        eng._check_in_position(ts, o=100.5, h=103.0, l=100.3, c=102.5)
        # TP1 set but TP2 must NOT have fired on same bar
        assert eng.pos is not None, "position must still be open — TP2 blocked on TP1 bar"
        assert eng.pos.hit_tp1 is True
        assert len(eng.trades) == 0

    def test_short_tp1_then_tp2(self):
        # SHORT: fade high, TP1=mid (lower), TP2=opposite (even lower)
        eng = _engine_in_position(
            direction=-1, entry=100.0, stop=100.5, mid=99.0, opposite=98.0
        )
        ts1 = _BASE_TS + pd.Timedelta(hours=1)
        eng._check_in_position(ts1, o=99.5, h=99.8, l=98.8, c=99.2)
        assert eng.pos is not None
        assert eng.pos.hit_tp1 is True
        assert eng.pos.stop_price == pytest.approx(100.0)

        ts2 = _BASE_TS + pd.Timedelta(hours=2)
        eng._check_in_position(ts2, o=98.5, h=98.9, l=97.8, c=98.0)
        assert eng.pos is None
        trade = eng.trades[-1]
        assert trade["exit_reason"] == "tp2"
        assert trade["exit_price"] == pytest.approx((99.0 + 98.0) / 2)
