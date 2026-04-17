"""
Swing high/low zone provider.

A swing high at index i is a local maximum where candles[i].high is strictly
greater than the highest high among the n_bars candles on each side.
Symmetrically for swing lows.
"""
from __future__ import annotations

from typing import List

from ...data.candle_reader import Candle
from ..zone_types import LiquidityZone


class SwingProvider:
    def detect_zones(
        self,
        candles: List[Candle],
        n_bars: int = 3,
        lookback_bars: int = 200,
        max_zones: int = 30,
    ) -> List[LiquidityZone]:
        if lookback_bars > len(candles):
            return []

        window = candles[-lookback_bars:]
        n = len(window)

        swing_highs: List[tuple[int, float]] = []
        swing_lows: List[tuple[int, float]] = []

        for i in range(n_bars, n - n_bars):
            left_slice = window[i - n_bars : i]
            right_slice = window[i + 1 : i + n_bars + 1]

            if window[i].high > max(c.high for c in left_slice) and window[i].high > max(
                c.high for c in right_slice
            ):
                swing_highs.append((i, window[i].high))

            if window[i].low < min(c.low for c in left_slice) and window[i].low < min(
                c.low for c in right_slice
            ):
                swing_lows.append((i, window[i].low))

        # Most recent first
        swing_highs.sort(key=lambda x: x[0], reverse=True)
        swing_lows.sort(key=lambda x: x[0], reverse=True)

        zones: List[LiquidityZone] = []

        for rank, (idx, price) in enumerate(swing_highs[: max_zones // 2]):
            zones.append(_make_zone(window, idx, price, "SWING_HIGH", n, rank))

        for rank, (idx, price) in enumerate(swing_lows[: max_zones // 2]):
            zones.append(_make_zone(window, idx, price, "SWING_LOW", n, rank))

        return zones


def _make_zone(
    window: List[Candle],
    idx: int,
    price: float,
    zone_type: str,
    n: int,
    rank: int,
) -> LiquidityZone:
    bars_ago = (n - 1) - idx
    recency = max(0.0, 1.0 - bars_ago / max(n, 1))

    # How much the extreme stands out vs its immediate neighbors
    neighbors = [j for j in range(max(0, idx - 3), min(n, idx + 4)) if j != idx]
    if zone_type == "SWING_HIGH":
        ref = max((window[j].high for j in neighbors), default=price)
        prominence_bps = (price - ref) / price * 10_000.0 if price > 0 else 0.0
    else:
        ref = min((window[j].low for j in neighbors), default=price)
        prominence_bps = (ref - price) / price * 10_000.0 if price > 0 else 0.0

    intensity = min(1.0, recency * 0.5 + min(max(prominence_bps, 0.0) / 100.0, 0.5))

    return LiquidityZone(
        price_level=round(price, 8),
        zone_type=zone_type,
        source="ohlcv_swing",
        intensity_score=intensity,
        timestamp=window[idx].open_time,
        meta={"bars_ago": bars_ago, "prominence_bps": round(prominence_bps, 4), "rank": rank},
    )
