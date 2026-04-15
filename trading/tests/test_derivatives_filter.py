"""
Phase G — unit tests for derivatives_filter.py shadow filter logic.
"""
from __future__ import annotations

import pytest
from trading.core.engines.derivatives_filter import (
    FUNDING_RATE_EXTREME_THRESHOLD,
    OI_DELTA_ADVERSE_THRESHOLD,
    OB_IMBALANCE_ADVERSE_THRESHOLD,
    TAKER_AGGRESSION_LOW_THRESHOLD,
    ShadowFilterVerdict,
    evaluate_shadow_filter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(
    *,
    available: bool = True,
    freshness: str = "FRESH",
    funding_rate: float | None = None,
    oi_delta_1h: float | None = None,
    taker_aggression: float | None = None,
    orderbook_imbalance: float | None = None,
) -> dict:
    features = {
        "funding_rate": funding_rate,
        "oi_delta_1h": oi_delta_1h,
        "taker_aggression": taker_aggression,
        "orderbook_imbalance": orderbook_imbalance,
    }
    available_features = [k for k, v in features.items() if v is not None]
    return {
        "available": available,
        "freshness": freshness,
        "features": features,
        "available_features": available_features,
    }


# ---------------------------------------------------------------------------
# Unavailable / degraded derivatives
# ---------------------------------------------------------------------------

def test_pass_when_derivatives_unavailable():
    result = evaluate_shadow_filter("LONG", None)
    assert result.verdict == "PASS"
    assert result.data_quality == "INSUFFICIENT"


def test_pass_when_derivatives_not_available():
    ctx = _ctx(available=False, freshness="ABSENT")
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.verdict == "PASS"
    assert result.data_quality == "INSUFFICIENT"


def test_pass_when_stale():
    ctx = _ctx(available=True, freshness="STALE", taker_aggression=0.3, oi_delta_1h=-0.01)
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.verdict == "PASS"
    assert result.data_quality == "INSUFFICIENT"


# ---------------------------------------------------------------------------
# No adverse signals → PASS
# ---------------------------------------------------------------------------

def test_pass_no_adverse_signals_long():
    ctx = _ctx(
        funding_rate=0.0001,      # small — not extreme
        oi_delta_1h=0.005,        # rising OI → favorable for LONG
        taker_aggression=1.2,     # strong
        orderbook_imbalance=0.3,  # bid-side heavy → favorable for LONG
    )
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.verdict == "PASS"
    assert result.triggered_rules == []
    assert result.reason == "no_adverse_signals"


def test_pass_no_adverse_signals_short():
    ctx = _ctx(
        funding_rate=0.0001,
        oi_delta_1h=-0.005,       # falling OI → favorable for SHORT
        taker_aggression=1.5,
        orderbook_imbalance=-0.3, # ask-side heavy → favorable for SHORT
    )
    result = evaluate_shadow_filter("SHORT", ctx)
    assert result.verdict == "PASS"
    assert result.triggered_rules == []


# ---------------------------------------------------------------------------
# Single rule fires → PASS (requires corroboration)
# ---------------------------------------------------------------------------

def test_single_rule_pass_extreme_funding():
    ctx = _ctx(
        funding_rate=FUNDING_RATE_EXTREME_THRESHOLD + 0.0001,  # extreme
        oi_delta_1h=0.005,        # favorable
        taker_aggression=1.2,
        orderbook_imbalance=0.3,
    )
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.verdict == "PASS"
    assert "FUNDING_RATE_EXTREME" in result.triggered_rules
    assert result.reason.startswith("single_rule_pass")


# ---------------------------------------------------------------------------
# Two rules fire → BLOCK
# ---------------------------------------------------------------------------

def test_block_funding_extreme_and_oi_adverse_long():
    ctx = _ctx(
        funding_rate=FUNDING_RATE_EXTREME_THRESHOLD + 0.0005,  # extreme
        oi_delta_1h=OI_DELTA_ADVERSE_THRESHOLD - 0.001,       # adverse for LONG
        taker_aggression=1.2,
        orderbook_imbalance=0.1,
    )
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.verdict == "BLOCK"
    assert "FUNDING_RATE_EXTREME" in result.triggered_rules
    assert "OI_DELTA_ADVERSE" in result.triggered_rules
    assert result.reason.startswith("multi_rule_block")


def test_block_low_taker_and_ob_imbalance_adverse_long():
    ctx = _ctx(
        taker_aggression=TAKER_AGGRESSION_LOW_THRESHOLD - 0.1,  # weak
        orderbook_imbalance=OB_IMBALANCE_ADVERSE_THRESHOLD - 0.1,  # ask-heavy, adverse for LONG
    )
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.verdict == "BLOCK"
    assert "TAKER_AGGRESSION_LOW" in result.triggered_rules
    assert "OB_IMBALANCE_ADVERSE" in result.triggered_rules


def test_block_oi_adverse_and_low_taker_short():
    ctx = _ctx(
        oi_delta_1h=abs(OI_DELTA_ADVERSE_THRESHOLD) + 0.001,  # rising OI adverse for SHORT
        taker_aggression=TAKER_AGGRESSION_LOW_THRESHOLD - 0.1,
    )
    result = evaluate_shadow_filter("SHORT", ctx)
    assert result.verdict == "BLOCK"
    assert "OI_DELTA_ADVERSE" in result.triggered_rules
    assert "TAKER_AGGRESSION_LOW" in result.triggered_rules


# ---------------------------------------------------------------------------
# Feature values are captured
# ---------------------------------------------------------------------------

def test_feature_values_captured():
    ctx = _ctx(
        funding_rate=0.0005,
        oi_delta_1h=0.003,
        taker_aggression=0.9,
        orderbook_imbalance=0.2,
    )
    result = evaluate_shadow_filter("LONG", ctx)
    assert "funding_rate" in result.feature_values
    assert "oi_delta_1h" in result.feature_values
    assert result.feature_values["funding_rate"] == 0.0005


# ---------------------------------------------------------------------------
# Error safety — never raises
# ---------------------------------------------------------------------------

def test_never_raises_on_corrupt_input():
    result = evaluate_shadow_filter("LONG", {"available": True, "freshness": "FRESH", "features": None})
    assert isinstance(result, ShadowFilterVerdict)
    assert result.verdict == "PASS"


def test_never_raises_on_exception():
    # Pass a non-dict as ctx to trigger an exception path
    result = evaluate_shadow_filter("LONG", "not_a_dict")  # type: ignore[arg-type]
    assert isinstance(result, ShadowFilterVerdict)
    assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------

def test_data_quality_good_when_fresh_and_many_features():
    ctx = _ctx(
        funding_rate=0.0001,
        oi_delta_1h=0.003,
        taker_aggression=1.0,
        orderbook_imbalance=0.1,
    )
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.data_quality == "GOOD"


def test_data_quality_partial_when_few_features():
    ctx = _ctx(
        freshness="DEGRADED",
        taker_aggression=1.0,
        oi_delta_1h=0.003,
    )
    result = evaluate_shadow_filter("LONG", ctx)
    assert result.data_quality == "PARTIAL"
