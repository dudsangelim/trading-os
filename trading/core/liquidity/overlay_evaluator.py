"""
Initial liquidity overlay evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..engines.base import Direction
from .zone_models import LiquiditySnapshot


@dataclass
class OverlayDecision:
    action: str
    comparison_label: str
    reason: str
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    stake_usd: Optional[float] = None
    trigger_bar_ts: Optional[Any] = None
    features: Dict[str, Any] = field(default_factory=dict)


class LiquidityOverlayEvaluator:
    overlay_name = "binance_liquidity_v1"

    def evaluate(
        self,
        signal_row: Dict[str, Any],
        base_decision: Optional[Dict[str, Any]],
        base_trade: Optional[Dict[str, Any]],
        snapshot: LiquiditySnapshot,
        bankroll: float,
    ) -> OverlayDecision:
        signal_data = signal_row.get("signal_data") or {}
        direction = Direction(signal_row["direction"])
        entry = _as_float(base_trade["entry_price"]) if base_trade else _as_float(signal_data.get("bar_close"))
        stop = _as_float(base_trade["stop_price"]) if base_trade else _as_float(base_decision.get("stop_price")) if base_decision else None
        target = _as_float(base_trade["target_price"]) if base_trade else _as_float(base_decision.get("target_price")) if base_decision else None
        stake_usd = _as_float(base_trade["stake_usd"]) if base_trade else None

        above_bps = _as_float(snapshot.features.get("distance_to_nearest_above_bps"))
        below_bps = _as_float(snapshot.features.get("distance_to_nearest_below_bps"))
        session_high_bps = _as_float(snapshot.features.get("distance_to_session_high_bps"))
        session_low_bps = _as_float(snapshot.features.get("distance_to_session_low_bps"))
        features = dict(snapshot.features)
        features["overlay_name"] = self.overlay_name

        if entry is None:
            return OverlayDecision(
                action="SKIP",
                comparison_label="skip",
                reason="MISSING_ENTRY_REFERENCE",
                features=features,
            )

        if direction == Direction.LONG and above_bps is not None and 0 <= above_bps <= 35:
            return OverlayDecision(
                action="SKIP",
                comparison_label="skip",
                reason="LIQUIDITY_REJECTION_ABOVE",
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                stake_usd=stake_usd,
                trigger_bar_ts=signal_row["bar_timestamp"],
                features=features,
            )

        if direction == Direction.SHORT and below_bps is not None and -35 <= below_bps <= 0:
            return OverlayDecision(
                action="SKIP",
                comparison_label="skip",
                reason="LIQUIDITY_REJECTION_BELOW",
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                stake_usd=stake_usd,
                trigger_bar_ts=signal_row["bar_timestamp"],
                features=features,
            )

        adjusted_stop = stop
        adjusted_target = target
        comparison_label = "same"
        reason = "LIQUIDITY_CONFIRMS_BASE"

        if direction == Direction.LONG:
            below_zone = _as_float(snapshot.zones.get("nearest_below"))
            above_zone = _as_float(snapshot.zones.get("session_high"))
            if below_zone is not None and (stop is None or below_zone < stop):
                adjusted_stop = below_zone
                comparison_label = "stop_wider"
                reason = "STOP_BELOW_NEAREST_LIQUIDITY"
            if above_zone is not None and above_zone > entry:
                adjusted_target = above_zone
                if comparison_label == "same":
                    comparison_label = "tp_higher"
                    reason = "TARGET_AT_NEXT_LIQUIDITY_POOL"
        else:
            above_zone = _as_float(snapshot.zones.get("nearest_above"))
            below_zone = _as_float(snapshot.zones.get("session_low"))
            if above_zone is not None and (stop is None or above_zone > stop):
                adjusted_stop = above_zone
                comparison_label = "stop_wider"
                reason = "STOP_ABOVE_NEAREST_LIQUIDITY"
            if below_zone is not None and below_zone < entry:
                adjusted_target = below_zone
                if comparison_label == "same":
                    comparison_label = "tp_lower"
                    reason = "TARGET_AT_NEXT_LIQUIDITY_POOL"

        if stake_usd is None:
            stake_usd = round(max(bankroll * 0.005, 1.0), 2)

        action = "ENTER" if base_decision is None or base_decision.get("action") != "SKIP" else "ENTER"
        if comparison_label != "same":
            action = "ADJUST"

        features["entry_session_high_bps"] = session_high_bps
        features["entry_session_low_bps"] = session_low_bps
        return OverlayDecision(
            action=action,
            comparison_label=comparison_label,
            reason=reason,
            entry_price=entry,
            stop_price=adjusted_stop,
            target_price=adjusted_target,
            stake_usd=stake_usd,
            trigger_bar_ts=signal_row["bar_timestamp"],
            features=features,
        )


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
