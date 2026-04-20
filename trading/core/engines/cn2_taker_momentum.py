"""
Engine CN2 — Taker Aggression Momentum (crypto-native)

Detects bursts of aggressive market orders (taker buy/sell) confirmed
by rising OI (new positions, not just closing) and non-extreme funding.

SIGNAL_ONLY — records signals, never opens positions.
Requires derivatives data in ctx.extra["derivatives"].

Entry rules:
  LONG:  taker_aggression > 1.5 + oi_delta_1h > 0 + |funding_rate| < 0.05%
  SHORT: taker_aggression < 0.65 + oi_delta_1h > 0 + |funding_rate| < 0.05%
  Confirmation: orderbook_imbalance in same direction

Stop:  1.2x ATR(14) on 15m
Target: 1.8R
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
TAKER_LONG_THRESHOLD = 1.5      # buyers 50%+ more aggressive
TAKER_SHORT_THRESHOLD = 0.65    # sellers dominant
OI_GROWTH_MIN = 0.0             # OI must be growing (new positions)
FUNDING_EXTREME = 0.0005        # |funding| < 0.05% per 8h (not overcrowded)
OB_IMBALANCE_CONFIRM = 0.10     # orderbook tilt confirms direction
REWARD_RISK = 1.8


class CN2TakerMomentumEngine(BaseEngine):
    ENGINE_ID = "cn2_taker_momentum"

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

        taker = deriv.get("taker_aggression")
        oi_delta_1h = deriv.get("oi_delta_1h")
        funding = deriv.get("funding_rate")
        ob_imbalance = deriv.get("orderbook_imbalance")

        if taker is None or oi_delta_1h is None:
            self.set_skip_reason(ctx, "MISSING_CORE_FEATURES")
            return None

        signal_data: Dict[str, Any] = {
            "taker_aggression": taker,
            "oi_delta_1h": oi_delta_1h,
            "funding_rate": funding,
            "orderbook_imbalance": ob_imbalance,
        }

        # OI must be growing (new positions entering, not just closing)
        if oi_delta_1h < OI_GROWTH_MIN:
            self.set_skip_reason(ctx, "OI_NOT_GROWING", oi_delta_1h=oi_delta_1h)
            return None

        # Funding must not be extreme (avoid crowded trades)
        if funding is not None and abs(funding) > FUNDING_EXTREME:
            self.set_skip_reason(
                ctx, "FUNDING_EXTREME",
                funding=funding,
                threshold=FUNDING_EXTREME,
            )
            return None

        direction = None

        # LONG: aggressive buying + OI growing + funding ok
        if taker > TAKER_LONG_THRESHOLD:
            # Optional confirmation: orderbook tilted to buy side
            if ob_imbalance is not None and ob_imbalance < -OB_IMBALANCE_CONFIRM:
                self.set_skip_reason(
                    ctx, "OB_IMBALANCE_CONTRADICTS_LONG",
                    ob_imbalance=ob_imbalance,
                )
                return None
            direction = Direction.LONG
            signal_data["trigger"] = "taker_aggression_long_burst"

        # SHORT: aggressive selling + OI growing + funding ok
        elif taker < TAKER_SHORT_THRESHOLD:
            if ob_imbalance is not None and ob_imbalance > OB_IMBALANCE_CONFIRM:
                self.set_skip_reason(
                    ctx, "OB_IMBALANCE_CONTRADICTS_SHORT",
                    ob_imbalance=ob_imbalance,
                )
                return None
            direction = Direction.SHORT
            signal_data["trigger"] = "taker_aggression_short_burst"

        if direction is None:
            self.set_skip_reason(
                ctx, "TAKER_IN_NEUTRAL_ZONE",
                taker=taker,
                long_threshold=TAKER_LONG_THRESHOLD,
                short_threshold=TAKER_SHORT_THRESHOLD,
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
            reason="TAKER_MOMENTUM",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        # Reserved for future ACTIVE role
        entry = signal.signal_data.get("price_ref", 0.0) or 0.0

        atr = entry * 0.004  # ~0.4% fallback ATR
        stop_dist = 1.2 * atr

        if signal.direction == Direction.LONG:
            stop = entry - stop_dist
            target = entry + stop_dist * REWARD_RISK
        else:
            stop = entry + stop_dist
            target = entry - stop_dist * REWARD_RISK

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
        # Reserved for future ACTIVE role
        return PositionAction.HOLD
