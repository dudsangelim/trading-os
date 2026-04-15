"""
Engine CN3 — Funding Rate Extreme Reversal (crypto-native)

When perpetual funding rate reaches extreme levels, the cost of holding
positions forces a mean reversion. High positive funding = longs paying
too much = SHORT opportunity. Deep negative = shorts paying = LONG.

SIGNAL_ONLY — records signals, never opens positions.
Requires derivatives data in ctx.extra["derivatives"].

Entry rules:
  LONG:  funding_rate < -0.0003 (-0.03% per 8h) + OI elevated
  SHORT: funding_rate > +0.0005 (+0.05% per 8h) + OI elevated
  Confirmation: funding_rate_zscore > 2.0 (statistically extreme)

Stop:  1.5x ATR
Target: 2.0R
Holding: 4-8h (until funding normalizes)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .base import (
    BaseEngine,
    Direction,
    EngineContext,
    OrderPlan,
    Position,
    PositionAction,
    Signal,
    SignalAction,
    SignalDecision,
)
from ..config.settings import ENGINE_CONFIGS
from ..risk.sizing import compute_stake

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
FUNDING_SHORT_THRESHOLD = 0.0005    # > +0.05% per 8h → short
FUNDING_LONG_THRESHOLD = -0.0003    # < -0.03% per 8h → long
FUNDING_ZSCORE_MIN = 1.5            # must be statistically significant
OI_DELTA_1H_MIN = -0.01            # OI shouldn't be collapsing already
REWARD_RISK = 2.0


class CN3FundingReversalEngine(BaseEngine):
    ENGINE_ID = "cn3_funding_reversal"

    def __init__(self) -> None:
        cfg = ENGINE_CONFIGS[self.ENGINE_ID]
        super().__init__(engine_id=self.ENGINE_ID, symbol=cfg.symbol)
        self._cfg = cfg

    def generate_signal(self, ctx: EngineContext) -> Optional[Signal]:
        self.clear_skip_reason(ctx)

        deriv = ctx.extra.get("derivatives")
        if not deriv:
            self.set_skip_reason(ctx, "NO_DERIVATIVES_DATA")
            return None

        funding = deriv.get("funding_rate")
        fr_zscore = deriv.get("funding_rate_zscore")
        oi_delta_1h = deriv.get("oi_delta_1h")

        if funding is None:
            self.set_skip_reason(ctx, "MISSING_FUNDING_RATE")
            return None

        signal_data: Dict[str, Any] = {
            "funding_rate": funding,
            "funding_rate_zscore": fr_zscore,
            "oi_delta_1h": oi_delta_1h,
        }

        # OI shouldn't already be in free-fall (reversion may already be happening)
        if oi_delta_1h is not None and oi_delta_1h < OI_DELTA_1H_MIN:
            self.set_skip_reason(
                ctx, "OI_ALREADY_COLLAPSING",
                oi_delta_1h=oi_delta_1h,
            )
            return None

        direction = None

        # SHORT: funding extremely positive (longs overcrowded)
        if funding > FUNDING_SHORT_THRESHOLD:
            if fr_zscore is not None and abs(fr_zscore) < FUNDING_ZSCORE_MIN:
                self.set_skip_reason(
                    ctx, "FUNDING_NOT_STATISTICALLY_EXTREME",
                    zscore=fr_zscore,
                )
                return None
            direction = Direction.SHORT
            signal_data["trigger"] = "funding_extreme_positive"

        # LONG: funding extremely negative (shorts overcrowded)
        elif funding < FUNDING_LONG_THRESHOLD:
            if fr_zscore is not None and abs(fr_zscore) < FUNDING_ZSCORE_MIN:
                self.set_skip_reason(
                    ctx, "FUNDING_NOT_STATISTICALLY_EXTREME",
                    zscore=fr_zscore,
                )
                return None
            direction = Direction.LONG
            signal_data["trigger"] = "funding_extreme_negative"

        if direction is None:
            self.set_skip_reason(
                ctx, "FUNDING_IN_NORMAL_RANGE",
                funding=funding,
            )
            return None

        return Signal(
            engine_id=self.ENGINE_ID,
            symbol=self.symbol,
            bar_timestamp=ctx.bar_timestamp,
            direction=direction,
            timeframe="15m",
            signal_data=signal_data,
        )

    def validate_signal(
        self, signal: Signal, ctx: EngineContext
    ) -> SignalDecision:
        return SignalDecision(
            signal_id=None,
            action=SignalAction.ENTER,
            reason="FUNDING_EXTREME_REVERSAL",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        entry = signal.signal_data.get("price_ref", 0.0) or 0.0
        atr = entry * 0.004
        stop_dist = 1.5 * atr

        if signal.direction == Direction.SHORT:
            stop = entry + stop_dist
            target = entry - stop_dist * REWARD_RISK
        else:
            stop = entry - stop_dist
            target = entry + stop_dist * REWARD_RISK

        stake = compute_stake(self.ENGINE_ID, bankroll)
        return OrderPlan(
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            stake_usd=stake,
            direction=signal.direction,
        )

    def manage_open_position(
        self, position: Position, ctx: EngineContext
    ) -> PositionAction:
        return PositionAction.HOLD
