"""
Operational reviewer (Phase E) for Trading OS.

This module is intentionally analytical and read-only. It does not place orders,
change risk, edit engine state, clear pauses, or decide trades.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

FINDING_STALE_INPUT = "stale input"
FINDING_DEGRADED_COVERAGE = "degraded coverage"
FINDING_RUNTIME_INCONSISTENCY = "runtime inconsistency"
FINDING_RECOVERY_ANOMALY = "recovery anomaly"
FINDING_PROLONGED_BLOCKED_RISK = "prolonged blocked risk"
FINDING_MISSING_HEARTBEAT = "missing heartbeat"
FINDING_NO_RECENT_TRANSITIONS = "no recent transitions"
FINDING_HEALTHY_STEADY_STATE = "healthy steady-state"
FINDING_DERIVATIVES_DEGRADED = "derivatives degraded"
FINDING_DERIVATIVES_DISCONNECTED = "derivatives disconnected"
FINDING_DERIVATIVES_ADVISORY = "derivatives advisory mode"
FINDING_DERIVATIVES_FALLBACK = "derivatives fallback active"

_BLOCKED_RISK_PROLONGED_SECONDS = 10 * 60
_TRANSITIONS_STALE_SECONDS = 30 * 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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


def _severity_rank(level: str) -> int:
    if level == SEVERITY_CRITICAL:
        return 3
    if level == SEVERITY_WARNING:
        return 2
    return 1


def _is_runtime_inconsistency_event(event_type: str) -> bool:
    return event_type.startswith("STARTUP_INCONSISTENCY") or event_type.startswith("ORPHANED_")


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _add_finding(
    findings: List[Dict[str, Any]],
    *,
    finding_type: str,
    severity: str,
    summary: str,
    evidence: Dict[str, Any],
    recommendation: str,
) -> None:
    findings.append(
        {
            "finding_type": finding_type,
            "severity": severity,
            "summary": summary,
            "evidence": evidence,
            "recommendation": recommendation,
        }
    )


def build_operational_review(
    *,
    system_status: Dict[str, Any],
    state_transitions: List[Dict[str, Any]],
    recovery_state: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Build the Phase E operational review payload from canonical sources:
      - /system/status
      - /state-transitions
      - /operator/recovery-state
    """
    now = now or _now_utc()
    findings: List[Dict[str, Any]] = []

    worker = system_status.get("worker", {}) or {}
    inputs = system_status.get("inputs", {}) or {}
    risk = system_status.get("risk", {}) or {}
    derivatives = system_status.get("derivatives", {}) or {}
    engines = system_status.get("engines", []) or []
    input_symbols = inputs.get("symbols", []) or []

    stale_symbols = [
        s.get("symbol")
        for s in input_symbols
        if (s.get("quality") in {"STALE", "ABSENT"}) or (s.get("status") in {"stale", "absent"})
    ]
    if stale_symbols:
        _add_finding(
            findings,
            finding_type=FINDING_STALE_INPUT,
            severity=SEVERITY_CRITICAL,
            summary=f"Input stale/absent detected for {len(stale_symbols)} symbol(s).",
            evidence={"symbols": stale_symbols},
            recommendation="Investigate candle-collector feed and ingestion freshness before trusting signals.",
        )

    degraded_symbols = [s.get("symbol") for s in input_symbols if s.get("quality") == "DEGRADED"]
    if degraded_symbols:
        _add_finding(
            findings,
            finding_type=FINDING_DEGRADED_COVERAGE,
            severity=SEVERITY_WARNING,
            summary=f"Degraded candle coverage detected for {len(degraded_symbols)} symbol(s).",
            evidence={"symbols": degraded_symbols},
            recommendation="Monitor feed quality and verify coverage recovers; treat as advisory unless it escalates to stale.",
        )

    heartbeat_blocked = (worker.get("heartbeat_check") or {}).get("status") == "blocked"
    worker_status = str(worker.get("status", "")).lower()
    engines_missing_heartbeat = [
        e.get("engine_id")
        for e in engines
        if e.get("heartbeat_status") in {"UNKNOWN", "STOPPED"} or e.get("last_run_at") is None
    ]
    if heartbeat_blocked or worker_status == "stopped" or engines_missing_heartbeat:
        _add_finding(
            findings,
            finding_type=FINDING_MISSING_HEARTBEAT,
            severity=SEVERITY_CRITICAL,
            summary="Missing or stale heartbeat detected in worker/engines.",
            evidence={
                "worker_status": worker.get("status"),
                "heartbeat_check": worker.get("heartbeat_check"),
                "engines": engines_missing_heartbeat,
            },
            recommendation="Validate worker liveness and DB write path; confirm engine loops are progressing.",
        )

    blocked_engines: List[str] = []
    for engine in engines:
        hb_status = str(engine.get("heartbeat_status", "")).upper()
        if hb_status != "BLOCKED_RISK":
            continue
        age = _seconds_since(now, engine.get("updated_at"))
        if age is not None and age >= _BLOCKED_RISK_PROLONGED_SECONDS:
            blocked_engines.append(engine.get("engine_id"))

    worker_blocked_age = worker.get("last_loop_completed_age_seconds")
    worker_blocked_long = (
        worker_status == "blocked_risk"
        and isinstance(worker_blocked_age, (int, float))
        and float(worker_blocked_age) >= _BLOCKED_RISK_PROLONGED_SECONDS
    )
    if worker_blocked_long or blocked_engines:
        _add_finding(
            findings,
            finding_type=FINDING_PROLONGED_BLOCKED_RISK,
            severity=SEVERITY_WARNING,
            summary="Risk blocker has remained active for a prolonged interval.",
            evidence={
                "worker_status": worker.get("status"),
                "worker_blocked_age_seconds": worker_blocked_age,
                "engines": blocked_engines,
            },
            recommendation="Review active pauses and recent risk events; clear only after confirming root cause is resolved.",
        )

    active_pauses = recovery_state.get("active_pauses", []) or []
    recovery_events = recovery_state.get("recovery_events", []) or []
    inconsistency_events = [
        e for e in recovery_events if _is_runtime_inconsistency_event(str(e.get("event_type", "")))
    ]
    if inconsistency_events:
        _add_finding(
            findings,
            finding_type=FINDING_RUNTIME_INCONSISTENCY,
            severity=SEVERITY_WARNING,
            summary=f"Recovery inconsistency signals detected: {len(inconsistency_events)} event(s).",
            evidence={"event_types": [e.get("event_type") for e in inconsistency_events[:10]]},
            recommendation="Inspect startup/recovery events and validate persisted state coherence before interventions.",
        )

    if active_pauses or recovery_state.get("pending_setups_count", 0):
        _add_finding(
            findings,
            finding_type=FINDING_RECOVERY_ANOMALY,
            severity=SEVERITY_WARNING if active_pauses else SEVERITY_INFO,
            summary="Recovery surface reports active pauses or pending setups requiring operator awareness.",
            evidence={
                "active_pauses_count": recovery_state.get("active_pauses_count"),
                "pending_setups_count": recovery_state.get("pending_setups_count"),
            },
            recommendation="Use /operator/recovery-state to confirm whether pauses/pending setups are expected for current runtime state.",
        )

    latest_transition_age: Optional[float] = None
    if state_transitions:
        latest_transition_age = _seconds_since(now, state_transitions[0].get("recorded_at"))
    if not state_transitions or (
        latest_transition_age is not None and latest_transition_age >= _TRANSITIONS_STALE_SECONDS
    ):
        _add_finding(
            findings,
            finding_type=FINDING_NO_RECENT_TRANSITIONS,
            severity=SEVERITY_INFO,
            summary="No recent state transitions detected in audit log window.",
            evidence={
                "count": len(state_transitions),
                "latest_transition_age_seconds": latest_transition_age,
            },
            recommendation="Confirm if the system is in steady state; if not, verify transition recording and worker activity.",
        )

    derivatives_mode = str(derivatives.get("mode") or "").upper()
    derivatives_fallback_active = bool(derivatives.get("fallback_active"))
    derivatives_symbols = derivatives.get("symbols", []) or []
    derivatives_available_symbols = [
        s.get("symbol") for s in derivatives_symbols if isinstance(s, dict) and s.get("available")
    ]
    derivatives_context_note = "derivative_context_unavailable"
    if derivatives_mode == "DEGRADED" or derivatives_fallback_active:
        derivatives_context_note = "derivative_context_degraded"
    elif derivatives_available_symbols:
        derivatives_context_note = "derivative_context_available"
    if derivatives_mode == "DEGRADED":
        _add_finding(
            findings,
            finding_type=FINDING_DERIVATIVES_DEGRADED,
            severity=SEVERITY_WARNING,
            summary="Derivatives adapter is degraded; core remained on candle baseline.",
            evidence={
                "mode": derivatives.get("mode"),
                "fallback_reason": derivatives.get("fallback_reason"),
                "available_symbols": (derivatives.get("summary") or {}).get("available_symbols"),
            },
            recommendation="Treat derivatives as optional enrichment and inspect collector freshness/quality.",
        )
    elif derivatives_mode == "DISCONNECTED":
        _add_finding(
            findings,
            finding_type=FINDING_DERIVATIVES_DISCONNECTED,
            severity=SEVERITY_INFO,
            summary="Derivatives adapter is disconnected; runtime is operating on baseline candle inputs.",
            evidence={"mode": derivatives.get("mode"), "flags": derivatives.get("flags")},
            recommendation="Enable derivatives flags only when the isolated collector path is ready.",
        )
    elif derivatives_mode == "ADVISORY_ONLY":
        _add_finding(
            findings,
            finding_type=FINDING_DERIVATIVES_ADVISORY,
            severity=SEVERITY_INFO,
            summary="Derivatives path is active in advisory-only mode.",
            evidence={"mode": derivatives.get("mode"), "flags": derivatives.get("flags")},
            recommendation="Keep hard filters disabled in this phase and monitor advisory value.",
        )

    if derivatives_fallback_active:
        _add_finding(
            findings,
            finding_type=FINDING_DERIVATIVES_FALLBACK,
            severity=SEVERITY_INFO,
            summary="Derivatives fallback activated without stopping engine loop.",
            evidence={
                "mode": derivatives.get("mode"),
                "fallback_reason": derivatives.get("fallback_reason"),
            },
            recommendation="Continue baseline operation and observe if derivatives source recovers.",
        )

    if not [f for f in findings if f["severity"] in {SEVERITY_CRITICAL, SEVERITY_WARNING}]:
        _add_finding(
            findings,
            finding_type=FINDING_HEALTHY_STEADY_STATE,
            severity=SEVERITY_INFO,
            summary="System is in healthy steady-state with no actionable degradations.",
            evidence={
                "system_status": system_status.get("status"),
                "worker_status": worker.get("status"),
                "risk_status": risk.get("status"),
            },
            recommendation="Keep monitoring cadence and continue normal operator supervision.",
        )

    findings_sorted = sorted(findings, key=lambda f: _severity_rank(f["severity"]), reverse=True)
    active_alerts = [f for f in findings_sorted if f["severity"] in {SEVERITY_CRITICAL, SEVERITY_WARNING}]

    if any(f["severity"] == SEVERITY_CRITICAL for f in findings_sorted):
        overall_status = SEVERITY_CRITICAL
    elif any(f["severity"] == SEVERITY_WARNING for f in findings_sorted):
        overall_status = SEVERITY_WARNING
    else:
        overall_status = SEVERITY_INFO

    degraded_components = _dedupe(
        [f"input:{s}" for s in stale_symbols + degraded_symbols]
        + [f"engine:{e.get('engine_id')}" for e in engines if e.get("heartbeat_status") in {"DEGRADED", "BLOCKED_RISK", "STOPPED", "UNKNOWN"}]
        + (["worker"] if worker_status in {"degraded", "slow", "blocked_risk", "stopped"} else [])
        + (["derivatives"] if derivatives_mode == "DEGRADED" else [])
    )

    recovery_findings = [
        f
        for f in findings_sorted
        if f["finding_type"] in {FINDING_RUNTIME_INCONSISTENCY, FINDING_RECOVERY_ANOMALY}
    ]

    engine_highlights = [
        {
            "engine_id": e.get("engine_id"),
            "symbol": e.get("symbol"),
            "heartbeat_status": e.get("heartbeat_status"),
            "input_quality": (e.get("input") or {}).get("quality"),
            "has_open_position": e.get("open_position") is not None,
            "has_pending_setup": e.get("pending_setup") is not None,
            "last_run_at": e.get("last_run_at"),
        }
        for e in engines
    ]

    operator_actions = _dedupe([f["recommendation"] for f in active_alerts])[:5]
    if not operator_actions:
        operator_actions = ["No immediate action required; continue monitoring."]

    engine_summary = {
        "total": len(engines),
        "ok": sum(1 for e in engines if e.get("heartbeat_status") in {"OK", "PROCESSING"}),
        "blocked_risk": sum(1 for e in engines if e.get("heartbeat_status") == "BLOCKED_RISK"),
        "degraded": sum(1 for e in engines if e.get("heartbeat_status") in {"DEGRADED", "ERROR"}),
        "unknown_or_stopped": sum(1 for e in engines if e.get("heartbeat_status") in {"UNKNOWN", "STOPPED"}),
    }

    top_issues = [f"{f['finding_type']}: {f['summary']}" for f in active_alerts[:3]]
    if not top_issues:
        top_issues = ["healthy steady-state"]

    review_payload = {
        "reviewer_profile": "operational_analytical_non_decision_maker",
        "timestamp": now.isoformat(),
        "overall_status": overall_status,
        "system_summary": {
            "system_status": system_status.get("status"),
            "worker_status": worker.get("status"),
            "input_status": inputs.get("status"),
            "risk_status": risk.get("status"),
            "derivatives_mode": derivatives.get("mode"),
            "derivatives_adapter_status": derivatives.get("status"),
            "derivatives_fallback_active": derivatives.get("fallback_active"),
            "derivatives_context_note": derivatives_context_note,
            "derivatives_available_symbols": derivatives_available_symbols,
            "postgres": system_status.get("postgres"),
            "state_transitions_count": len(state_transitions),
            "active_pauses_count": recovery_state.get("active_pauses_count", len(active_pauses)),
            "pending_setups_count": recovery_state.get("pending_setups_count", 0),
        },
        "active_alerts": active_alerts,
        "degraded_components": degraded_components,
        "recovery_findings": recovery_findings,
        "engine_highlights": engine_highlights,
        "operator_actions": operator_actions,
        "all_findings": findings_sorted,
        "briefing": {
            "timestamp": now.isoformat(),
            "overall_status": overall_status,
            "top_issues": top_issues,
            "engine_summary": engine_summary,
            "derivatives_context_note": derivatives_context_note,
            "derivatives": {
                "mode": derivatives.get("mode"),
                "adapter_status": derivatives.get("status"),
                "fallback_active": derivatives.get("fallback_active"),
                "available_symbols": derivatives_available_symbols,
            },
            "operator_recommendations": operator_actions,
        },
    }
    return review_payload
