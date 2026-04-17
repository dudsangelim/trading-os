"""
Unit tests for FairValueGapProvider.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading.core.data.candle_reader import Candle
from trading.core.liquidity.providers.fair_value_gap_provider import FairValueGapProvider

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
provider = FairValueGapProvider()


def _c(i: int, high: float, low: float, close: float | None = None) -> Candle:
    if close is None:
        close = (high + low) / 2
    return Candle(
        symbol="BTCUSDT",
        open_time=_BASE + timedelta(minutes=15 * i),
        open=close - 0.1,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def test_returns_empty_when_lookback_exceeds_candles():
    candles = [_c(i, 100.0, 99.0) for i in range(5)]
    assert provider.detect_zones(candles, lookback_bars=50) == []


def test_detects_bullish_fvg():
    # Three candles: prev.high=100, impulse, next.low=101.5 → gap=100-101.5
    candles = [
        _c(0, high=100.0, low=98.0),   # prev: high=100
        _c(1, high=103.0, low=100.5),  # impulse (strong bullish)
        _c(2, high=104.0, low=101.5),  # next: low=101.5 > prev.high=100
    ]
    # Wrap in a larger series to satisfy lookback
    full = [_c(i, 99.0, 98.0) for i in range(100)]
    full[-3] = _c(97, high=100.0, low=98.0)
    full[-2] = _c(98, high=103.0, low=100.5)
    full[-1] = _c(99, high=104.0, low=101.5)

    zones = provider.detect_zones(full, min_gap_bps=5.0, lookback_bars=100)
    bullish = [z for z in zones if z.zone_type == "BULLISH_FVG"]
    assert len(bullish) >= 1
    zone = bullish[0]
    assert zone.meta["gap_bot"] == 100.0
    assert zone.meta["gap_top"] == 101.5
    assert zone.meta["gap_bps"] > 0
    assert zone.source == "ohlcv_fvg"


def test_detects_bearish_fvg():
    # prev.low=98, next.high=96.5 → gap from 96.5 to 98
    full = [_c(i, 99.0, 98.0) for i in range(100)]
    full[-3] = _c(97, high=101.0, low=98.0)  # prev: low=98
    full[-2] = _c(98, high=99.0, low=95.0)   # impulse (strong bearish)
    full[-1] = _c(99, high=96.5, low=94.0)   # next: high=96.5 < prev.low=98

    zones = provider.detect_zones(full, min_gap_bps=5.0, lookback_bars=100)
    bearish = [z for z in zones if z.zone_type == "BEARISH_FVG"]
    assert len(bearish) >= 1
    zone = bearish[0]
    assert zone.meta["gap_bot"] == 96.5
    assert zone.meta["gap_top"] == 98.0


def test_ignores_gap_below_min_bps():
    # Exactly 3 candles, lookback=3: one potential bullish FVG with gap < 5 bps
    # prev.high=100.0, next.low=100.001 → gap = 0.001, bps = 0.1 → below min_gap_bps=5.0
    candles = [
        _c(0, high=100.0, low=98.5),    # prev
        _c(1, high=100.05, low=100.0),  # impulse
        _c(2, high=100.1, low=100.001), # next: low(100.001) > prev.high(100.0) but tiny gap
    ]
    zones = provider.detect_zones(candles, min_gap_bps=5.0, lookback_bars=3)
    bullish = [z for z in zones if z.zone_type == "BULLISH_FVG"]
    assert len(bullish) == 0


def test_max_zones_respected():
    # Create many bullish FVGs
    candles = []
    for i in range(0, 150, 3):
        candles.append(_c(i,   high=100.0,         low=99.0))
        candles.append(_c(i+1, high=103.0 + i*0.1, low=100.5))
        candles.append(_c(i+2, high=104.0 + i*0.1, low=101.5))

    zones = provider.detect_zones(candles, min_gap_bps=1.0, lookback_bars=len(candles), max_zones=6)
    assert len([z for z in zones if z.zone_type == "BULLISH_FVG"]) <= 4


def test_midpoint_is_center_of_gap():
    full = [_c(i, 99.0, 98.0) for i in range(100)]
    full[-3] = _c(97, high=100.0, low=98.0)
    full[-2] = _c(98, high=103.0, low=100.5)
    full[-1] = _c(99, high=104.0, low=101.0)  # gap: 100→101, mid=100.5

    zones = provider.detect_zones(full, min_gap_bps=1.0, lookback_bars=100)
    bullish = [z for z in zones if z.zone_type == "BULLISH_FVG"]
    assert len(bullish) >= 1
    zone = bullish[0]
    expected_mid = (zone.meta["gap_top"] + zone.meta["gap_bot"]) / 2
    assert abs(zone.price_level - expected_mid) < 0.0001
