"""
Liquidity overlay evaluator v2.

Uses typed zone context (SWING_HIGH/LOW, EQUAL_HIGH/LOW, FVG, PRIOR levels)
in addition to the scalar snapshot features used by v1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config.settings import OVERLAY_GHOST_VARIANTS_ENABLED, OVERLAY_VARIANTS_MIN_INTENSITY
from ..engines.base import Direction
from .zone_models import LiquiditySnapshot
from .zone_types import LiquidityZone

# Minimum distance (bps) above/below entry to consider a zone "in the way"
_REJECTION_WINDOW_BPS = 35.0
# Minimum intensity to treat a zone as a hard blocker
_MIN_BLOCKER_INTENSITY = 0.4


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
    variant_label: str = "legacy"


class LiquidityOverlayEvaluator:
    overlay_name = "binance_liquidity_v2"

    def evaluate(
        self,
        signal_row: Dict[str, Any],
        base_decision: Optional[Dict[str, Any]],
        base_trade: Optional[Dict[str, Any]],
        snapshot: LiquiditySnapshot,
        bankroll: float,
    ) -> List[OverlayDecision]:
        """
        Returns 1 or 3 OverlayDecision instances:
        - 1 if OVERLAY_GHOST_VARIANTS_ENABLED=False  (legacy behaviour)
        - 3 if True: [legacy, zones_nearest, zones_weighted]
          (2 or 1 if a variant produces SKIP due to missing qualifying zones)
        """
        signal_data = signal_row.get("signal_data") or {}
        direction = Direction(signal_row["direction"])
        entry = _as_float(base_trade["entry_price"]) if base_trade else _as_float(signal_data.get("bar_close"))
        stop = _as_float(base_trade["stop_price"]) if base_trade else _as_float((base_decision or {}).get("stop_price"))
        target = _as_float(base_trade["target_price"]) if base_trade else _as_float((base_decision or {}).get("target_price"))
        stake_usd = _as_float(base_trade["stake_usd"]) if base_trade else None

        features: Dict[str, Any] = dict(snapshot.features)
        features["overlay_name"] = self.overlay_name

        if entry is None:
            return [OverlayDecision(
                action="SKIP",
                comparison_label="skip",
                reason="MISSING_ENTRY_REFERENCE",
                features=features,
                variant_label="legacy",
            )]

        # ── Rejection rules (SKIP) ─────────────────────────────────────────
        skip_reason = _check_rejection(direction, entry, snapshot)
        if skip_reason is not None:
            features["rejection_reason"] = skip_reason
            return [OverlayDecision(
                action="SKIP",
                comparison_label="skip",
                reason=skip_reason,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                stake_usd=stake_usd,
                trigger_bar_ts=signal_row["bar_timestamp"],
                features=features,
                variant_label="legacy",
            )]

        # ── Adjustment rules ───────────────────────────────────────────────
        adjusted_stop, stop_reason = _adjust_stop(direction, entry, stop, snapshot)
        adjusted_target, target_reason = _adjust_target(direction, entry, target, snapshot)

        comparison_label = "same"
        reason = "LIQUIDITY_CONFIRMS_BASE"

        if adjusted_stop != stop and adjusted_stop is not None:
            comparison_label = "stop_wider"
            reason = stop_reason or "STOP_AT_LIQUIDITY_EXTREME"
        if adjusted_target != target and adjusted_target is not None:
            tp_label = "tp_higher" if direction == Direction.LONG else "tp_lower"
            if comparison_label == "same":
                comparison_label = tp_label
                reason = target_reason or "TARGET_AT_LIQUIDITY_POOL"
            else:
                comparison_label = "adjust"
                reason = f"{stop_reason}+{target_reason}"

        if stake_usd is None:
            stake_usd = round(max(bankroll * 0.005, 1.0), 2)

        action = "ENTER"
        if comparison_label != "same":
            action = "ADJUST"

        # Annotate nearest typed zones for reporting
        features.update(_nearest_zone_features(direction, entry, snapshot.typed_zones))
        baseline = OverlayDecision(
            action=action,
            comparison_label=comparison_label,
            reason=reason,
            entry_price=entry,
            stop_price=adjusted_stop,
            target_price=adjusted_target,
            stake_usd=stake_usd,
            trigger_bar_ts=signal_row["bar_timestamp"],
            features=features,
            variant_label="legacy",
        )

        if not OVERLAY_GHOST_VARIANTS_ENABLED:
            return [baseline]

        bar_ts = signal_row["bar_timestamp"]
        nearest = _evaluate_variant_nearest(
            direction, entry, stake_usd, bar_ts, snapshot, OVERLAY_VARIANTS_MIN_INTENSITY
        )
        weighted = _evaluate_variant_weighted(
            direction, entry, stake_usd, bar_ts, snapshot, OVERLAY_VARIANTS_MIN_INTENSITY
        )
        return [baseline, nearest, weighted]


# ── Rejection logic ─────────────────────────────────────────────────────────

_RESISTANCE_TYPES = {"SWING_HIGH", "EQUAL_HIGH", "BEARISH_FVG", "PRIOR_DAY_HIGH", "PRIOR_WEEK_HIGH"}
_SUPPORT_TYPES = {"SWING_LOW", "EQUAL_LOW", "BULLISH_FVG", "PRIOR_DAY_LOW", "PRIOR_WEEK_LOW"}


def _check_rejection(
    direction: Direction,
    entry: float,
    snapshot: LiquiditySnapshot,
) -> Optional[str]:
    """Return a rejection reason string if the entry runs into a strong opposing zone."""
    if direction == Direction.LONG:
        # Check typed zones: resistance above entry within window
        for zone in snapshot.typed_zones:
            if zone.zone_type not in _RESISTANCE_TYPES:
                continue
            if zone.intensity_score < _MIN_BLOCKER_INTENSITY:
                continue
            dist_bps = (zone.price_level - entry) / entry * 10_000.0
            if 0 <= dist_bps <= _REJECTION_WINDOW_BPS:
                return f"LIQUIDITY_REJECTION_{zone.zone_type}"
        # Fallback to scalar nearest_above
        above_bps = _as_float(snapshot.features.get("distance_to_nearest_above_bps"))
        if above_bps is not None and 0 <= above_bps <= _REJECTION_WINDOW_BPS:
            return "LIQUIDITY_REJECTION_ABOVE"
    else:
        # Check typed zones: support below entry within window
        for zone in snapshot.typed_zones:
            if zone.zone_type not in _SUPPORT_TYPES:
                continue
            if zone.intensity_score < _MIN_BLOCKER_INTENSITY:
                continue
            dist_bps = (entry - zone.price_level) / entry * 10_000.0
            if 0 <= dist_bps <= _REJECTION_WINDOW_BPS:
                return f"LIQUIDITY_REJECTION_{zone.zone_type}"
        below_bps = _as_float(snapshot.features.get("distance_to_nearest_below_bps"))
        if below_bps is not None and -_REJECTION_WINDOW_BPS <= below_bps <= 0:
            return "LIQUIDITY_REJECTION_BELOW"
    return None


# ── Stop adjustment ─────────────────────────────────────────────────────────

def _adjust_stop(
    direction: Direction,
    entry: float,
    current_stop: Optional[float],
    snapshot: LiquiditySnapshot,
) -> tuple[Optional[float], Optional[str]]:
    """Return (new_stop, reason). Returns (current_stop, None) if no improvement found."""
    if direction == Direction.LONG:
        # Place stop below the nearest significant support zone below entry
        candidate = _nearest_zone_below(entry, snapshot.typed_zones, {"SWING_LOW", "EQUAL_LOW"})
        if candidate is not None and (current_stop is None or candidate < current_stop):
            return candidate, "STOP_BELOW_SWING_LOW"
        # Fallback to scalar nearest_below
        nb = _as_float(snapshot.zones.get("nearest_below"))
        if nb is not None and (current_stop is None or nb < current_stop):
            return nb, "STOP_BELOW_NEAREST_LIQUIDITY"
    else:
        # Place stop above the nearest significant resistance zone above entry
        candidate = _nearest_zone_above(entry, snapshot.typed_zones, {"SWING_HIGH", "EQUAL_HIGH"})
        if candidate is not None and (current_stop is None or candidate > current_stop):
            return candidate, "STOP_ABOVE_SWING_HIGH"
        na = _as_float(snapshot.zones.get("nearest_above"))
        if na is not None and (current_stop is None or na > current_stop):
            return na, "STOP_ABOVE_NEAREST_LIQUIDITY"
    return current_stop, None


# ── Target adjustment ───────────────────────────────────────────────────────

def _adjust_target(
    direction: Direction,
    entry: float,
    current_target: Optional[float],
    snapshot: LiquiditySnapshot,
) -> tuple[Optional[float], Optional[str]]:
    """Return (new_target, reason). Returns (current_target, None) if no improvement."""
    if direction == Direction.LONG:
        # Target at next bullish FVG (price fills it from below) or prior high
        fvg = _nearest_zone_above(entry, snapshot.typed_zones, {"BULLISH_FVG"})
        prior_high = _nearest_zone_above(entry, snapshot.typed_zones, {"PRIOR_DAY_HIGH", "PRIOR_WEEK_HIGH"})
        swing_high = _nearest_zone_above(entry, snapshot.typed_zones, {"SWING_HIGH", "EQUAL_HIGH"})
        # Pick closest meaningful target above entry
        candidate = _min_not_none(fvg, prior_high, swing_high)
        if candidate is None:
            # Fallback: session_high
            candidate = _as_float(snapshot.zones.get("session_high"))
        if candidate is not None and candidate > entry and (current_target is None or candidate > current_target):
            return candidate, "TARGET_AT_NEXT_LIQUIDITY_POOL"
    else:
        fvg = _nearest_zone_below(entry, snapshot.typed_zones, {"BEARISH_FVG"})
        prior_low = _nearest_zone_below(entry, snapshot.typed_zones, {"PRIOR_DAY_LOW", "PRIOR_WEEK_LOW"})
        swing_low = _nearest_zone_below(entry, snapshot.typed_zones, {"SWING_LOW", "EQUAL_LOW"})
        candidate = _max_not_none(fvg, prior_low, swing_low)
        if candidate is None:
            candidate = _as_float(snapshot.zones.get("session_low"))
        if candidate is not None and candidate < entry and (current_target is None or candidate < current_target):
            return candidate, "TARGET_AT_NEXT_LIQUIDITY_POOL"
    return current_target, None


# ── Feature annotation ──────────────────────────────────────────────────────

def _nearest_zone_features(
    direction: Direction,
    entry: float,
    typed_zones: List[LiquidityZone],
) -> Dict[str, Any]:
    """Add nearest resistance/support zone metadata to features for observability."""
    out: Dict[str, Any] = {}
    if direction == Direction.LONG:
        z = _nearest_typed_zone_above(entry, typed_zones, _RESISTANCE_TYPES)
    else:
        z = _nearest_typed_zone_below(entry, typed_zones, _SUPPORT_TYPES)
    if z is not None:
        out["nearest_opposing_zone_type"] = z.zone_type
        out["nearest_opposing_zone_bps"] = round(abs(z.price_level - entry) / entry * 10_000.0, 2)
        out["nearest_opposing_zone_intensity"] = z.intensity_score
    return out


# ── Helpers ─────────────────────────────────────────────────────────────────

def _nearest_zone_above(
    ref: float,
    zones: List[LiquidityZone],
    zone_types: set,
) -> Optional[float]:
    candidates = [z.price_level for z in zones if z.zone_type in zone_types and z.price_level > ref]
    return min(candidates) if candidates else None


def _nearest_zone_below(
    ref: float,
    zones: List[LiquidityZone],
    zone_types: set,
) -> Optional[float]:
    candidates = [z.price_level for z in zones if z.zone_type in zone_types and z.price_level < ref]
    return max(candidates) if candidates else None


def _nearest_typed_zone_above(
    ref: float, zones: List[LiquidityZone], zone_types: set
) -> Optional[LiquidityZone]:
    above = [z for z in zones if z.zone_type in zone_types and z.price_level > ref]
    return min(above, key=lambda z: z.price_level) if above else None


def _nearest_typed_zone_below(
    ref: float, zones: List[LiquidityZone], zone_types: set
) -> Optional[LiquidityZone]:
    below = [z for z in zones if z.zone_type in zone_types and z.price_level < ref]
    return max(below, key=lambda z: z.price_level) if below else None


def _min_not_none(*values: Optional[float]) -> Optional[float]:
    valid = [v for v in values if v is not None]
    return min(valid) if valid else None


def _max_not_none(*values: Optional[float]) -> Optional[float]:
    valid = [v for v in values if v is not None]
    return max(valid) if valid else None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Variant helpers ──────────────────────────────────────────────────────────

def _evaluate_variant_nearest(
    direction: Direction,
    entry: float,
    stake_usd: float,
    bar_ts: Any,
    snapshot: LiquiditySnapshot,
    min_intensity: float,
) -> OverlayDecision:
    """GHOST_ZONES_NEAREST: stop and target at nearest qualifying zones."""
    zones = [z for z in snapshot.typed_zones if z.intensity_score >= min_intensity]
    if direction == Direction.LONG:
        stop_zones = sorted(
            [z for z in zones if z.price_level < entry],
            key=lambda z: entry - z.price_level,
        )
        target_zones = sorted(
            [z for z in zones if z.price_level > entry],
            key=lambda z: z.price_level - entry,
        )
    else:
        stop_zones = sorted(
            [z for z in zones if z.price_level > entry],
            key=lambda z: z.price_level - entry,
        )
        target_zones = sorted(
            [z for z in zones if z.price_level < entry],
            key=lambda z: entry - z.price_level,
        )

    if not stop_zones or not target_zones:
        return OverlayDecision(
            action="SKIP",
            comparison_label="skip",
            reason="ENTRY_SKIPPED_NO_ZONE",
            entry_price=entry,
            stake_usd=stake_usd,
            trigger_bar_ts=bar_ts,
            features={"variant_skip_reason": "no_qualifying_zone_for_nearest"},
            variant_label="zones_nearest",
        )

    return OverlayDecision(
        action="ENTER",
        comparison_label="zones_nearest",
        reason="STOP_AND_TARGET_AT_NEAREST_ZONE",
        entry_price=entry,
        stop_price=stop_zones[0].price_level,
        target_price=target_zones[0].price_level,
        stake_usd=stake_usd,
        trigger_bar_ts=bar_ts,
        features={
            "stop_zone_type": stop_zones[0].zone_type,
            "stop_zone_source": stop_zones[0].source,
            "stop_zone_intensity": stop_zones[0].intensity_score,
            "target_zone_type": target_zones[0].zone_type,
            "target_zone_source": target_zones[0].source,
            "target_zone_intensity": target_zones[0].intensity_score,
        },
        variant_label="zones_nearest",
    )


def _evaluate_variant_weighted(
    direction: Direction,
    entry: float,
    stake_usd: float,
    bar_ts: Any,
    snapshot: LiquiditySnapshot,
    min_intensity: float,
) -> OverlayDecision:
    """GHOST_ZONES_WEIGHTED: stop and target at highest-intensity qualifying zones."""
    zones = [z for z in snapshot.typed_zones if z.intensity_score >= min_intensity]
    if direction == Direction.LONG:
        stop_zones = sorted(
            [z for z in zones if z.price_level < entry],
            key=lambda z: (-z.intensity_score, entry - z.price_level),
        )
        target_zones = sorted(
            [z for z in zones if z.price_level > entry],
            key=lambda z: (-z.intensity_score, z.price_level - entry),
        )
    else:
        stop_zones = sorted(
            [z for z in zones if z.price_level > entry],
            key=lambda z: (-z.intensity_score, z.price_level - entry),
        )
        target_zones = sorted(
            [z for z in zones if z.price_level < entry],
            key=lambda z: (-z.intensity_score, entry - z.price_level),
        )

    if not stop_zones or not target_zones:
        return OverlayDecision(
            action="SKIP",
            comparison_label="skip",
            reason="ENTRY_SKIPPED_NO_ZONE",
            entry_price=entry,
            stake_usd=stake_usd,
            trigger_bar_ts=bar_ts,
            features={"variant_skip_reason": "no_qualifying_zone_for_weighted"},
            variant_label="zones_weighted",
        )

    return OverlayDecision(
        action="ENTER",
        comparison_label="zones_weighted",
        reason="STOP_AND_TARGET_AT_STRONGEST_ZONE",
        entry_price=entry,
        stop_price=stop_zones[0].price_level,
        target_price=target_zones[0].price_level,
        stake_usd=stake_usd,
        trigger_bar_ts=bar_ts,
        features={
            "stop_zone_type": stop_zones[0].zone_type,
            "stop_zone_source": stop_zones[0].source,
            "stop_zone_intensity": stop_zones[0].intensity_score,
            "target_zone_type": target_zones[0].zone_type,
            "target_zone_source": target_zones[0].source,
            "target_zone_intensity": target_zones[0].intensity_score,
        },
        variant_label="zones_weighted",
    )
