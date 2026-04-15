"""
Builds liquidity snapshots from Binance-compatible OHLCV data already stored
in the local trading database.
"""
from __future__ import annotations

from datetime import datetime

from ..data.candle_reader import CandleReader
from .zone_models import LiquiditySnapshot, build_liquidity_snapshot


class BinanceLiquidityReader:
    def __init__(self, candle_reader: CandleReader) -> None:
        self._reader = candle_reader

    async def snapshot_for_signal(
        self,
        symbol: str,
        bar_timestamp: datetime,
        tf_minutes: int = 15,
        lookback: int = 64,
    ) -> LiquiditySnapshot:
        candles = await self._reader.get_latest_tf_candles(
            symbol,
            tf_minutes=tf_minutes,
            limit=lookback,
            before=bar_timestamp,
        )
        return build_liquidity_snapshot(symbol=symbol, candles=candles, timeframe=f"{tf_minutes}m")
