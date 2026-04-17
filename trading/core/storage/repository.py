"""
Repository — asyncpg CRUD for all tr_* tables.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

log = logging.getLogger(__name__)

from ..config.settings import (
    DERIVATIVES_ENABLED,
    DERIVATIVES_FEATURE_ADAPTER_ENABLED,
    DERIVATIVES_FRESHNESS_THRESHOLD_SECONDS,
    DERIVATIVES_MIN_FEATURES_REQUIRED,
    DERIVATIVES_STALE_THRESHOLD_SECONDS,
    ENGINE_DERIVATIVES_ADVISORY_ENABLED,
    ENGINE_DERIVATIVES_HARD_FILTER_ENABLED,
)
from ..engines.base import Direction, Position, Signal, SignalDecision

@dataclass
class LiquidationHeatmapBin:
    symbol: str
    snapshot_ts: datetime
    price_low: float
    price_high: float
    price_mid: float
    long_liq_volume_usd: float
    short_liq_volume_usd: float
    net_liq_volume_usd: float
    total_liq_volume_usd: float
    intensity: float
    distance_from_price_pct: float
    nearest_side: str  # "LONG" | "SHORT"


_HEATMAP_SQL = """
    SELECT
        symbol,
        timestamp,
        price_low,
        price_high,
        total_long_liq_volume,
        total_short_liq_volume,
        net_liq_volume,
        intensity,
        distance_from_price_pct,
        nearest_side
    FROM liquidation_heatmap
    WHERE symbol = $1
      AND timestamp = (
          SELECT MAX(timestamp)
          FROM liquidation_heatmap
          WHERE symbol = $1
      )
      AND timestamp <= $2
      AND timestamp >= $2 - INTERVAL '1 minute' * $3
    ORDER BY price_low ASC
"""


DERIVATIVE_FEATURE_KEYS: Tuple[str, ...] = (
    "orderbook_imbalance",
    "orderbook_imbalance_delta",
    "funding_rate",
    "funding_rate_zscore",
    "oi_delta_1h",
    "oi_delta_4h",
    "taker_aggression",
    "liquidation_intensity",
    "liquidation_imbalance",
    "squeeze_score",
    "trend_conviction",
)

DERIVATIVE_MODE_DISCONNECTED = "DISCONNECTED"
DERIVATIVE_MODE_ADVISORY_ONLY = "ADVISORY_ONLY"
DERIVATIVE_MODE_DEGRADED = "DEGRADED"
DERIVATIVE_MODE_AVAILABLE = "AVAILABLE"


class TradingRepository:
    def __init__(
        self,
        pool: asyncpg.Pool,
        derivatives_pool: Optional[asyncpg.Pool] = None,
    ) -> None:
        self._pool = pool
        self._derivatives_pool = derivatives_pool

    @staticmethod
    def _seconds_since(now: datetime, ts: Optional[datetime]) -> Optional[float]:
        if ts is None:
            return None
        return round(max(0.0, (now - ts).total_seconds()), 3)

    @staticmethod
    def _to_iso(ts: Optional[datetime]) -> Optional[str]:
        return ts.isoformat() if ts is not None else None

    def _derivatives_flags(self) -> Dict[str, Any]:
        return {
            "derivatives_enabled": DERIVATIVES_ENABLED,
            "derivatives_feature_adapter_enabled": DERIVATIVES_FEATURE_ADAPTER_ENABLED,
            "engine_derivatives_advisory_enabled": ENGINE_DERIVATIVES_ADVISORY_ENABLED,
            "engine_derivatives_hard_filter_enabled": ENGINE_DERIVATIVES_HARD_FILTER_ENABLED,
        }

    def _empty_derivatives_snapshot(
        self,
        *,
        now: datetime,
        symbols: List[str],
        mode: str,
        adapter_status: str,
        fallback_reason: Optional[str],
    ) -> Dict[str, Any]:
        symbol_payload = []
        canonical_snapshot = []
        for symbol in symbols:
            entry = {
                "symbol": symbol,
                "available": False,
                "quality": "UNAVAILABLE",
                "freshness": "ABSENT",
                "last_observation_at": None,
                "age_seconds": None,
                "features": {},
                "available_features": [],
                "missing_features": list(DERIVATIVE_FEATURE_KEYS),
            }
            symbol_payload.append(entry)
            canonical_snapshot.append(
                {
                    "symbol": symbol,
                    "timestamp": now.isoformat(),
                    "source_status": adapter_status.upper(),
                    "available_features": [],
                    "freshness": entry["freshness"],
                    "quality": entry["quality"],
                    "fallback_active": True,
                    "fallback_reason": fallback_reason,
                }
            )
        return {
            "status": adapter_status,
            "mode": mode,
            "adapter_enabled": bool(
                DERIVATIVES_ENABLED and DERIVATIVES_FEATURE_ADAPTER_ENABLED and self._derivatives_pool
            ),
            "fallback_active": True,
            "fallback_reason": fallback_reason,
            "timestamp": now.isoformat(),
            "flags": self._derivatives_flags(),
            "contract": {
                "version": "F.1",
                "feature_keys": list(DERIVATIVE_FEATURE_KEYS),
                "freshness_threshold_seconds": DERIVATIVES_FRESHNESS_THRESHOLD_SECONDS,
                "stale_threshold_seconds": DERIVATIVES_STALE_THRESHOLD_SECONDS,
                "min_features_required": DERIVATIVES_MIN_FEATURES_REQUIRED,
            },
            "symbols": symbol_payload,
            "canonical_snapshot": canonical_snapshot,
            "summary": {
                "total_symbols": len(symbols),
                "available_symbols": 0,
                "degraded_symbols": len(symbols),
                "features_available_by_symbol": {symbol: [] for symbol in symbols},
                "collector_quality": "UNKNOWN",
            },
        }

    async def get_derivatives_feature_snapshot(
        self,
        symbols: List[str],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        symbols = sorted({s for s in symbols if s})
        if not symbols:
            return self._empty_derivatives_snapshot(
                now=now,
                symbols=[],
                mode=DERIVATIVE_MODE_DISCONNECTED,
                adapter_status="disconnected",
                fallback_reason="no_symbols",
            )

        if not DERIVATIVES_ENABLED:
            return self._empty_derivatives_snapshot(
                now=now,
                symbols=symbols,
                mode=DERIVATIVE_MODE_DISCONNECTED,
                adapter_status="disconnected",
                fallback_reason="derivatives_disabled",
            )
        if not DERIVATIVES_FEATURE_ADAPTER_ENABLED:
            return self._empty_derivatives_snapshot(
                now=now,
                symbols=symbols,
                mode=DERIVATIVE_MODE_DISCONNECTED,
                adapter_status="disconnected",
                fallback_reason="derivatives_feature_adapter_disabled",
            )
        if self._derivatives_pool is None:
            return self._empty_derivatives_snapshot(
                now=now,
                symbols=symbols,
                mode=DERIVATIVE_MODE_DISCONNECTED,
                adapter_status="disconnected",
                fallback_reason="derivatives_pool_unavailable",
            )

        try:
            async with self._derivatives_pool.acquire() as conn:
                feature_rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (symbol)
                        symbol,
                        timestamp,
                        orderbook_imbalance,
                        orderbook_imbalance_delta,
                        oi_delta_1h,
                        oi_delta_4h,
                        taker_aggression,
                        liquidation_intensity,
                        funding_rate,
                        funding_rate_zscore,
                        liquidation_imbalance,
                        squeeze_score,
                        trend_conviction
                    FROM derived_features
                    WHERE symbol = ANY($1::text[])
                    ORDER BY symbol, timestamp DESC
                    """,
                    symbols,
                )
                funding_rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (base_symbol)
                        base_symbol AS symbol,
                        timestamp,
                        funding_rate
                    FROM (
                        SELECT
                            CASE
                                WHEN symbol LIKE '%_PERP.A' THEN split_part(symbol, '_PERP.A', 1)
                                ELSE symbol
                            END AS base_symbol,
                            timestamp,
                            funding_rate
                        FROM coinalyze_funding_rate
                    ) mapped
                    WHERE base_symbol = ANY($1::text[])
                    ORDER BY base_symbol, timestamp DESC
                    """,
                    symbols,
                )
                quality_rows = await conn.fetch(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM collection_log
                    WHERE collected_at >= NOW() - INTERVAL '30 minutes'
                    GROUP BY status
                    """
                )
                # Fetch liquidation heatmap zones for CN5 engine
                heatmap_rows = await conn.fetch(
                    """
                    SELECT symbol, price_low, price_high,
                           total_long_liq_volume, total_short_liq_volume,
                           net_liq_volume, intensity,
                           distance_from_price_pct, nearest_side
                    FROM liquidation_heatmap
                    WHERE symbol = ANY($1::text[])
                      AND timestamp = (
                          SELECT MAX(timestamp) FROM liquidation_heatmap
                          WHERE symbol = ANY($1::text[])
                      )
                      AND intensity > 0.1
                    ORDER BY intensity DESC
                    """,
                    symbols,
                )
        except Exception as exc:
            return self._empty_derivatives_snapshot(
                now=now,
                symbols=symbols,
                mode=DERIVATIVE_MODE_DEGRADED,
                adapter_status="degraded",
                fallback_reason=f"derivatives_query_error:{type(exc).__name__}",
            )

        feature_by_symbol = {row["symbol"]: dict(row) for row in feature_rows}
        funding_by_symbol = {row["symbol"]: dict(row) for row in funding_rows}

        # Build heatmap zones per symbol for CN5 engine
        heatmap_by_symbol: Dict[str, Dict[str, Any]] = {}
        for row in heatmap_rows:
            sym = row["symbol"]
            zone = {
                "price_low": row["price_low"],
                "price_high": row["price_high"],
                "long_volume": row["total_long_liq_volume"],
                "short_volume": row["total_short_liq_volume"],
                "net_volume": row["net_liq_volume"],
                "intensity": row["intensity"],
                "distance_pct": row["distance_from_price_pct"],
                "side": row["nearest_side"],
            }
            if sym not in heatmap_by_symbol:
                heatmap_by_symbol[sym] = {"nearest_above": [], "nearest_below": [], "strongest_magnet": None}
            mid = (row["price_low"] + row["price_high"]) / 2
            # Use distance_pct sign to determine above/below
            if row["distance_from_price_pct"] > 0:
                heatmap_by_symbol[sym]["nearest_above"].append(zone)
            else:
                heatmap_by_symbol[sym]["nearest_below"].append(zone)
        # Set strongest magnet (within 3%) for each symbol
        for sym, hz in heatmap_by_symbol.items():
            hz["nearest_above"].sort(key=lambda z: abs(z["distance_pct"]))
            hz["nearest_below"].sort(key=lambda z: abs(z["distance_pct"]))
            hz["nearest_above"] = hz["nearest_above"][:5]
            hz["nearest_below"] = hz["nearest_below"][:5]
            close_zones = [
                z for z in (hz["nearest_above"] + hz["nearest_below"])
                if abs(z["distance_pct"]) <= 0.03
            ]
            hz["strongest_magnet"] = max(close_zones, key=lambda z: z["intensity"]) if close_zones else None

        collector_counts: Dict[str, int] = {}
        for row in quality_rows:
            key = str(row.get("status") or "").upper()
            collector_counts[key] = int(row.get("count") or 0)
        ok_count = collector_counts.get("OK", 0)
        err_count = collector_counts.get("ERROR", 0)
        total_count = max(1, sum(collector_counts.values()))
        error_ratio = err_count / total_count
        collector_quality = "GOOD" if error_ratio <= 0.35 else "DEGRADED"

        symbol_payload: List[Dict[str, Any]] = []
        canonical_snapshot: List[Dict[str, Any]] = []
        available_symbol_count = 0
        degraded_symbol_count = 0
        features_available_by_symbol: Dict[str, List[str]] = {}

        for symbol in symbols:
            feature_row = feature_by_symbol.get(symbol, {})
            funding_row = funding_by_symbol.get(symbol, {})

            row_ts = feature_row.get("timestamp")
            funding_ts = funding_row.get("timestamp")
            last_obs = max([ts for ts in (row_ts, funding_ts) if ts is not None], default=None)
            age = self._seconds_since(now, last_obs)

            if last_obs is None:
                freshness = "ABSENT"
            elif age is not None and age <= DERIVATIVES_FRESHNESS_THRESHOLD_SECONDS:
                freshness = "FRESH"
            elif age is not None and age <= DERIVATIVES_STALE_THRESHOLD_SECONDS:
                freshness = "DEGRADED"
            else:
                freshness = "STALE"

            features = {
                "orderbook_imbalance": feature_row.get("orderbook_imbalance"),
                "orderbook_imbalance_delta": feature_row.get("orderbook_imbalance_delta"),
                "funding_rate": feature_row.get("funding_rate") or funding_row.get("funding_rate"),
                "funding_rate_zscore": feature_row.get("funding_rate_zscore"),
                "oi_delta_1h": feature_row.get("oi_delta_1h"),
                "oi_delta_4h": feature_row.get("oi_delta_4h"),
                "taker_aggression": feature_row.get("taker_aggression"),
                "liquidation_intensity": feature_row.get("liquidation_intensity"),
                "liquidation_imbalance": feature_row.get("liquidation_imbalance"),
                "squeeze_score": feature_row.get("squeeze_score"),
                "trend_conviction": feature_row.get("trend_conviction"),
                "heatmap_zones": heatmap_by_symbol.get(symbol),
            }
            available_features = [k for k, v in features.items() if v is not None]
            missing_features = [k for k in DERIVATIVE_FEATURE_KEYS if k not in available_features]
            features_available_by_symbol[symbol] = available_features

            available = (
                freshness != "STALE"
                and len(available_features) >= DERIVATIVES_MIN_FEATURES_REQUIRED
            )
            if available:
                available_symbol_count += 1
            if not available or freshness in {"DEGRADED", "STALE", "ABSENT"}:
                degraded_symbol_count += 1

            quality = "GOOD" if available and freshness == "FRESH" else (
                "DEGRADED" if (available or available_features) else "UNAVAILABLE"
            )
            symbol_payload.append(
                {
                    "symbol": symbol,
                    "available": available,
                    "quality": quality,
                    "freshness": freshness,
                    "last_observation_at": self._to_iso(last_obs),
                    "age_seconds": age,
                    "features": features,
                    "available_features": available_features,
                    "missing_features": missing_features,
                }
            )
            canonical_snapshot.append(
                {
                    "symbol": symbol,
                    "timestamp": now.isoformat(),
                    "source_status": "OK" if available else "DEGRADED",
                    "available_features": available_features,
                    "freshness": freshness,
                    "quality": quality,
                    "fallback_active": False,
                    "fallback_reason": None,
                }
            )

        degraded = (
            collector_quality == "DEGRADED"
            or degraded_symbol_count > 0
            or available_symbol_count == 0
        )
        mode = (
            DERIVATIVE_MODE_DEGRADED
            if degraded
            else (
                DERIVATIVE_MODE_ADVISORY_ONLY
                if ENGINE_DERIVATIVES_ADVISORY_ENABLED
                else DERIVATIVE_MODE_AVAILABLE
            )
        )

        fallback_reason: Optional[str] = None
        if collector_quality == "DEGRADED":
            fallback_reason = "collector_degraded_recent_errors"
        elif degraded_symbol_count > 0:
            fallback_reason = "symbol_feature_unavailable_or_stale"
        if degraded:
            for row in canonical_snapshot:
                row["fallback_active"] = True
                row["fallback_reason"] = fallback_reason

        return {
            "status": "degraded" if degraded else "ok",
            "mode": mode,
            "adapter_enabled": True,
            "fallback_active": degraded,
            "fallback_reason": fallback_reason,
            "timestamp": now.isoformat(),
            "flags": self._derivatives_flags(),
            "contract": {
                "version": "F.1",
                "feature_keys": list(DERIVATIVE_FEATURE_KEYS),
                "freshness_threshold_seconds": DERIVATIVES_FRESHNESS_THRESHOLD_SECONDS,
                "stale_threshold_seconds": DERIVATIVES_STALE_THRESHOLD_SECONDS,
                "min_features_required": DERIVATIVES_MIN_FEATURES_REQUIRED,
            },
            "symbols": symbol_payload,
            "canonical_snapshot": canonical_snapshot,
            "summary": {
                "total_symbols": len(symbols),
                "available_symbols": available_symbol_count,
                "degraded_symbols": degraded_symbol_count,
                "features_available_by_symbol": features_available_by_symbol,
                "collector_quality": collector_quality,
                "collector_ok_samples_30m": ok_count,
                "collector_error_samples_30m": err_count,
                "collector_error_ratio_30m": round(error_ratio, 4),
            },
        }

    async def get_liquidation_heatmap_snapshot(
        self,
        symbol: str,
        reference_ts: datetime,
        max_age_minutes: int = 10,
    ) -> Optional[List[LiquidationHeatmapBin]]:
        """
        Returns the latest liquidation_heatmap snapshot for symbol, provided it
        is no older than max_age_minutes relative to reference_ts.

        Returns None if:
        - DERIVATIVES_ENABLED is False or _derivatives_pool is unavailable
        - No snapshot within the freshness window
        - Any connection/query error (never propagates exceptions)
        """
        if not DERIVATIVES_ENABLED or self._derivatives_pool is None:
            return None
        try:
            async with self._derivatives_pool.acquire() as conn:
                rows = await conn.fetch(_HEATMAP_SQL, symbol, reference_ts, max_age_minutes)
        except Exception as exc:
            log.warning("get_liquidation_heatmap_snapshot failed for %s: %s", symbol, exc)
            return None

        if not rows:
            return None

        bins: List[LiquidationHeatmapBin] = []
        for row in rows:
            p_low = float(row["price_low"])
            p_high = float(row["price_high"])
            long_vol = float(row["total_long_liq_volume"])
            short_vol = float(row["total_short_liq_volume"])
            bins.append(LiquidationHeatmapBin(
                symbol=row["symbol"],
                snapshot_ts=row["timestamp"],
                price_low=p_low,
                price_high=p_high,
                price_mid=(p_low + p_high) / 2.0,
                long_liq_volume_usd=long_vol,
                short_liq_volume_usd=short_vol,
                net_liq_volume_usd=float(row["net_liq_volume"]),
                total_liq_volume_usd=long_vol + short_vol,
                intensity=float(row["intensity"]),
                distance_from_price_pct=float(row["distance_from_price_pct"]),
                nearest_side=str(row["nearest_side"]),
            ))
        return bins

    # ------------------------------------------------------------------
    # tr_signals
    # ------------------------------------------------------------------

    async def insert_signal(self, signal: Signal) -> Optional[int]:
        """
        Insert signal, return its id.
        On duplicate (engine_id, bar_timestamp, direction) returns None
        (dedup handled by UNIQUE constraint — ON CONFLICT DO NOTHING).
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_signals
                    (engine_id, symbol, bar_timestamp, direction, signal_data)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (engine_id, bar_timestamp, direction) DO NOTHING
                RETURNING id
                """,
                signal.engine_id,
                signal.symbol,
                signal.bar_timestamp,
                signal.direction.value if hasattr(signal.direction, "value") else signal.direction,
                json.dumps({**signal.signal_data, "timeframe": signal.timeframe}),
            )
        return row["id"] if row else None

    async def get_signals(
        self,
        engine_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT id, engine_id, symbol, bar_timestamp, direction, signal_data, created_at
            FROM tr_signals
            {where}
            ORDER BY created_at DESC
            LIMIT $1
        """
        if engine_id:
            where = "WHERE engine_id = $2"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query.format(where=where), limit, engine_id)
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query.format(where=""), limit)
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # tr_signal_decisions
    # ------------------------------------------------------------------

    async def insert_decision(
        self,
        signal_id: Optional[int],
        decision: SignalDecision,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_signal_decisions
                    (signal_id, action, reason, entry_price, stop_price,
                     target_price, stake_usd)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                signal_id,
                decision.action.value if hasattr(decision.action, "value") else decision.action,
                decision.reason,
                decision.entry_price,
                decision.stop_price,
                decision.target_price,
                decision.stake_usd,
            )
        return row["id"]

    # ------------------------------------------------------------------
    # tr_paper_trades
    # ------------------------------------------------------------------

    async def open_trade(
        self,
        engine_id: str,
        symbol: str,
        signal_id: Optional[int],
        direction: Direction,
        entry_price: float,
        stop_price: float,
        target_price: float,
        stake_usd: float,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_paper_trades
                    (engine_id, symbol, signal_id, direction,
                     entry_price, stop_price, target_price, stake_usd, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'OPEN')
                RETURNING id
                """,
                engine_id,
                symbol,
                signal_id,
                direction.value if hasattr(direction, "value") else direction,
                entry_price,
                stop_price,
                target_price,
                stake_usd,
            )
        return row["id"]

    async def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        close_reason: str,
        closed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tr_paper_trades
                SET exit_price  = $2,
                    pnl_usd     = $3,
                    pnl_pct     = $4,
                    status      = $5,
                    close_reason= $6,
                    closed_at   = $7
                WHERE id = $1
                """,
                trade_id,
                exit_price,
                pnl_usd,
                pnl_pct,
                "CLOSED",
                close_reason,
                closed_at,
            )

    async def update_trade_stop(self, trade_id: int, stop_price: float) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE tr_paper_trades SET stop_price = $2 WHERE id = $1",
                trade_id,
                stop_price,
            )

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, engine_id, symbol, signal_id, direction,
                       entry_price, stop_price, target_price, stake_usd, opened_at, status
                FROM tr_paper_trades
                WHERE status = 'OPEN'
                ORDER BY opened_at ASC
                """
            )
        return [dict(r) for r in rows]

    async def get_trades(
        self,
        engine_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM tr_paper_trades
                    WHERE engine_id = $1
                    ORDER BY opened_at DESC LIMIT $2
                    """,
                    engine_id,
                    limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM tr_paper_trades
                    ORDER BY opened_at DESC LIMIT $1
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # tr_daily_equity
    # ------------------------------------------------------------------

    async def upsert_daily_equity(
        self,
        engine_id: str,
        date_utc: date,
        starting_equity: float,
        ending_equity: Optional[float] = None,
        pnl_usd: Optional[float] = None,
        trades_count: int = 0,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tr_daily_equity
                    (engine_id, date_utc, starting_equity, ending_equity, pnl_usd, trades_count)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (engine_id, date_utc) DO UPDATE
                SET ending_equity = EXCLUDED.ending_equity,
                    pnl_usd       = EXCLUDED.pnl_usd,
                    trades_count  = EXCLUDED.trades_count
                """,
                engine_id,
                date_utc,
                starting_equity,
                ending_equity,
                pnl_usd,
                trades_count,
            )

    async def get_equity(
        self, engine_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM tr_daily_equity WHERE engine_id=$1 ORDER BY date_utc DESC",
                    engine_id,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM tr_daily_equity ORDER BY date_utc DESC"
                )
        return [dict(r) for r in rows]

    async def get_today_pnl(self, engine_id: str) -> float:
        today = datetime.now(timezone.utc).date()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(pnl_usd), 0) AS total_pnl
                FROM tr_paper_trades
                WHERE engine_id = $1
                  AND DATE(closed_at AT TIME ZONE 'UTC') = $2
                  AND status != 'OPEN'
                """,
                engine_id,
                today,
            )
        return float(row["total_pnl"]) if row else 0.0

    async def get_today_starting_equity(self, engine_id: str) -> Optional[float]:
        today = datetime.now(timezone.utc).date()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT starting_equity FROM tr_daily_equity WHERE engine_id=$1 AND date_utc=$2",
                engine_id,
                today,
            )
        return float(row["starting_equity"]) if row else None

    # ------------------------------------------------------------------
    # tr_engine_heartbeat
    # ------------------------------------------------------------------

    async def upsert_heartbeat(
        self,
        engine_id: str,
        status: str,
        last_bar_at: Optional[datetime] = None,
        detail: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tr_engine_heartbeat
                    (engine_id, last_run_at, last_bar_at, status, detail, updated_at)
                VALUES ($1, $2, $3, $4, $5, $2)
                ON CONFLICT (engine_id) DO UPDATE
                SET last_run_at = EXCLUDED.last_run_at,
                    last_bar_at = COALESCE(EXCLUDED.last_bar_at, tr_engine_heartbeat.last_bar_at),
                    status      = EXCLUDED.status,
                    detail      = EXCLUDED.detail,
                    updated_at  = EXCLUDED.updated_at
                """,
                engine_id,
                now,
                last_bar_at,
                status,
                detail,
            )

    async def get_heartbeats(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM tr_engine_heartbeat")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # tr_engine_runtime_state
    # ------------------------------------------------------------------

    async def upsert_engine_runtime_state(
        self,
        engine_id: str,
        status: str,
        detail: Optional[str] = None,
        last_bar_seen: Optional[datetime] = None,
        last_processed_at: Optional[datetime] = None,
        runtime_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tr_engine_runtime_state
                    (engine_id, last_bar_seen, last_processed_at, status, detail, runtime_state, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                ON CONFLICT (engine_id) DO UPDATE
                SET last_bar_seen = COALESCE(EXCLUDED.last_bar_seen, tr_engine_runtime_state.last_bar_seen),
                    last_processed_at = COALESCE(EXCLUDED.last_processed_at, tr_engine_runtime_state.last_processed_at),
                    status = EXCLUDED.status,
                    detail = EXCLUDED.detail,
                    runtime_state = EXCLUDED.runtime_state,
                    updated_at = EXCLUDED.updated_at
                """,
                engine_id,
                last_bar_seen,
                last_processed_at,
                status,
                detail,
                json.dumps(runtime_state or {}),
                now,
            )

    async def get_engine_runtime_states(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tr_engine_runtime_state ORDER BY engine_id ASC"
            )
        result = []
        for r in rows:
            row = dict(r)
            rs = row.get("runtime_state")
            if isinstance(rs, str):
                try:
                    row["runtime_state"] = json.loads(rs)
                except (json.JSONDecodeError, TypeError):
                    row["runtime_state"] = {}
            result.append(row)
        return result

    # ------------------------------------------------------------------
    # tr_pending_setups
    # ------------------------------------------------------------------

    async def upsert_pending_setup(
        self,
        engine_id: str,
        setup_type: str,
        state_data: Dict[str, Any],
        reference_bar_ts: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
        detail: Optional[str] = None,
        status: str = "ACTIVE",
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tr_pending_setups
                    (engine_id, setup_type, status, detail, reference_bar_ts, expires_at, state_data, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                ON CONFLICT (engine_id) DO UPDATE
                SET setup_type = EXCLUDED.setup_type,
                    status = EXCLUDED.status,
                    detail = EXCLUDED.detail,
                    reference_bar_ts = EXCLUDED.reference_bar_ts,
                    expires_at = EXCLUDED.expires_at,
                    state_data = EXCLUDED.state_data,
                    updated_at = EXCLUDED.updated_at
                """,
                engine_id,
                setup_type,
                status,
                detail,
                reference_bar_ts,
                expires_at,
                json.dumps(state_data),
                now,
            )

    async def clear_pending_setup(
        self,
        engine_id: str,
        detail: Optional[str] = None,
        status: str = "CLEARED",
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tr_pending_setups
                SET status = $2,
                    detail = $3,
                    updated_at = $4
                WHERE engine_id = $1
                """,
                engine_id,
                status,
                detail,
                now,
            )

    async def get_active_pending_setups(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM tr_pending_setups
                WHERE status = 'ACTIVE'
                ORDER BY engine_id ASC
                """
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # tr_runtime_pauses
    # ------------------------------------------------------------------

    async def upsert_runtime_pause(
        self,
        pause_key: str,
        scope: str,
        pause_type: str,
        *,
        engine_id: Optional[str] = None,
        reason: Optional[str] = None,
        started_at: Optional[datetime] = None,
        observed_at: Optional[datetime] = None,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "ACTIVE",
    ) -> None:
        now = datetime.now(timezone.utc)
        active_since = started_at or now
        observed = observed_at or now
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tr_runtime_pauses
                    (pause_key, scope, engine_id, pause_type, status, reason, started_at, observed_at, payload, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
                ON CONFLICT (pause_key) DO UPDATE
                SET scope = EXCLUDED.scope,
                    engine_id = EXCLUDED.engine_id,
                    pause_type = EXCLUDED.pause_type,
                    status = EXCLUDED.status,
                    reason = EXCLUDED.reason,
                    started_at = LEAST(tr_runtime_pauses.started_at, EXCLUDED.started_at),
                    observed_at = EXCLUDED.observed_at,
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                pause_key,
                scope,
                engine_id,
                pause_type,
                status,
                reason,
                active_since,
                observed,
                json.dumps(payload or {}),
                now,
            )

    async def resume_runtime_pause(
        self,
        pause_key: str,
        *,
        reason: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tr_runtime_pauses
                SET status = 'RESUMED',
                    reason = COALESCE($2, reason),
                    resumed_at = $3,
                    observed_at = $3,
                    payload = $4::jsonb,
                    updated_at = $3
                WHERE pause_key = $1
                """,
                pause_key,
                reason,
                now,
                json.dumps(payload or {}),
            )

    async def get_active_runtime_pauses(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM tr_runtime_pauses
                WHERE status = 'ACTIVE'
                ORDER BY updated_at DESC
                """
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # tr_worker_runtime_state
    # ------------------------------------------------------------------

    async def upsert_worker_runtime_state(
        self,
        worker_id: str,
        status: str,
        detail: Optional[str] = None,
        *,
        last_loop_started_at: Optional[datetime] = None,
        last_loop_completed_at: Optional[datetime] = None,
        last_freshness_check_at: Optional[datetime] = None,
        runtime_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tr_worker_runtime_state
                    (worker_id, last_loop_started_at, last_loop_completed_at, last_freshness_check_at,
                     status, detail, runtime_state, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                ON CONFLICT (worker_id) DO UPDATE
                SET last_loop_started_at = COALESCE(EXCLUDED.last_loop_started_at, tr_worker_runtime_state.last_loop_started_at),
                    last_loop_completed_at = COALESCE(EXCLUDED.last_loop_completed_at, tr_worker_runtime_state.last_loop_completed_at),
                    last_freshness_check_at = COALESCE(EXCLUDED.last_freshness_check_at, tr_worker_runtime_state.last_freshness_check_at),
                    status = EXCLUDED.status,
                    detail = EXCLUDED.detail,
                    runtime_state = EXCLUDED.runtime_state,
                    updated_at = EXCLUDED.updated_at
                """,
                worker_id,
                last_loop_started_at,
                last_loop_completed_at,
                last_freshness_check_at,
                status,
                detail,
                json.dumps(runtime_state or {}),
                now,
            )

    async def get_worker_runtime_state(self, worker_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM tr_worker_runtime_state
                WHERE worker_id = $1
                """,
                worker_id,
            )
        if not row:
            return None
        result = dict(row)
        rs = result.get("runtime_state")
        if isinstance(rs, str):
            try:
                result["runtime_state"] = json.loads(rs)
            except (json.JSONDecodeError, TypeError):
                result["runtime_state"] = {}
        return result

    # ------------------------------------------------------------------
    # tr_risk_events
    # ------------------------------------------------------------------

    async def insert_risk_event(
        self,
        event_type: str,
        engine_id: Optional[str] = None,
        description: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_risk_events (engine_id, event_type, description, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING id
                """,
                engine_id,
                event_type,
                description,
                json.dumps(payload or {}),
            )
        return row["id"]

    async def get_risk_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tr_risk_events ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # tr_skip_events
    # ------------------------------------------------------------------

    async def insert_skip_event(
        self,
        engine_id: str,
        symbol: str,
        bar_timestamp: datetime,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_skip_events
                    (engine_id, symbol, bar_timestamp, reason, details)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING id
                """,
                engine_id,
                symbol,
                bar_timestamp,
                reason,
                json.dumps(details or {}),
            )
        return row["id"]

    async def get_skip_events(
        self,
        engine_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM tr_skip_events
                    WHERE engine_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    engine_id,
                    limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM tr_skip_events
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    async def get_skip_summary(
        self,
        engine_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT engine_id, reason, COUNT(*) AS count, MAX(created_at) AS last_seen_at
                    FROM tr_skip_events
                    WHERE engine_id = $1
                    GROUP BY engine_id, reason
                    ORDER BY count DESC, reason ASC
                    LIMIT $2
                    """,
                    engine_id,
                    limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT engine_id, reason, COUNT(*) AS count, MAX(created_at) AS last_seen_at
                    FROM tr_skip_events
                    GROUP BY engine_id, reason
                    ORDER BY count DESC, engine_id ASC, reason ASC
                    LIMIT $1
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Bankroll helpers
    # ------------------------------------------------------------------

    async def get_current_bankroll(self, engine_id: str) -> float:
        """
        Current bankroll = initial_capital + sum(pnl_usd) of all closed trades.
        """
        from ..config.settings import ENGINE_CONFIGS
        initial = ENGINE_CONFIGS[engine_id].initial_capital_usd
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(pnl_usd), 0) AS total_pnl
                FROM tr_paper_trades
                WHERE engine_id = $1 AND status != 'OPEN'
                """,
                engine_id,
            )
        total_pnl = float(row["total_pnl"]) if row else 0.0
        return round(initial + total_pnl, 2)

    # ------------------------------------------------------------------
    # Overlay / liquidity helpers
    # ------------------------------------------------------------------

    async def get_signal_by_id(self, signal_id: int) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, engine_id, symbol, bar_timestamp, direction, signal_data, created_at
                FROM tr_signals
                WHERE id = $1
                """,
                signal_id,
            )
        return dict(row) if row else None

    async def get_pending_overlay_signals(
        self,
        overlay_name: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.*
                FROM tr_signals s
                LEFT JOIN tr_overlay_decisions od
                  ON od.signal_id = s.id
                 AND od.overlay_name = $1
                WHERE od.id IS NULL
                ORDER BY s.created_at ASC
                LIMIT $2
                """,
                overlay_name,
                limit,
            )
        return [dict(r) for r in rows]

    async def get_signal_decision(self, signal_id: int) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM tr_signal_decisions
                WHERE signal_id = $1
                ORDER BY decided_at DESC, id DESC
                LIMIT 1
                """,
                signal_id,
            )
        return dict(row) if row else None

    async def get_trade_by_signal_id(self, signal_id: int) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM tr_paper_trades
                WHERE signal_id = $1
                ORDER BY opened_at DESC, id DESC
                LIMIT 1
                """,
                signal_id,
            )
        return dict(row) if row else None

    async def insert_liquidity_snapshot(
        self,
        symbol: str,
        tf: str,
        snapshot_ts: datetime,
        zones: Dict[str, Any],
        features: Optional[Dict[str, Any]] = None,
        source: str = "binance",
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_liquidity_snapshots
                    (symbol, tf, snapshot_ts, source, zones, features)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
                ON CONFLICT (symbol, tf, snapshot_ts, source) DO UPDATE
                SET zones = EXCLUDED.zones,
                    features = EXCLUDED.features
                RETURNING id
                """,
                symbol,
                tf,
                snapshot_ts,
                source,
                json.dumps(zones),
                json.dumps(features or {}),
            )
        return row["id"]

    async def get_liquidity_snapshots(
        self,
        symbol: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if symbol:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_liquidity_snapshots
                    WHERE symbol = $1
                    ORDER BY snapshot_ts DESC
                    LIMIT $2
                    """,
                    symbol,
                    limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_liquidity_snapshots
                    ORDER BY snapshot_ts DESC
                    LIMIT $1
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    async def insert_overlay_decision(
        self,
        signal_id: int,
        engine_id: str,
        symbol: str,
        overlay_name: str,
        action: str,
        comparison_label: str,
        reason: Optional[str] = None,
        entry_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        stake_usd: Optional[float] = None,
        trigger_bar_ts: Optional[datetime] = None,
        features: Optional[Dict[str, Any]] = None,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_overlay_decisions
                    (signal_id, engine_id, symbol, overlay_name, action, comparison_label,
                     reason, entry_price, stop_price, target_price, stake_usd, trigger_bar_ts, features)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
                ON CONFLICT (signal_id, overlay_name) DO UPDATE
                SET action = EXCLUDED.action,
                    comparison_label = EXCLUDED.comparison_label,
                    reason = EXCLUDED.reason,
                    entry_price = EXCLUDED.entry_price,
                    stop_price = EXCLUDED.stop_price,
                    target_price = EXCLUDED.target_price,
                    stake_usd = EXCLUDED.stake_usd,
                    trigger_bar_ts = EXCLUDED.trigger_bar_ts,
                    features = EXCLUDED.features
                RETURNING id
                """,
                signal_id,
                engine_id,
                symbol,
                overlay_name,
                action,
                comparison_label,
                reason,
                entry_price,
                stop_price,
                target_price,
                stake_usd,
                trigger_bar_ts,
                json.dumps(features or {}),
            )
        return row["id"]

    async def get_overlay_decisions(
        self,
        engine_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_overlay_decisions
                    WHERE engine_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    engine_id,
                    limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_overlay_decisions
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    async def get_overlay_decision_by_signal(
        self,
        signal_id: int,
        overlay_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if overlay_name:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM tr_overlay_decisions
                    WHERE signal_id = $1 AND overlay_name = $2
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    signal_id,
                    overlay_name,
                )
        else:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM tr_overlay_decisions
                    WHERE signal_id = $1
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    signal_id,
                )
        return dict(row) if row else None

    async def open_ghost_trade(
        self,
        overlay_decision_id: int,
        signal_id: int,
        base_trade_id: Optional[int],
        engine_id: str,
        symbol: str,
        direction: Direction,
        entry_price: float,
        stop_price: float,
        target_price: float,
        stake_usd: float,
        opened_at: datetime,
        variant_label: str = "legacy",
        zone_sources_used: Optional[Dict[str, Any]] = None,
        stop_zone_info: Optional[Dict[str, Any]] = None,
        target_zone_info: Optional[Dict[str, Any]] = None,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_ghost_trades
                    (overlay_decision_id, signal_id, base_trade_id, engine_id, symbol,
                     direction, entry_price, stop_price, target_price, stake_usd, status, opened_at,
                     variant_label, zone_sources_used, stop_zone_info, target_zone_info)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'OPEN', $11,
                        $12, $13::jsonb, $14::jsonb, $15::jsonb)
                RETURNING id
                """,
                overlay_decision_id,
                signal_id,
                base_trade_id,
                engine_id,
                symbol,
                direction.value if hasattr(direction, "value") else direction,
                entry_price,
                stop_price,
                target_price,
                stake_usd,
                opened_at,
                variant_label,
                json.dumps(zone_sources_used) if zone_sources_used else None,
                json.dumps(stop_zone_info) if stop_zone_info else None,
                json.dumps(target_zone_info) if target_zone_info else None,
            )
        return row["id"]

    async def get_ghost_trade_by_signal_variant(
        self,
        signal_id: int,
        variant_label: str,
    ) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM tr_ghost_trades
                WHERE signal_id = $1 AND variant_label = $2
                ORDER BY opened_at DESC, id DESC
                LIMIT 1
                """,
                signal_id,
                variant_label,
            )
        return dict(row) if row else None

    async def get_zone_comparison_stats(
        self,
        since_days: int = 30,
        engine_id: Optional[str] = None,
        min_trades_per_variant: int = 30,
    ) -> Dict[str, Any]:
        """
        Computes per-variant performance metrics from closed ghost trades.
        Returns bootstrap p-value (1000 resamples) for Sharpe comparison vs legacy.
        """
        import random
        import math

        where = "WHERE status != 'OPEN' AND closed_at >= NOW() - ($1 || ' days')::interval"
        params: list = [since_days]
        if engine_id:
            where += " AND engine_id = $2"
            params.append(engine_id)

        query = f"""
            SELECT variant_label, engine_id, pnl_usd, pnl_pct,
                   entry_price, stop_price, stake_usd
            FROM tr_ghost_trades
            {where}
            ORDER BY variant_label, opened_at DESC
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        # Group returns by variant
        by_variant: Dict[str, list] = {}
        by_engine: Dict[str, Dict[str, list]] = {}
        for row in rows:
            vlabel = str(row["variant_label"] or "legacy")
            pnl_pct = float(row["pnl_pct"]) if row["pnl_pct"] is not None else None
            if pnl_pct is None:
                continue
            by_variant.setdefault(vlabel, []).append(pnl_pct)
            eng = str(row["engine_id"])
            by_engine.setdefault(eng, {}).setdefault(vlabel, []).append(pnl_pct)

        def _metrics(returns: list) -> Dict[str, Any]:
            if not returns:
                return {"n_trades_closed": 0, "win_rate": None, "avg_pnl_pct": None,
                        "sharpe_ratio": None, "avg_r_multiple": None, "expectancy_r": None}
            n = len(returns)
            wins = sum(1 for r in returns if r > 0)
            avg = sum(returns) / n
            std = math.sqrt(sum((r - avg) ** 2 for r in returns) / n) if n > 1 else 0.0
            sharpe = avg / std if std > 0 else 0.0
            return {
                "n_trades_closed": n,
                "win_rate": round(wins / n, 4),
                "avg_pnl_pct": round(avg, 6),
                "sharpe_ratio": round(sharpe, 4),
                "avg_r_multiple": round(avg, 6),
                "expectancy_r": round(avg, 6),
            }

        def _bootstrap_sharpe_pvalue(base_returns: list, variant_returns: list, n_resamples: int = 1000) -> float:
            if len(base_returns) < 5 or len(variant_returns) < 5:
                return 1.0
            rng = random.Random(42)

            def sharpe(rs: list) -> float:
                if len(rs) < 2:
                    return 0.0
                m = sum(rs) / len(rs)
                s = math.sqrt(sum((r - m) ** 2 for r in rs) / len(rs))
                return m / s if s > 0 else 0.0

            observed_diff = sharpe(variant_returns) - sharpe(base_returns)
            combined = base_returns + variant_returns
            count_extreme = 0
            for _ in range(n_resamples):
                rng.shuffle(combined)
                half = len(base_returns)
                diff = sharpe(combined[:half]) - sharpe(combined[half:])
                if abs(diff) >= abs(observed_diff):
                    count_extreme += 1
            return round(count_extreme / n_resamples, 4)

        variants_out: Dict[str, Any] = {}
        for vlabel, returns in by_variant.items():
            variants_out[vlabel] = _metrics(returns)

        # Ensure all known variants appear
        for vlabel in ("legacy", "zones_nearest", "zones_weighted"):
            if vlabel not in variants_out:
                variants_out[vlabel] = _metrics([])

        legacy_returns = by_variant.get("legacy", [])
        statistical: Dict[str, Any] = {"min_required": min_trades_per_variant}
        can_decide = all(
            m["n_trades_closed"] >= min_trades_per_variant
            for m in variants_out.values()
        )
        statistical["can_decide"] = can_decide

        for vlabel in ("zones_nearest", "zones_weighted"):
            v_returns = by_variant.get(vlabel, [])
            if can_decide:
                p = _bootstrap_sharpe_pvalue(legacy_returns, v_returns)
                statistical[f"sharpe_delta_{vlabel}_vs_legacy"] = round(
                    (variants_out[vlabel].get("sharpe_ratio") or 0.0)
                    - (variants_out["legacy"].get("sharpe_ratio") or 0.0), 4
                )
                statistical[f"sharpe_bootstrap_p_value_{vlabel}"] = p
            else:
                statistical[f"sharpe_delta_{vlabel}_vs_legacy"] = None
                statistical[f"sharpe_bootstrap_p_value_{vlabel}"] = None
        statistical["sample_size_adequate"] = can_decide

        # Per-engine breakdown
        per_engine: Dict[str, Any] = {}
        for eng, vmap in by_engine.items():
            per_engine[eng] = {vlabel: _metrics(rets) for vlabel, rets in vmap.items()}

        return {
            "window_days": since_days,
            "variants": variants_out,
            "statistical_comparison": statistical,
            "per_engine_breakdown": per_engine,
        }

    async def close_ghost_trade(
        self,
        ghost_trade_id: int,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        close_reason: str,
        closed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tr_ghost_trades
                SET exit_price = $2,
                    pnl_usd = $3,
                    pnl_pct = $4,
                    status = 'CLOSED',
                    close_reason = $5,
                    closed_at = $6
                WHERE id = $1
                """,
                ghost_trade_id,
                exit_price,
                pnl_usd,
                pnl_pct,
                close_reason,
                closed_at,
            )

    async def get_open_ghost_trades(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM tr_ghost_trades
                WHERE status = 'OPEN'
                ORDER BY opened_at ASC
                """
            )
        return [dict(r) for r in rows]

    async def get_ghost_trades(
        self,
        engine_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_ghost_trades
                    WHERE engine_id = $1
                    ORDER BY opened_at DESC, id DESC
                    LIMIT $2
                    """,
                    engine_id,
                    limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_ghost_trades
                    ORDER BY opened_at DESC, id DESC
                    LIMIT $1
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    async def insert_trade_comparison(
        self,
        signal_id: int,
        overlay_decision_id: int,
        engine_id: str,
        symbol: str,
        comparison_label: str,
        base_trade_id: Optional[int] = None,
        ghost_trade_id: Optional[int] = None,
        entry_delta_bps: Optional[float] = None,
        stop_delta_bps: Optional[float] = None,
        target_delta_bps: Optional[float] = None,
        pnl_delta_usd: Optional[float] = None,
        pnl_delta_pct: Optional[float] = None,
        base_close_reason: Optional[str] = None,
        ghost_close_reason: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_trade_comparisons
                    (signal_id, base_trade_id, ghost_trade_id, overlay_decision_id, engine_id,
                     symbol, comparison_label, entry_delta_bps, stop_delta_bps, target_delta_bps,
                     pnl_delta_usd, pnl_delta_pct, base_close_reason, ghost_close_reason, summary)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb)
                ON CONFLICT (signal_id, overlay_decision_id) DO UPDATE
                SET base_trade_id = EXCLUDED.base_trade_id,
                    ghost_trade_id = EXCLUDED.ghost_trade_id,
                    comparison_label = EXCLUDED.comparison_label,
                    entry_delta_bps = EXCLUDED.entry_delta_bps,
                    stop_delta_bps = EXCLUDED.stop_delta_bps,
                    target_delta_bps = EXCLUDED.target_delta_bps,
                    pnl_delta_usd = EXCLUDED.pnl_delta_usd,
                    pnl_delta_pct = EXCLUDED.pnl_delta_pct,
                    base_close_reason = EXCLUDED.base_close_reason,
                    ghost_close_reason = EXCLUDED.ghost_close_reason,
                    summary = EXCLUDED.summary
                RETURNING id
                """,
                signal_id,
                base_trade_id,
                ghost_trade_id,
                overlay_decision_id,
                engine_id,
                symbol,
                comparison_label,
                entry_delta_bps,
                stop_delta_bps,
                target_delta_bps,
                pnl_delta_usd,
                pnl_delta_pct,
                base_close_reason,
                ghost_close_reason,
                json.dumps(summary or {}),
            )
        return row["id"]

    async def get_trade_comparisons(
        self,
        engine_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_trade_comparisons
                    WHERE engine_id = $1
                    ORDER BY created_at DESC, id DESC
                    LIMIT $2
                    """,
                    engine_id,
                    limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM tr_trade_comparisons
                    ORDER BY created_at DESC, id DESC
                    LIMIT $1
                    """,
                    limit,
                )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # tr_state_transitions — append-only audit log
    # ------------------------------------------------------------------

    async def insert_state_transition(
        self,
        entity_type: str,
        entity_id: str,
        from_status: Optional[str],
        to_status: str,
        detail: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Append a status transition record.  Called whenever an engine or worker
        transitions from one status to another.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tr_state_transitions
                    (entity_type, entity_id, from_status, to_status, detail, payload)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id
                """,
                entity_type,
                entity_id,
                from_status,
                to_status,
                detail,
                json.dumps(payload or {}),
            )
        return row["id"]

    async def get_state_transitions(
        self,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conditions = []
        params: List[Any] = [limit]
        param_idx = 2

        if entity_id is not None:
            conditions.append(f"entity_id = ${param_idx}")
            params.append(entity_id)
            param_idx += 1
        if entity_type is not None:
            conditions.append(f"entity_type = ${param_idx}")
            params.append(entity_type)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"""
            SELECT id, entity_type, entity_id, from_status, to_status, detail, payload, recorded_at
            FROM tr_state_transitions
            {where_clause}
            ORDER BY recorded_at DESC
            LIMIT $1
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]

    async def get_overlay_summary(
        self,
        engine_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if engine_id:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT engine_id,
                           comparison_label,
                           COUNT(*) AS count,
                           AVG(pnl_delta_usd) AS avg_pnl_delta_usd,
                           SUM(pnl_delta_usd) AS sum_pnl_delta_usd
                    FROM tr_trade_comparisons
                    WHERE engine_id = $1
                    GROUP BY engine_id, comparison_label
                    ORDER BY count DESC, comparison_label ASC
                    """,
                    engine_id,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT engine_id,
                           comparison_label,
                           COUNT(*) AS count,
                           AVG(pnl_delta_usd) AS avg_pnl_delta_usd,
                           SUM(pnl_delta_usd) AS sum_pnl_delta_usd
                    FROM tr_trade_comparisons
                    GROUP BY engine_id, comparison_label
                    ORDER BY engine_id ASC, count DESC, comparison_label ASC
                    """
                )
        return [dict(r) for r in rows]
