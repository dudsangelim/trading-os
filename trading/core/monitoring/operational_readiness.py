"""
Phase E.5 — Operational stabilization and promotion gating for Trading OS.

This module is read-only analytics. It does not mutate runtime, engines, risk,
or execution flow.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config.settings import ENGINE_CONFIGS, HEARTBEAT_STALE_MINUTES
from .operational_reviewer import SEVERITY_CRITICAL, SEVERITY_WARNING


READINESS_NOT_READY = "NOT_READY"
READINESS_PAPER_STABLE = "PAPER_STABLE"
READINESS_PAPER_STABLE_WITH_WARNINGS = "PAPER_STABLE_WITH_WARNINGS"
READINESS_READY_FOR_PHASE_F = "READY_FOR_PHASE_F"

DERIV_READINESS_NO_VALUE_YET = "NO_VALUE_YET"
DERIV_READINESS_INFRA_READY_DATA_POOR = "INFRA_READY_DATA_POOR"
DERIV_READINESS_ADVISORY_READY = "ADVISORY_READY"
DERIV_READINESS_CANDIDATE_FOR_FILTER_EXPERIMENT = "CANDIDATE_FOR_FILTER_EXPERIMENT"
DERIV_READINESS_SHADOW_FILTER_ACTIVE = "SHADOW_FILTER_ACTIVE"


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _seconds_since(now: datetime, value: Any) -> Optional[float]:
    ts = _parse_dt(value)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds())


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, dict)]


def _recent(rows: Iterable[Dict[str, Any]], now: datetime, lookback_hours: int, ts_field: str) -> List[Dict[str, Any]]:
    cutoff = now - timedelta(hours=lookback_hours)
    out: List[Dict[str, Any]] = []
    for row in rows:
        ts = _parse_dt(row.get(ts_field))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            out.append(row)
    return out


def _reviewer_alert_stats(review_payload: Dict[str, Any]) -> Tuple[int, int]:
    alerts = _as_list(review_payload.get("active_alerts"))
    critical_count = sum(1 for a in alerts if a.get("severity") == SEVERITY_CRITICAL)
    return len(alerts), critical_count


def _latest_signal_by_engine(signals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in signals:
        engine_id = row.get("engine_id")
        if not engine_id:
            continue
        existing = latest.get(engine_id)
        current_ts = _parse_dt(row.get("created_at")) or _parse_dt(row.get("bar_timestamp"))
        existing_ts = None
        if existing is not None:
            existing_ts = _parse_dt(existing.get("created_at")) or _parse_dt(existing.get("bar_timestamp"))
        if existing is None or (current_ts and (existing_ts is None or current_ts > existing_ts)):
            latest[engine_id] = row
    return latest


def _derive_derivatives_readiness(
    *,
    availability_rate: float,
    fresh_rate: float,
    degraded_rate: float,
    fallback_rate: float,
    advisory_usage_count: int,
    symbols_with_real_feature_coverage: List[str],
    shadow_filter_evaluated_count: int = 0,
) -> Dict[str, Any]:
    # Phase G: shadow filter active is the highest state — requires sufficient evaluation samples
    if shadow_filter_evaluated_count >= 10:
        return {
            "status": DERIV_READINESS_SHADOW_FILTER_ACTIVE,
            "reason": (
                f"Shadow filter is collecting counterfactual data "
                f"({shadow_filter_evaluated_count} evaluations)."
            ),
        }
    if availability_rate < 0.2 and advisory_usage_count == 0:
        return {
            "status": DERIV_READINESS_NO_VALUE_YET,
            "reason": "Low availability and no advisory usage observed.",
        }
    if availability_rate >= 0.2 and (
        fresh_rate < 0.5 or degraded_rate > 0.5 or fallback_rate > 0.6
    ):
        return {
            "status": DERIV_READINESS_INFRA_READY_DATA_POOR,
            "reason": "Adapter reachable but freshness/quality/fallback metrics are still weak.",
        }
    if (
        availability_rate >= 0.8
        and fresh_rate >= 0.75
        and degraded_rate <= 0.25
        and fallback_rate <= 0.2
        and advisory_usage_count >= 3
        and len(symbols_with_real_feature_coverage) >= 2
    ):
        return {
            "status": DERIV_READINESS_CANDIDATE_FOR_FILTER_EXPERIMENT,
            "reason": "Availability, freshness and advisory usage are consistently strong.",
        }
    if availability_rate >= 0.5 and fresh_rate >= 0.6 and advisory_usage_count > 0:
        return {
            "status": DERIV_READINESS_ADVISORY_READY,
            "reason": "Derivatives context is sufficiently available for continued advisory evaluation.",
        }
    return {
        "status": DERIV_READINESS_NO_VALUE_YET,
        "reason": "Derivatives data is not yet consistent enough to infer operational value.",
    }


def build_operational_readiness_report(
    *,
    system_status: Dict[str, Any],
    state_transitions: List[Dict[str, Any]],
    recovery_state: Dict[str, Any],
    reviewer_payload: Dict[str, Any],
    risk_events: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    lookback_hours: int = 24,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)

    worker = system_status.get("worker", {}) or {}
    inputs = system_status.get("inputs", {}) or {}
    engines = _as_list(system_status.get("engines"))
    input_symbols = _as_list(inputs.get("symbols"))
    derivatives = system_status.get("derivatives", {}) or {}
    derivative_symbols = _as_list(derivatives.get("symbols"))

    recent_transitions = _recent(state_transitions, now, lookback_hours, "recorded_at")
    recent_risk_events = _recent(risk_events, now, lookback_hours, "created_at")
    recent_signals = _recent(signals, now, lookback_hours, "created_at")

    reviewer_alert_count, reviewer_critical_count = _reviewer_alert_stats(reviewer_payload)

    worker_loop_gap = worker.get("last_loop_completed_age_seconds")
    worker_loop_latency = worker_loop_gap
    stale_input_events = sum(
        1
        for e in recent_risk_events
        if e.get("event_type") in {"KILL_STALE_DATA", "NO_VALID_INPUT"}
    )
    engine_block_events = sum(1 for e in recent_risk_events if e.get("event_type") == "ENGINE_BLOCKED")
    recovery_success_count = sum(
        1
        for e in recent_risk_events
        if str(e.get("event_type", "")).startswith(("STARTUP_PAUSE_AUTO_RESUMED", "OPERATOR_PAUSE_CLEARED", "OPERATOR_PENDING_RESET"))
    )
    recovery_inconsistency_count = sum(
        1
        for e in recent_risk_events
        if str(e.get("event_type", "")).startswith(("STARTUP_INCONSISTENCY", "ORPHANED_"))
    )
    service_degraded_count = sum(
        1
        for t in recent_transitions
        if str(t.get("to_status", "")).upper() in {"DEGRADED", "STOPPED", "ERROR"}
    )
    degraded_symbol_count = sum(1 for s in input_symbols if s.get("quality") == "DEGRADED")
    coverage_degraded_frequency = (
        round(degraded_symbol_count / max(1, len(input_symbols)), 4) if input_symbols else 0.0
    )
    derivative_available_count = sum(1 for s in derivative_symbols if s.get("available"))
    derivative_fresh_count = sum(1 for s in derivative_symbols if s.get("freshness") == "FRESH")
    derivative_degraded_count = sum(
        1 for s in derivative_symbols if s.get("freshness") in {"DEGRADED", "STALE", "ABSENT"}
    )
    derivative_availability_rate = (
        round(derivative_available_count / max(1, len(derivative_symbols)), 4)
        if derivative_symbols else 0.0
    )
    derivative_fresh_rate = (
        round(derivative_fresh_count / max(1, len(derivative_symbols)), 4)
        if derivative_symbols else 0.0
    )
    derivative_degraded_rate = (
        round(derivative_degraded_count / max(1, len(derivative_symbols)), 4)
        if derivative_symbols else 0.0
    )
    fallback_events = [
        e for e in recent_risk_events if e.get("event_type") == "DERIVATIVES_FALLBACK_ACTIVE"
    ]
    degraded_events = [
        e for e in recent_risk_events if e.get("event_type") == "DERIVATIVES_DEGRADED"
    ]
    loop_events = [
        e for e in recent_risk_events if e.get("event_type") in {"WORKER_LOOP_COMPLETED", "WORKER_LOOP_DEGRADED"}
    ]
    derivative_fallback_rate = round(
        len(fallback_events) / max(1, len(loop_events)),
        4,
    )
    advisory_usage_count = sum(
        1 for e in recent_risk_events if e.get("event_type") == "DERIVATIVE_CONTEXT_AVAILABLE"
    )
    symbols_with_real_feature_coverage = sorted(
        {
            s.get("symbol")
            for s in derivative_symbols
            if s.get("available") and len(s.get("available_features") or []) > 0
        }
    )
    degraded_reason_frequency: Dict[str, int] = {}
    for event in degraded_events:
        raw_payload = event.get("payload") or {}
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except Exception:
                raw_payload = {}
        payload: Dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        reason = payload.get("fallback_reason") or event.get("description") or "unknown"
        degraded_reason_frequency[str(reason)] = degraded_reason_frequency.get(str(reason), 0) + 1

    # Phase G — shadow filter metrics
    shadow_filter_events = [
        e for e in recent_risk_events
        if e.get("event_type") in {"DERIVATIVE_SHADOW_FILTER_PASS", "DERIVATIVE_SHADOW_FILTER_BLOCK"}
    ]
    shadow_filter_evaluated_count = len(shadow_filter_events)
    shadow_filter_block_count = sum(
        1 for e in shadow_filter_events if e.get("event_type") == "DERIVATIVE_SHADOW_FILTER_BLOCK"
    )
    shadow_filter_pass_count = shadow_filter_evaluated_count - shadow_filter_block_count
    shadow_filter_block_rate = round(
        shadow_filter_block_count / max(1, shadow_filter_evaluated_count), 4
    ) if shadow_filter_evaluated_count > 0 else 0.0

    # Per-engine shadow filter breakdown
    shadow_filter_by_engine: Dict[str, Dict[str, int]] = {}
    for e in shadow_filter_events:
        eid = e.get("engine_id") or "unknown"
        entry = shadow_filter_by_engine.setdefault(eid, {"evaluated": 0, "blocked": 0, "passed": 0})
        entry["evaluated"] += 1
        if e.get("event_type") == "DERIVATIVE_SHADOW_FILTER_BLOCK":
            entry["blocked"] += 1
        else:
            entry["passed"] += 1

    derivatives_readiness = _derive_derivatives_readiness(
        availability_rate=derivative_availability_rate,
        fresh_rate=derivative_fresh_rate,
        degraded_rate=derivative_degraded_rate,
        fallback_rate=derivative_fallback_rate,
        advisory_usage_count=advisory_usage_count,
        symbols_with_real_feature_coverage=symbols_with_real_feature_coverage,
        shadow_filter_evaluated_count=shadow_filter_evaluated_count,
    )

    latest_signals = _latest_signal_by_engine(signals)
    no_signal_duration: Dict[str, Optional[float]] = {}
    for engine_id, cfg in ENGINE_CONFIGS.items():
        row = latest_signals.get(engine_id)
        if row is None:
            no_signal_duration[engine_id] = None
            continue
        no_signal_duration[engine_id] = _seconds_since(
            now,
            row.get("created_at") or row.get("bar_timestamp"),
        )

    heartbeat_unknown_or_stopped = [
        e.get("engine_id")
        for e in engines
        if e.get("heartbeat_status") in {"UNKNOWN", "STOPPED"}
    ]
    heartbeat_missing_last_run = [
        e.get("engine_id")
        for e in engines
        if e.get("last_run_at") is None
    ]
    latest_transition_age = (
        _seconds_since(now, recent_transitions[0].get("recorded_at"))
        if recent_transitions else None
    )

    metrics = {
        "window_hours": lookback_hours,
        "worker_loop_latency": worker_loop_latency,
        "worker_loop_gap": worker_loop_gap,
        "stale_input_events": stale_input_events,
        "engine_block_events": engine_block_events,
        "recovery_success_count": recovery_success_count,
        "recovery_inconsistency_count": recovery_inconsistency_count,
        "reviewer_alert_count": reviewer_alert_count,
        "service_degraded_count": service_degraded_count,
        "no_signal_duration": no_signal_duration,
        "coverage_degraded_frequency": coverage_degraded_frequency,
        "derivatives_mode": derivatives.get("mode"),
        "derivatives_adapter_status": derivatives.get("status"),
        "derivatives_fallback_active": derivatives.get("fallback_active"),
        "derivatives_available_symbols": (derivatives.get("summary") or {}).get("available_symbols"),
        "derivative_availability_rate": derivative_availability_rate,
        "derivative_fresh_rate": derivative_fresh_rate,
        "derivative_degraded_rate": derivative_degraded_rate,
        "derivative_fallback_rate": derivative_fallback_rate,
        "advisory_usage_count": advisory_usage_count,
        "symbols_with_real_feature_coverage": symbols_with_real_feature_coverage,
        "derivative_degraded_reason_frequency": degraded_reason_frequency,
        # Phase G — shadow filter metrics
        "shadow_filter_evaluated_count": shadow_filter_evaluated_count,
        "shadow_filter_block_count": shadow_filter_block_count,
        "shadow_filter_pass_count": shadow_filter_pass_count,
        "shadow_filter_block_rate": shadow_filter_block_rate,
        "shadow_filter_by_engine": shadow_filter_by_engine,
    }

    required_briefing_keys = {
        "timestamp",
        "overall_status",
        "top_issues",
        "engine_summary",
        "operator_recommendations",
    }
    briefing = reviewer_payload.get("briefing", {})
    briefing_quality_ok = isinstance(briefing, dict) and required_briefing_keys.issubset(briefing.keys())

    hard_gates = [
        {
            "gate": "worker_uptime_observed",
            "passed": (
                worker.get("heartbeat_check", {}).get("status") != "blocked"
                and str(worker.get("status", "")).lower() != "stopped"
                and isinstance(worker_loop_gap, (int, float))
                and float(worker_loop_gap) <= HEARTBEAT_STALE_MINUTES * 60
            ),
            "threshold": f"worker loop gap <= {HEARTBEAT_STALE_MINUTES * 60}s and no stopped/blocked heartbeat",
            "category": "hard",
            "blocker_if_failed": True,
        },
        {
            "gate": "system_status_stability",
            "passed": (
                bool(system_status.get("postgres"))
                and system_status.get("status") != "error"
                and isinstance(system_status.get("contract_version"), str)
            ),
            "threshold": "postgres=true, status!=error, contract_version present",
            "category": "hard",
            "blocker_if_failed": True,
        },
        {
            "gate": "recovery_consistency_after_restart",
            "passed": recovery_inconsistency_count == 0,
            "threshold": "recovery_inconsistency_count == 0 in lookback window",
            "category": "hard",
            "blocker_if_failed": True,
        },
        {
            "gate": "absence_unhandled_critical_error",
            "passed": reviewer_critical_count == 0,
            "threshold": "reviewer critical alerts == 0",
            "category": "hard",
            "blocker_if_failed": True,
        },
        {
            "gate": "heartbeat_consistency_by_service",
            "passed": not heartbeat_unknown_or_stopped and not heartbeat_missing_last_run,
            "threshold": "all engines with heartbeat_status not UNKNOWN/STOPPED and last_run_at present",
            "category": "hard",
            "blocker_if_failed": True,
        },
    ]

    soft_gates = [
        {
            "gate": "reviewer_briefing_quality",
            "passed": briefing_quality_ok,
            "threshold": "briefing contains canonical keys",
            "category": "soft",
            "blocker_if_failed": False,
        },
        {
            "gate": "no_silent_degradation_prolonged",
            "passed": bool(recent_transitions) and (latest_transition_age is not None and latest_transition_age <= 30 * 60),
            "threshold": "state transition observed within last 30m",
            "category": "soft",
            "blocker_if_failed": False,
        },
        {
            "gate": "degraded_coverage_not_prolonged",
            "passed": coverage_degraded_frequency <= 0.34,
            "threshold": "coverage_degraded_frequency <= 0.34 (snapshot proxy)",
            "category": "soft",
            "blocker_if_failed": False,
        },
    ]

    promotion_gates = [
        {
            "gate": "paper_runtime_noise_low",
            "passed": service_degraded_count == 0 and stale_input_events == 0,
            "threshold": "service_degraded_count == 0 and stale_input_events == 0 in lookback",
            "category": "promotion",
            "blocker_if_failed": False,
        },
        {
            "gate": "signal_health_minimum",
            "passed": all(
                (duration is not None and duration <= 12 * 3600)
                for engine_id, duration in no_signal_duration.items()
                if not ENGINE_CONFIGS[engine_id].signal_only
            ),
            "threshold": "active engines must emit signal within 12h",
            "category": "promotion",
            "blocker_if_failed": False,
        },
        {
            "gate": "reviewer_alert_budget",
            "passed": reviewer_alert_count <= 2,
            "threshold": "reviewer_alert_count <= 2",
            "category": "promotion",
            "blocker_if_failed": False,
        },
    ]

    all_gates = hard_gates + soft_gates + promotion_gates
    hard_failures = [g for g in hard_gates if not g["passed"]]
    soft_failures = [g for g in soft_gates if not g["passed"]]
    promotion_failures = [g for g in promotion_gates if not g["passed"]]

    if hard_failures:
        readiness_status = READINESS_NOT_READY
    elif soft_failures:
        readiness_status = READINESS_PAPER_STABLE_WITH_WARNINGS
    elif promotion_failures:
        readiness_status = READINESS_PAPER_STABLE
    else:
        readiness_status = READINESS_READY_FOR_PHASE_F

    blocking_issues = [
        {"gate": g["gate"], "threshold": g["threshold"], "category": g["category"]}
        for g in hard_failures + soft_failures
    ]

    recent_incidents = [
        {
            "event_type": e.get("event_type"),
            "engine_id": e.get("engine_id"),
            "description": e.get("description"),
            "created_at": e.get("created_at"),
        }
        for e in recent_risk_events[:20]
        if e.get("event_type") in {
            "KILL_STALE_DATA",
            "NO_VALID_INPUT",
            "ENGINE_BLOCKED",
            "STARTUP_INCONSISTENCY",
            "ORPHANED_POSITION",
        }
    ]

    recommendation = (
        "Proceed to Phase F planning with controlled scope."
        if readiness_status == READINESS_READY_FOR_PHASE_F
        else (
            "Remain in paper stabilization; address blocking gates before promotion."
            if readiness_status == READINESS_NOT_READY
            else "Remain in paper; clear warnings and re-evaluate readiness."
        )
    )

    return {
        "timestamp": now.isoformat(),
        "current_readiness_status": readiness_status,
        "blocking_issues": blocking_issues,
        "recent_incidents": recent_incidents,
        "recovery_health": {
            "active_pauses_count": recovery_state.get("active_pauses_count"),
            "pending_setups_count": recovery_state.get("pending_setups_count"),
            "recovery_success_count": recovery_success_count,
            "recovery_inconsistency_count": recovery_inconsistency_count,
        },
        "signal_health": {
            "no_signal_duration": no_signal_duration,
            "signals_seen_in_window": len(recent_signals),
            "coverage_degraded_frequency": coverage_degraded_frequency,
        },
        "service_health": {
            "worker_status": worker.get("status"),
            "system_status": system_status.get("status"),
            "reviewer_alert_count": reviewer_alert_count,
            "stale_input_events": stale_input_events,
            "engine_block_events": engine_block_events,
            "service_degraded_count": service_degraded_count,
            "derivatives_mode": derivatives.get("mode"),
            "derivatives_adapter_status": derivatives.get("status"),
            "derivatives_fallback_active": derivatives.get("fallback_active"),
        },
        "derivatives_readiness": {
            "status": derivatives_readiness["status"],
            "reason": derivatives_readiness["reason"],
            "derivative_availability_rate": derivative_availability_rate,
            "derivative_fresh_rate": derivative_fresh_rate,
            "derivative_degraded_rate": derivative_degraded_rate,
            "derivative_fallback_rate": derivative_fallback_rate,
            "advisory_usage_count": advisory_usage_count,
            "symbols_with_real_feature_coverage": symbols_with_real_feature_coverage,
            "degraded_reason_frequency": degraded_reason_frequency,
            "canonical_snapshot": derivatives.get("canonical_snapshot", []),
            # Phase G — shadow filter summary
            "shadow_filter_evaluated_count": shadow_filter_evaluated_count,
            "shadow_filter_block_count": shadow_filter_block_count,
            "shadow_filter_pass_count": shadow_filter_pass_count,
            "shadow_filter_block_rate": shadow_filter_block_rate,
        },
        "metrics": metrics,
        "acceptance_gates": all_gates,
        "recommendation": recommendation,
    }
