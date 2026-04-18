"""
trading_api — read-only FastAPI service + operator repair endpoints.

Endpoints:
  GET  /health
  GET  /status
  GET  /system/status                          ← stable operations contract
  GET  /positions
  GET  /trades?engine_id=&limit=
  GET  /signals?engine_id=&limit=
  GET  /equity?engine_id=
  GET  /risk-events?limit=
  GET  /state-transitions?entity_id=&entity_type=&limit=
  GET  /overlay/decisions?engine_id=&limit=
  GET  /overlay/ghost-trades?engine_id=&limit=
  GET  /overlay/comparisons?engine_id=&limit=
  GET  /overlay/summary?engine_id=
  GET  /overlay/liquidity?symbol=&limit=

  POST /operator/clear-pause/{pause_key}       ← requires X-Operator-Token
  POST /operator/reset-pending/{engine_id}     ← requires X-Operator-Token
  GET  /operator/recovery-state                ← diagnostic dump
  GET  /operator/reviewer-briefing             ← operational review/briefing
  GET  /operator/readiness-report              ← operational stabilization gates
  GET  /operator/derivatives-snapshot          ← canonical derivatives snapshot
  GET  /operator/derivatives-readiness         ← derivatives value/readiness
  GET  /operator/shadow-filter-report          ← Phase G shadow filter evaluation report
  GET  /operator/zone-comparison-report        ← Phase 4 ghost trade variant comparison
  GET  /operator/fill-stats?engine_id=&hours=  ← fill decision counters (skip event aggregation)
"""
from __future__ import annotations

import os
import sys
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from trading.core.config.settings import (
    DATABASE_URL,
    DERIVATIVES_DATABASE_URL,
    DERIVATIVES_ENABLED,
    DERIVATIVES_FEATURE_ADAPTER_ENABLED,
    OPERATOR_TOKEN,
)
from trading.core.monitoring.healthcheck import build_system_status, healthcheck
from trading.core.monitoring.operational_readiness import build_operational_readiness_report
from trading.core.monitoring.operational_reviewer import build_operational_review
from trading.core.storage.migrations import run_migrations
from trading.core.storage.repository import TradingRepository


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

_pool: Optional[asyncpg.Pool] = None
_derivatives_pool: Optional[asyncpg.Pool] = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs so asyncpg returns dicts instead of strings."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _derivatives_pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, init=_init_conn)
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
    await run_migrations(_pool)
    yield
    if _pool:
        await _pool.close()
    if _derivatives_pool:
        await _derivatives_pool.close()


app = FastAPI(
    title="Trading API",
    description="Read-only API for the Jarvis Capital trading system.",
    version="1.0.0",
    lifespan=lifespan,
)


def get_repo() -> TradingRepository:
    assert _pool is not None, "Pool not initialised"
    return TradingRepository(_pool, derivatives_pool=_derivatives_pool)


def _jsonify(data: Any) -> Any:
    """Recursively convert non-serialisable types (date, datetime, Decimal)."""
    import datetime as dt
    import decimal

    if isinstance(data, list):
        return [_jsonify(item) for item in data]
    if isinstance(data, dict):
        return {k: _jsonify(v) for k, v in data.items()}
    if isinstance(data, (dt.datetime, dt.date)):
        return data.isoformat()
    if isinstance(data, decimal.Decimal):
        return float(data)
    if isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return _jsonify(json.loads(stripped))
            except json.JSONDecodeError:
                return data
    return data


async def _build_state_transitions_payload(
    repo: TradingRepository,
    *,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    rows = await repo.get_state_transitions(
        entity_id=entity_id,
        entity_type=entity_type,
        limit=limit,
    )
    return {"state_transitions": rows, "count": len(rows)}


async def _build_recovery_state_payload(repo: TradingRepository) -> Dict[str, Any]:
    active_pauses = await repo.get_active_runtime_pauses()
    pending_setups = await repo.get_active_pending_setups()
    engine_states = await repo.get_engine_runtime_states()
    worker_state = await repo.get_worker_runtime_state("trading_worker")
    open_positions = await repo.get_open_positions()
    recent_events = await repo.get_risk_events(limit=30)
    transitions_payload = await _build_state_transitions_payload(repo, limit=30)
    recent_transitions = transitions_payload["state_transitions"]

    recovery_events = [
        e for e in recent_events
        if e.get("event_type", "").startswith(("STARTUP_", "OPERATOR_", "ORPHANED_"))
    ]

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "active_pauses": active_pauses,
        "active_pauses_count": len(active_pauses),
        "pending_setups": pending_setups,
        "pending_setups_count": len(pending_setups),
        "engine_states": engine_states,
        "worker_state": worker_state,
        "open_positions": open_positions,
        "open_positions_count": len(open_positions),
        "recovery_events": recovery_events,
        "recent_transitions": recent_transitions,
    }


async def _build_risk_events_payload(repo: TradingRepository, *, limit: int = 200) -> Dict[str, Any]:
    rows = await repo.get_risk_events(limit=limit)
    return {"risk_events": rows, "count": len(rows)}


async def _build_signals_payload(
    repo: TradingRepository,
    *,
    engine_id: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    rows = await repo.get_signals(engine_id=engine_id, limit=limit)
    return {"signals": rows, "count": len(rows)}


def _build_derivatives_snapshot_payload(system_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    derivatives = system_snapshot.get("derivatives", {}) or {}
    return {
        "timestamp": system_snapshot.get("ts"),
        "mode": derivatives.get("mode"),
        "status": derivatives.get("status"),
        "fallback_active": derivatives.get("fallback_active"),
        "fallback_reason": derivatives.get("fallback_reason"),
        "summary": derivatives.get("summary", {}),
        "canonical_snapshot": derivatives.get("canonical_snapshot", []),
        "symbols": derivatives.get("symbols", []),
        "flags": derivatives.get("flags", {}),
        "contract": derivatives.get("contract", {}),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    if _pool is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    result = await healthcheck(_pool, derivatives_pool=_derivatives_pool)
    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(_jsonify(result), status_code=status_code)


@app.get("/status")
async def status():
    assert _pool is not None, "Pool not initialised"
    snapshot = await build_system_status(_pool, derivatives_pool=_derivatives_pool)
    return JSONResponse(_jsonify(snapshot))


@app.get("/system/status")
async def system_status():
    """
    Stable operations contract endpoint for Trading Core.

    This is the canonical interface for operational consumers (OpenClaw, monitoring).
    The response schema is defined in core/monitoring/status_model.py.
    The ``contract_version`` field in the response body signals schema changes.

    Downstream consumers should parse ``contract_version`` and alert on unexpected
    values rather than failing silently on unexpected fields.
    """
    assert _pool is not None, "Pool not initialised"
    snapshot = await build_system_status(_pool, derivatives_pool=_derivatives_pool)
    return JSONResponse(_jsonify(snapshot))


@app.get("/positions")
async def positions():
    repo = get_repo()
    rows = await repo.get_open_positions()
    return JSONResponse({"positions": _jsonify(rows), "count": len(rows)})


@app.get("/trades")
async def trades(
    engine_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
):
    repo = get_repo()
    rows = await repo.get_trades(engine_id=engine_id, limit=limit)
    return JSONResponse({"trades": _jsonify(rows), "count": len(rows)})


@app.get("/signals")
async def signals(
    engine_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
):
    repo = get_repo()
    payload = await _build_signals_payload(repo, engine_id=engine_id, limit=limit)
    return JSONResponse(_jsonify(payload))


@app.get("/equity")
async def equity(engine_id: Optional[str] = Query(default=None)):
    repo = get_repo()
    rows = await repo.get_equity(engine_id=engine_id)
    return JSONResponse({"equity": _jsonify(rows)})


@app.get("/risk-events")
async def risk_events(limit: int = Query(default=50, le=500)):
    repo = get_repo()
    payload = await _build_risk_events_payload(repo, limit=limit)
    return JSONResponse(_jsonify(payload))


@app.get("/overlay/decisions")
async def overlay_decisions(
    engine_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
):
    repo = get_repo()
    rows = await repo.get_overlay_decisions(engine_id=engine_id, limit=limit)
    return JSONResponse({"overlay_decisions": _jsonify(rows), "count": len(rows)})


@app.get("/overlay/ghost-trades")
async def overlay_ghost_trades(
    engine_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
):
    repo = get_repo()
    rows = await repo.get_ghost_trades(engine_id=engine_id, limit=limit)
    return JSONResponse({"ghost_trades": _jsonify(rows), "count": len(rows)})


@app.get("/overlay/comparisons")
async def overlay_comparisons(
    engine_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
):
    repo = get_repo()
    rows = await repo.get_trade_comparisons(engine_id=engine_id, limit=limit)
    return JSONResponse({"comparisons": _jsonify(rows), "count": len(rows)})


@app.get("/overlay/summary")
async def overlay_summary(engine_id: Optional[str] = Query(default=None)):
    repo = get_repo()
    rows = await repo.get_overlay_summary(engine_id=engine_id)
    return JSONResponse({"summary": _jsonify(rows), "count": len(rows)})


@app.get("/overlay/liquidity")
async def overlay_liquidity(
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
):
    repo = get_repo()
    rows = await repo.get_liquidity_snapshots(symbol=symbol, limit=limit)
    return JSONResponse({"liquidity_snapshots": _jsonify(rows), "count": len(rows)})


# ---------------------------------------------------------------------------
# State transition audit log
# ---------------------------------------------------------------------------

@app.get("/state-transitions")
async def state_transitions(
    entity_id: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
):
    """Append-only audit log of engine and worker status transitions."""
    repo = get_repo()
    payload = await _build_state_transitions_payload(
        repo,
        entity_id=entity_id,
        entity_type=entity_type,
        limit=limit,
    )
    return JSONResponse(_jsonify(payload))


# ---------------------------------------------------------------------------
# Operator repair endpoints
# ---------------------------------------------------------------------------

def _check_operator_token(x_operator_token: Optional[str]) -> None:
    """Validate operator bearer token if OPERATOR_TOKEN env var is configured."""
    if not OPERATOR_TOKEN:
        return  # no auth configured — allow (dev/local only)
    if x_operator_token != f"Bearer {OPERATOR_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing operator token")


@app.post("/operator/clear-pause/{pause_key}")
async def operator_clear_pause(
    pause_key: str = Path(..., description="pause_key from tr_runtime_pauses"),
    reason: Optional[str] = Query(default=None, description="Reason for manual clearance"),
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Manually resume (clear) an active operational pause.

    Takes effect on the next worker loop iteration.
    Records an OPERATOR_PAUSE_CLEARED risk event for audit.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)
    now = datetime.now(timezone.utc)
    effective_reason = reason or "operator_manual_clear"

    await repo.resume_runtime_pause(
        pause_key,
        reason=effective_reason,
        payload={"resumed_at": now.isoformat(), "operator_action": True},
    )
    await repo.insert_risk_event(
        "OPERATOR_PAUSE_CLEARED",
        None,
        f"Pause {pause_key!r} manually cleared by operator: {effective_reason}",
        {"pause_key": pause_key, "reason": effective_reason, "cleared_at": now.isoformat()},
    )
    return JSONResponse({"ok": True, "pause_key": pause_key, "reason": effective_reason})


@app.post("/operator/reset-pending/{engine_id}")
async def operator_reset_pending(
    engine_id: str = Path(..., description="engine_id to reset pending setup for"),
    reason: Optional[str] = Query(default=None, description="Reason for manual reset"),
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Manually clear the active pending setup for an engine.

    Takes effect on the next worker loop iteration.
    Records an OPERATOR_PENDING_RESET risk event for audit.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)
    now = datetime.now(timezone.utc)
    effective_reason = reason or "operator_manual_reset"

    await repo.clear_pending_setup(
        engine_id,
        detail=f"OPERATOR_RESET: {effective_reason}",
        status="OPERATOR_CLEARED",
    )
    await repo.insert_risk_event(
        "OPERATOR_PENDING_RESET",
        engine_id,
        f"Pending setup for {engine_id!r} manually reset by operator: {effective_reason}",
        {"engine_id": engine_id, "reason": effective_reason, "reset_at": now.isoformat()},
    )
    return JSONResponse({"ok": True, "engine_id": engine_id, "reason": effective_reason})


@app.get("/operator/recovery-state")
async def operator_recovery_state(
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Full diagnostic dump for operator-assisted recovery.

    Returns: active pauses, pending setups, engine runtime states,
    worker runtime state, recent inconsistency/operator events,
    and open positions.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)
    payload = await _build_recovery_state_payload(repo)
    return JSONResponse(_jsonify(payload))


@app.get("/operator/reviewer-briefing")
async def operator_reviewer_briefing(
    transition_limit: int = Query(default=50, le=500),
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Operational reviewer (Phase E): analytical briefing for operators/OpenClaw.

    Read-only layer that classifies visible runtime findings and emits a stable
    briefing payload without changing risk/engines/trading execution.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)

    system_snapshot = await build_system_status(
        _pool,
        derivatives_pool=_derivatives_pool,
    )  # equivalent to GET /system/status
    transitions_payload = await _build_state_transitions_payload(repo, limit=transition_limit)
    recovery_payload = await _build_recovery_state_payload(repo)  # equivalent to GET /operator/recovery-state

    review = build_operational_review(
        system_status=system_snapshot,
        state_transitions=transitions_payload["state_transitions"],
        recovery_state=recovery_payload,
    )
    return JSONResponse(_jsonify(review))


@app.get("/operator/readiness-report")
async def operator_readiness_report(
    lookback_hours: int = Query(default=24, ge=1, le=168),
    transition_limit: int = Query(default=500, ge=50, le=2000),
    events_limit: int = Query(default=500, ge=50, le=2000),
    signals_limit: int = Query(default=500, ge=50, le=2000),
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Phase E.5 readiness report with objective acceptance gates for promotion.

    Read-only operational stabilization surface. Produces readiness status from:
      - /system/status
      - /state-transitions
      - /operator/recovery-state
      - recent risk events and signals
      - reviewer output (internal synthesis)
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)

    system_snapshot = await build_system_status(_pool, derivatives_pool=_derivatives_pool)
    transitions_payload = await _build_state_transitions_payload(repo, limit=transition_limit)
    recovery_payload = await _build_recovery_state_payload(repo)
    reviewer_payload = build_operational_review(
        system_status=system_snapshot,
        state_transitions=transitions_payload["state_transitions"],
        recovery_state=recovery_payload,
    )
    risk_payload = await _build_risk_events_payload(repo, limit=events_limit)
    signals_payload = await _build_signals_payload(repo, limit=signals_limit)

    report = build_operational_readiness_report(
        system_status=system_snapshot,
        state_transitions=transitions_payload["state_transitions"],
        recovery_state=recovery_payload,
        reviewer_payload=reviewer_payload,
        risk_events=risk_payload["risk_events"],
        signals=signals_payload["signals"],
        lookback_hours=lookback_hours,
    )
    return JSONResponse(_jsonify(report))


@app.get("/operator/derivatives-snapshot")
async def operator_derivatives_snapshot(
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Canonical derivatives snapshot (Phase F.5).

    Read-only artifact exposing per-symbol derivatives availability/freshness/quality
    and fallback state for operational evaluation.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    system_snapshot = await build_system_status(_pool, derivatives_pool=_derivatives_pool)
    payload = _build_derivatives_snapshot_payload(system_snapshot)
    return JSONResponse(_jsonify(payload))


@app.get("/operator/derivatives-readiness")
async def operator_derivatives_readiness(
    lookback_hours: int = Query(default=24, ge=1, le=168),
    events_limit: int = Query(default=1000, ge=100, le=5000),
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Derivatives operational value report (Phase F.5).

    Uses the same readiness synthesis and returns only the derivatives-readiness
    block plus canonical snapshot and metrics relevant to Phase F.5 decisions.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)

    system_snapshot = await build_system_status(_pool, derivatives_pool=_derivatives_pool)
    transitions_payload = await _build_state_transitions_payload(repo, limit=500)
    recovery_payload = await _build_recovery_state_payload(repo)
    reviewer_payload = build_operational_review(
        system_status=system_snapshot,
        state_transitions=transitions_payload["state_transitions"],
        recovery_state=recovery_payload,
    )
    risk_payload = await _build_risk_events_payload(repo, limit=events_limit)
    signals_payload = await _build_signals_payload(repo, limit=500)

    readiness_report = build_operational_readiness_report(
        system_status=system_snapshot,
        state_transitions=transitions_payload["state_transitions"],
        recovery_state=recovery_payload,
        reviewer_payload=reviewer_payload,
        risk_events=risk_payload["risk_events"],
        signals=signals_payload["signals"],
        lookback_hours=lookback_hours,
    )
    derivatives_payload = _build_derivatives_snapshot_payload(system_snapshot)
    return JSONResponse(
        _jsonify(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "derivatives_readiness": readiness_report.get("derivatives_readiness", {}),
                "derivatives_metrics": {
                    k: v
                    for k, v in (readiness_report.get("metrics", {}) or {}).items()
                    if k.startswith("derivative_")
                    or k in {"advisory_usage_count", "symbols_with_real_feature_coverage"}
                },
                "derivatives_snapshot": derivatives_payload,
            }
        )
    )


@app.get("/operator/shadow-filter-report")
async def operator_shadow_filter_report(
    lookback_hours: int = Query(default=72, ge=1, le=336),
    events_limit: int = Query(default=2000, ge=100, le=10000),
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Phase G — Derivatives Shadow Filter counterfactual evaluation report.

    Shows how many ENTER signals were evaluated by the shadow filter,
    how many would have been blocked, the block rate, triggered rules breakdown,
    and trade outcome correlation (actual PnL for shadow-blocked vs shadow-passed trades).

    Read-only. Does not affect runtime, risk, or execution.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)

    now = datetime.now(timezone.utc)
    risk_payload = await _build_risk_events_payload(repo, limit=events_limit)
    all_risk_events = risk_payload.get("risk_events", [])

    cutoff = now - timedelta(hours=lookback_hours)

    def _parse_dt(v: Any) -> Optional[datetime]:
        if isinstance(v, datetime):
            return v
        if not isinstance(v, str):
            return None
        raw = v.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _in_window(event: Dict[str, Any]) -> bool:
        ts = _parse_dt(event.get("created_at"))
        if ts is None:
            return False
        if ts.tzinfo is None:
            from datetime import timezone as _tz
            ts = ts.replace(tzinfo=_tz.utc)
        return ts >= cutoff

    shadow_events = [
        e for e in all_risk_events
        if e.get("event_type") in {"DERIVATIVE_SHADOW_FILTER_PASS", "DERIVATIVE_SHADOW_FILTER_BLOCK"}
        and _in_window(e)
    ]

    evaluated_count = len(shadow_events)
    block_events = [e for e in shadow_events if e.get("event_type") == "DERIVATIVE_SHADOW_FILTER_BLOCK"]
    pass_events = [e for e in shadow_events if e.get("event_type") == "DERIVATIVE_SHADOW_FILTER_PASS"]
    block_count = len(block_events)
    pass_count = len(pass_events)
    block_rate = round(block_count / max(1, evaluated_count), 4) if evaluated_count > 0 else 0.0

    # Triggered rules frequency
    rule_frequency: Dict[str, int] = {}
    for e in block_events:
        payload = e.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        for rule in (payload.get("triggered_rules") or []):
            rule_frequency[str(rule)] = rule_frequency.get(str(rule), 0) + 1

    # Per-engine breakdown
    by_engine: Dict[str, Dict[str, Any]] = {}
    for e in shadow_events:
        eid = e.get("engine_id") or "unknown"
        entry = by_engine.setdefault(eid, {"evaluated": 0, "blocked": 0, "passed": 0, "block_rate": 0.0})
        entry["evaluated"] += 1
        if e.get("event_type") == "DERIVATIVE_SHADOW_FILTER_BLOCK":
            entry["blocked"] += 1
        else:
            entry["passed"] += 1
    for entry in by_engine.values():
        entry["block_rate"] = round(entry["blocked"] / max(1, entry["evaluated"]), 4)

    # Data quality distribution
    quality_counts: Dict[str, int] = {}
    for e in shadow_events:
        payload = e.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        q = payload.get("data_quality") or "UNKNOWN"
        quality_counts[str(q)] = quality_counts.get(str(q), 0) + 1

    # Trade outcome correlation: fetch closed trades and join with shadow events by signal_id proximity
    # Approach: for each shadow event, find the trade opened at the same engine around the same time,
    # then compare PnL between shadow-BLOCK and shadow-PASS groups.
    trades_rows = await repo.get_trades(limit=500)
    closed_trades = [t for t in trades_rows if t.get("status") == "closed" and t.get("pnl") is not None]

    def _match_trade(shadow_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find the trade opened within 5s of this shadow event for the same engine."""
        eid = shadow_event.get("engine_id")
        ts = _parse_dt(shadow_event.get("created_at"))
        if ts is None or eid is None:
            return None
        if ts.tzinfo is None:
            from datetime import timezone as _tz
            ts = ts.replace(tzinfo=_tz.utc)
        best = None
        best_delta = 10.0
        for t in closed_trades:
            if t.get("engine_id") != eid:
                continue
            t_ts = _parse_dt(t.get("opened_at"))
            if t_ts is None:
                continue
            if t_ts.tzinfo is None:
                from datetime import timezone as _tz
                t_ts = t_ts.replace(tzinfo=_tz.utc)
            delta = abs((t_ts - ts).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best = t
        return best

    block_pnls: List[float] = []
    pass_pnls: List[float] = []
    for e in block_events:
        t = _match_trade(e)
        if t is not None:
            try:
                block_pnls.append(float(t["pnl"]))
            except (TypeError, ValueError):
                pass
    for e in pass_events:
        t = _match_trade(e)
        if t is not None:
            try:
                pass_pnls.append(float(t["pnl"]))
            except (TypeError, ValueError):
                pass

    def _pnl_stats(pnls: List[float]) -> Dict[str, Any]:
        if not pnls:
            return {"count": 0, "avg_pnl": None, "win_rate": None, "total_pnl": None}
        wins = sum(1 for p in pnls if p > 0)
        return {
            "count": len(pnls),
            "avg_pnl": round(sum(pnls) / len(pnls), 4),
            "win_rate": round(wins / len(pnls), 4),
            "total_pnl": round(sum(pnls), 4),
        }

    outcome_correlation = {
        "note": (
            "Trade outcomes for signals evaluated by shadow filter. "
            "shadow_blocked = trades that WOULD have been blocked. "
            "shadow_passed = trades that passed the filter."
        ),
        "shadow_blocked_trades": _pnl_stats(block_pnls),
        "shadow_passed_trades": _pnl_stats(pass_pnls),
        "matched_trades_total": len(block_pnls) + len(pass_pnls),
    }

    return JSONResponse(
        _jsonify(
            {
                "timestamp": now.isoformat(),
                "lookback_hours": lookback_hours,
                "shadow_filter_summary": {
                    "evaluated_count": evaluated_count,
                    "block_count": block_count,
                    "pass_count": pass_count,
                    "block_rate": block_rate,
                },
                "triggered_rules_frequency": rule_frequency,
                "by_engine": by_engine,
                "data_quality_distribution": quality_counts,
                "outcome_correlation": outcome_correlation,
            }
        )
    )


@app.get("/operator/zone-comparison-report")
async def operator_zone_comparison_report(
    engine_id: Optional[str] = Query(default=None, description="Filter by engine_id"),
    since_days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    min_trades_per_variant: int = Query(default=30, ge=1, description="Minimum closed ghost trades per variant to include in stats"),
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Phase 4 — Ghost trade variant zone-comparison report.

    Compares performance of the three ghost trade variants:
      - legacy: baseline (current overlay logic, stops/targets unchanged)
      - zones_nearest: stop/target pinned to nearest qualifying zone
      - zones_weighted: stop/target pinned to highest-intensity qualifying zone

    Returns per-variant Sharpe ratio, win rate, PnL statistics, and a
    bootstrap permutation p-value for the Sharpe delta between variants.

    Read-only. Does not affect runtime, risk, or execution.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)
    stats = await repo.get_zone_comparison_stats(
        since_days=since_days,
        engine_id=engine_id,
        min_trades_per_variant=min_trades_per_variant,
    )
    return JSONResponse(_jsonify(stats))


@app.get("/operator/carry-status")
async def operator_carry_status(
    x_operator_token: Optional[str] = Header(default=None),
):
    """
    Carry Neutral BTC engine status.

    Returns current carry position (or null if none), recent carry risk events,
    and current configuration flags.
    """
    _check_operator_token(x_operator_token)
    assert _pool is not None, "Pool not initialised"
    from trading.core.config.settings import (
        CARRY_NEUTRAL_ENABLED,
        CARRY_NEUTRAL_MAX_HOLD_HOURS,
        CARRY_NEUTRAL_STAKE_USD,
        CARRY_NEUTRAL_THRESHOLD_LOWER_ANNUALIZED,
        CARRY_NEUTRAL_THRESHOLD_UPPER_ANNUALIZED,
        CARRY_NEUTRAL_VARIANT,
    )

    repo = TradingRepository(_pool, derivatives_pool=_derivatives_pool)
    now = datetime.now(timezone.utc)

    open_position = await repo.get_open_carry_position("carry_neutral_btc")
    recent_risk = await repo.get_risk_events(limit=200)
    carry_events = [
        e for e in recent_risk
        if (e.get("event_type") or "").startswith("CARRY_NEUTRAL")
    ][:20]

    heartbeats = await repo.get_heartbeats()
    carry_heartbeat = next(
        (h for h in heartbeats if h.get("engine_id") == "carry_neutral_btc"),
        None,
    )

    return JSONResponse(
        _jsonify(
            {
                "timestamp": now.isoformat(),
                "config": {
                    "carry_neutral_enabled": CARRY_NEUTRAL_ENABLED,
                    "threshold_upper_annualized": CARRY_NEUTRAL_THRESHOLD_UPPER_ANNUALIZED,
                    "threshold_lower_annualized": CARRY_NEUTRAL_THRESHOLD_LOWER_ANNUALIZED,
                    "max_hold_hours": CARRY_NEUTRAL_MAX_HOLD_HOURS,
                    "stake_usd": CARRY_NEUTRAL_STAKE_USD,
                    "variant": CARRY_NEUTRAL_VARIANT,
                },
                "open_position": open_position,
                "heartbeat": carry_heartbeat,
                "recent_carry_events": carry_events,
            }
        )
    )


@app.get("/operator/fill-stats")
async def operator_fill_stats(
    x_operator_token: Optional[str] = Header(default=None),
    engine_id: Optional[str] = Query(default="m3_eth_shadow"),
    hours: int = Query(default=24, ge=1, le=720),
):
    """
    Fill decision counters — aggregated skip events per reason for an engine.

    Returns counts since `now - hours` (default 24h). Use engine_id=m3_eth_shadow
    to validate that ZONE_SWEEP_THRESHOLD_BPS is filtering signals as expected.

    Reasons for m3_eth_shadow:
      NO_PENDING_DIRECT_BREAKOUT   — no 1h breakout pending on this bar
      TREND_FILTER_FALSE           — breakout existed but 4h trend gate blocked it
      CONSERVATIVE_FILL_NOT_MET    — breakout + trend ok but sweep threshold not reached
    """
    _check_operator_token(x_operator_token)
    repo = get_repo()
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    # Aggregate counts from tr_skip_events
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT reason, COUNT(*) AS count, MAX(created_at) AS last_seen_at
            FROM tr_skip_events
            WHERE engine_id = $1
              AND created_at >= $2
            GROUP BY reason
            ORDER BY count DESC
            """,
            engine_id,
            since,
        )

    summary = [
        {
            "reason": r["reason"],
            "count": r["count"],
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
        }
        for r in rows
    ]

    total_skipped = sum(r["count"] for r in summary)

    # Count generated signals in the same window
    async with _pool.acquire() as conn:
        sig_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS count
            FROM tr_signals
            WHERE engine_id = $1
              AND created_at >= $2
            """,
            engine_id,
            since,
        )
    signals_generated = sig_row["count"] if sig_row else 0
    total_candidates = total_skipped + signals_generated

    return JSONResponse(
        _jsonify(
            {
                "engine_id": engine_id,
                "window_hours": hours,
                "window_start": since.isoformat(),
                "window_end": now.isoformat(),
                "signals_generated": signals_generated,
                "signals_skipped": total_skipped,
                "total_candidates": total_candidates,
                "pass_rate_pct": round(signals_generated / total_candidates * 100, 1)
                if total_candidates > 0
                else None,
                "skip_breakdown": summary,
                "note": "skip_events populated since worker deploy with fill observability patch",
            }
        )
    )
