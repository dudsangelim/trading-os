"""
test_close_by_timeout.py

After bars_held >= timeout_bars the position must be closed with CLOSED_TIMEOUT.
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import pytest

from trading.core.data.candle_reader import Candle
from trading.core.engines.base import Direction, Position
from trading.core.execution.position_manager import check_position, PositionAction
from trading.core.storage.repository import TradingRepository


def _safe_candle(symbol: str) -> Candle:
    """A candle that never hits stop or target for a BTC LONG position."""
    return Candle(
        symbol=symbol,
        open_time=datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc),
        open=68500.0,
        high=69000.0,   # below target 70000
        low=67500.0,    # above stop 67000
        close=68700.0,
        volume=1000.0,
    )


def _make_position(trade_id: int, bars_held: int) -> Position:
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
        bars_held=bars_held,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_timeout_on_exact_bar(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m2_btc", "BTCUSDT", None, Direction.LONG, 68000.0, 67000.0, 70000.0, 10.0
    )
    # bars_held=5 means after this candle it becomes 6 → equal to timeout_bars=6
    pos = _make_position(trade_id, bars_held=5)
    candle = _safe_candle("BTCUSDT")

    result = check_position(pos, candle, timeout_bars=6)

    assert result is not None
    assert result.action == PositionAction.CLOSE_TIMEOUT
    assert result.close_reason == "CLOSED_TIMEOUT"
    assert result.exit_price == candle.close


@pytest.mark.asyncio(loop_scope="session")
async def test_no_timeout_before_limit(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m2_btc", "BTCUSDT", None, Direction.LONG, 68000.0, 67000.0, 70000.0, 10.0
    )
    pos = _make_position(trade_id, bars_held=3)
    candle = _safe_candle("BTCUSDT")

    result = check_position(pos, candle, timeout_bars=6)
    # bars_held becomes 4 after this candle — still below 6
    assert result is None


@pytest.mark.asyncio(loop_scope="session")
async def test_timeout_beyond_limit(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)
    trade_id = await repo.open_trade(
        "m2_btc", "BTCUSDT", None, Direction.LONG, 68000.0, 67000.0, 70000.0, 10.0
    )
    pos = _make_position(trade_id, bars_held=10)
    candle = _safe_candle("BTCUSDT")

    result = check_position(pos, candle, timeout_bars=6)
    assert result is not None
    assert result.action == PositionAction.CLOSE_TIMEOUT
