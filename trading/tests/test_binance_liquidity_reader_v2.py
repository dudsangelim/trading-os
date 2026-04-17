"""Tests for BinanceLiquidityReader — legacy mode and aggregator mode."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.core.data.candle_reader import Candle
from trading.core.liquidity.binance_liquidity_reader import BinanceLiquidityReader
from trading.core.liquidity.zone_aggregator import AggregatedLiquiditySnapshot, LiquidityZoneAggregator
from trading.core.liquidity.zone_types import LiquidityZone

_TS = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)


def _candles(n: int = 20, start_price: float = 100.0) -> List[Candle]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    price = start_price
    candles = []
    for i in range(n):
        candles.append(Candle(
            symbol="BTCUSDT",
            open_time=base + timedelta(minutes=15 * i),
            open=price, high=price + 1.0, low=price - 0.5, close=price + 0.3,
            volume=1000.0,
        ))
        price += 0.1
    return candles


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_legacy_mode_no_aggregator():
    """When aggregator=None, snapshot is built from legacy OHLCV providers."""
    mock_reader = MagicMock()
    mock_reader.get_latest_tf_candles = AsyncMock(return_value=_candles())
    reader = BinanceLiquidityReader(mock_reader, aggregator=None)

    snap = _run(reader.snapshot_for_signal("BTCUSDT", _TS, tf_minutes=15))
    assert snap.symbol == "BTCUSDT"
    assert snap.timeframe == "15m"
    assert isinstance(snap.typed_zones, list)
    assert "typed_zone_count" in snap.features


def test_aggregator_mode_uses_aggregator():
    """When aggregator is provided, snapshot is built from aggregated zones."""
    mock_reader = MagicMock()
    mock_reader.get_latest_tf_candles = AsyncMock(return_value=_candles())

    mock_zone = LiquidityZone(price_level=105.0, zone_type="LIQUIDATION_CLUSTER",
                               source="liquidation_estimator", intensity_score=0.8, timestamp=_TS)
    mock_agg_snap = AggregatedLiquiditySnapshot(
        symbol="BTCUSDT",
        reference_ts=_TS,
        all_zones=[mock_zone],
        zones_above=[mock_zone],
        zones_below=[],
        nearest_above_any=mock_zone,
        nearest_below_any=None,
        strongest_above=mock_zone,
        strongest_below=None,
        sources_active=["liquidation_estimator"],
        sources_failed=[],
        provider_counts={"liquidation_estimator": 1},
        features={"agg_total_zones": 1},
    )

    mock_aggregator = MagicMock(spec=LiquidityZoneAggregator)
    mock_aggregator.build_snapshot = AsyncMock(return_value=mock_agg_snap)

    reader = BinanceLiquidityReader(mock_reader, aggregator=mock_aggregator)
    snap = _run(reader.snapshot_for_signal("BTCUSDT", _TS, tf_minutes=15))

    assert snap.symbol == "BTCUSDT"
    assert any(z.zone_type == "LIQUIDATION_CLUSTER" for z in snap.typed_zones)
    assert snap.features.get("agg_total_zones") == 1
    assert snap.features.get("typed_zone_count") == 1
    mock_aggregator.build_snapshot.assert_called_once()


def test_empty_candles_returns_empty_snapshot():
    """If candle reader returns empty list, snapshot is empty — both modes."""
    mock_reader = MagicMock()
    mock_reader.get_latest_tf_candles = AsyncMock(return_value=[])
    reader = BinanceLiquidityReader(mock_reader, aggregator=None)

    snap = _run(reader.snapshot_for_signal("BTCUSDT", _TS, tf_minutes=15))
    assert snap.symbol == "BTCUSDT"
    assert snap.typed_zones == []
