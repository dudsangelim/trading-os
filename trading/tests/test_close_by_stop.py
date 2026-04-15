"""
test_close_by_stop.py

Simulate a candle crossing the stop price — verify CLOSED_SL.
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import pytest

from trading.core.data.candle_reader import Candle
from trading.core.engines.base import Direction, Position
from trading.core.execution.position_manager import check_position, PositionAction
from trading.core.storage.repository import TradingRepository


def _candle(symbol: str, low: float, high: float, close: float) -> Candle:
    return Candle(
        symbol=symbol,
        open_time=datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _long_position(trade_id: int) -> Position:
    return Position(
        trade_id=trade_id,
        engine_id="m2_btc",
        symbol="BTCUSDT",
        signal_id=None,
        direction=Direction.LONG,
        entry_price=68000.0,
        stop_price=67000.0,
        target_price=70000.0,
        stake_usd=10.0,
        opened_at=datetime.now(timezone.utc),
        bars_held=0,
    )


def _short_position(trade_id: int) -> Position:
    return Position(
        trade_id=trade_id,
        engine_id="m1_eth",
        symbol="ETHUSDT",
        signal_id=None,
        direction=Direction.SHORT,
        entry_price=3000.0,
        stop_price=3100.0,
        target_price=2800.0,
        stake_usd=6.0,
        opened_at=datetime.now(timezone.utc),
        bars_held=0,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_long_stop_triggered(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m2_btc", "BTCUSDT", None, Direction.LONG, 68000.0, 67000.0, 70000.0, 10.0
    )
    pos = _long_position(trade_id)

    # Candle dips below stop
    candle = _candle("BTCUSDT", low=66500.0, high=68200.0, close=67200.0)
    result = check_position(pos, candle, timeout_bars=6)

    assert result is not None
    assert result.action == PositionAction.CLOSE_SL
    assert result.exit_price == 67000.0
    assert result.close_reason == "CLOSED_SL"
    assert result.pnl_usd < 0  # loss


@pytest.mark.asyncio(loop_scope="session")
async def test_short_stop_triggered(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m1_eth", "ETHUSDT", None, Direction.SHORT, 3000.0, 3100.0, 2800.0, 6.0
    )
    pos = _short_position(trade_id)

    # Candle spikes above stop
    candle = _candle("ETHUSDT", low=2980.0, high=3150.0, close=3050.0)
    result = check_position(pos, candle, timeout_bars=6)

    assert result is not None
    assert result.action == PositionAction.CLOSE_SL
    assert result.close_reason == "CLOSED_SL"


@pytest.mark.asyncio(loop_scope="session")
async def test_stop_not_triggered_if_price_holds(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m2_btc", "BTCUSDT", None, Direction.LONG, 68000.0, 67000.0, 70000.0, 10.0
    )
    pos = _long_position(trade_id)

    # Low does NOT reach stop
    candle = _candle("BTCUSDT", low=67100.0, high=68500.0, close=68200.0)
    result = check_position(pos, candle, timeout_bars=6)

    assert result is None
