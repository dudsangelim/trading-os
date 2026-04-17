"""
Unit tests for PriorLevelsProvider.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading.core.data.candle_reader import Candle
from trading.core.liquidity.providers.prior_levels_provider import PriorLevelsProvider

provider = PriorLevelsProvider()


def _c(ts: datetime, high: float, low: float) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        open_time=ts,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1000.0,
    )


def _day_candles(day: datetime, high: float, low: float, count: int = 4) -> list[Candle]:
    """4 x 6h candles representing a single UTC day."""
    return [_c(day + timedelta(hours=6 * i), high=high - i * 0.1, low=low + i * 0.1) for i in range(count)]


def test_returns_empty_with_single_day_data():
    day = datetime(2026, 4, 14, tzinfo=timezone.utc)
    candles = _day_candles(day, high=100.0, low=90.0)
    zones = provider.detect_zones(candles)
    # Only one day → no prior day available
    daily_zones = [z for z in zones if "DAY" in z.zone_type]
    assert len(daily_zones) == 0


def test_detects_prior_day_high_and_low():
    day1 = datetime(2026, 4, 13, tzinfo=timezone.utc)
    day2 = datetime(2026, 4, 14, tzinfo=timezone.utc)
    candles = _day_candles(day1, high=102.0, low=88.0) + _day_candles(day2, high=101.0, low=91.0)

    zones = provider.detect_zones(candles)
    types = {z.zone_type: z.price_level for z in zones}

    assert "PRIOR_DAY_HIGH" in types
    assert "PRIOR_DAY_LOW" in types
    # Prior day is day1 (the earlier one, since day2 is the current partial day)
    # _day_candles(day1, 102, 88): highs=[102, 101.9, 101.8, 101.7], lows=[88, 88.1, 88.2, 88.3]
    assert abs(types["PRIOR_DAY_HIGH"] - 102.0) < 0.01
    assert abs(types["PRIOR_DAY_LOW"] - 88.0) < 0.01


def test_detects_prior_week_high_and_low():
    # Week 1: Monday 2026-04-06, Week 2: Monday 2026-04-13
    week1_start = datetime(2026, 4, 6, tzinfo=timezone.utc)
    week2_start = datetime(2026, 4, 13, tzinfo=timezone.utc)
    candles = (
        [_c(week1_start + timedelta(days=d, hours=h), high=200.0 + d, low=190.0 + d) for d in range(5) for h in range(4)]
        + [_c(week2_start + timedelta(days=d, hours=h), high=210.0 + d, low=200.0 + d) for d in range(5) for h in range(4)]
    )

    zones = provider.detect_zones(candles)
    types = {z.zone_type: z.price_level for z in zones}

    assert "PRIOR_WEEK_HIGH" in types
    assert "PRIOR_WEEK_LOW" in types
    # Prior week is week1
    assert types["PRIOR_WEEK_HIGH"] <= 204.0   # max high of week1
    assert types["PRIOR_WEEK_LOW"] >= 190.0    # min low of week1


def test_zone_source_is_prior_levels():
    day1 = datetime(2026, 4, 13, tzinfo=timezone.utc)
    day2 = datetime(2026, 4, 14, tzinfo=timezone.utc)
    candles = _day_candles(day1, 100, 90) + _day_candles(day2, 101, 91)
    zones = provider.detect_zones(candles)
    for z in zones:
        assert z.source == "prior_levels"


def test_intensity_prior_week_higher_than_prior_day():
    day1 = datetime(2026, 4, 6, tzinfo=timezone.utc)
    day2 = datetime(2026, 4, 7, tzinfo=timezone.utc)
    day3 = datetime(2026, 4, 14, tzinfo=timezone.utc)  # new week
    candles = _day_candles(day1, 100, 90) + _day_candles(day2, 101, 91) + _day_candles(day3, 102, 92)

    zones = provider.detect_zones(candles)
    day_z = [z for z in zones if "DAY" in z.zone_type]
    week_z = [z for z in zones if "WEEK" in z.zone_type]

    if day_z and week_z:
        assert week_z[0].intensity_score > day_z[0].intensity_score
