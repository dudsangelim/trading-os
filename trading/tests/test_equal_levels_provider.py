"""
Unit tests for EqualLevelsProvider and SweepDetector.
All tests use synthetic candle data — no DB required.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from trading.core.data.candle_reader import Candle
from trading.core.liquidity.providers.equal_levels_provider import EqualLevelsProvider
from trading.core.liquidity.providers.sweep_detector import SweepDetector
from trading.core.liquidity.zone_types import LiquidityZone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, high: float, low: Optional[float] = None, close: Optional[float] = None) -> Candle:
    if low is None:
        low = high - 1.0
    if close is None:
        close = (high + low) / 2
    return Candle(
        symbol="BTCUSDT",
        open_time=_BASE_TS + timedelta(minutes=15 * i),
        open=close - 0.1,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _flat_candles(n: int, high: float = 100.0) -> List[Candle]:
    """n flat candles with no pivot (all equal highs — not local maxima)."""
    return [_candle(i, high) for i in range(n)]


def _series(highs: List[float]) -> List[Candle]:
    """Build candles from a list of high prices (lows = high - 1)."""
    return [_candle(i, h) for i, h in enumerate(highs)]


def _zone(price: float, zone_type: str = "EQUAL_HIGH", intensity: float = 0.5) -> LiquidityZone:
    return LiquidityZone(
        price_level=price,
        zone_type=zone_type,
        source="equal_levels",
        intensity_score=intensity,
        timestamp=_BASE_TS,
        meta={},
    )


provider = EqualLevelsProvider()
detector = SweepDetector()


# ---------------------------------------------------------------------------
# EqualLevelsProvider tests
# ---------------------------------------------------------------------------

def test_returns_empty_when_lookback_exceeds_candles():
    candles = _flat_candles(50)
    result = provider.detect_zones(candles, lookback_bars=200)
    assert result == []


def test_detects_two_equal_highs_within_tolerance():
    # Two pivot highs at 100.0 and 100.05 → diff = 5 bps < 10 bps
    highs = [99.0, 100.0, 98.0, 98.0, 100.05, 98.0, 98.0]
    candles = _series(highs)
    zones = provider.detect_zones(candles, tolerance_bps=10.0, min_touches=2, lookback_bars=7)
    equal_highs = [z for z in zones if z.zone_type == "EQUAL_HIGH"]
    assert len(equal_highs) == 1
    zone = equal_highs[0]
    assert abs(zone.price_level - 100.025) < 0.01
    assert zone.meta["touches"] == 2
    assert zone.source == "equal_levels"


def test_does_not_mark_highs_outside_tolerance():
    # Two pivot highs at 100.0 and 101.5 → diff = 150 bps > 10 bps
    highs = [99.0, 100.0, 98.0, 98.0, 101.5, 98.0, 98.0]
    candles = _series(highs)
    zones = provider.detect_zones(candles, tolerance_bps=10.0, min_touches=2, lookback_bars=7)
    equal_highs = [z for z in zones if z.zone_type == "EQUAL_HIGH"]
    assert len(equal_highs) == 0


def test_intensity_score_increases_with_more_touches():
    # Zone A (3 touches at ~100): more recent (second half of sequence)
    # Zone B (2 touches at ~200): older (first half of sequence)
    # Result: Zone A dominates via both touch count and recency.
    highs = [50.0] * 25
    # Zone B pivots at 2 and 5
    highs[2] = 200.0
    highs[5] = 200.03
    # Zone A pivots at 14, 18, 22 (last A touch at 22)
    highs[14] = 100.0
    highs[18] = 100.02
    highs[22] = 100.01

    candles = _series(highs)
    n = len(candles)
    zones = provider.detect_zones(candles, tolerance_bps=10.0, min_touches=2, lookback_bars=n)
    equal_highs = [z for z in zones if z.zone_type == "EQUAL_HIGH"]
    assert len(equal_highs) == 2

    zone_3 = next(z for z in equal_highs if z.meta["touches"] == 3)
    zone_2 = next(z for z in equal_highs if z.meta["touches"] == 2)
    assert zone_3.intensity_score > zone_2.intensity_score


def test_intensity_score_decreases_with_low_recency():
    # Two zones with 2 touches each, but one has last touch near end (recent)
    # and the other near the beginning (old). lookback_bars=20.
    # Build: zone A peaks at indices 1 and 3 (price ~100), old
    #        zone B peaks at indices 16 and 18 (price ~200), recent
    highs = [0.0] * 20
    highs[0] = 99.0
    highs[1] = 100.0  # pivot A first
    highs[2] = 99.0
    highs[3] = 100.02  # pivot A second (last touch at index 3)
    highs[4] = 99.0
    # fill rest with neutral
    for i in range(5, 15):
        highs[i] = 99.0
    highs[15] = 198.0
    highs[16] = 200.0  # pivot B first
    highs[17] = 198.0
    highs[18] = 200.02  # pivot B second (last touch at index 18)
    highs[19] = 198.0

    candles = _series(highs)
    zones = provider.detect_zones(candles, tolerance_bps=10.0, min_touches=2, lookback_bars=20)
    equal_highs = [z for z in zones if z.zone_type == "EQUAL_HIGH"]
    assert len(equal_highs) == 2

    zone_old = next(z for z in equal_highs if z.price_level < 150)
    zone_recent = next(z for z in equal_highs if z.price_level > 150)

    # zone_old: last touch at idx 3, bars_since = 19-3 = 16, recency = max(0, 1-16/20) = 0.2
    # zone_recent: last touch at idx 18, bars_since = 19-18 = 1, recency = max(0, 1-1/20) = 0.95
    assert zone_recent.intensity_score > zone_old.intensity_score


# ---------------------------------------------------------------------------
# SweepDetector tests
# ---------------------------------------------------------------------------

def test_sweep_detector_identifies_wick_and_reversal():
    # EQUAL_HIGH zone at 100.0, wick_threshold = 5 bps → threshold = 100.05
    # Candle: high = 100.1 (> 100.05), close = 99.8 (< 100.0) → UP_SWEEP
    zone = _zone(100.0, "EQUAL_HIGH")
    sweep_candle = _candle(0, high=100.1, low=99.5, close=99.8)
    sweeps = detector.detect_sweeps([sweep_candle], [zone], wick_threshold_bps=5.0)
    assert len(sweeps) == 1
    assert sweeps[0].direction == "UP_SWEEP"
    assert sweeps[0].wick_extension_bps > 0


def test_sweep_detector_identifies_down_sweep():
    # EQUAL_LOW zone at 100.0, threshold = 99.95
    # Candle: low = 99.9 (< 99.95), close = 100.2 (> 100.0) → DOWN_SWEEP
    zone = _zone(100.0, "EQUAL_LOW")
    sweep_candle = _candle(0, high=100.5, low=99.9, close=100.2)
    sweeps = detector.detect_sweeps([sweep_candle], [zone], wick_threshold_bps=5.0)
    assert len(sweeps) == 1
    assert sweeps[0].direction == "DOWN_SWEEP"
    assert sweeps[0].wick_extension_bps > 0


def test_sweep_detector_no_mark_when_wick_insufficient():
    # EQUAL_HIGH zone at 100.0, threshold = 100.05
    # Candle: high = 100.03 (< 100.05) → wick not enough → no sweep
    zone = _zone(100.0, "EQUAL_HIGH")
    candle = _candle(0, high=100.03, low=99.5, close=99.8)
    sweeps = detector.detect_sweeps([candle], [zone], wick_threshold_bps=5.0)
    assert len(sweeps) == 0


def test_sweep_detector_no_mark_when_close_does_not_reverse():
    # EQUAL_HIGH zone at 100.0, wick extends but close is still above level → no reversal
    zone = _zone(100.0, "EQUAL_HIGH")
    candle = _candle(0, high=100.1, low=99.5, close=100.2)  # closes above 100.0
    sweeps = detector.detect_sweeps([candle], [zone], wick_threshold_bps=5.0)
    assert len(sweeps) == 0


def test_sweep_detector_ignores_non_equal_zone_types():
    zone = LiquidityZone(
        price_level=100.0,
        zone_type="HVN",
        source="volume_profile",
        intensity_score=0.5,
        timestamp=_BASE_TS,
        meta={},
    )
    candle = _candle(0, high=100.1, low=99.5, close=99.8)
    sweeps = detector.detect_sweeps([candle], [zone], wick_threshold_bps=5.0)
    assert len(sweeps) == 0
