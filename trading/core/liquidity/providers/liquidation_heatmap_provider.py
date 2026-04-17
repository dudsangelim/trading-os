"""
Liquidation heatmap provider.

Reads pre-computed liquidation_heatmap bins from the derivatives-collector DB
and exposes high-intensity bins as LiquidityZone instances.

Unlike OHLCV-derived providers this provider is async and returns an empty
list whenever the derivatives adapter is unavailable — it never raises or
fails the snapshot.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, List

from ..zone_types import LiquidityZone

if TYPE_CHECKING:
    from ...storage.repository import TradingRepository

from ...config.settings import DERIVATIVES_ENABLED

log = logging.getLogger(__name__)

_RISK_EVENT_TYPE = "LIQUIDATION_HEATMAP_UNAVAILABLE"


class LiquidationHeatmapProvider:
    def __init__(self, repository: "TradingRepository") -> None:
        self._repo = repository

    async def detect_zones(
        self,
        symbol: str,
        reference_ts: datetime,
        min_intensity: float = 0.3,
        max_zones_per_side: int = 5,
        max_age_minutes: int = 10,
    ) -> List[LiquidityZone]:
        """
        Parameters
        ----------
        min_intensity : threshold for including a bin (0.0–1.0)
        max_zones_per_side : cap on zones above price and below price separately
        max_age_minutes : freshness requirement for the snapshot

        Returns empty list if heatmap is unavailable or no bins pass the filter.
        """
        bins = await self._repo.get_liquidation_heatmap_snapshot(
            symbol=symbol,
            reference_ts=reference_ts,
            max_age_minutes=max_age_minutes,
        )

        if not bins:
            if DERIVATIVES_ENABLED:
                try:
                    await self._repo.insert_risk_event(
                        event_type=_RISK_EVENT_TYPE,
                        description=f"liquidation_heatmap unavailable for {symbol}",
                        payload={"symbol": symbol, "reference_ts": reference_ts.isoformat()},
                    )
                except Exception as exc:
                    log.warning("Failed to insert risk event %s: %s", _RISK_EVENT_TYPE, exc)
            return []

        qualified = [b for b in bins if b.intensity >= min_intensity]
        if not qualified:
            return []

        above = sorted(
            [b for b in qualified if b.distance_from_price_pct > 0],
            key=lambda b: b.intensity,
            reverse=True,
        )[:max_zones_per_side]

        below = sorted(
            [b for b in qualified if b.distance_from_price_pct < 0],
            key=lambda b: b.intensity,
            reverse=True,
        )[:max_zones_per_side]

        zones: List[LiquidityZone] = []
        for bin_ in above + below:
            zones.append(LiquidityZone(
                price_level=bin_.price_mid,
                zone_type="LIQUIDATION_CLUSTER",
                source="liquidation_heatmap",
                intensity_score=bin_.intensity,
                timestamp=bin_.snapshot_ts,
                meta={
                    "side_dominant": bin_.nearest_side,
                    "long_liq_usd": bin_.long_liq_volume_usd,
                    "short_liq_usd": bin_.short_liq_volume_usd,
                    "net_liq_usd": bin_.net_liq_volume_usd,
                    "distance_pct": bin_.distance_from_price_pct,
                    "bin_width_pct": (bin_.price_high - bin_.price_low) / bin_.price_mid,
                },
            ))
        return zones
