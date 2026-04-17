"""
Unit tests for LiquidationHeatmapProvider.

All tests use mocked TradingRepository — no real DB is touched.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.core.liquidity.providers.liquidation_heatmap_provider import LiquidationHeatmapProvider
from trading.core.storage.repository import LiquidationHeatmapBin

_TS = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
_SYMBOL = "BTCUSDT"


def _bin(
    distance_pct: float,
    intensity: float,
    nearest_side: str = "SHORT",
    price_mid: float = 80000.0,
) -> LiquidationHeatmapBin:
    width = price_mid * 0.002
    return LiquidationHeatmapBin(
        symbol=_SYMBOL,
        snapshot_ts=_TS,
        price_low=price_mid - width / 2,
        price_high=price_mid + width / 2,
        price_mid=price_mid,
        long_liq_volume_usd=0.0 if nearest_side == "SHORT" else 1e9,
        short_liq_volume_usd=1e9 if nearest_side == "SHORT" else 0.0,
        net_liq_volume_usd=-1e9 if nearest_side == "SHORT" else 1e9,
        total_liq_volume_usd=1e9,
        intensity=intensity,
        distance_from_price_pct=distance_pct,
        nearest_side=nearest_side,
    )


def _make_repo(snapshot_return=None, risk_event_raises=False):
    repo = MagicMock()
    repo.get_liquidation_heatmap_snapshot = AsyncMock(return_value=snapshot_return)
    if risk_event_raises:
        repo.insert_risk_event = AsyncMock(side_effect=Exception("db down"))
    else:
        repo.insert_risk_event = AsyncMock(return_value=1)
    return repo


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── 1. Returns empty when repository returns None ─────────────────────────────

def test_returns_empty_when_repository_returns_none():
    provider = LiquidationHeatmapProvider(_make_repo(snapshot_return=None))
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", False):
        zones = run(provider.detect_zones(_SYMBOL, _TS))
    assert zones == []


# ── 2. Returns empty when repository returns empty list ───────────────────────

def test_returns_empty_when_snapshot_is_empty_list():
    provider = LiquidationHeatmapProvider(_make_repo(snapshot_return=[]))
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", False):
        zones = run(provider.detect_zones(_SYMBOL, _TS))
    assert zones == []


# ── 3. Emits risk event when derivatives enabled and snapshot unavailable ─────

def test_emits_risk_event_when_derivatives_enabled_and_unavailable():
    repo = _make_repo(snapshot_return=None)
    provider = LiquidationHeatmapProvider(repo)
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", True):
        run(provider.detect_zones(_SYMBOL, _TS))
    repo.insert_risk_event.assert_awaited_once()
    call_kwargs = repo.insert_risk_event.call_args
    assert call_kwargs.kwargs["event_type"] == "LIQUIDATION_HEATMAP_UNAVAILABLE"


# ── 4. Does NOT emit risk event when derivatives disabled ─────────────────────

def test_no_risk_event_when_derivatives_disabled():
    repo = _make_repo(snapshot_return=None)
    provider = LiquidationHeatmapProvider(repo)
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", False):
        run(provider.detect_zones(_SYMBOL, _TS))
    repo.insert_risk_event.assert_not_awaited()


# ── 5. Risk event failure does not propagate (provider stays silent) ──────────

def test_risk_event_failure_does_not_raise():
    repo = _make_repo(snapshot_return=None, risk_event_raises=True)
    provider = LiquidationHeatmapProvider(repo)
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", True):
        zones = run(provider.detect_zones(_SYMBOL, _TS))  # must not raise
    assert zones == []


# ── 6. Filters bins below min_intensity ───────────────────────────────────────

def test_filters_bins_below_min_intensity():
    bins = [
        _bin(distance_pct=0.02, intensity=0.1),   # below 0.3 → excluded
        _bin(distance_pct=0.03, intensity=0.5),   # above → included
        _bin(distance_pct=-0.02, intensity=0.05),  # below → excluded
    ]
    provider = LiquidationHeatmapProvider(_make_repo(snapshot_return=bins))
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", True):
        zones = run(provider.detect_zones(_SYMBOL, _TS, min_intensity=0.3))
    assert len(zones) == 1
    assert zones[0].intensity_score == 0.5


# ── 7. Returns empty when all bins are below min_intensity ────────────────────

def test_returns_empty_when_all_bins_below_min_intensity():
    bins = [_bin(0.01, 0.1), _bin(-0.01, 0.05)]
    provider = LiquidationHeatmapProvider(_make_repo(snapshot_return=bins))
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", True):
        zones = run(provider.detect_zones(_SYMBOL, _TS, min_intensity=0.3))
    assert zones == []


# ── 8. Separates zones above and below price, max_zones_per_side respected ───

def test_max_zones_per_side_respected():
    # 4 above + 4 below, limit=2 per side → max 4 total
    bins = (
        [_bin(distance_pct=0.01 * (i + 1), intensity=0.8 - i * 0.05) for i in range(4)]
        + [_bin(distance_pct=-0.01 * (i + 1), intensity=0.8 - i * 0.05) for i in range(4)]
    )
    provider = LiquidationHeatmapProvider(_make_repo(snapshot_return=bins))
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", True):
        zones = run(provider.detect_zones(_SYMBOL, _TS, min_intensity=0.3, max_zones_per_side=2))
    above = [z for z in zones if z.meta["distance_pct"] > 0]
    below = [z for z in zones if z.meta["distance_pct"] < 0]
    assert len(above) <= 2
    assert len(below) <= 2


# ── 9. Zones ordered by intensity descending within each side ─────────────────

def test_zones_ordered_by_intensity_descending():
    bins = [
        _bin(distance_pct=0.01, intensity=0.4),
        _bin(distance_pct=0.02, intensity=0.9),
        _bin(distance_pct=0.03, intensity=0.6),
    ]
    provider = LiquidationHeatmapProvider(_make_repo(snapshot_return=bins))
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", True):
        zones = run(provider.detect_zones(_SYMBOL, _TS, min_intensity=0.3, max_zones_per_side=5))
    scores = [z.intensity_score for z in zones]
    assert scores == sorted(scores, reverse=True)


# ── 10. LiquidityZone fields correctly mapped from bin ────────────────────────

def test_liquidityzone_fields_correctly_mapped():
    b = _bin(distance_pct=0.05, intensity=0.75, nearest_side="SHORT", price_mid=82000.0)
    provider = LiquidationHeatmapProvider(_make_repo(snapshot_return=[b]))
    with patch("trading.core.liquidity.providers.liquidation_heatmap_provider.DERIVATIVES_ENABLED", True):
        zones = run(provider.detect_zones(_SYMBOL, _TS, min_intensity=0.3))

    assert len(zones) == 1
    z = zones[0]
    assert z.zone_type == "LIQUIDATION_CLUSTER"
    assert z.source == "liquidation_heatmap"
    assert abs(z.price_level - 82000.0) < 0.01
    assert z.intensity_score == 0.75
    assert z.timestamp == _TS
    assert z.meta["side_dominant"] == "SHORT"
    assert z.meta["distance_pct"] == 0.05
    assert "bin_width_pct" in z.meta
