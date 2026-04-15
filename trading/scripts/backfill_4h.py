"""
scripts/backfill_4h.py — Backfill historical 4h candles from Binance public API.

Usage (from inside trading_worker container):
    python3 /app/trading/scripts/backfill_4h.py [--days 730] [--symbols BTCUSDT ETHUSDT SOLUSDT]

Fetches up to --days of 4h OHLCV from Binance and stores in tr_candles_4h.
Safe to re-run: uses ON CONFLICT DO NOTHING.

Binance rate limit: 1200 weight/min. Each /klines call = 2 weight. This script
stays well under the limit by sleeping 0.25s between requests.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from urllib.request import urlopen
from urllib.error import URLError

import asyncpg

sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("backfill_4h")

BINANCE_BASE = "https://api.binance.com"
INTERVAL = "4h"
LIMIT = 1000          # max bars per request
SLEEP_BETWEEN = 0.25  # seconds between requests (well under rate limit)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_DAYS = 730    # 2 years


# ---------------------------------------------------------------------------
# Binance fetch
# ---------------------------------------------------------------------------

def fetch_klines(symbol: str, end_time_ms: int) -> List[List]:
    """
    Fetch up to LIMIT 4h klines ending at end_time_ms (exclusive).
    Returns raw Binance klines: [open_time_ms, O, H, L, C, V, ...]
    """
    url = (
        f"{BINANCE_BASE}/api/v3/klines"
        f"?symbol={symbol}&interval={INTERVAL}&limit={LIMIT}&endTime={end_time_ms}"
    )
    try:
        with urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode())
    except URLError as e:
        log.error("Binance request failed for %s: %s", symbol, e)
        return []


def klines_to_rows(symbol: str, raw: List[List]) -> List[Tuple]:
    """Convert raw klines to (symbol, open_time, O, H, L, C, V) tuples."""
    rows = []
    for k in raw:
        open_time = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
        rows.append((
            symbol,
            open_time,
            float(k[1]),  # open
            float(k[2]),  # high
            float(k[3]),  # low
            float(k[4]),  # close
            float(k[5]),  # volume
        ))
    return rows


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

async def upsert_rows(pool: asyncpg.Pool, rows: List[Tuple]) -> int:
    if not rows:
        return 0
    async with pool.acquire() as conn:
        result = await conn.executemany(
            """
            INSERT INTO tr_candles_4h
                (symbol, open_time, open, high, low, close, volume)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (symbol, open_time) DO NOTHING
            """,
            rows,
        )
    # executemany result is "INSERT 0 N" — parse N
    try:
        inserted = int(str(result).split()[-1])
    except Exception:
        inserted = len(rows)
    return inserted


# ---------------------------------------------------------------------------
# Per-symbol backfill
# ---------------------------------------------------------------------------

async def backfill_symbol(pool: asyncpg.Pool, symbol: str, days: int) -> None:
    start_dt = datetime.now(timezone.utc) - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)

    # Find what we already have in the DB
    async with pool.acquire() as conn:
        existing_min = await conn.fetchval(
            "SELECT MIN(open_time) FROM tr_candles_4h WHERE symbol = $1", symbol
        )
        existing_count = await conn.fetchval(
            "SELECT COUNT(*) FROM tr_candles_4h WHERE symbol = $1", symbol
        )

    log.info(
        "%s: existing rows=%d, oldest=%s",
        symbol,
        existing_count or 0,
        existing_min.isoformat() if existing_min else "none",
    )

    # End time starts at now and pages backwards
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    total_inserted = 0
    total_fetched = 0
    pages = 0

    while end_ms > start_ms:
        raw = fetch_klines(symbol, end_time_ms=end_ms)
        if not raw:
            log.warning("%s: empty response at end_ms=%d, stopping", symbol, end_ms)
            break

        rows = klines_to_rows(symbol, raw)
        total_fetched += len(rows)
        pages += 1

        # Insert
        inserted = await upsert_rows(pool, rows)
        total_inserted += inserted

        # Move window back: use the open_time of the first bar as new end
        earliest_ms = int(raw[0][0])
        if earliest_ms <= start_ms:
            break

        # Advance end_ms backward
        end_ms = earliest_ms  # next fetch ends just before this bar

        log.info(
            "%s: page %d — fetched %d bars, inserted %d new | window end → %s",
            symbol,
            pages,
            len(rows),
            inserted,
            datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
        )

        time.sleep(SLEEP_BETWEEN)

    log.info(
        "%s: DONE — %d pages, %d bars fetched, %d new rows inserted",
        symbol,
        pages,
        total_fetched,
        total_inserted,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(symbols: List[str], days: int) -> None:
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://jarvis:jarvispass@postgres:5432/jarvis"
    )
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)

    # Ensure table exists
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tr_candles_4h (
                id          BIGSERIAL PRIMARY KEY,
                symbol      TEXT NOT NULL,
                open_time   TIMESTAMPTZ NOT NULL,
                open        NUMERIC(18,8) NOT NULL,
                high        NUMERIC(18,8) NOT NULL,
                low         NUMERIC(18,8) NOT NULL,
                close       NUMERIC(18,8) NOT NULL,
                volume      NUMERIC(18,8) NOT NULL,
                UNIQUE (symbol, open_time)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tr_candles_4h ON tr_candles_4h (symbol, open_time DESC)"
        )

    for symbol in symbols:
        log.info("=== Backfilling %s — last %d days ===", symbol, days)
        await backfill_symbol(pool, symbol, days)

    await pool.close()
    log.info("Backfill complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill 4h candles from Binance")
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help=f"Days of history to fetch (default: {DEFAULT_DAYS})"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=DEFAULT_SYMBOLS,
        help=f"Symbols to backfill (default: {DEFAULT_SYMBOLS})"
    )
    args = parser.parse_args()

    asyncio.run(main(symbols=args.symbols, days=args.days))
