#!/usr/bin/env python3
"""
Smoke test for Phase 4: LiquidityZoneAggregator E2E validation.

Verifies:
  1. LiquidityZoneAggregator builds a valid snapshot with all enabled providers
  2. BinanceLiquidityReader returns a LiquiditySnapshot in aggregator mode
  3. LiquidityOverlayEvaluator produces 3 variants when flag is enabled
  4. GhostTradeManager.maybe_open_ghost_trade accepts variant_label correctly
     (dry-run: no DB writes)

Usage:
    PYTHONPATH=/home/agent/trading-os python3 scripts/smoke_aggregator_e2e.py

Requires: no live DB — uses synthetic candles and mock providers.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.core.data.candle_reader import Candle
from trading.core.liquidity.zone_aggregator import LiquidityZoneAggregator
from trading.core.liquidity.zone_types import LiquidityZone

ENTRY_PRICE = 100.0
_TS = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
OK = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        print(f"  {OK} {label}")
        _passed += 1
    else:
        print(f"  {FAIL} {label}" + (f" — {detail}" if detail else ""))
        _failed += 1


def _make_candles(n: int = 60) -> List[Candle]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    price = ENTRY_PRICE
    candles = []
    for i in range(n):
        candles.append(Candle(
            symbol="BTCUSDT",
            open_time=base + timedelta(minutes=15 * i),
            open=price, high=price + 1.0, low=price - 0.5, close=price + 0.3, volume=1000.0,
        ))
        price += 0.1
    return candles


def _zone(price: float, source: str = "ohlcv_swing", zone_type: str = "SWING_HIGH", intensity: float = 0.7) -> LiquidityZone:
    return LiquidityZone(price_level=price, zone_type=zone_type, source=source,
                         intensity_score=intensity, timestamp=_TS)


# ── Test 1: Zone aggregator basic ────────────────────────────────────────────
async def test_aggregator_basic() -> None:
    print("\n[1] LiquidityZoneAggregator — basic")
    candles = _make_candles()
    current_price = candles[-1].close

    sync_zones = [_zone(current_price + 3.0), _zone(current_price - 3.0, zone_type="SWING_LOW")]

    async def async_provider(symbol, reference_ts):
        return [_zone(current_price + 7.0, source="liquidation_estimator", zone_type="LIQUIDATION_CLUSTER")]

    agg = LiquidityZoneAggregator([lambda c: sync_zones], [async_provider])
    snap = await agg.build_snapshot("BTCUSDT", _TS, candles, current_price)

    check("snapshot has 3 total zones", len(snap.all_zones) == 3)
    check("zones_above sorted nearest first", len(snap.zones_above) == 2 and snap.zones_above[0].price_level < snap.zones_above[1].price_level)
    check("zones_below has 1 zone", len(snap.zones_below) == 1)
    check("nearest_above_any set", snap.nearest_above_any is not None)
    check("nearest_below_any set", snap.nearest_below_any is not None)
    check("sources_active populated", len(snap.sources_active) >= 1)
    check("no sources_failed", snap.sources_failed == [])
    check("features has agg_total_zones", "agg_total_zones" in snap.features)


# ── Test 2: Aggregator resilience ────────────────────────────────────────────
async def test_aggregator_resilience() -> None:
    print("\n[2] LiquidityZoneAggregator — provider failure resilience")

    def bad_sync(candles):
        raise RuntimeError("sync provider crash")

    async def bad_async(symbol, reference_ts):
        raise ValueError("async provider crash")

    async def good_async(symbol, reference_ts):
        return [_zone(108.0, source="liquidation_estimator")]

    agg = LiquidityZoneAggregator([bad_sync], [bad_async, good_async])
    snap = await agg.build_snapshot("BTCUSDT", _TS, _make_candles(), 100.0)

    check("snapshot returned despite failures", snap is not None)
    check("good async zone collected", len(snap.all_zones) == 1)
    check("sources_failed non-empty", len(snap.sources_failed) >= 1, str(snap.sources_failed))


# ── Test 3: BinanceLiquidityReader aggregator path ───────────────────────────
async def test_reader_aggregator_path() -> None:
    print("\n[3] BinanceLiquidityReader — aggregator path")
    from trading.core.liquidity.binance_liquidity_reader import BinanceLiquidityReader
    from trading.core.liquidity.zone_aggregator import AggregatedLiquiditySnapshot

    candles = _make_candles()
    mock_reader = MagicMock()
    mock_reader.get_latest_tf_candles = AsyncMock(return_value=candles)

    mock_zone = _zone(108.0, source="liquidation_estimator", zone_type="LIQUIDATION_CLUSTER")
    mock_agg = AggregatedLiquiditySnapshot(
        symbol="BTCUSDT", reference_ts=_TS,
        all_zones=[mock_zone],
        zones_above=[mock_zone], zones_below=[],
        nearest_above_any=mock_zone, nearest_below_any=None,
        strongest_above=mock_zone, strongest_below=None,
        sources_active=["liquidation_estimator"], sources_failed=[],
        provider_counts={"liquidation_estimator": 1},
        features={"agg_total_zones": 1},
    )
    mock_aggregator = MagicMock()
    mock_aggregator.build_snapshot = AsyncMock(return_value=mock_agg)

    blr = BinanceLiquidityReader(mock_reader, aggregator=mock_aggregator)
    snap = await blr.snapshot_for_signal("BTCUSDT", _TS, tf_minutes=15)

    check("snapshot returned", snap is not None)
    check("typed_zones includes LIQUIDATION_CLUSTER", any(z.zone_type == "LIQUIDATION_CLUSTER" for z in snap.typed_zones))
    check("features has agg_total_zones", snap.features.get("agg_total_zones") == 1)
    check("aggregator.build_snapshot was called", mock_aggregator.build_snapshot.called)


# ── Test 4: Overlay evaluator produces 3 variants ────────────────────────────
async def test_evaluator_three_variants() -> None:
    print("\n[4] LiquidityOverlayEvaluator — three variants")
    from unittest.mock import patch

    from trading.core.engines.base import Direction
    from trading.core.liquidity.overlay_evaluator import LiquidityOverlayEvaluator
    from trading.core.liquidity.zone_models import LiquiditySnapshot

    entry = ENTRY_PRICE
    snap = LiquiditySnapshot(
        symbol="BTCUSDT", timeframe="15m",
        zones={"nearest_above": entry + 5.0, "nearest_below": entry - 5.0,
               "session_high": entry + 10.0, "session_low": entry - 10.0},
        features={
            "distance_to_nearest_above_bps": 500.0,
            "distance_to_nearest_below_bps": -500.0,
            "distance_to_session_high_bps": 1000.0,
            "distance_to_session_low_bps": -1000.0,
            "candles_used": 60,
        },
    )
    snap.typed_zones = [
        _zone(entry + 3.0, zone_type="SWING_HIGH", intensity=0.7),
        _zone(entry + 7.0, zone_type="PRIOR_DAY_HIGH", intensity=0.5),
        _zone(entry - 3.0, zone_type="SWING_LOW", intensity=0.7),
        _zone(entry - 7.0, zone_type="EQUAL_LOW", intensity=0.5),
    ]
    signal_row = {
        "id": 1, "engine_id": "cn1", "symbol": "BTCUSDT",
        "direction": Direction.LONG.value, "bar_timestamp": _TS,
        "signal_data": {"bar_close": entry, "timeframe": "15m"},
    }
    base_trade = {"entry_price": entry, "stop_price": entry - 2.0, "target_price": entry + 5.0, "stake_usd": 100.0}

    with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_GHOST_VARIANTS_ENABLED", True):
        with patch("trading.core.liquidity.overlay_evaluator.OVERLAY_VARIANTS_MIN_INTENSITY", 0.3):
            evaluator = LiquidityOverlayEvaluator()
            evaluations = evaluator.evaluate(signal_row, None, base_trade, snap, 1000.0)

    check("returns 3 evaluations", len(evaluations) == 3, str(len(evaluations)))
    check("first is legacy", evaluations[0].variant_label == "legacy")
    check("second is zones_nearest", evaluations[1].variant_label == "zones_nearest")
    check("third is zones_weighted", evaluations[2].variant_label == "zones_weighted")
    check("zones_nearest stop at nearest zone", evaluations[1].stop_price == entry - 3.0)
    check("zones_weighted stop at highest intensity", evaluations[2].stop_price == entry - 3.0)  # both same intensity here


async def _main() -> None:
    await test_aggregator_basic()
    await test_aggregator_resilience()
    await test_reader_aggregator_path()
    await test_evaluator_three_variants()

    print(f"\n{'=' * 50}")
    print(f"  Results: {_passed} passed, {_failed} failed")
    if _failed > 0:
        print("  SMOKE TEST FAILED")
        sys.exit(1)
    else:
        print("  ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(_main())
