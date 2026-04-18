"""
trading_worker — main entry point.

Startup sequence:
  1. Run migrations (idempotent)
  2. Load config
  3. Rehydrate open positions from tr_paper_trades
  4. Rebuild heartbeat from tr_engine_heartbeat
  5. Enter main loop

Loop (every minute):
  1. Check stale data
  2. For each active engine:
     a. Check circuit breakers (DD, bankroll min)
     b. Check if a new bar of the engine's TF has closed
     c. If yes: prepare context → generate signal
     d. If signal: validate → execute (or signal-only)
     e. Check open positions (stop/target/timeout)
  3. Write heartbeat (tr_engine_heartbeat)
  4. Sleep to next minute

Graceful shutdown on SIGTERM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set

import asyncpg

# Make sure the trading package is importable when running as __main__
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from trading.core.config.settings import (
    DATABASE_URL,
    DERIVATIVES_DATABASE_URL,
    DERIVATIVES_ENABLED,
    DERIVATIVES_FEATURE_ADAPTER_ENABLED,
    ENGINE_CONFIGS,
    ENGINE_DERIVATIVES_ADVISORY_ENABLED,
    ENGINE_DERIVATIVES_HARD_FILTER_ENABLED,
    ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED,
    ENGINE_ROLES,
    WORKER_LOOP_INTERVAL_SECONDS,
)
from trading.core.data.candle_reader import CandleReader
from trading.core.engines.base import Direction, Position
from trading.core.engines.derivatives_filter import evaluate_shadow_filter
from trading.core.engines.registry import build_registry
from trading.core.execution.fill_model import simulated_fill_price
from trading.core.execution.position_manager import check_position
from trading.core.monitoring.healthcheck import build_system_status
from trading.core.monitoring.structured_log import log, log_health_snapshot
from trading.core.monitoring.telegram_alerts import (
    fmt_critical_error,
    fmt_daily_dd_limit,
    fmt_signal_accepted,
    fmt_signal_decision,
    fmt_trade_closed,
    fmt_trade_opened,
    send_alert,
)
from trading.core.risk.circuit_breakers import (
    BreakerResult,
    check_bankroll_minimum,
    check_daily_drawdown,
    check_one_position,
    check_stale_data,
)
from trading.core.storage.migrations import run_migrations
from trading.core.storage.repository import TradingRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")

WORKER_ID = "trading_worker"

# ---------------------------------------------------------------------------
# Global shutdown flag
# ---------------------------------------------------------------------------
_shutdown = False


def _on_sigterm(*_: Any) -> None:
    global _shutdown
    _shutdown = True
    log("worker", "shutdown_requested", reason="SIGTERM")


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigterm)

# ---------------------------------------------------------------------------
# State tracked across loop iterations
# ---------------------------------------------------------------------------

_last_bar_seen: Dict[str, Optional[datetime]] = {}
_dd_paused: Set[str] = set()
_dd_pause_date: Dict[str, date] = {}
_bankroll_paused: Set[str] = set()
_engine_runtime_status: Dict[str, str] = {}
_engine_runtime_detail: Dict[str, Optional[str]] = {}
_engine_blocked_since: Dict[str, datetime] = {}
_global_blocked_since: Optional[datetime] = None
# Phase D: track worker-level status for transition recording
_worker_runtime_status: Optional[str] = None
_derivatives_pool: Optional[asyncpg.Pool] = None


def _tf_bar_open(now: datetime, tf_minutes: int) -> datetime:
    """Return the open_time of the most recently completed TF bar."""
    minutes_since_epoch = int(now.timestamp() // 60)
    completed_bar_minute = (minutes_since_epoch // tf_minutes) * tf_minutes - tf_minutes
    return datetime.fromtimestamp(completed_bar_minute * 60, tz=timezone.utc)


def _parse_optional_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _pause_key(scope: str, pause_type: str, engine_id: Optional[str] = None) -> str:
    if scope == "engine" and engine_id:
        return f"engine:{engine_id}:{pause_type}"
    return f"{scope}:{pause_type}"


def _breaker_payload(result: BreakerResult, *, now: datetime, engine_id: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "breaker_name": result.breaker_name,
        "reason": result.reason,
        "timestamp": now.isoformat(),
        **result.details,
    }
    if engine_id:
        payload["engine_id"] = engine_id
    if result.affected_engine:
        payload["affected_engine"] = result.affected_engine
    if result.affected_symbol:
        payload["affected_symbol"] = result.affected_symbol
    if result.observed_duration_seconds is not None:
        payload["observed_duration_seconds"] = round(result.observed_duration_seconds, 3)
    return payload


async def _record_worker_event(
    repo: TradingRepository,
    event_type: str,
    description: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    await repo.insert_risk_event(event_type, None, description, payload or {})


async def _sync_engine_runtime_state(
    repo: TradingRepository,
    engine_id: str,
    status: str,
    detail: Optional[str],
    timestamp: datetime,
    *,
    last_bar_at: Optional[datetime] = None,
) -> None:
    engine = _registry[engine_id]
    await repo.upsert_engine_runtime_state(
        engine_id=engine_id,
        status=status,
        detail=detail,
        last_bar_seen=last_bar_at,
        last_processed_at=timestamp,
        runtime_state=engine.export_runtime_state(),
    )

    pending = engine.export_pending_state()
    if pending is not None:
        await repo.upsert_pending_setup(
            engine_id=engine_id,
            setup_type=pending["setup_type"],
            state_data=pending["state_data"],
            reference_bar_ts=_parse_optional_datetime(pending.get("reference_bar_ts")),
            expires_at=_parse_optional_datetime(pending.get("expires_at")),
            detail=detail,
            status="ACTIVE",
        )
    else:
        await repo.clear_pending_setup(engine_id, detail="NO_ACTIVE_PENDING", status="CLEARED")


async def _persist_worker_runtime_state(
    repo: TradingRepository,
    status: str,
    detail: Optional[str],
    *,
    last_loop_started_at: Optional[datetime] = None,
    last_loop_completed_at: Optional[datetime] = None,
    last_freshness_check_at: Optional[datetime] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
) -> None:
    global _worker_runtime_status
    # Record state transition when status actually changes
    if _worker_runtime_status != status:
        try:
            await repo.insert_state_transition(
                entity_type="worker",
                entity_id=WORKER_ID,
                from_status=_worker_runtime_status,
                to_status=status,
                detail=detail,
                payload={"timestamp": datetime.now(timezone.utc).isoformat()},
            )
        except Exception:
            pass  # transition log is best-effort; never block worker
        _worker_runtime_status = status
    await repo.upsert_worker_runtime_state(
        WORKER_ID,
        status=status,
        detail=detail,
        last_loop_started_at=last_loop_started_at,
        last_loop_completed_at=last_loop_completed_at,
        last_freshness_check_at=last_freshness_check_at,
        runtime_state=runtime_state,
    )


async def _set_engine_heartbeat(
    repo: TradingRepository,
    engine_id: str,
    status: str,
    detail: Optional[str] = None,
    *,
    last_bar_at: Optional[datetime] = None,
    breaker: Optional[BreakerResult] = None,
    now: Optional[datetime] = None,
) -> None:
    timestamp = now or datetime.now(timezone.utc)
    prev_status = _engine_runtime_status.get(engine_id)
    prev_detail = _engine_runtime_detail.get(engine_id)
    pause_type = detail or (breaker.breaker_name if breaker else status)

    if prev_status == "BLOCKED_RISK" and status == "BLOCKED_RISK" and prev_detail and prev_detail != pause_type:
        await repo.resume_runtime_pause(
            _pause_key("engine", prev_detail, engine_id),
            reason=f"superseded_by:{pause_type}",
            payload={"engine_id": engine_id, "resumed_at": timestamp.isoformat()},
        )
        _engine_blocked_since.pop(engine_id, None)

    if status == "BLOCKED_RISK" and prev_status != "BLOCKED_RISK":
        blocked_since = timestamp
        _engine_blocked_since[engine_id] = blocked_since
        payload = {
            "engine_id": engine_id,
            "status": status,
            "detail": detail,
            "blocked_at": blocked_since.isoformat(),
        }
        if breaker is not None:
            payload.update(_breaker_payload(breaker, now=timestamp, engine_id=engine_id))
        log(engine_id, "engine_blocked", **payload)
        await repo.insert_risk_event("ENGINE_BLOCKED", engine_id, detail or status, payload)
    if status == "BLOCKED_RISK":
        await repo.upsert_runtime_pause(
            _pause_key("engine", pause_type, engine_id),
            scope="engine",
            engine_id=engine_id,
            pause_type=pause_type,
            reason=detail or status,
            started_at=_engine_blocked_since.get(engine_id, timestamp),
            observed_at=timestamp,
            payload={
                "engine_id": engine_id,
                "status": status,
                "detail": detail,
                **(_breaker_payload(breaker, now=timestamp, engine_id=engine_id) if breaker is not None else {}),
            },
        )

    if prev_status == "BLOCKED_RISK" and status != "BLOCKED_RISK":
        blocked_since = _engine_blocked_since.pop(engine_id, None)
        duration_seconds = None
        if blocked_since is not None:
            duration_seconds = round((timestamp - blocked_since).total_seconds(), 3)
        payload = {
            "engine_id": engine_id,
            "status": status,
            "detail": detail,
            "previous_detail": prev_detail,
            "resumed_at": timestamp.isoformat(),
            "blocked_duration_seconds": duration_seconds,
        }
        log(engine_id, "engine_resumed", **payload)
        await repo.insert_risk_event("ENGINE_RESUMED", engine_id, detail or status, payload)
        if prev_detail:
            await repo.resume_runtime_pause(
                _pause_key("engine", prev_detail, engine_id),
                reason=detail or status,
                payload=payload,
            )

    # Record state transition when status actually changes (best-effort; never blocks)
    if _engine_runtime_status.get(engine_id) != status:
        try:
            await repo.insert_state_transition(
                entity_type="engine",
                entity_id=engine_id,
                from_status=_engine_runtime_status.get(engine_id),
                to_status=status,
                detail=detail,
                payload={"timestamp": timestamp.isoformat()},
            )
        except Exception:
            pass
    _engine_runtime_status[engine_id] = status
    _engine_runtime_detail[engine_id] = detail
    await repo.upsert_heartbeat(engine_id, status, last_bar_at=last_bar_at, detail=detail)
    await _sync_engine_runtime_state(
        repo,
        engine_id,
        status,
        detail,
        timestamp,
        last_bar_at=last_bar_at,
    )


async def _handle_global_stale(
    repo: TradingRepository,
    now: datetime,
    stale_result: BreakerResult,
) -> None:
    global _global_blocked_since

    payload = _breaker_payload(stale_result, now=now)
    if _global_blocked_since is None:
        _global_blocked_since = now
        payload["blocked_at"] = now.isoformat()
        await repo.insert_risk_event("KILL_STALE_DATA", None, stale_result.reason, payload)
    await repo.upsert_runtime_pause(
        _pause_key("worker", stale_result.breaker_name),
        scope="worker",
        pause_type=stale_result.breaker_name,
        reason=stale_result.reason,
        started_at=_global_blocked_since,
        observed_at=now,
        payload=payload,
    )
    await _persist_worker_runtime_state(
        repo,
        status="BLOCKED_RISK",
        detail=stale_result.breaker_name,
        last_freshness_check_at=now,
        runtime_state={
            "last_freshness_result": payload,
            "global_blocked_since": _global_blocked_since.isoformat() if _global_blocked_since else None,
        },
    )

    log(
        "worker",
        "input_stale",
        breaker=stale_result.breaker_name,
        reason=stale_result.reason,
        affected_symbol=stale_result.affected_symbol,
        observed_duration_seconds=stale_result.observed_duration_seconds,
    )
    for engine_id in _registry:
        await _set_engine_heartbeat(
            repo,
            engine_id,
            "BLOCKED_RISK",
            detail=stale_result.breaker_name,
            last_bar_at=_last_bar_seen.get(engine_id),
            breaker=stale_result,
            now=now,
        )


async def _resume_global_stale_if_needed(repo: TradingRepository, now: datetime) -> None:
    global _global_blocked_since
    if _global_blocked_since is None:
        return
    payload = {
        "resumed_at": now.isoformat(),
        "blocked_duration_seconds": round((now - _global_blocked_since).total_seconds(), 3),
    }
    await repo.resume_runtime_pause(
        _pause_key("worker", "KILL_STALE_DATA"),
        reason="freshness_restored",
        payload=payload,
    )
    _global_blocked_since = None


async def _emit_health_snapshot(
    pool: asyncpg.Pool,
    derivatives_pool: Optional[asyncpg.Pool],
    loop_start: datetime,
    loop_elapsed: float,
    degraded_reasons: List[str],
) -> None:
    snapshot = await build_system_status(pool, derivatives_pool=derivatives_pool)
    worker = snapshot.get("worker", {})
    engines = snapshot.get("engines", [])
    inputs = snapshot.get("inputs", {})
    risk = snapshot.get("risk", {})
    log_health_snapshot(
        worker.get("status", "unknown"),
        loop_started_at=loop_start.isoformat(),
        loop_elapsed_s=round(loop_elapsed, 2),
        degraded_reasons=degraded_reasons,
        inputs_status=inputs.get("status"),
        risk_status=risk.get("status"),
        engine_states={engine["engine_id"]: engine["heartbeat_status"] for engine in engines},
    )


async def _register_startup_inconsistency(
    repo: TradingRepository,
    engine_id: str,
    detail: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    log(engine_id, "loop_degraded", stage="startup_recovery", error=detail)
    await repo.insert_risk_event(
        "STARTUP_INCONSISTENCY",
        engine_id,
        detail,
        payload or {},
    )


async def _recover_runtime_state(
    repo: TradingRepository,
    reader: CandleReader,
) -> None:
    global _global_blocked_since

    runtime_rows = {row["engine_id"]: row for row in await repo.get_engine_runtime_states()}
    pending_rows = {row["engine_id"]: row for row in await repo.get_active_pending_setups()}
    active_pauses = await repo.get_active_runtime_pauses()
    worker_runtime = await repo.get_worker_runtime_state(WORKER_ID)
    now = datetime.now(timezone.utc)

    if worker_runtime:
        _global_blocked_since = None
        runtime_state = worker_runtime.get("runtime_state") or {}
        blocked_since = runtime_state.get("global_blocked_since")
        if blocked_since:
            _global_blocked_since = _parse_optional_datetime(blocked_since)

    # Phase D: fetch candle times once for startup pause validation.
    # Used to determine if KILL_STALE_DATA pauses are still relevant.
    _startup_symbols: List[str] = sorted({cfg.symbol for cfg in ENGINE_CONFIGS.values()})
    _startup_candle_times: Dict[str, Optional[datetime]] = {
        sym: await reader.get_last_candle_time(sym) for sym in _startup_symbols
    }
    _startup_stale = check_stale_data(_startup_candle_times)

    for pause in active_pauses:
        scope = pause["scope"]
        pause_type = pause["pause_type"]
        engine_id = pause.get("engine_id")
        if scope == "worker" and pause_type == "KILL_STALE_DATA":
            if not _startup_stale.triggered:
                # Data is fresh at startup — auto-resume this pause.
                started_at = pause.get("started_at")
                duration = (
                    round((now - started_at).total_seconds(), 3)
                    if started_at is not None else None
                )
                await repo.resume_runtime_pause(
                    pause["pause_key"],
                    reason="startup_data_fresh",
                    payload={"resumed_at": now.isoformat(), "blocked_duration_seconds": duration},
                )
                await repo.insert_risk_event(
                    "STARTUP_PAUSE_AUTO_RESUMED",
                    None,
                    "KILL_STALE_DATA auto-resumed: candle feed is fresh at startup",
                    {"pause_key": pause["pause_key"], "blocked_duration_seconds": duration},
                )
                log("worker", "startup_pause_auto_resumed", pause_key=pause["pause_key"], duration_seconds=duration)
                # Do not restore _global_blocked_since — feed is healthy now
            else:
                _global_blocked_since = pause.get("started_at")
        if scope != "engine" or not engine_id:
            continue
        if pause_type == "CIRCUIT_DD_DIARIO":
            if pause["started_at"].date() == now.date():
                _dd_paused.add(engine_id)
                _dd_pause_date[engine_id] = pause["started_at"].date()
            else:
                await repo.resume_runtime_pause(
                    pause["pause_key"],
                    reason="startup_dd_expired",
                    payload={"resumed_at": now.isoformat()},
                )
        elif pause_type == "CIRCUIT_BANKROLL_MIN":
            _bankroll_paused.add(engine_id)

    open_positions = await repo.get_open_positions()
    open_position_engines = {row["engine_id"] for row in open_positions}

    for engine_id, engine in _registry.items():
        runtime_row = runtime_rows.get(engine_id)
        if runtime_row:
            runtime_state = runtime_row.get("runtime_state") or {}
            if isinstance(runtime_state, str):
                runtime_state = {}
            engine.import_runtime_state(runtime_state)
            _last_bar_seen[engine_id] = runtime_row.get("last_bar_seen")
            _engine_runtime_status[engine_id] = runtime_row.get("status")
            _engine_runtime_detail[engine_id] = runtime_row.get("detail")

        pending_row = pending_rows.get(engine_id)
        if pending_row:
            pending_state = pending_row.get("state_data") or {}
            if engine_id in open_position_engines:
                engine.clear_pending_state()
                await repo.clear_pending_setup(engine_id, detail="INVALIDATED_OPEN_POSITION", status="INVALIDATED")
                await _register_startup_inconsistency(
                    repo,
                    engine_id,
                    "Pending setup invalidated because open position already exists",
                    payload={"engine_id": engine_id},
                )
            else:
                expires_at = pending_row.get("expires_at")
                if expires_at and expires_at < now:
                    engine.clear_pending_state()
                    await repo.clear_pending_setup(engine_id, detail="EXPIRED_AT_STARTUP", status="INVALIDATED")
                else:
                    engine.import_pending_state(pending_row["setup_type"], pending_state)

        last_candle = await reader.get_last_candle_time(ENGINE_CONFIGS[engine_id].symbol)
        last_bar_seen = _last_bar_seen.get(engine_id)
        safe_bar = _tf_bar_open(now, ENGINE_CONFIGS[engine_id].timeframe_minutes)
        if last_bar_seen and last_bar_seen > safe_bar:
            _last_bar_seen[engine_id] = None
            await _register_startup_inconsistency(
                repo,
                engine_id,
                "Persisted last_bar_seen is ahead of the latest safe completed bar",
                payload={
                    "persisted_last_bar_seen": last_bar_seen.isoformat(),
                    "safe_bar": safe_bar.isoformat(),
                },
            )
            await repo.upsert_engine_runtime_state(
                engine_id,
                status="DEGRADED",
                detail="STARTUP_LAST_BAR_INCONSISTENT",
                last_bar_seen=None,
                last_processed_at=now,
                runtime_state=engine.export_runtime_state(),
            )
            _engine_runtime_status[engine_id] = "DEGRADED"
            _engine_runtime_detail[engine_id] = "STARTUP_LAST_BAR_INCONSISTENT"

        pending_active = engine.export_pending_state()
        if pending_active is not None and last_candle is None:
            engine.clear_pending_state()
            await repo.clear_pending_setup(engine_id, detail="INVALIDATED_NO_CANDLE_CONTEXT", status="INVALIDATED")
            await _register_startup_inconsistency(
                repo,
                engine_id,
                "Pending setup invalidated because no current candle context is available",
                payload={"engine_id": engine_id},
            )
            pending_active = None

        if pending_active is not None:
            reference_bar_ts = _parse_optional_datetime(pending_active.get("reference_bar_ts"))
            expires_at = _parse_optional_datetime(pending_active.get("expires_at"))
            if expires_at and expires_at < now:
                engine.clear_pending_state()
                await repo.clear_pending_setup(engine_id, detail="EXPIRED_AT_STARTUP", status="INVALIDATED")
            elif reference_bar_ts and reference_bar_ts > safe_bar:
                engine.clear_pending_state()
                await repo.clear_pending_setup(engine_id, detail="INVALIDATED_REFERENCE_AHEAD_OF_SAFE_BAR", status="INVALIDATED")
                await _register_startup_inconsistency(
                    repo,
                    engine_id,
                    "Pending setup reference bar is ahead of the latest safe completed bar",
                    payload={
                        "reference_bar_ts": reference_bar_ts.isoformat(),
                        "safe_bar": safe_bar.isoformat(),
                    },
                )


async def _process_engine(
    engine_id: str,
    now: datetime,
    repo: TradingRepository,
    reader: CandleReader,
    loop_degraded_reasons: List[str],
    derivatives_snapshot: Dict[str, Any],
) -> None:
    global _last_bar_seen, _dd_paused, _bankroll_paused, _dd_pause_date

    cfg = ENGINE_CONFIGS[engine_id]
    engine = _registry[engine_id]
    today = now.date()
    derivatives_mode = derivatives_snapshot.get("mode", "DISCONNECTED")
    derivatives_by_symbol = {
        item.get("symbol"): item
        for item in derivatives_snapshot.get("symbols", [])
        if isinstance(item, dict)
    }

    if engine_id in _dd_paused:
        paused_on = _dd_pause_date.get(engine_id)
        if paused_on and paused_on < today:
            _dd_paused.discard(engine_id)
            _dd_pause_date.pop(engine_id, None)
            await repo.resume_runtime_pause(
                _pause_key("engine", "CIRCUIT_DD_DIARIO", engine_id),
                reason="midnight_reset",
                payload={"engine_id": engine_id, "resumed_at": now.isoformat()},
            )

    if engine_id in _dd_paused:
        await _set_engine_heartbeat(
            repo,
            engine_id,
            "BLOCKED_RISK",
            detail="CIRCUIT_DD_DIARIO",
            last_bar_at=_last_bar_seen.get(engine_id),
            now=now,
        )
        return

    bankroll = await repo.get_current_bankroll(engine_id)

    if engine_id in _bankroll_paused:
        await _set_engine_heartbeat(
            repo,
            engine_id,
            "BLOCKED_RISK",
            detail="CIRCUIT_BANKROLL_MIN",
            last_bar_at=_last_bar_seen.get(engine_id),
            now=now,
        )
        return

    br_result = check_bankroll_minimum(engine_id, bankroll)
    if br_result.triggered:
        _bankroll_paused.add(engine_id)
        await repo.insert_risk_event(
            "CIRCUIT_BANKROLL_MIN",
            engine_id,
            br_result.reason,
            _breaker_payload(br_result, now=now, engine_id=engine_id),
        )
        await _set_engine_heartbeat(
            repo,
            engine_id,
            "BLOCKED_RISK",
            detail=br_result.breaker_name,
            last_bar_at=_last_bar_seen.get(engine_id),
            breaker=br_result,
            now=now,
        )
        return

    today_starting = await repo.get_today_starting_equity(engine_id)
    if today_starting is None:
        await repo.upsert_daily_equity(engine_id, today, bankroll, bankroll, 0.0, 0)
        today_starting = bankroll

    dd_result = check_daily_drawdown(engine_id, today_starting, bankroll)
    if dd_result.triggered:
        _dd_paused.add(engine_id)
        _dd_pause_date[engine_id] = today
        await repo.insert_risk_event(
            "CIRCUIT_DD_DIARIO",
            engine_id,
            dd_result.reason,
            _breaker_payload(dd_result, now=now, engine_id=engine_id),
        )
        dd_pct = (today_starting - bankroll) / today_starting
        await send_alert(fmt_daily_dd_limit(engine_id, dd_pct))
        await _set_engine_heartbeat(
            repo,
            engine_id,
            "BLOCKED_RISK",
            detail=dd_result.breaker_name,
            last_bar_at=_last_bar_seen.get(engine_id),
            breaker=dd_result,
            now=now,
        )
        return

    await _set_engine_heartbeat(
        repo,
        engine_id,
        "PROCESSING",
        detail="LOOP_ACTIVE",
        last_bar_at=_last_bar_seen.get(engine_id),
        now=now,
    )

    tf = cfg.timeframe_minutes
    bar_open = _tf_bar_open(now, tf)
    last_seen = _last_bar_seen.get(engine_id)
    new_bar_closed = last_seen is None or bar_open > last_seen

    if new_bar_closed:
        _last_bar_seen[engine_id] = bar_open

        pos = engine.open_position
        if pos is not None:
            candle = await reader.aggregate_tf_candle(cfg.symbol, bar_open, tf)
            if candle is not None:
                pos.bars_held += 1
                stop_before = pos.stop_price
                close_result = check_position(
                    pos,
                    candle,
                    cfg.timeout_bars,
                    slippage_bps=cfg.slippage_bps,
                    fee_bps=cfg.fee_bps,
                )
                if close_result is None and pos.stop_price != stop_before:
                    await repo.update_trade_stop(pos.trade_id, pos.stop_price)
                if close_result is not None:
                    await repo.close_trade(
                        pos.trade_id,
                        close_result.exit_price,
                        close_result.pnl_usd,
                        close_result.pnl_pct,
                        close_result.close_reason,
                        close_result.closed_at,
                    )
                    new_bankroll = await repo.get_current_bankroll(engine_id)
                    engine.set_open_position(None)
                    log(
                        engine_id,
                        "trade_closed",
                        trade_id=pos.trade_id,
                        reason=close_result.close_reason,
                        pnl_usd=close_result.pnl_usd,
                        bankroll=new_bankroll,
                    )
                    await send_alert(
                        fmt_trade_closed(
                            engine_id,
                            cfg.symbol,
                            pos.direction.value if hasattr(pos.direction, "value") else str(pos.direction),
                            close_result.exit_price,
                            close_result.close_reason,
                            close_result.pnl_usd,
                            close_result.pnl_pct,
                            new_bankroll,
                        )
                    )
                    today_pnl = await repo.get_today_pnl(engine_id)
                    await repo.upsert_daily_equity(
                        engine_id,
                        today,
                        today_starting,
                        new_bankroll,
                        today_pnl,
                    )

        op_check = check_one_position(engine_id, engine.has_open_position)
        if op_check.triggered:
            await _set_engine_heartbeat(
                repo,
                engine_id,
                "OK",
                detail="ONE_POSITION_OPEN",
                last_bar_at=bar_open,
                now=now,
            )
            return

        candle = await reader.aggregate_tf_candle(cfg.symbol, bar_open, tf)
        if candle is None:
            reason = f"Missing aggregated {tf}m candle for {cfg.symbol} at {bar_open.isoformat()}"
            breaker = BreakerResult(
                triggered=True,
                breaker_name="NO_VALID_INPUT",
                reason=reason,
                affected_engine=engine_id,
                affected_symbol=cfg.symbol,
                details={
                    "engine_id": engine_id,
                    "symbol": cfg.symbol,
                    "bar_open": bar_open.isoformat(),
                    "timeframe_minutes": tf,
                },
            )
            log(engine_id, "input_stale", breaker=breaker.breaker_name, reason=reason, bar=bar_open.isoformat())
            await repo.insert_risk_event(
                "NO_VALID_INPUT",
                engine_id,
                reason,
                _breaker_payload(breaker, now=now, engine_id=engine_id),
            )
            await _set_engine_heartbeat(
                repo,
                engine_id,
                "BLOCKED_RISK",
                detail=breaker.breaker_name,
                last_bar_at=bar_open,
                breaker=breaker,
                now=now,
            )
            loop_degraded_reasons.append(f"{engine_id}:NO_VALID_INPUT")
            return

        recent = await reader.get_latest_candles(cfg.symbol, limit=50, before=bar_open)
        recent_closes = [c.close for c in recent]
        if not recent_closes:
            reason = f"No closed 1m candles available for {cfg.symbol} before {bar_open.isoformat()}"
            breaker = BreakerResult(
                triggered=True,
                breaker_name="NO_VALID_INPUT",
                reason=reason,
                affected_engine=engine_id,
                affected_symbol=cfg.symbol,
                details={
                    "engine_id": engine_id,
                    "symbol": cfg.symbol,
                    "bar_open": bar_open.isoformat(),
                    "timeframe_minutes": tf,
                },
            )
            log(engine_id, "input_stale", breaker=breaker.breaker_name, reason=reason, bar=bar_open.isoformat())
            await repo.insert_risk_event(
                "NO_VALID_INPUT",
                engine_id,
                reason,
                _breaker_payload(breaker, now=now, engine_id=engine_id),
            )
            await _set_engine_heartbeat(
                repo,
                engine_id,
                "BLOCKED_RISK",
                detail=breaker.breaker_name,
                last_bar_at=bar_open,
                breaker=breaker,
                now=now,
            )
            loop_degraded_reasons.append(f"{engine_id}:NO_VALID_INPUT")
            return

        ctx = engine.prepare_context(
            symbol=cfg.symbol,
            now=now,
            bar_open=candle.open,
            bar_high=candle.high,
            bar_low=candle.low,
            bar_close=candle.close,
            bar_volume=candle.volume,
            bar_timestamp=bar_open,
            recent_closes=recent_closes,
            extra={
                "derivatives_mode": derivatives_mode,
                "derivatives": derivatives_by_symbol.get(cfg.symbol),
                "derivatives_advisory_enabled": ENGINE_DERIVATIVES_ADVISORY_ENABLED,
                "derivatives_hard_filter_enabled": ENGINE_DERIVATIVES_HARD_FILTER_ENABLED,
            },
        )
        derivative_ctx = derivatives_by_symbol.get(cfg.symbol) or {}
        if derivatives_mode == "DEGRADED" or derivative_ctx.get("freshness") in {"DEGRADED", "STALE", "ABSENT"}:
            ctx.extra["derivative_context_note"] = "derivative_context_degraded"
            derivative_event_type = "DERIVATIVE_CONTEXT_DEGRADED"
        elif derivative_ctx.get("available"):
            ctx.extra["derivative_context_note"] = "derivative_context_available"
            derivative_event_type = "DERIVATIVE_CONTEXT_AVAILABLE"
        else:
            ctx.extra["derivative_context_note"] = "derivative_context_unavailable"
            derivative_event_type = "DERIVATIVE_CONTEXT_UNAVAILABLE"
        log(
            engine_id,
            "derivatives_context",
            mode=derivatives_mode,
            advisory_enabled=ENGINE_DERIVATIVES_ADVISORY_ENABLED,
            hard_filter_enabled=ENGINE_DERIVATIVES_HARD_FILTER_ENABLED,
            available=bool((derivatives_by_symbol.get(cfg.symbol) or {}).get("available")),
            fallback_active=bool(derivatives_snapshot.get("fallback_active")),
        )
        try:
            await repo.insert_risk_event(
                derivative_event_type,
                engine_id,
                f"{ctx.extra.get('derivative_context_note')}:{cfg.symbol}",
                {
                    "engine_id": engine_id,
                    "symbol": cfg.symbol,
                    "mode": derivatives_mode,
                    "quality": derivative_ctx.get("quality"),
                    "freshness": derivative_ctx.get("freshness"),
                    "available_features": derivative_ctx.get("available_features", []),
                    "fallback_active": derivatives_snapshot.get("fallback_active"),
                    "fallback_reason": derivatives_snapshot.get("fallback_reason"),
                    "timestamp": now.isoformat(),
                },
            )
        except Exception:
            pass

        try:
            ctx = await engine.enrich_context(ctx, reader)
        except Exception as exc:
            log(engine_id, "loop_degraded", stage="enrich_context", error=str(exc))
            loop_degraded_reasons.append(f"{engine_id}:enrich_context")

        try:
            signal_obj = engine.generate_signal(ctx)
        except NotImplementedError:
            await _set_engine_heartbeat(repo, engine_id, "DEGRADED", detail="NOT_IMPLEMENTED", last_bar_at=bar_open, now=now)
            loop_degraded_reasons.append(f"{engine_id}:NOT_IMPLEMENTED")
            return
        except Exception as exc:
            log(engine_id, "loop_degraded", stage="generate_signal", error=str(exc))
            await repo.insert_risk_event("ERROR", engine_id, str(exc), {"stage": "generate_signal", "timestamp": now.isoformat()})
            await send_alert(fmt_critical_error(engine_id, str(exc)))
            await _set_engine_heartbeat(repo, engine_id, "DEGRADED", detail="SIGNAL_ERROR", last_bar_at=bar_open, now=now)
            loop_degraded_reasons.append(f"{engine_id}:SIGNAL_ERROR")
            return

        if signal_obj is None:
            await _set_engine_heartbeat(repo, engine_id, "OK", last_bar_at=bar_open, now=now)
            return

        signal_id = await repo.insert_signal(signal_obj)
        if signal_id is None:
            await _set_engine_heartbeat(repo, engine_id, "OK", last_bar_at=bar_open, now=now)
            return

        try:
            decision = engine.validate_signal(signal_obj, ctx)
        except NotImplementedError:
            await _set_engine_heartbeat(repo, engine_id, "DEGRADED", detail="NOT_IMPLEMENTED", last_bar_at=bar_open, now=now)
            loop_degraded_reasons.append(f"{engine_id}:NOT_IMPLEMENTED")
            return
        except Exception as exc:
            log(engine_id, "loop_degraded", stage="validate_signal", error=str(exc))
            await repo.insert_risk_event("ERROR", engine_id, str(exc), {"stage": "validate_signal", "timestamp": now.isoformat()})
            await _set_engine_heartbeat(repo, engine_id, "DEGRADED", detail="VALIDATION_ERROR", last_bar_at=bar_open, now=now)
            loop_degraded_reasons.append(f"{engine_id}:VALIDATION_ERROR")
            return

        decision.signal_id = signal_id
        await repo.insert_decision(signal_id, decision)
        action = decision.action.value if hasattr(decision.action, "value") else str(decision.action)

        log(
            engine_id,
            "signal_decision",
            signal_id=signal_id,
            action=action,
            reason=decision.reason,
        )
        await send_alert(
            fmt_signal_decision(
                engine_id,
                cfg.symbol,
                signal_obj.direction.value if hasattr(signal_obj.direction, "value") else str(signal_obj.direction),
                action,
                decision.reason,
            )
        )

        # Architectural role guard: ENGINE_ROLES is the authoritative source.
        # Even if validate_signal() returned ENTER, a SIGNAL_ONLY role blocks execution.
        engine_role = ENGINE_ROLES.get(engine_id, "ACTIVE")
        if cfg.signal_only or action == "SIGNAL_ONLY" or engine_role == "SIGNAL_ONLY":
            await send_alert(
                fmt_signal_accepted(
                    engine_id,
                    cfg.symbol,
                    signal_obj.direction.value if hasattr(signal_obj.direction, "value") else str(signal_obj.direction),
                    signal_obj.timeframe,
                    bar_open.strftime("%Y-%m-%d %H:%M UTC"),
                    "SIGNAL_ONLY",
                    0.0,
                )
            )
            await _set_engine_heartbeat(repo, engine_id, "OK", last_bar_at=bar_open, now=now)
            return

        if action != "ENTER":
            await _set_engine_heartbeat(repo, engine_id, "OK", last_bar_at=bar_open, now=now)
            return

        # Phase G — shadow filter: evaluate derivatives advisory filter without blocking the trade
        if ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED:
            try:
                _deriv_ctx = (derivatives_by_symbol or {}).get(cfg.symbol) or {}
                _sf_verdict = evaluate_shadow_filter(
                    direction=signal_obj.direction.value,
                    derivatives_ctx=_deriv_ctx,
                )
                _sf_event = (
                    "DERIVATIVE_SHADOW_FILTER_BLOCK"
                    if _sf_verdict.verdict == "BLOCK"
                    else "DERIVATIVE_SHADOW_FILTER_PASS"
                )
                log(
                    engine_id,
                    "shadow_filter",
                    verdict=_sf_verdict.verdict,
                    triggered_rules=_sf_verdict.triggered_rules,
                    data_quality=_sf_verdict.data_quality,
                    reason=_sf_verdict.reason,
                )
                await repo.insert_risk_event(
                    _sf_event,
                    engine_id,
                    f"shadow_filter:{_sf_verdict.verdict}:{cfg.symbol}",
                    {
                        "engine_id": engine_id,
                        "symbol": cfg.symbol,
                        "direction": signal_obj.direction.value,
                        "verdict": _sf_verdict.verdict,
                        "triggered_rules": _sf_verdict.triggered_rules,
                        "features_used": _sf_verdict.features_used,
                        "data_quality": _sf_verdict.data_quality,
                        "reason": _sf_verdict.reason,
                        "feature_values": _sf_verdict.feature_values,
                        "timestamp": now.isoformat(),
                    },
                )
            except Exception:
                pass  # shadow filter must never affect the trading loop

        try:
            plan = engine.build_order_plan(signal_obj, bankroll)
        except NotImplementedError:
            await _set_engine_heartbeat(repo, engine_id, "DEGRADED", detail="NOT_IMPLEMENTED", last_bar_at=bar_open, now=now)
            loop_degraded_reasons.append(f"{engine_id}:NOT_IMPLEMENTED")
            return
        except Exception as exc:
            log(engine_id, "loop_degraded", stage="build_order_plan", error=str(exc))
            await repo.insert_risk_event("ERROR", engine_id, str(exc), {"stage": "build_order_plan", "timestamp": now.isoformat()})
            await _set_engine_heartbeat(repo, engine_id, "DEGRADED", detail="ORDER_PLAN_ERROR", last_bar_at=bar_open, now=now)
            loop_degraded_reasons.append(f"{engine_id}:ORDER_PLAN_ERROR")
            return

        direction = signal_obj.direction
        entry_price = simulated_fill_price(candle.close, direction, engine_id)

        trade_id = await repo.open_trade(
            engine_id=engine_id,
            symbol=cfg.symbol,
            signal_id=signal_id,
            direction=direction,
            entry_price=entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            stake_usd=plan.stake_usd,
        )

        position = Position(
            trade_id=trade_id,
            engine_id=engine_id,
            symbol=cfg.symbol,
            signal_id=signal_id,
            direction=direction,
            entry_price=entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            stake_usd=plan.stake_usd,
            opened_at=now,
            bars_held=0,
        )
        engine.set_open_position(position)

        log(
            engine_id,
            "trade_opened",
            trade_id=trade_id,
            direction=direction.value if hasattr(direction, "value") else str(direction),
            entry=entry_price,
            stake=plan.stake_usd,
        )

        await send_alert(
            fmt_trade_opened(
                engine_id,
                cfg.symbol,
                direction.value if hasattr(direction, "value") else str(direction),
                entry_price,
                plan.stake_usd,
                plan.stop_price,
                plan.target_price,
                cfg.timeout_bars,
                signal_obj.timeframe,
            )
        )
        await send_alert(
            fmt_signal_accepted(
                engine_id,
                cfg.symbol,
                direction.value if hasattr(direction, "value") else str(direction),
                signal_obj.timeframe,
                bar_open.strftime("%Y-%m-%d %H:%M UTC"),
                "ENTER",
                plan.stake_usd,
            )
        )

    await _set_engine_heartbeat(repo, engine_id, "OK", last_bar_at=_last_bar_seen.get(engine_id), now=now)


_registry: Dict[str, Any] = {}


def _log_startup_version() -> None:
    try:
        repo_root = os.path.dirname(os.path.abspath(__file__))
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        git_hash = "UNKNOWN (no git in container)"

    by_role: Dict[str, list] = {}
    for engine_id, role in ENGINE_ROLES.items():
        by_role.setdefault(role, []).append(engine_id)

    log(
        "worker",
        "startup_version",
        git_hash=git_hash,
        engine_roles=by_role,
    )


async def main() -> None:
    global _registry, _global_blocked_since, _derivatives_pool

    _log_startup_version()
    log("worker", "startup_begin")

    async def _init_conn(conn: asyncpg.Connection) -> None:
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10, init=_init_conn)
    if DERIVATIVES_ENABLED and DERIVATIVES_FEATURE_ADAPTER_ENABLED and DERIVATIVES_DATABASE_URL:
        try:
            _derivatives_pool = await asyncpg.create_pool(
                DERIVATIVES_DATABASE_URL,
                min_size=1,
                max_size=3,
                init=_init_conn,
            )
        except Exception:
            _derivatives_pool = None
    await run_migrations(pool)
    log("worker", "migrations_applied")

    _registry = build_registry()
    repo = TradingRepository(pool, derivatives_pool=_derivatives_pool)
    reader = CandleReader(pool)
    await _persist_worker_runtime_state(
        repo,
        status="RECOVERING",
        detail="STARTUP_BEGIN",
        runtime_state={"startup_at": datetime.now(timezone.utc).isoformat()},
    )

    for engine_id, engine in _registry.items():
        try:
            await engine.initialize(reader)
            log(engine_id, "engine_initialized")
        except Exception as exc:
            log(engine_id, "loop_degraded", stage="engine_initialize", error=str(exc))

    open_positions = await repo.get_open_positions()
    for row in open_positions:
        engine_id = row["engine_id"]
        if engine_id not in _registry:
            # Phase D: record orphaned positions instead of silently skipping
            await repo.insert_risk_event(
                "ORPHANED_POSITION",
                engine_id,
                f"Open trade id={row['id']} belongs to engine not in current registry",
                {
                    "trade_id": row["id"],
                    "symbol": row["symbol"],
                    "direction": row.get("direction"),
                    "opened_at": row["opened_at"].isoformat() if row.get("opened_at") else None,
                },
            )
            log(engine_id, "orphaned_position", trade_id=row["id"], symbol=row["symbol"])
            continue
        engine = _registry[engine_id]
        pos = Position(
            trade_id=row["id"],
            engine_id=engine_id,
            symbol=row["symbol"],
            signal_id=row.get("signal_id"),
            direction=Direction(row["direction"]),
            entry_price=float(row["entry_price"]),
            stop_price=float(row["stop_price"]),
            target_price=float(row["target_price"]),
            stake_usd=float(row["stake_usd"]),
            opened_at=row["opened_at"],
            bars_held=0,
        )
        engine.rehydrate(pos)
        log(engine_id, "position_rehydrated", trade_id=row["id"])

    await _recover_runtime_state(repo, reader)

    today = datetime.now(timezone.utc).date()
    for engine_id in ENGINE_CONFIGS:
        starting = await repo.get_today_starting_equity(engine_id)
        if starting is None:
            bankroll = await repo.get_current_bankroll(engine_id)
            await repo.upsert_daily_equity(engine_id, today, bankroll, bankroll, 0.0, 0)

    # Log engine roles at startup for observability
    for eid in _registry:
        role = ENGINE_ROLES.get(eid, "ACTIVE")
        log(eid, "engine_role", role=role)
        if role == "EXPERIMENTAL":
            log(eid, "engine_skipped", reason="EXPERIMENTAL_ROLE")
        if role == "ARCHIVED":
            log(eid, "engine_skipped", reason="ARCHIVED_ROLE")

    log("worker", "startup_complete", engines=list(_registry.keys()))
    await _persist_worker_runtime_state(
        repo,
        status="ALIVE",
        detail="STARTUP_COMPLETE",
        runtime_state={"engines": list(_registry.keys())},
    )

    while not _shutdown:
        loop_start = datetime.now(timezone.utc)
        loop_degraded_reasons: List[str] = []

        log("worker", "loop_started", loop_started_at=loop_start.isoformat())
        await _persist_worker_runtime_state(
            repo,
            status="PROCESSING",
            detail="LOOP_STARTED",
            last_loop_started_at=loop_start,
            runtime_state={"loop_started_at": loop_start.isoformat()},
        )
        await _record_worker_event(
            repo,
            "WORKER_LOOP_STARTED",
            "worker loop started",
            {"loop_started_at": loop_start.isoformat()},
        )

        symbols_needed = list({cfg.symbol for cfg in ENGINE_CONFIGS.values()})
        last_candle_times: Dict[str, Optional[datetime]] = {}
        for sym in symbols_needed:
            last_candle_times[sym] = await reader.get_last_candle_time(sym)

        stale_result = check_stale_data(last_candle_times)
        await _persist_worker_runtime_state(
            repo,
            status="PROCESSING",
            detail="FRESHNESS_CHECKED",
            last_loop_started_at=loop_start,
            last_freshness_check_at=loop_start,
            runtime_state={
                "last_freshness_check_at": loop_start.isoformat(),
                "symbols_checked": {
                    sym: ts.isoformat() if ts is not None else None
                    for sym, ts in last_candle_times.items()
                },
                "stale_data_triggered": stale_result.triggered,
                "stale_reason": stale_result.reason,
            },
        )
        if stale_result.triggered:
            await _handle_global_stale(repo, loop_start, stale_result)
            loop_degraded_reasons.append(stale_result.breaker_name)
            elapsed = (datetime.now(timezone.utc) - loop_start).total_seconds()
            log("worker", "loop_degraded", degraded_reasons=loop_degraded_reasons, elapsed_s=round(elapsed, 2))
            await _record_worker_event(
                repo,
                "WORKER_LOOP_DEGRADED",
                "worker loop degraded",
                {
                    "loop_started_at": loop_start.isoformat(),
                    "loop_completed_at": datetime.now(timezone.utc).isoformat(),
                    "degraded_reasons": loop_degraded_reasons,
                },
            )
            await _persist_worker_runtime_state(
                repo,
                status="BLOCKED_RISK",
                detail=stale_result.breaker_name,
                last_loop_started_at=loop_start,
                last_loop_completed_at=datetime.now(timezone.utc),
                last_freshness_check_at=loop_start,
                runtime_state={
                    "degraded_reasons": loop_degraded_reasons,
                    "last_freshness_result": _breaker_payload(stale_result, now=loop_start),
                },
            )
            await _emit_health_snapshot(
                pool,
                _derivatives_pool,
                loop_start,
                elapsed,
                loop_degraded_reasons,
            )
            await asyncio.sleep(WORKER_LOOP_INTERVAL_SECONDS)
            continue

        await _resume_global_stale_if_needed(repo, loop_start)

        derivatives_snapshot = await repo.get_derivatives_feature_snapshot(
            symbols=symbols_needed,
            now=loop_start,
        )
        if derivatives_snapshot.get("fallback_active"):
            log(
                "worker",
                "derivatives_fallback_active",
                mode=derivatives_snapshot.get("mode"),
                reason=derivatives_snapshot.get("fallback_reason"),
            )
            try:
                await repo.insert_risk_event(
                    "DERIVATIVES_FALLBACK_ACTIVE",
                    None,
                    derivatives_snapshot.get("fallback_reason"),
                    {
                        "mode": derivatives_snapshot.get("mode"),
                        "fallback_reason": derivatives_snapshot.get("fallback_reason"),
                        "timestamp": loop_start.isoformat(),
                    },
                )
            except Exception:
                pass
        if derivatives_snapshot.get("mode") == "DEGRADED":
            try:
                await repo.insert_risk_event(
                    "DERIVATIVES_DEGRADED",
                    None,
                    derivatives_snapshot.get("fallback_reason"),
                    {
                        "mode": derivatives_snapshot.get("mode"),
                        "fallback_reason": derivatives_snapshot.get("fallback_reason"),
                        "timestamp": loop_start.isoformat(),
                    },
                )
            except Exception:
                pass

        for engine_id in list(_registry.keys()):
            if ENGINE_ROLES.get(engine_id, "ACTIVE") in ("EXPERIMENTAL", "ARCHIVED"):
                continue  # EXPERIMENTAL/ARCHIVED engines are registered but not called
            try:
                await _process_engine(
                    engine_id,
                    loop_start,
                    repo,
                    reader,
                    loop_degraded_reasons,
                    derivatives_snapshot,
                )
            except Exception as exc:
                log(engine_id, "loop_degraded", stage="engine_loop", error=str(exc))
                await repo.insert_risk_event("ERROR", engine_id, str(exc), {"stage": "engine_loop", "timestamp": loop_start.isoformat()})
                await send_alert(fmt_critical_error(engine_id, str(exc)))
                await _set_engine_heartbeat(
                    repo,
                    engine_id,
                    "DEGRADED",
                    detail="ENGINE_LOOP_ERROR",
                    last_bar_at=_last_bar_seen.get(engine_id),
                    now=datetime.now(timezone.utc),
                )
                loop_degraded_reasons.append(f"{engine_id}:ENGINE_LOOP_ERROR")

        loop_completed_at = datetime.now(timezone.utc)
        elapsed = (loop_completed_at - loop_start).total_seconds()
        sleep_for = max(0.0, WORKER_LOOP_INTERVAL_SECONDS - elapsed)

        worker_event_type = "WORKER_LOOP_COMPLETED"
        worker_description = "worker loop completed"
        if loop_degraded_reasons or elapsed > WORKER_LOOP_INTERVAL_SECONDS:
            worker_event_type = "WORKER_LOOP_DEGRADED"
            worker_description = "worker loop degraded"
            if elapsed > WORKER_LOOP_INTERVAL_SECONDS:
                loop_degraded_reasons.append("LOOP_SLOW")
            log(
                "worker",
                "loop_degraded",
                degraded_reasons=loop_degraded_reasons,
                elapsed_s=round(elapsed, 2),
                sleep_s=round(sleep_for, 2),
            )
        else:
            log("worker", "loop_completed", elapsed_s=round(elapsed, 2), sleep_s=round(sleep_for, 2))

        await _record_worker_event(
            repo,
            worker_event_type,
            worker_description,
            {
                "loop_started_at": loop_start.isoformat(),
                "loop_completed_at": loop_completed_at.isoformat(),
                "elapsed_seconds": round(elapsed, 3),
                "sleep_seconds": round(sleep_for, 3),
                "degraded_reasons": loop_degraded_reasons,
            },
        )
        await _persist_worker_runtime_state(
            repo,
            status="DEGRADED" if worker_event_type == "WORKER_LOOP_DEGRADED" else "ALIVE",
            detail=worker_event_type,
            last_loop_started_at=loop_start,
            last_loop_completed_at=loop_completed_at,
            last_freshness_check_at=loop_start,
            runtime_state={
                "elapsed_seconds": round(elapsed, 3),
                "sleep_seconds": round(sleep_for, 3),
                "degraded_reasons": loop_degraded_reasons,
                "derivatives_mode": derivatives_snapshot.get("mode"),
                "derivatives_fallback_active": derivatives_snapshot.get("fallback_active"),
            },
        )
        await _emit_health_snapshot(
            pool,
            _derivatives_pool,
            loop_start,
            elapsed,
            loop_degraded_reasons,
        )
        await asyncio.sleep(sleep_for)

    log("worker", "shutdown_begin")
    await _persist_worker_runtime_state(
        repo,
        status="STOPPED",
        detail="SIGTERM",
        runtime_state={"stopped_at": datetime.now(timezone.utc).isoformat()},
    )
    for engine_id in _registry:
        await _set_engine_heartbeat(repo, engine_id, "STOPPED", detail="SIGTERM", last_bar_at=_last_bar_seen.get(engine_id))
    if _derivatives_pool:
        await _derivatives_pool.close()
    await pool.close()
    log("worker", "shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
