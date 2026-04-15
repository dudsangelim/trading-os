"""
test_rehydration.py

Insert a tr_paper_trades row with status=OPEN, then call the rehydration
logic and verify that the engine has the position in memory.
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import pytest

from trading.core.engines.base import Direction, Position
from trading.core.engines.registry import build_registry
from trading.core.storage.repository import TradingRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_rehydration_loads_open_position(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    # Insert an OPEN trade directly (simulates worker restart)
    trade_id = await repo.open_trade(
        engine_id="m2_btc",
        symbol="BTCUSDT",
        signal_id=None,
        direction=Direction.LONG,
        entry_price=67500.0,
        stop_price=66500.0,
        target_price=70000.0,
        stake_usd=9.0,
    )

    # Simulate rehydration (same logic as worker startup)
    registry = build_registry()
    open_positions = await repo.get_open_positions()
    rehydrated_count = 0

    for row in open_positions:
        engine_id = row["engine_id"]
        if engine_id not in registry:
            continue
        engine = registry[engine_id]
        direction = Direction(row["direction"])
        pos = Position(
            trade_id=row["id"],
            engine_id=engine_id,
            symbol=row["symbol"],
            signal_id=row.get("signal_id"),
            direction=direction,
            entry_price=float(row["entry_price"]),
            stop_price=float(row["stop_price"]),
            target_price=float(row["target_price"]),
            stake_usd=float(row["stake_usd"]),
            opened_at=row["opened_at"],
            bars_held=0,
        )
        engine.rehydrate(pos)
        rehydrated_count += 1

    assert rehydrated_count == 1
    btc_engine = registry["m2_btc"]
    assert btc_engine.has_open_position is True
    assert btc_engine.open_position.trade_id == trade_id
    assert btc_engine.open_position.entry_price == 67500.0


@pytest.mark.asyncio(loop_scope="session")
async def test_rehydration_ignores_closed_trades(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    # Insert and then close a trade
    trade_id = await repo.open_trade(
        "m3_sol", "SOLUSDT", None, Direction.SHORT, 150.0, 155.0, 140.0, 5.0
    )
    await repo.close_trade(
        trade_id, 140.0, 0.67, 0.0067, "CLOSED_TP", datetime.now(timezone.utc)
    )

    registry = build_registry()
    open_positions = await repo.get_open_positions()

    for row in open_positions:
        engine_id = row["engine_id"]
        if engine_id in registry:
            engine = registry[engine_id]
            engine.rehydrate(Position(
                trade_id=row["id"],
                engine_id=engine_id,
                symbol=row["symbol"],
                signal_id=None,
                direction=Direction(row["direction"]),
                entry_price=float(row["entry_price"]),
                stop_price=float(row["stop_price"]),
                target_price=float(row["target_price"]),
                stake_usd=float(row["stake_usd"]),
                opened_at=row["opened_at"],
            ))

    assert not registry["m3_sol"].has_open_position
