"""Unit tests for LiquidityZoneAggregator."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List
from unittest.mock import MagicMock

import pytest

from trading.core.data.candle_reader import Candle
from trading.core.liquidity.zone_aggregator import LiquidityZoneAggregator
from trading.core.liquidity.zone_types import LiquidityZone

_TS = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)


def _zone(price: float, zone_type: str = "SWING_HIGH", source: str = "ohlcv_swing", intensity: float = 0.6) -> LiquidityZone:
    return LiquidityZone(price_level=price, zone_type=zone_type, source=source, intensity_score=intensity, timestamp=_TS)


def _candles(n: int = 10) -> List[Candle]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    from datetime import timedelta
    return [
        Candle(symbol="BTCUSDT", open_time=base + timedelta(minutes=15 * i),
               open=100.0, high=101.0, low=99.0, close=100.5, volume=1000.0)
        for i in range(n)
    ]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# 1. Empty aggregator returns empty snapshot
def test_empty_aggregator_returns_empty():
    agg = LiquidityZoneAggregator([], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert snap.all_zones == []
    assert snap.zones_above == []
    assert snap.zones_below == []
    assert snap.nearest_above_any is None
    assert snap.nearest_below_any is None


# 2. Sync provider zones are collected
def test_sync_provider_zones_collected():
    zones = [_zone(102.0), _zone(98.0, zone_type="SWING_LOW", source="ohlcv_swing")]
    agg = LiquidityZoneAggregator([lambda candles: zones], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert len(snap.all_zones) == 2
    assert len(snap.zones_above) == 1
    assert len(snap.zones_below) == 1


# 3. zones_above sorted by distance ASC
def test_zones_above_sorted_by_distance():
    zones = [_zone(110.0), _zone(103.0), _zone(107.0)]
    agg = LiquidityZoneAggregator([lambda c: zones], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    prices = [z.price_level for z in snap.zones_above]
    assert prices == sorted(prices)


# 4. zones_below sorted by distance ASC (closest first)
def test_zones_below_sorted_by_distance():
    zones = [_zone(90.0, zone_type="SWING_LOW"), _zone(97.0, zone_type="SWING_LOW"), _zone(93.0, zone_type="SWING_LOW")]
    agg = LiquidityZoneAggregator([lambda c: zones], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    prices = [z.price_level for z in snap.zones_below]
    assert prices == sorted(prices, reverse=True)


# 5. nearest_above_any and nearest_below_any are correct
def test_nearest_above_and_below():
    zones = [_zone(105.0), _zone(103.0), _zone(97.0, zone_type="SWING_LOW"), _zone(95.0, zone_type="SWING_LOW")]
    agg = LiquidityZoneAggregator([lambda c: zones], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert snap.nearest_above_any.price_level == 103.0
    assert snap.nearest_below_any.price_level == 97.0


# 6. strongest_above and strongest_below use max intensity
def test_strongest_above_and_below():
    zones = [
        _zone(104.0, intensity=0.5),
        _zone(106.0, intensity=0.9),
        _zone(98.0, zone_type="SWING_LOW", intensity=0.3),
        _zone(96.0, zone_type="SWING_LOW", intensity=0.8),
    ]
    agg = LiquidityZoneAggregator([lambda c: zones], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert snap.strongest_above.price_level == 106.0
    assert snap.strongest_below.price_level == 96.0


# 7. Failing sync provider is captured in sources_failed, snapshot still returns
def test_failing_sync_provider_captured():
    def bad_provider(candles):
        raise RuntimeError("oops")

    agg = LiquidityZoneAggregator([bad_provider], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert snap.all_zones == []
    assert len(snap.sources_failed) >= 1


# 8. Async provider zones are collected
def test_async_provider_zones_collected():
    async def async_provider(symbol, reference_ts):
        return [_zone(105.0, source="liquidation_estimator")]

    agg = LiquidityZoneAggregator([], [async_provider])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert len(snap.all_zones) == 1
    assert snap.zones_above[0].source == "liquidation_estimator"


# 9. Failing async provider captured in sources_failed, other zones not lost
def test_failing_async_provider_captured():
    async def bad_async(symbol, reference_ts):
        raise ValueError("async fail")

    async def good_async(symbol, reference_ts):
        return [_zone(108.0, source="liquidation_estimator")]

    agg = LiquidityZoneAggregator([], [bad_async, good_async])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert len(snap.all_zones) == 1  # good_async's zone retained
    assert len(snap.sources_failed) == 1


# 10. provider_counts tracks per-source zone count
def test_provider_counts_accurate():
    zones = [
        _zone(104.0, source="ohlcv_swing"),
        _zone(106.0, source="ohlcv_swing"),
        _zone(98.0, zone_type="EQUAL_LOW", source="equal_levels"),
    ]
    agg = LiquidityZoneAggregator([lambda c: zones], [])
    snap = _run(agg.build_snapshot("BTCUSDT", _TS, _candles(), current_price=100.0))
    assert snap.provider_counts["ohlcv_swing"] == 2
    assert snap.provider_counts["equal_levels"] == 1
