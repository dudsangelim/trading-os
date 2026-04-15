#!/usr/bin/env bash
# bootstrap.sh — one-shot initialization for the trading system
# Run from inside the trading_worker container or directly on the host
# after setting the DATABASE_URL environment variable.
set -euo pipefail

echo "=== Trading System Bootstrap ==="
echo "DATABASE_URL: ${DATABASE_URL:-postgresql://jarvis:jarvispass@postgres:5432/jarvis}"

export DATABASE_URL="${DATABASE_URL:-postgresql://jarvis:jarvispass@postgres:5432/jarvis}"
export REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"

# ---- 1. Apply migrations (idempotent) -----------------------------------
echo ""
echo "[1/5] Applying DDL migrations..."
python3 -c "
import asyncio, sys, os
sys.path.insert(0, '/app')
from trading.core.storage.migrations import run_migrations
import asyncpg

async def go():
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
    await run_migrations(pool)
    await pool.close()
    print('  Migrations OK')

asyncio.run(go())
"

# ---- 2. Initialize bankroll rows in tr_daily_equity ---------------------
echo ""
echo "[2/6] Initializing engine bankrolls..."
python3 -c "
import asyncio, sys, os
from datetime import datetime, timezone
sys.path.insert(0, '/app')
from trading.core.config.settings import ENGINE_CONFIGS, DATABASE_URL
from trading.core.storage.repository import TradingRepository
import asyncpg

async def go():
    pool = await asyncpg.create_pool(os.environ.get('DATABASE_URL', DATABASE_URL), min_size=1, max_size=2)
    repo = TradingRepository(pool)
    today = datetime.now(timezone.utc).date()
    for engine_id, cfg in ENGINE_CONFIGS.items():
        existing = await repo.get_today_starting_equity(engine_id)
        if existing is None:
            bankroll = await repo.get_current_bankroll(engine_id)
            await repo.upsert_daily_equity(engine_id, today, bankroll, bankroll, 0.0, 0)
            print(f'  {engine_id}: bankroll initialized at \${bankroll:.2f}')
        else:
            print(f'  {engine_id}: already initialized (\${existing:.2f})')
    await pool.close()

asyncio.run(go())
"

# ---- 3. Validate 4h warm-up inventory -----------------------------------
echo ""
echo "[3/6] Validating persisted 4h warm-up..."
python3 -c "
import asyncio, sys, os
sys.path.insert(0, '/app')
from trading.core.config.settings import ENGINE_CONFIGS, DATABASE_URL
from trading.core.engines.regime_hmm import MIN_BARS
import asyncpg

async def go():
    pool = await asyncpg.create_pool(os.environ.get('DATABASE_URL', DATABASE_URL), min_size=1, max_size=2)
    async with pool.acquire() as conn:
        for engine_id, cfg in ENGINE_CONFIGS.items():
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM tr_candles_4h WHERE symbol = \$1',
                cfg.symbol,
            )
            oldest = await conn.fetchval(
                'SELECT MIN(open_time) FROM tr_candles_4h WHERE symbol = \$1',
                cfg.symbol,
            )
            newest = await conn.fetchval(
                'SELECT MAX(open_time) FROM tr_candles_4h WHERE symbol = \$1',
                cfg.symbol,
            )
            status = 'OK' if (count or 0) >= MIN_BARS else 'INSUFFICIENT'
            print(
                f'  {engine_id:20s} {cfg.symbol:10s} 4h_bars={count or 0:<5d} '
                f'status={status} oldest={oldest} newest={newest}'
            )
        print(f'  HMM minimum bars required: {MIN_BARS}')
    await pool.close()

asyncio.run(go())
"

# ---- 4. Validate PostgreSQL connection ----------------------------------
echo ""
echo "[4/6] Validating PostgreSQL connection..."
python3 -c "
import asyncio, sys, os
sys.path.insert(0, '/app')
import asyncpg

async def go():
    pool = await asyncpg.create_pool(os.environ.get('DATABASE_URL', 'postgresql://jarvis:jarvispass@postgres:5432/jarvis'), min_size=1, max_size=1)
    result = await pool.fetchval('SELECT version()')
    print(f'  PostgreSQL: {result[:60]}')
    await pool.close()

asyncio.run(go())
"

# ---- 5. Validate el_candles_1m ------------------------------------------
echo ""
echo "[5/6] Validating el_candles_1m (source candle table)..."
python3 -c "
import asyncio, sys, os
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '/app')
import asyncpg

async def go():
    pool = await asyncpg.create_pool(os.environ.get('DATABASE_URL', 'postgresql://jarvis:jarvispass@postgres:5432/jarvis'), min_size=1, max_size=1)
    count = await pool.fetchval('SELECT COUNT(*) FROM el_candles_1m')
    last_ts = await pool.fetchval(\"SELECT MAX(open_time) FROM el_candles_1m\")
    await pool.close()
    if count == 0:
        print('  WARNING: el_candles_1m is empty!')
        sys.exit(1)
    age_min = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60 if last_ts else 999
    print(f'  el_candles_1m: {count} rows, last candle {age_min:.1f}min ago')
    if age_min > 30:
        print('  WARNING: last candle is stale!')

asyncio.run(go())
"

# ---- 6. Print engine status ---------------------------------------------
echo ""
echo "[6/6] Engine status:"
python3 -c "
import asyncio, sys, os
sys.path.insert(0, '/app')
from trading.core.config.settings import ENGINE_CONFIGS, DATABASE_URL
from trading.core.storage.repository import TradingRepository
import asyncpg

async def go():
    pool = await asyncpg.create_pool(os.environ.get('DATABASE_URL', DATABASE_URL), min_size=1, max_size=2)
    repo = TradingRepository(pool)
    for engine_id, cfg in ENGINE_CONFIGS.items():
        bankroll = await repo.get_current_bankroll(engine_id)
        mode = 'SIGNAL_ONLY' if cfg.signal_only else 'ACTIVE'
        print(f'  {engine_id:20s} {cfg.symbol:10s} bankroll=\${bankroll:.2f}  mode={mode}')
    await pool.close()

asyncio.run(go())
"

echo ""
echo "=== Bootstrap complete ==="
