#!/usr/bin/env python3
"""
Smoke test for Phase 2 liquidity zone providers.

Reads 200 recent BTCUSDT 15m candles from the local DB, runs all zone
providers (EqualLevels, Swing, FVG, PriorLevels, SweepDetector) and
prints a summary of detected zones.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db python3 scripts/smoke_liquidity_providers.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.core.liquidity.providers import (
    EqualLevelsProvider,
    FairValueGapProvider,
    PriorLevelsProvider,
    SweepDetector,
    SwingProvider,
)


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "postgresql://jarvis:jarvispass@localhost:5432/jarvis")

    try:
        import asyncpg
    except ImportError:
        print("asyncpg not available — skipping DB read")
        return

    pool = None
    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, timeout=5)
    except Exception as exc:
        print(f"DB connection failed ({exc}) — smoke test skipped")
        return

    try:
        from trading.core.data.candle_reader import CandleReader

        reader = CandleReader(pool)
        candles = await reader.get_latest_tf_candles("BTCUSDT", tf_minutes=15, limit=200)

        if not candles:
            print("No candles — is the DB populated?")
            return

        print(f"Loaded {len(candles)} BTCUSDT 15m candles  ({candles[0].open_time} → {candles[-1].open_time})")
        close = candles[-1].close
        print(f"Current close: {close:.2f}\n")

        # ── Equal levels ──────────────────────────────────────────────────
        equal_zones = EqualLevelsProvider().detect_zones(candles, tolerance_bps=10.0, min_touches=2, lookback_bars=200)
        print(f"[EqualLevels] {len(equal_zones)} zone(s):")
        _print_zones(equal_zones, close)

        # ── Sweeps ────────────────────────────────────────────────────────
        sweeps = SweepDetector().detect_sweeps(candles, equal_zones, wick_threshold_bps=5.0)
        print(f"\n[Sweeps] {len(sweeps)} event(s):")
        for s in sweeps:
            print(f"  {s.direction:<12}  zone={s.zone.price_level:>12.2f}  ext={s.wick_extension_bps:.1f} bps  ts={s.sweep_candle_timestamp}")

        # ── Swing highs/lows ──────────────────────────────────────────────
        swing_zones = SwingProvider().detect_zones(candles, n_bars=3, lookback_bars=200)
        print(f"\n[Swing] {len(swing_zones)} zone(s):")
        _print_zones(swing_zones, close)

        # ── Fair Value Gaps ───────────────────────────────────────────────
        fvg_zones = FairValueGapProvider().detect_zones(candles, min_gap_bps=5.0, lookback_bars=100)
        print(f"\n[FVG] {len(fvg_zones)} zone(s):")
        for z in sorted(fvg_zones, key=lambda z: z.intensity_score, reverse=True):
            gap = z.meta.get("gap_bps", 0)
            side = "above" if z.price_level > close else "below"
            print(
                f"  {z.zone_type:<12}  mid={z.price_level:>12.2f}  gap={gap:.1f} bps  {side}"
                f"  intensity={z.intensity_score:.3f}"
            )

        # ── Prior levels ──────────────────────────────────────────────────
        prior_zones = PriorLevelsProvider().detect_zones(candles)
        print(f"\n[PriorLevels] {len(prior_zones)} zone(s):")
        _print_zones(prior_zones, close)

    finally:
        await pool.close()


def _print_zones(zones, close: float) -> None:
    if not zones:
        print("  (none)")
        return
    for z in sorted(zones, key=lambda z: z.intensity_score, reverse=True)[:10]:
        dist_bps = (z.price_level - close) / close * 10_000.0
        side = f"+{dist_bps:.0f}" if dist_bps >= 0 else f"{dist_bps:.0f}"
        print(
            f"  {z.zone_type:<18}  price={z.price_level:>12.2f}  {side} bps"
            f"  intensity={z.intensity_score:.3f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
