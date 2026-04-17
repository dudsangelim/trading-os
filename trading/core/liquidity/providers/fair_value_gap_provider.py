"""
Fair Value Gap (FVG / imbalance) zone provider.

A bullish FVG forms in a 3-candle sequence [i-1, i, i+1] when:
    candle[i+1].low > candle[i-1].high
There is an untouched gap between prev-high and next-low.  Price tends to
revisit (fill) it from above, making it a support zone.

A bearish FVG forms when:
    candle[i+1].high < candle[i-1].low
Untouched gap acts as resistance.

The zone price_level is the midpoint of the gap.  meta carries gap_top and
gap_bot so the evaluator can test whether price is inside the range.
"""
from __future__ import annotations

from typing import List

from ...data.candle_reader import Candle
from ..zone_types import LiquidityZone


class FairValueGapProvider:
    def detect_zones(
        self,
        candles: List[Candle],
        min_gap_bps: float = 5.0,
        lookback_bars: int = 100,
        max_zones: int = 10,
    ) -> List[LiquidityZone]:
        if lookback_bars > len(candles):
            return []

        window = candles[-lookback_bars:]
        n = len(window)

        zones: List[LiquidityZone] = []
        bullish_found = 0
        bearish_found = 0
        half = max_zones // 2 + max_zones % 2  # ceiling half

        # Scan most recent first so we fill quota with freshest gaps
        for i in range(n - 2, 0, -1):
            if bullish_found + bearish_found >= max_zones:
                break

            prev = window[i - 1]
            nxt = window[i + 1]

            # Bullish FVG
            if nxt.low > prev.high and bullish_found < half:
                gap_bot = prev.high
                gap_top = nxt.low
                gap_bps = (gap_top - gap_bot) / gap_bot * 10_000.0 if gap_bot > 0 else 0.0
                if gap_bps >= min_gap_bps:
                    midpoint = (gap_top + gap_bot) / 2
                    bars_ago = (n - 1) - i
                    zones.append(_fvg_zone(window[i].open_time, midpoint, "BULLISH_FVG", gap_top, gap_bot, gap_bps, bars_ago, n))
                    bullish_found += 1

            # Bearish FVG
            elif nxt.high < prev.low and bearish_found < half:
                gap_bot = nxt.high
                gap_top = prev.low
                gap_bps = (gap_top - gap_bot) / gap_bot * 10_000.0 if gap_bot > 0 else 0.0
                if gap_bps >= min_gap_bps:
                    midpoint = (gap_top + gap_bot) / 2
                    bars_ago = (n - 1) - i
                    zones.append(_fvg_zone(window[i].open_time, midpoint, "BEARISH_FVG", gap_top, gap_bot, gap_bps, bars_ago, n))
                    bearish_found += 1

        return zones


def _fvg_zone(
    ts,
    midpoint: float,
    zone_type: str,
    gap_top: float,
    gap_bot: float,
    gap_bps: float,
    bars_ago: int,
    n: int,
) -> LiquidityZone:
    recency = max(0.0, 1.0 - bars_ago / max(n, 1))
    intensity = min(1.0, recency * 0.6 + min(gap_bps / 100.0, 0.4))
    return LiquidityZone(
        price_level=round(midpoint, 8),
        zone_type=zone_type,
        source="ohlcv_fvg",
        intensity_score=intensity,
        timestamp=ts,
        meta={
            "gap_top": round(gap_top, 8),
            "gap_bot": round(gap_bot, 8),
            "gap_bps": round(gap_bps, 4),
            "bars_ago": bars_ago,
        },
    )
