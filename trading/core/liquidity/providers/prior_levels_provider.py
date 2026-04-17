"""
Prior day / prior week high-low zone provider.

Groups candles by UTC calendar day and ISO week, then returns the prior
completed day and prior completed week as reference price levels.
Only a completed period qualifies — the current (partial) day/week is skipped.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Tuple

from ...data.candle_reader import Candle
from ..zone_types import LiquidityZone


class PriorLevelsProvider:
    def detect_zones(self, candles: List[Candle]) -> List[LiquidityZone]:
        if not candles:
            return []

        daily: Dict[date, Tuple[float, float]] = {}
        weekly: Dict[Tuple[int, int], Tuple[float, float]] = {}

        for c in candles:
            d = c.open_time.date()
            iso = c.open_time.isocalendar()
            wk = (iso.year, iso.week)  # type: ignore[attr-defined]

            if d not in daily:
                daily[d] = (c.high, c.low)
            else:
                h, l = daily[d]
                daily[d] = (max(h, c.high), min(l, c.low))

            if wk not in weekly:
                weekly[wk] = (c.high, c.low)
            else:
                h, l = weekly[wk]
                weekly[wk] = (max(h, c.high), min(l, c.low))

        last_ts = candles[-1].open_time
        sorted_days = sorted(daily.keys(), reverse=True)
        sorted_weeks = sorted(weekly.keys(), reverse=True)

        zones: List[LiquidityZone] = []

        if len(sorted_days) >= 2:
            prior_day = sorted_days[1]
            high, low = daily[prior_day]
            zones.append(LiquidityZone(
                price_level=round(high, 8),
                zone_type="PRIOR_DAY_HIGH",
                source="prior_levels",
                intensity_score=0.75,
                timestamp=last_ts,
                meta={"day": str(prior_day)},
            ))
            zones.append(LiquidityZone(
                price_level=round(low, 8),
                zone_type="PRIOR_DAY_LOW",
                source="prior_levels",
                intensity_score=0.75,
                timestamp=last_ts,
                meta={"day": str(prior_day)},
            ))

        if len(sorted_weeks) >= 2:
            prior_week = sorted_weeks[1]
            high, low = weekly[prior_week]
            zones.append(LiquidityZone(
                price_level=round(high, 8),
                zone_type="PRIOR_WEEK_HIGH",
                source="prior_levels",
                intensity_score=0.9,
                timestamp=last_ts,
                meta={"year": prior_week[0], "week": prior_week[1]},
            ))
            zones.append(LiquidityZone(
                price_level=round(low, 8),
                zone_type="PRIOR_WEEK_LOW",
                source="prior_levels",
                intensity_score=0.9,
                timestamp=last_ts,
                meta={"year": prior_week[0], "week": prior_week[1]},
            ))

        return zones
