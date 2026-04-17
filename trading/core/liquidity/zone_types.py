"""
Shared data models for all liquidity zone providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


@dataclass
class LiquidityZone:
    price_level: float
    zone_type: str  # EQUAL_HIGH | EQUAL_LOW | SWING_HIGH | SWING_LOW
                    # BULLISH_FVG | BEARISH_FVG
                    # PRIOR_DAY_HIGH | PRIOR_DAY_LOW | PRIOR_WEEK_HIGH | PRIOR_WEEK_LOW
                    # LIQUIDATION_CLUSTER | HVN | LVN (future)
    source: str     # equal_levels | ohlcv_swing | ohlcv_fvg | prior_levels
                    # liquidation_estimator | volume_profile (future)
    intensity_score: float  # 0.0 to 1.0
    timestamp: datetime     # last touch timestamp
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SweepEvent:
    zone: LiquidityZone
    sweep_candle_timestamp: datetime
    wick_extension_bps: float
    direction: str  # UP_SWEEP | DOWN_SWEEP
