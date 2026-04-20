"""
Unit tests for carry_neutral_btc engine decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import pytest
from trading.core.engines.carry_neutral_btc import (
    CarryAction,
    CarryContext,
    CarryNeutralBtcEngine,
)


def _make_ctx(
    rate: float = 0.20,
    history: Optional[list] = None,
    open_position: Optional[Dict[str, Any]] = None,
    bankroll: float = 300.0,
) -> CarryContext:
    return CarryContext(
        funding_rate_annualized=rate,
        funding_rate_history_24h=history or [rate, rate, rate],
        spot_price=80000.0,
        perp_price=80100.0,
        bankroll_usd=bankroll,
        open_position=open_position,
    )


def _open_pos(hours_ago: float = 10.0, events: int = 1) -> Dict[str, Any]:
    return {
        "id": 1,
        "opened_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        "funding_events_count": events,
    }


@pytest.fixture
def engine() -> CarryNeutralBtcEngine:
    return CarryNeutralBtcEngine()


class TestEvaluateCarryNoPosition:
    def test_skip_when_below_upper(self, engine):
        d = engine.evaluate_carry(_make_ctx(rate=0.10), threshold_upper=0.15)
        assert d.action == CarryAction.SKIP
        assert "THRESHOLD" in d.reason

    def test_skip_when_bankroll_zero(self, engine):
        d = engine.evaluate_carry(_make_ctx(rate=0.20, bankroll=0.0))
        assert d.action == CarryAction.SKIP

    def test_skip_when_history_unstable(self, engine):
        d = engine.evaluate_carry(_make_ctx(rate=0.20, history=[0.05, 0.04, 0.20]))
        assert d.action == CarryAction.SKIP
        assert "UNSTABLE" in d.reason

    def test_open_when_rate_above_threshold_stable(self, engine):
        d = engine.evaluate_carry(_make_ctx(rate=0.20, history=[0.18, 0.19, 0.20]))
        assert d.action == CarryAction.OPEN_CARRY

    def test_open_when_empty_history(self, engine):
        # No historical data: should open if current rate > threshold
        d = engine.evaluate_carry(_make_ctx(rate=0.20, history=[]))
        assert d.action == CarryAction.OPEN_CARRY

    def test_open_exactly_at_threshold(self, engine):
        # Rate == upper threshold: at-or-above opens (threshold means >=)
        d = engine.evaluate_carry(_make_ctx(rate=0.15), threshold_upper=0.15)
        assert d.action == CarryAction.OPEN_CARRY


class TestEvaluateCarryWithPosition:
    def test_hold_when_rate_between_thresholds(self, engine):
        ctx = _make_ctx(rate=0.10, open_position=_open_pos())
        d = engine.evaluate_carry(ctx, threshold_upper=0.15, threshold_lower=0.05)
        assert d.action == CarryAction.HOLD

    def test_close_when_rate_below_lower(self, engine):
        ctx = _make_ctx(rate=0.03, open_position=_open_pos())
        d = engine.evaluate_carry(ctx, threshold_lower=0.05)
        assert d.action == CarryAction.CLOSE_CARRY
        assert "LOWER_THRESHOLD" in d.reason

    def test_close_when_max_hold_exceeded(self, engine):
        ctx = _make_ctx(rate=0.25, open_position=_open_pos(hours_ago=200))
        d = engine.evaluate_carry(ctx, max_hold_hours=168)
        assert d.action == CarryAction.CLOSE_CARRY
        assert "MAX_HOLD" in d.reason

    def test_hold_when_just_under_max_hold(self, engine):
        ctx = _make_ctx(rate=0.20, open_position=_open_pos(hours_ago=167))
        d = engine.evaluate_carry(ctx, threshold_lower=0.05, max_hold_hours=168)
        assert d.action == CarryAction.HOLD

    def test_close_when_exactly_at_max_hold(self, engine):
        ctx = _make_ctx(rate=0.20, open_position=_open_pos(hours_ago=168))
        d = engine.evaluate_carry(ctx, threshold_lower=0.05, max_hold_hours=168)
        assert d.action == CarryAction.CLOSE_CARRY


class TestBaseEngineStubs:
    def test_generate_signal_returns_none(self, engine):
        from trading.core.engines.base import EngineContext
        ctx = EngineContext(
            engine_id="carry_neutral_btc",
            symbol="BTCUSDT",
            bar_timestamp=datetime.now(timezone.utc),
            bar_open=80000.0,
            bar_high=80100.0,
            bar_low=79900.0,
            bar_close=80050.0,
            bar_volume=100.0,
            recent_closes=[80000.0],
        )
        assert engine.generate_signal(ctx) is None

    def test_validate_signal_returns_skip(self, engine):
        from trading.core.engines.base import EngineContext, Signal, Direction, SignalAction
        ctx = EngineContext(
            engine_id="carry_neutral_btc",
            symbol="BTCUSDT",
            bar_timestamp=datetime.now(timezone.utc),
            bar_open=80000.0,
            bar_high=80100.0,
            bar_low=79900.0,
            bar_close=80050.0,
            bar_volume=100.0,
            recent_closes=[80000.0],
        )
        sig = Signal(
            engine_id="carry_neutral_btc",
            symbol="BTCUSDT",
            bar_timestamp=datetime.now(timezone.utc),
            direction=Direction.LONG,
        )
        decision = engine.validate_signal(sig, ctx)
        assert decision.action == SignalAction.SKIP
