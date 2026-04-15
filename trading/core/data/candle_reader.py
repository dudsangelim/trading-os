"""
Reads candle data from el_candles_1m (owned by jarvis_edgelab).
This module NEVER writes to that table — read-only by design.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import asyncpg


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
from dataclasses import dataclass


@dataclass
class Candle:
    symbol: str
    open_time: datetime   # UTC, candle open (= bar_timestamp)
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def bar_timestamp(self) -> datetime:
        return self.open_time


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class CandleReader:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_latest_candles(
        self,
        symbol: str,
        limit: int = 200,
        before: Optional[datetime] = None,
    ) -> List[Candle]:
        """
        Return the most recent *closed* 1-minute candles for ``symbol``.
        A candle at minute T is considered closed once the clock has passed T+1.

        ``before`` restricts to candles with open_time < before (default: now).
        """
        cutoff = before or datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT symbol, open_time, open, high, low, close, volume
                FROM el_candles_1m
                WHERE symbol = $1
                  AND open_time < $2
                ORDER BY open_time DESC
                LIMIT $3
                """,
                symbol,
                cutoff,
                limit,
            )
        # Return in ascending time order (oldest first)
        candles = [
            Candle(
                symbol=row["symbol"],
                open_time=row["open_time"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in reversed(rows)
        ]
        return candles

    async def get_latest_closed_candle(
        self, symbol: str, before: Optional[datetime] = None
    ) -> Optional[Candle]:
        """Return the single most recently closed candle."""
        candles = await self.get_latest_candles(symbol, limit=1, before=before)
        return candles[0] if candles else None

    async def get_last_candle_time(self, symbol: str) -> Optional[datetime]:
        """Return the open_time of the newest candle in the table for ``symbol``."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT MAX(open_time) AS last_ts
                FROM el_candles_1m
                WHERE symbol = $1
                """,
                symbol,
            )
        if row and row["last_ts"]:
            ts = row["last_ts"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        return None

    async def get_candles_for_bar(
        self,
        symbol: str,
        bar_open: datetime,
        timeframe_minutes: int,
    ) -> List[Candle]:
        """
        Return all 1m candles that fall within a given TF bar
        [bar_open, bar_open + timeframe_minutes).
        """
        from datetime import timedelta

        bar_close = bar_open + timedelta(minutes=timeframe_minutes)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT symbol, open_time, open, high, low, close, volume
                FROM el_candles_1m
                WHERE symbol = $1
                  AND open_time >= $2
                  AND open_time < $3
                ORDER BY open_time ASC
                """,
                symbol,
                bar_open,
                bar_close,
            )
        return [
            Candle(
                symbol=row["symbol"],
                open_time=row["open_time"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in rows
        ]

    async def get_latest_tf_candles(
        self,
        symbol: str,
        tf_minutes: int,
        limit: int,
        before: Optional[datetime] = None,
    ) -> List[Candle]:
        """
        Return the last `limit` completed TF candles aggregated from 1m data.
        Only returns fully complete bars (bar_open + tf_minutes <= before).
        """
        cutoff = before or datetime.now(timezone.utc)
        # Fetch enough 1m candles to cover limit TF bars plus one extra
        fetch_count = limit * tf_minutes + tf_minutes
        raw = await self.get_latest_candles(symbol, limit=fetch_count, before=cutoff)
        if not raw:
            return []

        # Group 1m candles by TF bar open time
        # bar_key = floor(open_time_minutes / tf_minutes) * tf_minutes  (in seconds)
        bars: dict = {}
        for c in raw:
            m = int(c.open_time.timestamp()) // 60
            bar_key_min = (m // tf_minutes) * tf_minutes
            bar_key = bar_key_min * 60  # as epoch seconds
            if bar_key not in bars:
                bars[bar_key] = []
            bars[bar_key].append(c)

        # Build aggregated candles, only include complete bars
        result = []
        for bar_epoch in sorted(bars.keys()):
            bar_open_dt = datetime.fromtimestamp(bar_epoch, tz=timezone.utc)
            bar_close_dt = bar_open_dt + timedelta(minutes=tf_minutes)
            # Only include if the bar is fully complete
            if bar_close_dt > cutoff:
                continue
            group = bars[bar_epoch]
            agg = Candle(
                symbol=symbol,
                open_time=bar_open_dt,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
            result.append(agg)

        # Return the last `limit` bars
        return result[-limit:] if len(result) > limit else result

    async def get_latest_persisted_4h_candles(
        self,
        symbol: str,
        limit: int = 200,
        before: Optional[datetime] = None,
    ) -> List[Candle]:
        """
        Return persisted 4h candles from tr_candles_4h.
        This is the preferred source for HMM warm-up because it can be backfilled
        independently from the live 1m feed horizon.
        """
        cutoff = before or datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT symbol, open_time, open, high, low, close, volume
                FROM tr_candles_4h
                WHERE symbol = $1
                  AND open_time < $2
                ORDER BY open_time DESC
                LIMIT $3
                """,
                symbol,
                cutoff,
                limit,
            )
        return [
            Candle(
                symbol=row["symbol"],
                open_time=row["open_time"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in reversed(rows)
        ]

    async def get_latest_4h_candles(
        self,
        symbol: str,
        limit: int = 200,
        before: Optional[datetime] = None,
        prefer_persisted: bool = True,
    ) -> List[Candle]:
        """
        Return the last completed 4h candles.
        Prefer the persisted backfill table when available; fall back to
        aggregation from el_candles_1m so the worker remains operational.
        """
        if prefer_persisted:
            candles = await self.get_latest_persisted_4h_candles(symbol, limit=limit, before=before)
            if candles:
                return candles
        return await self.get_latest_tf_candles(symbol, tf_minutes=240, limit=limit, before=before)

    async def aggregate_tf_candle(
        self,
        symbol: str,
        bar_open: datetime,
        timeframe_minutes: int,
    ) -> Optional[Candle]:
        """
        Aggregate 1m candles into a single OHLCV candle for the given TF bar.
        Returns None if no 1m candles exist for that bar.
        """
        candles = await self.get_candles_for_bar(symbol, bar_open, timeframe_minutes)
        if not candles:
            return None
        return Candle(
            symbol=symbol,
            open_time=bar_open,
            open=candles[0].open,
            high=max(c.high for c in candles),
            low=min(c.low for c in candles),
            close=candles[-1].close,
            volume=sum(c.volume for c in candles),
        )
