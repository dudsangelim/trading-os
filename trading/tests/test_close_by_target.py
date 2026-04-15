"""
test_close_by_target.py

Simulate a candle crossing the target price — verify CLOSED_TP.
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
        open_time=datetime(2026, 3, 24, 10, 5, tzinfo=timezone.utc),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=500.0,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_long_target_hit(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m2_btc", "BTCUSDT", None, Direction.LONG, 68000.0, 67000.0, 70000.0, 10.0
    )
    pos = Position(
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

    # Candle reaches target
    candle = _candle("BTCUSDT", low=68500.0, high=70200.0, close=69800.0)
    result = check_position(pos, candle, timeout_bars=6)

    assert result is not None
    assert result.action == PositionAction.CLOSE_TP
    assert result.exit_price == 70000.0
    assert result.close_reason == "CLOSED_TP"
    assert result.pnl_usd > 0  # profit


@pytest.mark.asyncio(loop_scope="session")
async def test_short_target_hit(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m1_eth", "ETHUSDT", None, Direction.SHORT, 3000.0, 3100.0, 2800.0, 6.0
    )
    pos = Position(
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

    # Candle dips to target
    candle = _candle("ETHUSDT", low=2780.0, high=3010.0, close=2820.0)
    result = check_position(pos, candle, timeout_bars=6)

    assert result is not None
    assert result.action == PositionAction.CLOSE_TP
    assert result.close_reason == "CLOSED_TP"
    assert result.pnl_usd > 0


@pytest.mark.asyncio(loop_scope="session")
async def test_target_not_hit_if_candle_short(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m2_btc", "BTCUSDT", None, Direction.LONG, 68000.0, 67000.0, 70000.0, 10.0
    )
    pos = Position(
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

    candle = _candle("BTCUSDT", low=67500.0, high=69500.0, close=69000.0)
    result = check_position(pos, candle, timeout_bars=6)
    assert result is None
