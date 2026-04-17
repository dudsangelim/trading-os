"""
Builds enriched liquidity snapshots from Binance-compatible OHLCV data.

Runs all OHLCV-based zone providers and populates typed_zones in the snapshot
so the overlay evaluator can use structured zone context.

Phase 4: when an aggregator is provided, delegates to it and adapts the result
back to LiquiditySnapshot. Legacy path (aggregator=None) is byte-for-byte unchanged.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from ..data.candle_reader import Candle, CandleReader
from .providers.equal_levels_provider import EqualLevelsProvider
from .providers.fair_value_gap_provider import FairValueGapProvider
from .providers.prior_levels_provider import PriorLevelsProvider
from .providers.sweep_detector import SweepDetector
from .providers.swing_provider import SwingProvider
from .zone_models import LiquiditySnapshot, build_liquidity_snapshot

if TYPE_CHECKING:
    from .zone_aggregator import AggregatedLiquiditySnapshot, LiquidityZoneAggregator


class BinanceLiquidityReader:
    def __init__(
        self,
        candle_reader: CandleReader,
        aggregator: Optional["LiquidityZoneAggregator"] = None,
    ) -> None:
        self._reader = candle_reader
        self._aggregator = aggregator
        self._swing = SwingProvider()
        self._equal = EqualLevelsProvider()
        self._sweep = SweepDetector()
        self._fvg = FairValueGapProvider()
        self._prior = PriorLevelsProvider()

    async def snapshot_for_signal(
        self,
        symbol: str,
        bar_timestamp: datetime,
        tf_minutes: int = 15,
        lookback: int = 200,
    ) -> LiquiditySnapshot:
        candles = await self._reader.get_latest_tf_candles(
            symbol,
            tf_minutes=tf_minutes,
            limit=lookback,
            before=bar_timestamp,
        )
        snapshot = build_liquidity_snapshot(
            symbol=symbol, candles=candles, timeframe=f"{tf_minutes}m"
        )

        if not candles:
            return snapshot

        if self._aggregator is not None:
            agg = await self._aggregator.build_snapshot(
                symbol=symbol,
                reference_ts=bar_timestamp,
                candles=candles,
                current_price=candles[-1].close,
            )
            return self._adapt_aggregated_to_legacy(agg, symbol, candles, f"{tf_minutes}m")

        # ── Legacy path (byte-for-byte unchanged from Phase 1.5) ──────────
        typed_zones = []
        typed_zones.extend(self._swing.detect_zones(candles))
        equal_zones = self._equal.detect_zones(candles)
        typed_zones.extend(equal_zones)
        typed_zones.extend(self._fvg.detect_zones(candles))
        typed_zones.extend(self._prior.detect_zones(candles))
        snapshot.typed_zones = typed_zones

        sweep_events = self._sweep.detect_sweeps(candles, equal_zones)
        snapshot.sweep_events = sweep_events

        snapshot.features["typed_zone_count"] = len(typed_zones)
        snapshot.features["swing_high_count"] = sum(1 for z in typed_zones if z.zone_type == "SWING_HIGH")
        snapshot.features["swing_low_count"] = sum(1 for z in typed_zones if z.zone_type == "SWING_LOW")
        snapshot.features["equal_high_count"] = sum(1 for z in typed_zones if z.zone_type == "EQUAL_HIGH")
        snapshot.features["equal_low_count"] = sum(1 for z in typed_zones if z.zone_type == "EQUAL_LOW")
        snapshot.features["bullish_fvg_count"] = sum(1 for z in typed_zones if z.zone_type == "BULLISH_FVG")
        snapshot.features["bearish_fvg_count"] = sum(1 for z in typed_zones if z.zone_type == "BEARISH_FVG")
        snapshot.features["sweep_event_count"] = len(sweep_events)
        return snapshot

    def _adapt_aggregated_to_legacy(
        self,
        agg: "AggregatedLiquiditySnapshot",
        symbol: str,
        candles: List[Candle],
        tf: str,
    ) -> LiquiditySnapshot:
        """Convert AggregatedLiquiditySnapshot → LiquiditySnapshot for overlay compatibility."""
        snapshot = build_liquidity_snapshot(symbol=symbol, candles=candles, timeframe=tf)
        snapshot.typed_zones = agg.all_zones

        # SweepDetector still runs on OHLCV equal zones from aggregated results
        equal_zones = [z for z in agg.all_zones if z.zone_type in {"EQUAL_HIGH", "EQUAL_LOW"}]
        snapshot.sweep_events = self._sweep.detect_sweeps(candles, equal_zones)

        # Merge aggregator feature metadata
        snapshot.features.update(agg.features)
        snapshot.features["typed_zone_count"] = len(agg.all_zones)
        for zone_type in (
            "SWING_HIGH", "SWING_LOW", "EQUAL_HIGH", "EQUAL_LOW",
            "BULLISH_FVG", "BEARISH_FVG", "LIQUIDATION_CLUSTER",
        ):
            key = zone_type.lower() + "_count"
            snapshot.features[key] = sum(1 for z in agg.all_zones if z.zone_type == zone_type)
        snapshot.features["sweep_event_count"] = len(snapshot.sweep_events)
        return snapshot
