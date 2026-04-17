"""Unit tests for overlay variant logic (zones_nearest / zones_weighted)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List
from unittest.mock import patch

import pytest

from trading.core.engines.base import Direction
from trading.core.liquidity.overlay_evaluator import LiquidityOverlayEvaluator
from trading.core.liquidity.zone_models import LiquiditySnapshot
from trading.core.liquidity.zone_types import LiquidityZone

_TS = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)


def _zone(price: float, zone_type: str = "SWING_HIGH", source: str = "ohlcv_swing", intensity: float = 0.7) -> LiquidityZone:
    return LiquidityZone(price_level=price, zone_type=zone_type, source=source, intensity_score=intensity, timestamp=_TS)


def _snapshot_with_zones(zones: List[LiquidityZone]) -> LiquiditySnapshot:
    snap = LiquiditySnapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        zones={"nearest_above": 105.0, "nearest_below": 95.0, "session_high": 110.0, "session_low": 90.0},
        features={
            "distance_to_nearest_above_bps": 500.0,
            "distance_to_nearest_below_bps": -500.0,
            "distance_to_session_high_bps": 1000.0,
            "distance_to_session_low_bps": -1000.0,
            "candles_used": 64,
        },
    )
    snap.typed_zones = zones
    return snap


def _signal(direction: Direction = Direction.LONG, entry: float = 100.0) -> dict:
    return {
        "id": 1,
        "engine_id": "cn1",
        "symbol": "BTCUSDT",
        "direction": direction.value,
        "bar_timestamp": _TS,
        "signal_data": {"bar_close": entry, "timeframe": "15m"},
    }


def _base_trade(entry: float = 100.0) -> dict:
    return {"entry_price": entry, "stop_price": entry - 2.0, "target_price": entry + 5.0, "stake_usd": 100.0}


@patch.dict(os.environ, {"OVERLAY_GHOST_VARIANTS_ENABLED": "true"})
def test_variants_enabled_returns_three_decisions():
    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", True):
        evaluator = LiquidityOverlayEvaluator()
        zones = [
            _zone(103.0, zone_type="SWING_HIGH"),
            _zone(106.0, zone_type="PRIOR_DAY_HIGH"),
            _zone(97.0, zone_type="SWING_LOW"),
            _zone(94.0, zone_type="EQUAL_LOW"),
        ]
        snap = _snapshot_with_zones(zones)
        evaluations = evaluator.evaluate(_signal(), None, _base_trade(), snap, 1000.0)
        assert len(evaluations) == 3
        assert evaluations[0].variant_label == "legacy"
        assert evaluations[1].variant_label == "zones_nearest"
        assert evaluations[2].variant_label == "zones_weighted"


def test_variants_disabled_returns_one_decision():
    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", False):
        evaluator = LiquidityOverlayEvaluator()
        snap = _snapshot_with_zones([_zone(103.0)])
        evaluations = evaluator.evaluate(_signal(), None, _base_trade(), snap, 1000.0)
        assert len(evaluations) == 1
        assert evaluations[0].variant_label == "legacy"


def test_zones_nearest_selects_closest_zone():
    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", True):
        with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_VARIANTS_MIN_INTENSITY", 0.3):
            evaluator = LiquidityOverlayEvaluator()
            zones = [
                _zone(102.0, zone_type="SWING_HIGH", intensity=0.4),  # nearest above
                _zone(108.0, zone_type="SWING_HIGH", intensity=0.9),  # farther above
                _zone(98.0, zone_type="SWING_LOW", intensity=0.5),    # nearest below
                _zone(94.0, zone_type="SWING_LOW", intensity=0.8),    # farther below
            ]
            snap = _snapshot_with_zones(zones)
            evaluations = evaluator.evaluate(_signal(), None, _base_trade(), snap, 1000.0)
            nearest = evaluations[1]
            assert nearest.variant_label == "zones_nearest"
            # stop should be the closest zone below (98.0)
            assert nearest.stop_price == 98.0
            # target should be the closest zone above (102.0)
            assert nearest.target_price == 102.0


def test_zones_weighted_selects_highest_intensity():
    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", True):
        with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_VARIANTS_MIN_INTENSITY", 0.3):
            evaluator = LiquidityOverlayEvaluator()
            zones = [
                _zone(102.0, zone_type="SWING_HIGH", intensity=0.4),  # lower intensity above
                _zone(108.0, zone_type="SWING_HIGH", intensity=0.9),  # highest intensity above
                _zone(98.0, zone_type="SWING_LOW", intensity=0.5),    # lower intensity below
                _zone(94.0, zone_type="SWING_LOW", intensity=0.8),    # highest intensity below
            ]
            snap = _snapshot_with_zones(zones)
            evaluations = evaluator.evaluate(_signal(), None, _base_trade(), snap, 1000.0)
            weighted = evaluations[2]
            assert weighted.variant_label == "zones_weighted"
            # stop = highest intensity below = 94.0 (intensity 0.8)
            assert weighted.stop_price == 94.0
            # target = highest intensity above = 108.0 (intensity 0.9)
            assert weighted.target_price == 108.0


def test_variant_skips_when_no_qualifying_zones():
    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", True):
        with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_VARIANTS_MIN_INTENSITY", 0.9):
            evaluator = LiquidityOverlayEvaluator()
            # All zones have intensity < 0.9 → no qualifying zones for variants
            zones = [
                _zone(102.0, zone_type="SWING_HIGH", intensity=0.5),
                _zone(98.0, zone_type="SWING_LOW", intensity=0.5),
            ]
            snap = _snapshot_with_zones(zones)
            evaluations = evaluator.evaluate(_signal(), None, _base_trade(), snap, 1000.0)
            assert len(evaluations) == 3
            assert evaluations[1].action == "SKIP"
            assert evaluations[2].action == "SKIP"


def test_skip_on_rejection_returns_only_one_decision():
    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", True):
        evaluator = LiquidityOverlayEvaluator()
        # Put a strong resistance zone just above entry → rejection
        zones = [
            _zone(100.2, zone_type="SWING_HIGH", intensity=0.8),
        ]
        snap = _snapshot_with_zones(zones)
        snap.features["distance_to_nearest_above_bps"] = 20.0  # within rejection window
        evaluations = evaluator.evaluate(_signal(), None, _base_trade(), snap, 1000.0)
        # Should return single SKIP regardless of variants flag
        assert len(evaluations) == 1
        assert evaluations[0].action == "SKIP"


def test_short_direction_variant_nearest():
    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", True):
        with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_VARIANTS_MIN_INTENSITY", 0.3):
            evaluator = LiquidityOverlayEvaluator()
            entry = 100.0
            zones = [
                _zone(104.0, zone_type="SWING_HIGH", intensity=0.5),  # nearest above (stop for SHORT)
                _zone(109.0, zone_type="SWING_HIGH", intensity=0.5),
                _zone(96.0, zone_type="SWING_LOW", intensity=0.5),    # nearest below (target for SHORT)
                _zone(91.0, zone_type="SWING_LOW", intensity=0.5),
            ]
            snap = _snapshot_with_zones(zones)
            base = {"entry_price": entry, "stop_price": entry + 2.0, "target_price": entry - 5.0, "stake_usd": 100.0}
            evaluations = evaluator.evaluate(_signal(Direction.SHORT), None, base, snap, 1000.0)
            nearest = evaluations[1]
            assert nearest.variant_label == "zones_nearest"
            assert nearest.stop_price == 104.0  # nearest above
            assert nearest.target_price == 96.0  # nearest below
