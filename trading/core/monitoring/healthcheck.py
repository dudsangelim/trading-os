"""
In-process health and status helpers used by the worker and the API.

The output of build_system_status() conforms to the SystemStatusSnapshot schema
defined in core/monitoring/status_model.py.  That module is the authoritative
contract reference for downstream consumers (e.g. OpenClaw mj-trading skill).

Candle input freshness and coverage are assessed via validate_candle_inputs()
from core/data/candle_contract.py, which is the formal contract for the
candle-collector → Trading Core interface.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from ..config.settings import (
    ENGINE_CONFIGS,
    HEARTBEAT_STALE_MINUTES,
    WORKER_LOOP_INTERVAL_SECONDS,
)
from ..data.candle_contract import InputQuality, validate_candle_inputs
from ..data.candle_reader import CandleReader
from ..storage.repository import TradingRepository
from .status_model import CONTRACT_VERSION
from ..risk.circuit_breakers import check_heartbeat_stale, check_stale_data

_WORKER_EVENT_TYPES = (
    "WORKER_LOOP_STARTED",
    "WORKER_LOOP_COMPLETED",
    "WORKER_LOOP_DEGRADED",
)


def _to_iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.isoformat() if ts else None


def _seconds_since(now: datetime, ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    return round(max(0.0, (now - ts).total_seconds()), 3)


def _json_payload(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def _parse_row(row: asyncpg.Record) -> Dict[str, Any]:
    parsed = dict(row)
    for key in ("payload", "runtime_state", "state_data"):
        if key in parsed:
            parsed[key] = _json_payload(parsed[key])
    return parsed


def _derive_worker_status(
    now: datetime,
    last_loop_started_at: Optional[datetime],
    last_loop_completed_at: Optional[datetime],
    stale_data_triggered: bool,
    engine_states: List[str],
) -> str:
    if last_loop_completed_at is None:
        return "stopped"
    completed_age = _seconds_since(now, last_loop_completed_at) or 0.0
    slow_threshold = max(WORKER_LOOP_INTERVAL_SECONDS * 1.5, HEARTBEAT_STALE_MINUTES * 60 / 2)

    if completed_age > HEARTBEAT_STALE_MINUTES * 60:
        return "stopped"
    if last_loop_started_at and last_loop_started_at > last_loop_completed_at:
        return "processing"
    if stale_data_triggered or any(state == "BLOCKED_RISK" for state in engine_states):
        return "blocked_risk"
    if any(state in {"DEGRADED", "ERROR"} for state in engine_states):
        return "degraded"
    if completed_age > slow_threshold:
        return "slow"
    return "alive"


async def build_system_status(
    pool: asyncpg.Pool,
    derivatives_pool: Optional[asyncpg.Pool] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    result: Dict[str, Any] = {
        "status": "ok",
        "ts": now.isoformat(),
        "contract_version": CONTRACT_VERSION,
        "postgres": False,
        "worker": {},
        "inputs": {"status": "unknown", "symbols": []},
        "risk": {"status": "unknown", "global_pauses": [], "recent_events": []},
        "derivatives": {},
        "engines": [],
    }

    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        result["postgres"] = True

        async with pool.acquire() as conn:
            heartbeat_rows = await conn.fetch("SELECT * FROM tr_engine_heartbeat ORDER BY engine_id")
            engine_runtime_rows = await conn.fetch("SELECT * FROM tr_engine_runtime_state ORDER BY engine_id")
            pending_rows = await conn.fetch("SELECT * FROM tr_pending_setups WHERE status = 'ACTIVE' ORDER BY engine_id")
            pause_rows = await conn.fetch("SELECT * FROM tr_runtime_pauses WHERE status = 'ACTIVE' ORDER BY updated_at DESC")
            worker_runtime_row = await conn.fetchrow(
                "SELECT * FROM tr_worker_runtime_state WHERE worker_id = $1",
                "trading_worker",
            )
            position_rows = await conn.fetch(
                """
                SELECT id, engine_id, symbol, direction, entry_price, stop_price, target_price,
                       stake_usd, opened_at, status
                FROM tr_paper_trades
                WHERE status = 'OPEN'
                ORDER BY opened_at ASC
                """
            )
            risk_rows = await conn.fetch(
                """
                SELECT id, engine_id, event_type, description, payload, created_at
                FROM tr_risk_events
                ORDER BY created_at DESC
                LIMIT 50
                """
            )
            worker_rows = await conn.fetch(
                """
                SELECT id, engine_id, event_type, description, payload, created_at
                FROM tr_risk_events
                WHERE event_type = ANY($1::text[])
                ORDER BY created_at DESC
                LIMIT 20
                """,
                list(_WORKER_EVENT_TYPES),
            )

        reader = CandleReader(pool)
        # Formal candle input validation via the candle-collector contract.
        # This replaces the previous inline freshness-only loop and adds coverage data.
        candle_validation = await validate_candle_inputs(reader, now=now)
        last_candle_times: Dict[str, Optional[datetime]] = {
            s.symbol: s.last_candle_at for s in candle_validation.symbols
        }

        stale_result = check_stale_data(last_candle_times)
        heartbeat_rows_parsed = [_parse_row(row) for row in heartbeat_rows]
        engine_runtime_rows_parsed = [_parse_row(row) for row in engine_runtime_rows]
        pending_rows_parsed = [_parse_row(row) for row in pending_rows]
        pause_rows_parsed = [_parse_row(row) for row in pause_rows]
        risk_rows_parsed = [_parse_row(row) for row in risk_rows]
        worker_rows_parsed = [_parse_row(row) for row in worker_rows]
        positions = [_parse_row(row) for row in position_rows]
        worker_runtime = _parse_row(worker_runtime_row) if worker_runtime_row else {}

        latest_worker_events: Dict[str, Dict[str, Any]] = {}
        for row in worker_rows_parsed:
            latest_worker_events.setdefault(row["event_type"], row)

        def _event_timestamp(event_type: str, payload_key: str) -> Optional[datetime]:
            row = latest_worker_events.get(event_type)
            if not row:
                return None
            _raw = row.get("payload") or {}
            if isinstance(_raw, str):
                try:
                    _raw = json.loads(_raw)
                except Exception:
                    _raw = {}
            payload = _raw if isinstance(_raw, dict) else {}
            ts = payload.get(payload_key)
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts)
                except ValueError:
                    return row.get("created_at")
            return row.get("created_at")

        last_loop_started_at = worker_runtime.get("last_loop_started_at") or _event_timestamp("WORKER_LOOP_STARTED", "loop_started_at")
        last_loop_completed_at = worker_runtime.get("last_loop_completed_at") or _event_timestamp("WORKER_LOOP_COMPLETED", "loop_completed_at")
        last_loop_degraded_at = _event_timestamp("WORKER_LOOP_DEGRADED", "loop_completed_at")
        last_loop_observed_at = max(
            [ts for ts in (last_loop_completed_at, last_loop_degraded_at) if ts is not None],
            default=None,
        )
        heartbeat_result = check_heartbeat_stale(last_loop_observed_at)

        positions_by_engine = {row["engine_id"]: row for row in positions}
        latest_risk_event_by_engine: Dict[str, Dict[str, Any]] = {}
        for row in risk_rows_parsed:
            engine_id = row.get("engine_id")
            if engine_id and engine_id not in latest_risk_event_by_engine:
                latest_risk_event_by_engine[engine_id] = row

        # Build input_symbols from candle_validation (richer than the previous inline build).
        # "status" field remains "ok" | "stale" | "absent" for backward compatibility.
        # New "quality" and "coverage_in_window" fields provide additional signal.
        input_symbols: List[Dict[str, Any]] = [
            {
                "symbol": s.symbol,
                "last_candle_at": _to_iso(s.last_candle_at),
                "age_seconds": s.age_seconds,
                "threshold_minutes": s.freshness_threshold_minutes,
                "coverage_in_window": s.candles_in_window,
                "coverage_window_minutes": s.coverage_window_minutes,
                "quality": s.quality.value,
                # backward-compat binary: ok for fresh/degraded; stale/absent otherwise
                "status": "ok" if s.quality in (InputQuality.FRESH, InputQuality.DEGRADED) else s.quality.value,
            }
            for s in candle_validation.symbols
        ]

        heartbeats_by_engine = {row["engine_id"]: row for row in heartbeat_rows_parsed}
        engine_runtime_by_engine = {row["engine_id"]: row for row in engine_runtime_rows_parsed}
        pending_by_engine = {row["engine_id"]: row for row in pending_rows_parsed}
        engine_states: List[str] = []
        engines: List[Dict[str, Any]] = []
        for engine_id, cfg in ENGINE_CONFIGS.items():
            hb = heartbeats_by_engine.get(engine_id, {})
            runtime_row = engine_runtime_by_engine.get(engine_id, {})
            heartbeat_status = runtime_row.get("status") or hb.get("status", "UNKNOWN")
            engine_states.append(heartbeat_status)
            engines.append(
                {
                    "engine_id": engine_id,
                    "symbol": cfg.symbol,
                    "mode": "SIGNAL_ONLY" if cfg.signal_only else "ACTIVE",
                    "timeframe_minutes": cfg.timeframe_minutes,
                    "heartbeat_status": heartbeat_status,
                    "heartbeat_detail": runtime_row.get("detail") or hb.get("detail"),
                    "last_run_at": _to_iso(hb.get("last_run_at")),
                    "last_bar_at": _to_iso(runtime_row.get("last_bar_seen") or hb.get("last_bar_at")),
                    "last_processed_at": _to_iso(runtime_row.get("last_processed_at")),
                    "updated_at": _to_iso(hb.get("updated_at")),
                    "runtime_state": runtime_row.get("runtime_state") or {},
                    "input": next((item for item in input_symbols if item["symbol"] == cfg.symbol), None),
                    "open_position": positions_by_engine.get(engine_id),
                    "pending_setup": pending_by_engine.get(engine_id),
                    "latest_risk_event": latest_risk_event_by_engine.get(engine_id),
                }
            )

        worker_status = _derive_worker_status(
            now=now,
            last_loop_started_at=last_loop_started_at,
            last_loop_completed_at=last_loop_observed_at,
            stale_data_triggered=stale_result.triggered,
            engine_states=engine_states,
        )

        global_pauses = [
            {
                "reason": stale_result.breaker_name,
                "description": stale_result.reason,
                "affected_symbol": stale_result.affected_symbol,
                "observed_duration_seconds": stale_result.observed_duration_seconds,
                "details": stale_result.details,
            }
        ] if stale_result.triggered else []
        global_pauses.extend(
            {
                "pause_key": row.get("pause_key"),
                "reason": row["event_type"],
                "description": row.get("description"),
                "engine_id": row.get("engine_id"),
                "created_at": _to_iso(row.get("created_at")),
                "payload": row.get("payload"),
            }
            for row in risk_rows_parsed
            if row["event_type"] in {"ENGINE_BLOCKED", "KILL_STALE_DATA", "CIRCUIT_DD_DIARIO", "CIRCUIT_BANKROLL_MIN", "NO_VALID_INPUT"}
        )
        global_pauses.extend(
            {
                "pause_key": row["pause_key"],
                "scope": row["scope"],
                "reason": row["pause_type"],
                "description": row.get("reason"),
                "engine_id": row.get("engine_id"),
                "started_at": _to_iso(row.get("started_at")),
                "observed_at": _to_iso(row.get("observed_at")),
                "payload": row.get("payload"),
            }
            for row in pause_rows_parsed
        )

        result["worker"] = {
            "status": worker_runtime.get("status") or worker_status,
            "detail": worker_runtime.get("detail"),
            "last_loop_started_at": _to_iso(last_loop_started_at),
            "last_loop_completed_at": _to_iso(last_loop_completed_at),
            "last_loop_degraded_at": _to_iso(last_loop_degraded_at),
            "last_loop_observed_at": _to_iso(last_loop_observed_at),
            "last_loop_completed_age_seconds": _seconds_since(now, last_loop_observed_at),
            "last_freshness_check_at": _to_iso(worker_runtime.get("last_freshness_check_at")),
            "runtime_state": worker_runtime.get("runtime_state") or {},
            "heartbeat_check": {
                "status": "blocked" if heartbeat_result.triggered else "ok",
                "reason": heartbeat_result.reason,
                "observed_duration_seconds": heartbeat_result.observed_duration_seconds,
                "threshold_minutes": HEARTBEAT_STALE_MINUTES,
            },
        }
        result["inputs"] = {
            "status": "stale" if stale_result.triggered else "ok",
            "breaker": {
                "name": stale_result.breaker_name,
                "triggered": stale_result.triggered,
                "reason": stale_result.reason,
                "affected_symbol": stale_result.affected_symbol,
                "observed_duration_seconds": stale_result.observed_duration_seconds,
            },
            "symbols": input_symbols,
        }
        result["risk"] = {
            "status": "blocked" if stale_result.triggered or any(state == "BLOCKED_RISK" for state in engine_states) else (
                "degraded" if any(state in {"DEGRADED", "ERROR"} for state in engine_states) else "ok"
            ),
            "global_pauses": global_pauses[:20],
            "recent_events": [
                {
                    "id": row["id"],
                    "engine_id": row.get("engine_id"),
                    "event_type": row["event_type"],
                    "description": row.get("description"),
                    "payload": row.get("payload"),
                    "created_at": _to_iso(row.get("created_at")),
                }
                for row in risk_rows_parsed[:20]
            ],
        }
        result["engines"] = engines
        repo = TradingRepository(pool, derivatives_pool=derivatives_pool)
        derivatives_snapshot = await repo.get_derivatives_feature_snapshot(
            symbols=sorted({cfg.symbol for cfg in ENGINE_CONFIGS.values()}),
            now=now,
        )
        result["derivatives"] = derivatives_snapshot

        symbol_derivatives = {
            item.get("symbol"): item for item in derivatives_snapshot.get("symbols", [])
            if isinstance(item, dict)
        }
        derivatives_mode = derivatives_snapshot.get("mode")
        for engine in result["engines"]:
            symbol = engine.get("symbol")
            symbol_snapshot = symbol_derivatives.get(symbol, {})
            engine["derivatives"] = {
                "mode": derivatives_mode,
                "available": bool(symbol_snapshot.get("available")),
                "quality": symbol_snapshot.get("quality"),
                "freshness": symbol_snapshot.get("freshness"),
                "available_features": symbol_snapshot.get("available_features", []),
                "advisory_enabled": derivatives_snapshot.get("flags", {}).get(
                    "engine_derivatives_advisory_enabled",
                    False,
                ),
                "hard_filter_enabled": derivatives_snapshot.get("flags", {}).get(
                    "engine_derivatives_hard_filter_enabled",
                    False,
                ),
            }

        if result["worker"]["status"] in {"degraded", "slow", "DEGRADED"} or result["risk"]["status"] == "degraded":
            result["status"] = "degraded"
        if result["worker"]["status"] in {"stopped", "STOPPED"} or not result["postgres"]:
            result["status"] = "error"
        if result["worker"]["status"] in {"blocked_risk", "BLOCKED_RISK"} and result["status"] == "ok":
            result["status"] = "blocked"

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


async def healthcheck(
    pool: asyncpg.Pool,
    derivatives_pool: Optional[asyncpg.Pool] = None,
) -> Dict[str, Any]:
    snapshot = await build_system_status(pool, derivatives_pool=derivatives_pool)
    return {
        "status": snapshot["status"],
        "ts": snapshot["ts"],
        "postgres": snapshot["postgres"],
        "worker": snapshot.get("worker", {}),
        "inputs": snapshot.get("inputs", {}),
        "risk": {
            "status": snapshot.get("risk", {}).get("status"),
            "global_pauses": snapshot.get("risk", {}).get("global_pauses", [])[:10],
        },
        "engines": [
            {
                "engine_id": engine["engine_id"],
                "symbol": engine["symbol"],
                "heartbeat_status": engine["heartbeat_status"],
                "heartbeat_detail": engine["heartbeat_detail"],
                "last_run_at": engine["last_run_at"],
                "last_bar_at": engine["last_bar_at"],
                "has_open_position": engine["open_position"] is not None,
                "latest_risk_event": engine["latest_risk_event"],
            }
            for engine in snapshot.get("engines", [])
        ],
    }
