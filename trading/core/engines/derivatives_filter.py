"""
Phase G — Derivatives Shadow Filter.

Evaluates whether a trade signal WOULD be blocked by the derivatives hard-filter,
without actually blocking it.  The result is logged as a risk event for
counterfactual analysis.

This module is strictly read-only with respect to the trading loop:
  - does not open/close positions
  - does not alter risk settings
  - does not modify engine state
  - never raises (all errors → PASS with degraded note)

Rules (all directional — evaluated against signal direction):
  FUNDING_RATE_EXTREME   abs(funding_rate) exceeds threshold
  OI_DELTA_ADVERSE       oi_delta_1h adverse to direction
  TAKER_AGGRESSION_LOW   taker_aggression below threshold (weak demand side)
  OB_IMBALANCE_ADVERSE   orderbook_imbalance adverse to direction
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Thresholds (conservative defaults — chosen to fire rarely in normal markets)
# ---------------------------------------------------------------------------

FUNDING_RATE_EXTREME_THRESHOLD: float = 0.0010    # 0.10% per 8h — strong tilt
OI_DELTA_ADVERSE_THRESHOLD: float = -0.0020       # 2‰ drop in 1h OI vs direction
TAKER_AGGRESSION_LOW_THRESHOLD: float = 0.70      # ratio below 0.70 = weak aggression
OB_IMBALANCE_ADVERSE_THRESHOLD: float = -0.15     # imbalance adverse beyond −0.15


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ShadowFilterVerdict:
    verdict: str                        # "PASS" | "BLOCK"
    triggered_rules: List[str]          # rule names that fired
    features_used: List[str]            # feature keys actually evaluated
    data_quality: str                   # "GOOD" | "PARTIAL" | "INSUFFICIENT"
    reason: str                         # human-readable summary
    feature_values: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_shadow_filter(
    direction: str,
    derivatives_ctx: Optional[Dict[str, Any]],
) -> ShadowFilterVerdict:
    """
    Evaluate shadow filter for a signal with the given direction.

    Parameters
    ----------
    direction : "LONG" | "SHORT"
    derivatives_ctx : per-symbol derivatives snapshot from ctx.extra["derivatives"]
                      (may be None, empty, or degraded)

    Returns
    -------
    ShadowFilterVerdict
        Always returns a result — never raises.
    """
    try:
        return _evaluate(direction, derivatives_ctx or {})
    except Exception as exc:
        return ShadowFilterVerdict(
            verdict="PASS",
            triggered_rules=[],
            features_used=[],
            data_quality="INSUFFICIENT",
            reason=f"shadow_filter_error:{type(exc).__name__}",
        )


# ---------------------------------------------------------------------------
# Internal evaluation logic
# ---------------------------------------------------------------------------

def _evaluate(direction: str, ctx: Dict[str, Any]) -> ShadowFilterVerdict:
    is_long = str(direction).upper() == "LONG"

    # Verify we have usable derivatives data
    freshness = ctx.get("freshness", "ABSENT")
    available = bool(ctx.get("available"))
    features: Dict[str, Any] = ctx.get("features") or {}
    available_features: List[str] = ctx.get("available_features") or []

    if not available or freshness in {"STALE", "ABSENT"} or not features:
        return ShadowFilterVerdict(
            verdict="PASS",
            triggered_rules=[],
            features_used=[],
            data_quality="INSUFFICIENT",
            reason="derivatives_unavailable_or_stale",
        )

    data_quality = "GOOD" if freshness == "FRESH" and len(available_features) >= 4 else "PARTIAL"

    triggered: List[str] = []
    evaluated: List[str] = []
    values: Dict[str, Any] = {}

    # Rule 1: extreme funding rate (direction-agnostic — extreme in either direction is a risk)
    funding_rate = features.get("funding_rate")
    if funding_rate is not None:
        evaluated.append("funding_rate")
        values["funding_rate"] = funding_rate
        if abs(funding_rate) >= FUNDING_RATE_EXTREME_THRESHOLD:
            triggered.append("FUNDING_RATE_EXTREME")

    # Rule 2: adverse OI delta (OI falling against direction = weak conviction)
    oi_delta_1h = features.get("oi_delta_1h")
    if oi_delta_1h is not None:
        evaluated.append("oi_delta_1h")
        values["oi_delta_1h"] = oi_delta_1h
        # For LONG: falling OI is adverse; for SHORT: rising OI is adverse
        adverse_oi = (is_long and oi_delta_1h <= OI_DELTA_ADVERSE_THRESHOLD) or (
            not is_long and oi_delta_1h >= abs(OI_DELTA_ADVERSE_THRESHOLD)
        )
        if adverse_oi:
            triggered.append("OI_DELTA_ADVERSE")

    # Rule 3: low taker aggression (buying/selling pressure too weak for direction)
    taker_aggression = features.get("taker_aggression")
    if taker_aggression is not None:
        evaluated.append("taker_aggression")
        values["taker_aggression"] = taker_aggression
        if taker_aggression < TAKER_AGGRESSION_LOW_THRESHOLD:
            triggered.append("TAKER_AGGRESSION_LOW")

    # Rule 4: adverse orderbook imbalance
    ob_imbalance = features.get("orderbook_imbalance")
    if ob_imbalance is not None:
        evaluated.append("orderbook_imbalance")
        values["orderbook_imbalance"] = ob_imbalance
        # For LONG: negative imbalance (more asks) is adverse
        # For SHORT: positive imbalance (more bids) is adverse
        adverse_ob = (is_long and ob_imbalance <= OB_IMBALANCE_ADVERSE_THRESHOLD) or (
            not is_long and ob_imbalance >= abs(OB_IMBALANCE_ADVERSE_THRESHOLD)
        )
        if adverse_ob:
            triggered.append("OB_IMBALANCE_ADVERSE")

    # Verdict: block only when at least 2 rules fire (conservative — requires corroboration)
    verdict = "BLOCK" if len(triggered) >= 2 else "PASS"

    if not triggered:
        reason = "no_adverse_signals"
    elif verdict == "BLOCK":
        reason = f"multi_rule_block:{','.join(triggered)}"
    else:
        reason = f"single_rule_pass:{','.join(triggered)}"

    return ShadowFilterVerdict(
        verdict=verdict,
        triggered_rules=triggered,
        features_used=evaluated,
        data_quality=data_quality,
        reason=reason,
        feature_values=values,
    )
