"""
test_persistence.py

Insert signal → decision → trade → equity.  Verify data survives round-trips.
"""
from __future__ import annotations

from datetime import datetime, date, timezone

import asyncpg
import pytest
import pytest_asyncio

from trading.core.engines.base import Direction, Signal, SignalAction, SignalDecision
from trading.core.storage.repository import TradingRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_full_persistence_chain(pool: asyncpg.Pool, clean_db):
    repo = TradingRepository(pool)

    bar_ts = datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)

    # 1. Insert signal
    signal = Signal(
        engine_id="m2_btc",
        symbol="BTCUSDT",
        bar_timestamp=bar_ts,
        direction=Direction.LONG,
        timeframe="5m",
        signal_data={"pattern": "UUUU"},
    )
    signal_id = await repo.insert_signal(signal)
    assert signal_id is not None
    assert isinstance(signal_id, int)

    # 2. Insert decision
    decision = SignalDecision(
        signal_id=signal_id,
        action=SignalAction.ENTER,
        reason="Test enter",
        entry_price=68000.0,
        stop_price=67000.0,
        target_price=70000.0,
        stake_usd=10.0,
    )
    decision_id = await repo.insert_decision(signal_id, decision)
    assert decision_id is not None

    # 3. Open trade
    trade_id = await repo.open_trade(
        engine_id="m2_btc",
        symbol="BTCUSDT",
        signal_id=signal_id,
        direction=Direction.LONG,
        entry_price=68000.0,
        stop_price=67000.0,
        target_price=70000.0,
        stake_usd=10.0,
    )
    assert trade_id is not None

    # Verify status = OPEN
    trades = await repo.get_trades(engine_id="m2_btc", limit=10)
    assert len(trades) == 1
    assert trades[0]["status"] == "OPEN"

    # 4. Upsert daily equity
    today = datetime.now(timezone.utc).date()
    await repo.upsert_daily_equity("m2_btc", today, 200.0, 200.0, 0.0, 0)

    equity = await repo.get_equity(engine_id="m2_btc")
    assert len(equity) == 1
    assert float(equity[0]["starting_equity"]) == 200.0

    # 5. Close trade
    await repo.close_trade(
        trade_id,
        exit_price=70000.0,
        pnl_usd=2.94,
        pnl_pct=0.000294,
        close_reason="CLOSED_TP",
        closed_at=datetime.now(timezone.utc),
    )

    trades_after = await repo.get_trades(engine_id="m2_btc", limit=10)
    assert trades_after[0]["close_reason"] == "CLOSED_TP"
    assert trades_after[0]["exit_price"] is not None
