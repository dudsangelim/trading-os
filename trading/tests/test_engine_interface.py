"""
test_engine_interface.py

Verify that BaseEngine is correctly defined and all four concrete engines
implement every required method with correct signatures.
"""
from __future__ import annotations

import inspect
import pytest

from trading.core.engines.base import (
    BaseEngine,
    EngineContext,
    OrderPlan,
    Position,
    Signal,
    SignalDecision,
)
from trading.core.engines.m1_eth import M1EthEngine
from trading.core.engines.m2_btc import M2BtcEngine
from trading.core.engines.m3_sol import M3SolEngine
from trading.core.engines.m3_eth_shadow import M3EthShadowEngine


ENGINE_CLASSES = [M1EthEngine, M2BtcEngine, M3SolEngine, M3EthShadowEngine]

REQUIRED_METHODS = [
    "prepare_context",
    "generate_signal",
    "validate_signal",
    "build_order_plan",
    "manage_open_position",
]


@pytest.mark.parametrize("cls", ENGINE_CLASSES)
def test_engine_is_subclass_of_base(cls):
    assert issubclass(cls, BaseEngine)


@pytest.mark.parametrize("cls", ENGINE_CLASSES)
def test_engine_has_required_methods(cls):
    for method_name in REQUIRED_METHODS:
        assert hasattr(cls, method_name), f"{cls.__name__} missing {method_name}"
        assert callable(getattr(cls, method_name))


@pytest.mark.parametrize("cls", ENGINE_CLASSES)
def test_engine_instantiates(cls):
    engine = cls()
    assert engine.engine_id is not None
    assert engine.symbol is not None
    assert engine.has_open_position is False
    assert engine.open_position is None


@pytest.mark.parametrize("cls", ENGINE_CLASSES)
def test_generate_signal_returns_none_without_data(cls):
    """Implemented engines return None (no signal) when context has no enriched data."""
    from datetime import datetime, timezone
    engine = cls()
    ctx = EngineContext(
        engine_id=engine.engine_id,
        symbol=engine.symbol,
        bar_timestamp=datetime.now(timezone.utc),
        bar_open=100.0,
        bar_high=101.0,
        bar_low=99.0,
        bar_close=100.5,
        bar_volume=1000.0,
        recent_closes=[100.0, 100.5],
    )
    result = engine.generate_signal(ctx)
    assert result is None


@pytest.mark.parametrize("cls", ENGINE_CLASSES)
def test_build_order_plan_works_with_valid_signal(cls):
    """build_order_plan must return a valid OrderPlan given a properly structured signal."""
    from datetime import datetime, timezone
    from trading.core.engines.base import Direction, OrderPlan
    engine = cls()
    engine_id = engine.engine_id

    # Build a minimal valid signal_data for each engine
    if engine_id == "m1_eth":
        signal_data = {
            "bar_close": 3000.0,
            "compression_high": 3010.0,
            "compression_low": 2990.0,
        }
    elif engine_id == "m2_btc":
        signal_data = {
            "bar_close": 60000.0,
            "sweep_extreme": 59800.0,
            "low_ref": 59900.0,
            "high_ref": 60500.0,
        }
    elif engine_id in ("m3_sol", "m3_eth_shadow"):
        signal_data = {
            "entry_level": 150.0,
            "comp_high": 155.0,
            "comp_low": 145.0,
            "bar_close": 150.0,
        }
    else:
        signal_data = {"bar_close": 100.0}

    signal = Signal(
        engine_id=engine.engine_id,
        symbol=engine.symbol,
        bar_timestamp=datetime.now(timezone.utc),
        direction=Direction.LONG,
        signal_data=signal_data,
    )
    plan = engine.build_order_plan(signal, 200.0)
    assert isinstance(plan, OrderPlan)
    assert plan.entry_price > 0
    assert plan.stake_usd > 0


def test_rehydrate_sets_position():
    from datetime import datetime, timezone
    from trading.core.engines.base import Direction

    engine = M2BtcEngine()
    assert engine.has_open_position is False

    pos = Position(
        trade_id=42,
        engine_id="m2_btc",
        symbol="BTCUSDT",
        signal_id=1,
        direction=Direction.LONG,
        entry_price=60000.0,
        stop_price=59000.0,
        target_price=62000.0,
        stake_usd=10.0,
        opened_at=datetime.now(timezone.utc),
        bars_held=0,
    )
    engine.rehydrate(pos)
    assert engine.has_open_position is True
    assert engine.open_position is pos


def test_set_position_none_clears():
    from datetime import datetime, timezone
    from trading.core.engines.base import Direction

    engine = M2BtcEngine()
    pos = Position(
        trade_id=1,
        engine_id="m2_btc",
        symbol="BTCUSDT",
        signal_id=None,
        direction=Direction.SHORT,
        entry_price=60000.0,
        stop_price=61000.0,
        target_price=58000.0,
        stake_usd=10.0,
        opened_at=datetime.now(timezone.utc),
    )
    engine.rehydrate(pos)
    assert engine.has_open_position is True
    engine.set_open_position(None)
    assert engine.has_open_position is False
