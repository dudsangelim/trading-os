"""
test_open_position.py

Open a position via repository, verify status=OPEN in the database.
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import pytest

from trading.core.engines.base import Direction
from trading.core.storage.repository import TradingRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_open_position_status(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    trade_id = await repo.open_trade(
        engine_id="m3_sol",
        symbol="SOLUSDT",
        signal_id=None,
        direction=Direction.SHORT,
        entry_price=150.0,
        stop_price=155.0,
        target_price=140.0,
        stake_usd=5.0,
    )
    assert trade_id is not None

    open_positions = await repo.get_open_positions()
    assert len(open_positions) == 1
    pos = open_positions[0]
    assert pos["status"] == "OPEN"
    assert pos["engine_id"] == "m3_sol"
    assert float(pos["entry_price"]) == 150.0
    assert float(pos["stop_price"]) == 155.0
    assert float(pos["target_price"]) == 140.0


@pytest.mark.asyncio(loop_scope="session")
async def test_open_multiple_positions_tracked(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    id1 = await repo.open_trade("m1_eth", "ETHUSDT", None, Direction.LONG, 3000.0, 2900.0, 3200.0, 6.0)
    id2 = await repo.open_trade("m2_btc", "BTCUSDT", None, Direction.LONG, 65000.0, 64000.0, 68000.0, 9.0)

    open_positions = await repo.get_open_positions()
    assert len(open_positions) == 2
    ids = {p["id"] for p in open_positions}
    assert id1 in ids
    assert id2 in ids
