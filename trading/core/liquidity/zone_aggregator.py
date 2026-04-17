"""
Liquidity Zone Aggregator — Fase 4.

Consolidates zones from multiple providers (sync OHLCV + async derivatives)
into a single ranked snapshot. Provider failures are silenced; the aggregator
always returns a valid (possibly empty) snapshot.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..data.candle_reader import Candle
from .zone_types import LiquidityZone

log = logging.getLogger(__name__)

SyncZoneCallable = Callable[[List[Candle]], List[LiquidityZone]]
AsyncZoneCallable = Callable[..., Awaitable[List[LiquidityZone]]]


@dataclass
class AggregatedLiquiditySnapshot:
    symbol: str
    reference_ts: datetime
    all_zones: List[LiquidityZone]
    zones_above: List[LiquidityZone]
    zones_below: List[LiquidityZone]
    nearest_above_any: Optional[LiquidityZone]
    nearest_below_any: Optional[LiquidityZone]
    strongest_above: Optional[LiquidityZone]
    strongest_below: Optional[LiquidityZone]
    sources_active: List[str]
    sources_failed: List[str]
    provider_counts: Dict[str, int]
    features: Dict[str, Any] = field(default_factory=dict)


class LiquidityZoneAggregator:
    """
    Calls multiple zone providers and consolidates their output.

    Sync providers: called sequentially with (candles,) — all OHLCV-based.
    Async providers: called in parallel via asyncio.gather — e.g. LiquidationHeatmapProvider.
    A failing provider is logged and added to sources_failed; the snapshot is still returned.
    """

    def __init__(
        self,
        sync_providers: List[SyncZoneCallable],
        async_providers: List[AsyncZoneCallable],
    ) -> None:
        self._sync = sync_providers
        self._async = async_providers

    async def build_snapshot(
        self,
        symbol: str,
        reference_ts: datetime,
        candles: List[Candle],
        current_price: float,
    ) -> AggregatedLiquiditySnapshot:
        all_zones: List[LiquidityZone] = []
        sources_failed: List[str] = []
        provider_counts: Dict[str, int] = {}

        # ── Sync providers ─────────────────────────────────────────────────
        for provider_fn in self._sync:
            try:
                zones = provider_fn(candles)
                all_zones.extend(zones)
                for z in zones:
                    provider_counts[z.source] = provider_counts.get(z.source, 0) + 1
            except Exception as exc:
                src = getattr(provider_fn, "__self__", provider_fn).__class__.__name__
                log.warning("sync provider %s failed: %s", src, exc)
                sources_failed.append(src)

        # ── Async providers ────────────────────────────────────────────────
        if self._async:
            results = await asyncio.gather(
                *[p(symbol, reference_ts) for p in self._async],
                return_exceptions=True,
            )
            for i, result in enumerate(results):
                provider_fn = self._async[i]
                if isinstance(result, Exception):
                    src = getattr(provider_fn, "__self__", provider_fn).__class__.__name__
                    log.warning("async provider %s failed: %s", src, result)
                    sources_failed.append(src)
                else:
                    zones: List[LiquidityZone] = result  # type: ignore[assignment]
                    all_zones.extend(zones)
                    for z in zones:
                        provider_counts[z.source] = provider_counts.get(z.source, 0) + 1

        # ── Separate above/below and sort by distance ──────────────────────
        zones_above = sorted(
            [z for z in all_zones if z.price_level > current_price],
            key=lambda z: z.price_level - current_price,
        )
        zones_below = sorted(
            [z for z in all_zones if z.price_level < current_price],
            key=lambda z: current_price - z.price_level,
        )

        nearest_above = zones_above[0] if zones_above else None
        nearest_below = zones_below[0] if zones_below else None
        strongest_above = max(zones_above, key=lambda z: z.intensity_score) if zones_above else None
        strongest_below = max(zones_below, key=lambda z: z.intensity_score) if zones_below else None

        sources_active = [src for src, cnt in provider_counts.items() if cnt > 0]

        features: Dict[str, Any] = {
            "agg_total_zones": len(all_zones),
            "agg_zones_above": len(zones_above),
            "agg_zones_below": len(zones_below),
            "agg_sources_active": ",".join(sorted(sources_active)),
        }
        if sources_failed:
            features["agg_sources_failed"] = ",".join(sorted(set(sources_failed)))
        for src, cnt in provider_counts.items():
            features[f"agg_{src}_count"] = cnt

        return AggregatedLiquiditySnapshot(
            symbol=symbol,
            reference_ts=reference_ts,
            all_zones=all_zones,
            zones_above=zones_above,
            zones_below=zones_below,
            nearest_above_any=nearest_above,
            nearest_below_any=nearest_below,
            strongest_above=strongest_above,
            strongest_below=strongest_below,
            sources_active=sources_active,
            sources_failed=list(set(sources_failed)),
            provider_counts=provider_counts,
            features=features,
        )
