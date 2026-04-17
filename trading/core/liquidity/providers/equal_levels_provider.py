"""
Equal highs/lows liquidity zone provider.

Detects price levels where multiple swing highs or lows cluster within a
tolerance band, indicating accumulated stop orders or liquidity pools.
"""
from __future__ import annotations

from typing import List, Tuple

from ...data.candle_reader import Candle
from ..zone_types import LiquidityZone


class EqualLevelsProvider:
    def detect_zones(
        self,
        candles: List[Candle],
        tolerance_bps: float = 10.0,
        min_touches: int = 2,
        lookback_bars: int = 200,
    ) -> List[LiquidityZone]:
        if lookback_bars > len(candles):
            return []

        window = candles[-lookback_bars:]
        n = len(window)

        # Collect pivot highs: local maxima (need at least 3 candles)
        pivot_highs: List[Tuple[int, float]] = []
        for i in range(1, n - 1):
            if window[i].high > window[i - 1].high and window[i].high > window[i + 1].high:
                pivot_highs.append((i, window[i].high))

        # Collect pivot lows: local minima
        pivot_lows: List[Tuple[int, float]] = []
        for i in range(1, n - 1):
            if window[i].low < window[i - 1].low and window[i].low < window[i + 1].low:
                pivot_lows.append((i, window[i].low))

        zones: List[LiquidityZone] = []
        zones.extend(
            self._cluster_to_zones(pivot_highs, window, "EQUAL_HIGH", tolerance_bps, min_touches, lookback_bars)
        )
        zones.extend(
            self._cluster_to_zones(pivot_lows, window, "EQUAL_LOW", tolerance_bps, min_touches, lookback_bars)
        )
        return zones

    def _cluster_to_zones(
        self,
        pivots: List[Tuple[int, float]],
        window: List[Candle],
        zone_type: str,
        tolerance_bps: float,
        min_touches: int,
        lookback_bars: int,
    ) -> List[LiquidityZone]:
        if not pivots:
            return []

        sorted_pivots = sorted(pivots, key=lambda p: p[1])

        groups: List[List[Tuple[int, float]]] = []
        current_group: List[Tuple[int, float]] = [sorted_pivots[0]]

        for pivot in sorted_pivots[1:]:
            ref_price = current_group[0][1]
            diff_bps = abs(pivot[1] - ref_price) / ref_price * 10_000.0
            if diff_bps <= tolerance_bps:
                current_group.append(pivot)
            else:
                groups.append(current_group)
                current_group = [pivot]
        groups.append(current_group)

        zones: List[LiquidityZone] = []
        for group in groups:
            if len(group) < min_touches:
                continue

            price_level = sum(p[1] for p in group) / len(group)
            touches = len(group)

            last_touch_item = max(group, key=lambda p: p[0])
            last_touch_idx = last_touch_item[0]
            last_touch_ts = window[last_touch_idx].open_time

            bars_since_last_touch = (lookback_bars - 1) - last_touch_idx
            recency_factor = max(0.0, 1.0 - (bars_since_last_touch / lookback_bars))
            intensity_score = min(1.0, (touches - 1) * 0.2 + recency_factor)

            zones.append(LiquidityZone(
                price_level=price_level,
                zone_type=zone_type,
                source="equal_levels",
                intensity_score=intensity_score,
                timestamp=last_touch_ts,
                meta={
                    "tolerance_bps": tolerance_bps,
                    "lookback_bars": lookback_bars,
                    "touches": touches,
                },
            ))
        return zones
