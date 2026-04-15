"""
Engine CN5 — Liquidation Squeeze Zone (crypto-native)

Detects when price approaches a cluster of estimated liquidation levels
(a "liquidation magnet"). Large clusters attract price because cascading
liquidations create self-reinforcing momentum.

Uses the liquidation_heatmap table (materialized every 5 min) to find
the nearest high-intensity zone and trades in the direction of the squeeze.

SIGNAL_ONLY — records signals, never opens positions.
Requires derivatives data in ctx.extra["derivatives"] AND
heatmap zones in ctx.extra["derivatives"]["heatmap_zones"].

Entry rules:
  1. A high-intensity liquidation zone exists within 3% of current price
  2. The zone intensity > 0.4 (top 40% of all zones)
  3. squeeze_score > 1.0 (market stress is building)
  4. Direction: toward the zone (price is being pulled to liquidations)
     - Zone above price with mostly SHORT liqs → LONG (shorts will get squeezed)
     - Zone below price with mostly LONG liqs → SHORT (longs will get squeezed)

Stop:  1.5x ATR (tight — squeeze moves are fast)
Target: 2.0R
"""
from __future__ import annotations

import logging
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
ZONE_DISTANCE_MAX_PCT = 0.03      # zone must be within 3% of price
ZONE_INTENSITY_MIN = 0.4          # zone must be top 40% intensity
SQUEEZE_SCORE_MIN = 1.0           # market stress confirmation
LIQ_INTENSITY_MIN = 0.000005      # minimum liquidation activity
REWARD_RISK = 2.0


class CN5SqueezeZoneEngine(BaseEngine):
    ENGINE_ID = "cn5_squeeze_zone"

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

        # Core derivatives features
        squeeze_score = deriv.get("squeeze_score")
        liq_intensity = deriv.get("liquidation_intensity")

        # Heatmap zones (injected by the worker from derivatives DB)
        heatmap = deriv.get("heatmap_zones")
        if not heatmap:
            self.set_skip_reason(ctx, "NO_HEATMAP_DATA")
            return None

        strongest = heatmap.get("strongest_magnet")
        if not strongest:
            self.set_skip_reason(ctx, "NO_STRONG_MAGNET")
            return None

        zone_intensity = strongest.get("intensity", 0)
        zone_distance = abs(strongest.get("distance_pct", 1.0))
        zone_side = strongest.get("side", "")

        signal_data: Dict[str, Any] = {
            "squeeze_score": squeeze_score,
            "liquidation_intensity": liq_intensity,
            "zone_intensity": zone_intensity,
            "zone_distance_pct": zone_distance,
            "zone_side": zone_side,
            "zone_price_low": strongest.get("price_low"),
            "zone_price_high": strongest.get("price_high"),
            "zone_long_vol": strongest.get("long_volume"),
            "zone_short_vol": strongest.get("short_volume"),
            "nearest_above": len(heatmap.get("nearest_above", [])),
            "nearest_below": len(heatmap.get("nearest_below", [])),
        }

        # Gate 1: zone must be close enough
        if zone_distance > ZONE_DISTANCE_MAX_PCT:
            self.set_skip_reason(
                ctx, "ZONE_TOO_FAR",
                distance_pct=zone_distance,
            )
            return None

        # Gate 2: zone intensity must be significant
        if zone_intensity < ZONE_INTENSITY_MIN:
            self.set_skip_reason(
                ctx, "ZONE_INTENSITY_LOW",
                intensity=zone_intensity,
            )
            return None

        # Gate 3: squeeze score confirms stress
        if squeeze_score is not None and squeeze_score < SQUEEZE_SCORE_MIN:
            self.set_skip_reason(
                ctx, "SQUEEZE_SCORE_LOW",
                score=squeeze_score,
            )
            return None

        # Gate 4: some liquidation activity must exist
        if liq_intensity is not None and liq_intensity < LIQ_INTENSITY_MIN:
            self.set_skip_reason(
                ctx, "LIQUIDATION_TOO_LOW",
                intensity=liq_intensity,
            )
            return None

        # Direction: trade toward the liquidation cluster
        # Zone dominated by LONG liqs (below price) → price pulled down → SHORT
        # Zone dominated by SHORT liqs (above price) → price pulled up → LONG
        if zone_side == "SHORT":
            direction = Direction.LONG
            signal_data["trigger"] = "short_squeeze_zone_above"
        elif zone_side == "LONG":
            direction = Direction.SHORT
            signal_data["trigger"] = "long_squeeze_zone_below"
        else:
            self.set_skip_reason(ctx, "ZONE_SIDE_AMBIGUOUS")
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
            reason="SQUEEZE_ZONE_MAGNET",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        entry = signal.signal_data.get("price_ref", 0.0) or 0.0
        atr = entry * 0.004  # tight ATR — squeezes move fast
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
