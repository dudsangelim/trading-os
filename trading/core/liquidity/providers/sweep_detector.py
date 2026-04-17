"""
Liquidity sweep detector.

A sweep occurs when price wicks through an equal high/low zone but closes
on the opposite side, indicating stop orders were triggered (hunted).
"""
from __future__ import annotations

from typing import List

from ...data.candle_reader import Candle
from ..zone_types import LiquidityZone, SweepEvent


class SweepDetector:
    def detect_sweeps(
        self,
        candles: List[Candle],
        zones: List[LiquidityZone],
        wick_threshold_bps: float = 5.0,
    ) -> List[SweepEvent]:
        sweeps: List[SweepEvent] = []

        for zone in zones:
            if zone.zone_type not in {"EQUAL_HIGH", "EQUAL_LOW"}:
                continue

            price = zone.price_level
            threshold_factor = wick_threshold_bps / 10_000.0

            for candle in candles:
                if zone.zone_type == "EQUAL_HIGH":
                    threshold = price * (1.0 + threshold_factor)
                    if candle.high > threshold and candle.close < price:
                        ext_bps = (candle.high - price) / price * 10_000.0
                        sweeps.append(SweepEvent(
                            zone=zone,
                            sweep_candle_timestamp=candle.open_time,
                            wick_extension_bps=ext_bps,
                            direction="UP_SWEEP",
                        ))
                else:  # EQUAL_LOW
                    threshold = price * (1.0 - threshold_factor)
                    if candle.low < threshold and candle.close > price:
                        ext_bps = (price - candle.low) / price * 10_000.0
                        sweeps.append(SweepEvent(
                            zone=zone,
                            sweep_candle_timestamp=candle.open_time,
                            wick_extension_bps=ext_bps,
                            direction="DOWN_SWEEP",
                        ))

        return sweeps
