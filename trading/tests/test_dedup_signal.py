"""
test_dedup_signal.py

Inserting the same (engine_id, bar_timestamp, direction) twice must
silently return None on the second insert (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import pytest

from trading.core.engines.base import Direction, Signal
from trading.core.storage.repository import TradingRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_signal_ignored(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    bar_ts = datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
    signal = Signal(
        engine_id="m2_btc",
        symbol="BTCUSDT",
        bar_timestamp=bar_ts,
        direction=Direction.LONG,
        timeframe="5m",
        signal_data={"pattern": "UUUU"},
    )

    first_id = await repo.insert_signal(signal)
    assert first_id is not None, "First insert must succeed"

    second_id = await repo.insert_signal(signal)
    assert second_id is None, "Duplicate insert must return None"

    # Verify only one row in the table
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM tr_signals WHERE engine_id='m2_btc' AND bar_timestamp=$1",
            bar_ts,
        )
    assert count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_different_direction_not_deduped(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    bar_ts = datetime(2026, 3, 24, 11, 0, tzinfo=timezone.utc)
    long_signal = Signal(
        engine_id="m2_btc",
        symbol="BTCUSDT",
        bar_timestamp=bar_ts,
        direction=Direction.LONG,
        signal_data={},
    )
    short_signal = Signal(
        engine_id="m2_btc",
        symbol="BTCUSDT",
        bar_timestamp=bar_ts,
        direction=Direction.SHORT,
        signal_data={},
    )

    long_id = await repo.insert_signal(long_signal)
    short_id = await repo.insert_signal(short_signal)

    assert long_id is not None
    assert short_id is not None
    assert long_id != short_id


@pytest.mark.asyncio(loop_scope="session")
async def test_different_engine_not_deduped(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    bar_ts = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    sig1 = Signal("m2_btc", "BTCUSDT", bar_ts, Direction.LONG, signal_data={})
    sig2 = Signal("m1_eth", "ETHUSDT", bar_ts, Direction.LONG, signal_data={})

    id1 = await repo.insert_signal(sig1)
    id2 = await repo.insert_signal(sig2)

    assert id1 is not None
    assert id2 is not None
    assert id1 != id2
