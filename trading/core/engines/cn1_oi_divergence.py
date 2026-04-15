"""
Engine CN1 — OI-Price Divergence (crypto-native)

Detects divergence between price direction and open interest changes.
When price makes a new high/low but OI is declining, the move is driven
by position closures (not new money) and tends to reverse.

SIGNAL_ONLY — records signals, never opens positions.
Requires derivatives data in ctx.extra["derivatives"].

Entry rules:
  LONG:  price makes 4h low + oi_delta_4h < -0.02 (OI dropping = shorts closing)
  SHORT: price makes 4h high + oi_delta_4h < -0.02 (OI dropping = longs closing)
  Confirmation: taker_aggression fading in the direction of the move

Stop:  1.5x ATR(14) on 15m
Target: 2.0R
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
OI_DELTA_4H_THRESHOLD = -0.02       # OI must drop >2% over 4h
TAKER_FADE_LONG_MAX = 1.3           # for SHORT: buyers not dominant
TAKER_FADE_SHORT_MIN = 0.75         # for LONG: sellers not dominant
PRICE_LOOKBACK_BARS = 16            # 4h = 16 x 15m bars
ATR_PERIOD = 14
REWARD_RISK = 2.0


class CN1OiDivergenceEngine(BaseEngine):
    ENGINE_ID = "cn1_oi_divergence"

    def __init__(self) -> None:
        cfg = ENGINE_CONFIGS[self.ENGINE_ID]
        super().__init__(engine_id=self.ENGINE_ID, symbol=cfg.symbol)
        self._cfg = cfg

    # ------------------------------------------------------------------
    # No special state to persist (stateless signal generation)
    # ------------------------------------------------------------------

    def generate_signal(self, ctx: EngineContext) -> Optional[Signal]:
        self.clear_skip_reason(ctx)

        deriv = ctx.extra.get("derivatives")
        if not deriv:
            self.set_skip_reason(ctx, "NO_DERIVATIVES_DATA")
            return None

        oi_delta_4h = deriv.get("oi_delta_4h")
        taker_aggression = deriv.get("taker_aggression")
        if oi_delta_4h is None:
            self.set_skip_reason(ctx, "MISSING_OI_DELTA_4H")
            return None

        # Need enough price history
        closes = ctx.recent_closes
        if len(closes) < PRICE_LOOKBACK_BARS:
            self.set_skip_reason(ctx, "INSUFFICIENT_PRICE_HISTORY")
            return None

        lookback_closes = closes[-PRICE_LOOKBACK_BARS:]
        current_price = ctx.bar_close
        lookback_high = max(lookback_closes)
        lookback_low = min(lookback_closes)

        # OI must be declining significantly
        if oi_delta_4h > OI_DELTA_4H_THRESHOLD:
            self.set_skip_reason(
                ctx, "OI_NOT_DECLINING",
                oi_delta_4h=oi_delta_4h,
                threshold=OI_DELTA_4H_THRESHOLD,
            )
            return None

        direction = None
        signal_data: Dict[str, Any] = {
            "oi_delta_4h": oi_delta_4h,
            "taker_aggression": taker_aggression,
        }

        # SHORT: price at 4h high but OI declining (longs closing, weak rally)
        if current_price >= lookback_high * 0.998:  # within 0.2% of high
            if taker_aggression is not None and taker_aggression > TAKER_FADE_LONG_MAX:
                self.set_skip_reason(ctx, "TAKER_NOT_FADING_FOR_SHORT")
                return None
            direction = Direction.SHORT
            signal_data["trigger"] = "price_at_4h_high_oi_declining"
            signal_data["price_ref"] = lookback_high

        # LONG: price at 4h low but OI declining (shorts closing, weak selloff)
        elif current_price <= lookback_low * 1.002:  # within 0.2% of low
            if taker_aggression is not None and taker_aggression < TAKER_FADE_SHORT_MIN:
                self.set_skip_reason(ctx, "TAKER_NOT_FADING_FOR_LONG")
                return None
            direction = Direction.LONG
            signal_data["trigger"] = "price_at_4h_low_oi_declining"
            signal_data["price_ref"] = lookback_low

        if direction is None:
            self.set_skip_reason(
                ctx, "PRICE_NOT_AT_EXTREME",
                current=current_price,
                high=lookback_high,
                low=lookback_low,
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
            reason="OI_PRICE_DIVERGENCE",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        closes = signal.signal_data.get("recent_closes", [])
        entry = signal.signal_data.get("price_ref", 0.0)

        # Approximate ATR from recent closes if available
        atr = self._estimate_atr(entry)
        stop_dist = 1.5 * atr if atr > 0 else entry * 0.005

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

    @staticmethod
    def _estimate_atr(price: float) -> float:
        """Fallback ATR estimate: 0.4% of price (typical 15m BTC ATR)."""
        return price * 0.004
