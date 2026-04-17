#!/usr/bin/env python3
"""
Smoke test for LiquidationHeatmapProvider (Fase 2 V2).

Connects to the derivatives-collector DB, reads the latest liquidation_heatmap
snapshot for BTCUSDT and runs LiquidationHeatmapProvider against it.

Usage:
    DERIVATIVES_DATABASE_URL=postgresql://user:pass@localhost:5433/derivatives_data \
    DERIVATIVES_ENABLED=true \
    LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED=true \
    python3 scripts/smoke_liquidation_heatmap.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    derivatives_url = os.environ.get(
        "DERIVATIVES_DATABASE_URL",
        "postgresql://jarvis:jarvispass@localhost:5433/derivatives_data",
    )
    derivatives_enabled = os.environ.get("DERIVATIVES_ENABLED", "false").lower() in {"1", "true", "yes"}

    if not derivatives_enabled:
        print("DERIVATIVES_ENABLED is not set — set DERIVATIVES_ENABLED=true to run this smoke test")
        print("(Smoke test skipped)")
        return

    try:
        import asyncpg
    except ImportError:
        print("asyncpg not available — skipping")
        return

    print(f"Connecting to derivatives DB: {derivatives_url[:40]}...")
    try:
        derivatives_pool = await asyncpg.create_pool(
            derivatives_url, min_size=1, max_size=2, timeout=5
        )
    except Exception as exc:
        print(f"Derivatives DB connection failed ({exc}) — smoke test skipped")
        return

    try:
        from trading.core.storage.repository import TradingRepository
        from trading.core.liquidity.providers import LiquidationHeatmapProvider

        repo = TradingRepository(pool=None, derivatives_pool=derivatives_pool)
        provider = LiquidationHeatmapProvider(repo)

        reference_ts = datetime.now(timezone.utc)
        symbol = "BTCUSDT"

        print(f"\nReference ts: {reference_ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Symbol: {symbol}")

        # ── Raw snapshot ──────────────────────────────────────────────
        bins = await repo.get_liquidation_heatmap_snapshot(
            symbol=symbol,
            reference_ts=reference_ts,
            max_age_minutes=10,
        )

        if bins is None:
            print("\n[Repository] No snapshot within freshness window (max_age_minutes=10)")
            print("  → Try increasing max_age_minutes or check collector is running")
        else:
            print(f"\n[Repository] {len(bins)} bins in latest snapshot")
            print(f"  Snapshot ts: {bins[0].snapshot_ts}")
            high_intensity = sorted(bins, key=lambda b: b.intensity, reverse=True)[:5]
            print(f"  Top 5 by intensity:")
            for b in high_intensity:
                side = "above" if b.distance_from_price_pct > 0 else "below"
                print(
                    f"    price_mid={b.price_mid:>10.2f}  intensity={b.intensity:.3f}"
                    f"  dist={b.distance_from_price_pct:+.3f}  {side}"
                    f"  dominant={b.nearest_side}"
                )

        # ── Provider output ───────────────────────────────────────────
        print("\n[Provider] Running detect_zones (min_intensity=0.3, max_zones_per_side=5)...")
        zones = await provider.detect_zones(
            symbol=symbol,
            reference_ts=reference_ts,
            min_intensity=0.3,
            max_zones_per_side=5,
            max_age_minutes=15,
        )

        if not zones:
            print("  → No zones returned (heatmap unavailable or all bins below threshold)")
        else:
            print(f"  {len(zones)} LIQUIDATION_CLUSTER zone(s):")
            above = [z for z in zones if z.meta["distance_pct"] > 0]
            below = [z for z in zones if z.meta["distance_pct"] < 0]
            print(f"  Above price: {len(above)} zone(s)")
            for z in above:
                print(
                    f"    price={z.price_level:>10.2f}  intensity={z.intensity_score:.3f}"
                    f"  dist={z.meta['distance_pct']:+.3f}"
                    f"  dominant={z.meta['side_dominant']}"
                )
            print(f"  Below price: {len(below)} zone(s)")
            for z in below:
                print(
                    f"    price={z.price_level:>10.2f}  intensity={z.intensity_score:.3f}"
                    f"  dist={z.meta['distance_pct']:+.3f}"
                    f"  dominant={z.meta['side_dominant']}"
                )

        # ── Multi-symbol ──────────────────────────────────────────────
        print("\n[Multi-symbol] Checking ETHUSDT and SOLUSDT...")
        for sym in ("ETHUSDT", "SOLUSDT"):
            z = await provider.detect_zones(sym, reference_ts, min_intensity=0.3, max_age_minutes=15)
            print(f"  {sym}: {len(z)} zone(s)")

    finally:
        await derivatives_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
