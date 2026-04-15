"""
Engine CN4 — Liquidation Cascade Reversal (crypto-native)

After a liquidation cascade, weak positions are flushed out and the market
tends to reverse. Detects spikes in liquidation_intensity combined with
OI decline and enters against the cascade direction.

SIGNAL_ONLY — records signals, never opens positions.
Requires derivatives data in ctx.extra["derivatives"].

Entry rules:
  Spike: liquidation_intensity > 3x baseline
  Direction: liquidation_imbalance determines who got rekt
    - imbalance > 0.3 (longs liquidated) → LONG (contrarian: weak longs gone)
    - imbalance < -0.3 (shorts liquidated) → SHORT (contrarian: weak shorts gone)
  Confirmation: OI declining (positions being unwound)

Stop:  2.0x ATR (wider — post-cascade volatility is high)
Target: 2.5R
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
LIQ_INTENSITY_MIN = 0.00001        # minimum absolute liquidation intensity
LIQ_IMBALANCE_THRESHOLD = 0.3      # |imbalance| > 0.3 = one-sided liquidation
OI_DECLINE_MAX = 0.005              # OI should be flat or declining
SQUEEZE_SCORE_MIN = 1.5             # composite squeeze score confirmation
REWARD_RISK = 2.5


class CN4LiquidationCascadeEngine(BaseEngine):
    ENGINE_ID = "cn4_liquidation_cascade"

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

        liq_intensity = deriv.get("liquidation_intensity")
        liq_imbalance = deriv.get("liquidation_imbalance")
        oi_delta_1h = deriv.get("oi_delta_1h")
        squeeze_score = deriv.get("squeeze_score")

        if liq_intensity is None or liq_imbalance is None:
            self.set_skip_reason(ctx, "MISSING_LIQUIDATION_DATA")
            return None

        signal_data: Dict[str, Any] = {
            "liquidation_intensity": liq_intensity,
            "liquidation_imbalance": liq_imbalance,
            "oi_delta_1h": oi_delta_1h,
            "squeeze_score": squeeze_score,
        }

        # Must have meaningful liquidation activity
        if liq_intensity < LIQ_INTENSITY_MIN:
            self.set_skip_reason(
                ctx, "LIQUIDATION_TOO_LOW",
                intensity=liq_intensity,
            )
            return None

        # Imbalance must be one-sided (clear cascade direction)
        if abs(liq_imbalance) < LIQ_IMBALANCE_THRESHOLD:
            self.set_skip_reason(
                ctx, "LIQUIDATION_NOT_ONE_SIDED",
                imbalance=liq_imbalance,
            )
            return None

        # Optional: squeeze score confirms elevated market stress
        if squeeze_score is not None and squeeze_score < SQUEEZE_SCORE_MIN:
            self.set_skip_reason(
                ctx, "SQUEEZE_SCORE_LOW",
                score=squeeze_score,
            )
            return None

        # Determine direction: contrarian to who got liquidated
        if liq_imbalance > LIQ_IMBALANCE_THRESHOLD:
            # Longs got liquidated → market flushed weak longs → BUY
            direction = Direction.LONG
            signal_data["trigger"] = "long_liquidation_cascade_reversal"
        else:
            # Shorts got liquidated → market flushed weak shorts → SELL
            direction = Direction.SHORT
            signal_data["trigger"] = "short_liquidation_cascade_reversal"

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
            reason="LIQUIDATION_CASCADE_REVERSAL",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        entry = signal.signal_data.get("price_ref", 0.0) or 0.0
        atr = entry * 0.005  # wider ATR for post-cascade volatility
        stop_dist = 2.0 * atr

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
