"""
Shared pytest fixtures.

All tests use a real PostgreSQL database.
The schema is created via migrations.py in the session-scoped fixture.
"""
from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio

# Default to the docker-compose internal URL; override via env for CI.
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://jarvis:jarvispass@localhost:5432/jarvis",
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pool():
    """Session-scoped asyncpg pool. Migrations are run once."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

    from trading.core.storage.migrations import run_migrations

    p = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=5)
    await run_migrations(p)
    yield p
    await p.close()


@pytest_asyncio.fixture(loop_scope="session")
async def clean_db(pool: asyncpg.Pool):
    """
    Truncate all tr_* tables before each test so tests are independent.
    Truncate in FK-safe order (child tables first).
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE
                tr_risk_events,
                tr_daily_equity,
                tr_paper_trades,
                tr_signal_decisions,
                tr_signals,
                tr_engine_heartbeat
            RESTART IDENTITY CASCADE
            """
        )
    yield
