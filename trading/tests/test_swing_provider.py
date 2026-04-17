"""
Unit tests for SwingProvider.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from trading.core.data.candle_reader import Candle
from trading.core.liquidity.providers.swing_provider import SwingProvider

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
provider = SwingProvider()


def _c(i: int, high: float, low: float) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        open_time=_BASE + timedelta(minutes=15 * i),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1000.0,
    )


def _series(highs: List[float], lows: List[float] | None = None) -> List[Candle]:
    if lows is None:
        lows = [h - 1.0 for h in highs]
    return [_c(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]


def test_returns_empty_when_lookback_exceeds_candles():
    candles = _series([100.0] * 10)
    assert provider.detect_zones(candles, n_bars=3, lookback_bars=50) == []


def test_detects_single_swing_high():
    # Candle 3 is a local maximum with 3 bars on each side
    highs = [99, 99, 99, 105, 99, 99, 99]
    candles = _series(highs)
    zones = provider.detect_zones(candles, n_bars=3, lookback_bars=7)
    swing_highs = [z for z in zones if z.zone_type == "SWING_HIGH"]
    assert len(swing_highs) == 1
    assert swing_highs[0].price_level == 105.0
    assert swing_highs[0].source == "ohlcv_swing"


def test_detects_single_swing_low():
    highs = [101, 101, 101, 101, 101, 101, 101]
    lows =  [99,  99,  99,  90,  99,  99,  99]
    candles = _series(highs, lows)
    zones = provider.detect_zones(candles, n_bars=3, lookback_bars=7)
    swing_lows = [z for z in zones if z.zone_type == "SWING_LOW"]
    assert len(swing_lows) == 1
    assert swing_lows[0].price_level == 90.0


def test_no_swing_at_edge_candles():
    # First and last n_bars candles cannot form confirmed swings
    highs = [110, 99, 99, 105, 99, 99, 110]
    candles = _series(highs)
    zones = provider.detect_zones(candles, n_bars=3, lookback_bars=7)
    swing_highs = [z for z in zones if z.zone_type == "SWING_HIGH"]
    # index 0 and 6 are at edges → only index 3 can qualify
    assert all(abs(z.price_level - 105.0) < 0.01 for z in swing_highs)


def test_multiple_swings_returned_most_recent_first():
    # Two swing highs: at index 3 (price 105) and index 9 (price 106)
    highs = [99] * 13
    highs[3] = 105.0
    highs[9] = 106.0
    candles = _series(highs)
    zones = provider.detect_zones(candles, n_bars=3, lookback_bars=13)
    swing_highs = [z for z in zones if z.zone_type == "SWING_HIGH"]
    # Most recent (index 9) first
    assert swing_highs[0].price_level == 106.0
    assert swing_highs[1].price_level == 105.0


def test_intensity_higher_for_more_recent_swing():
    highs = [99] * 13
    highs[3] = 105.0   # older
    highs[9] = 106.0   # more recent
    candles = _series(highs)
    zones = provider.detect_zones(candles, n_bars=3, lookback_bars=13)
    swing_highs = [z for z in zones if z.zone_type == "SWING_HIGH"]
    recent = next(z for z in swing_highs if abs(z.price_level - 106.0) < 0.01)
    older = next(z for z in swing_highs if abs(z.price_level - 105.0) < 0.01)
    assert recent.intensity_score >= older.intensity_score


def test_max_zones_respected():
    highs = [99] * 30
    for i in range(3, 27, 3):
        highs[i] = 100 + i
    candles = _series(highs)
    zones = provider.detect_zones(candles, n_bars=3, lookback_bars=30, max_zones=6)
    assert len([z for z in zones if z.zone_type == "SWING_HIGH"]) <= 3
