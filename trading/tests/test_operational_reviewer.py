from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading.core.monitoring.operational_reviewer import (
    FINDING_DERIVATIVES_ADVISORY,
    FINDING_DERIVATIVES_DEGRADED,
    FINDING_DERIVATIVES_FALLBACK,
    FINDING_HEALTHY_STEADY_STATE,
    FINDING_MISSING_HEARTBEAT,
    FINDING_NO_RECENT_TRANSITIONS,
    FINDING_PROLONGED_BLOCKED_RISK,
    FINDING_STALE_INPUT,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    build_operational_review,
)


def _base_system_status(now: datetime) -> dict:
    return {
        "status": "ok",
        "ts": now.isoformat(),
        "postgres": True,
        "worker": {
            "status": "alive",
            "heartbeat_check": {"status": "ok"},
            "last_loop_completed_age_seconds": 30.0,
        },
        "inputs": {
            "status": "ok",
            "symbols": [
                {"symbol": "BTCUSDT", "quality": "FRESH", "status": "ok"},
                {"symbol": "ETHUSDT", "quality": "FRESH", "status": "ok"},
            ],
        },
        "risk": {"status": "ok"},
        "engines": [
            {
                "engine_id": "m2_btc",
                "symbol": "BTCUSDT",
                "heartbeat_status": "OK",
                "last_run_at": (now - timedelta(seconds=30)).isoformat(),
                "updated_at": (now - timedelta(seconds=30)).isoformat(),
                "input": {"quality": "FRESH"},
                "open_position": None,
                "pending_setup": None,
            }
        ],
    }


def _base_recovery_state(now: datetime) -> dict:
    return {
        "ts": now.isoformat(),
        "active_pauses": [],
        "active_pauses_count": 0,
        "pending_setups_count": 0,
        "recovery_events": [],
    }


def test_reviewer_detects_stale_input_and_missing_heartbeat():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    system = _base_system_status(now)
    recovery = _base_recovery_state(now)

    system["worker"]["status"] = "stopped"
    system["worker"]["heartbeat_check"] = {"status": "blocked"}
    system["inputs"]["symbols"][0]["quality"] = "STALE"
    system["engines"][0]["heartbeat_status"] = "STOPPED"
    system["engines"][0]["last_run_at"] = None

    review = build_operational_review(
        system_status=system,
        state_transitions=[],
        recovery_state=recovery,
        now=now,
    )

    finding_types = {f["finding_type"] for f in review["all_findings"]}
    assert FINDING_STALE_INPUT in finding_types
    assert FINDING_MISSING_HEARTBEAT in finding_types
    assert review["overall_status"] == SEVERITY_CRITICAL


def test_reviewer_detects_prolonged_blocked_risk():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    system = _base_system_status(now)
    recovery = _base_recovery_state(now)

    system["worker"]["status"] = "blocked_risk"
    system["worker"]["last_loop_completed_age_seconds"] = 1200.0
    system["engines"][0]["heartbeat_status"] = "BLOCKED_RISK"
    system["engines"][0]["updated_at"] = (now - timedelta(minutes=25)).isoformat()

    transitions = [
        {"recorded_at": (now - timedelta(minutes=2)).isoformat(), "entity_id": "m2_btc"}
    ]
    review = build_operational_review(
        system_status=system,
        state_transitions=transitions,
        recovery_state=recovery,
        now=now,
    )

    finding_map = {f["finding_type"]: f for f in review["all_findings"]}
    assert finding_map[FINDING_PROLONGED_BLOCKED_RISK]["severity"] == SEVERITY_WARNING
    assert review["overall_status"] == SEVERITY_WARNING


def test_reviewer_marks_healthy_steady_state():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    system = _base_system_status(now)
    recovery = _base_recovery_state(now)
    transitions = [{"recorded_at": (now - timedelta(minutes=1)).isoformat()}]

    review = build_operational_review(
        system_status=system,
        state_transitions=transitions,
        recovery_state=recovery,
        now=now,
    )

    finding_types = {f["finding_type"] for f in review["all_findings"]}
    assert FINDING_HEALTHY_STEADY_STATE in finding_types
    assert FINDING_NO_RECENT_TRANSITIONS not in finding_types
    assert review["overall_status"] == SEVERITY_INFO


def test_reviewer_reports_derivatives_mode_and_fallback():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    system = _base_system_status(now)
    recovery = _base_recovery_state(now)
    transitions = [{"recorded_at": (now - timedelta(minutes=1)).isoformat()}]

    system["derivatives"] = {
        "status": "degraded",
        "mode": "DEGRADED",
        "fallback_active": True,
        "fallback_reason": "collector_degraded_recent_errors",
        "summary": {"available_symbols": 1},
    }
    review = build_operational_review(
        system_status=system,
        state_transitions=transitions,
        recovery_state=recovery,
        now=now,
    )
    finding_types = {f["finding_type"] for f in review["all_findings"]}
    assert FINDING_DERIVATIVES_DEGRADED in finding_types
    assert FINDING_DERIVATIVES_FALLBACK in finding_types
    assert "derivatives" in review["degraded_components"]

    system["derivatives"]["status"] = "ok"
    system["derivatives"]["mode"] = "ADVISORY_ONLY"
    system["derivatives"]["fallback_active"] = False
    review_advisory = build_operational_review(
        system_status=system,
        state_transitions=transitions,
        recovery_state=recovery,
        now=now,
    )
    finding_types_advisory = {f["finding_type"] for f in review_advisory["all_findings"]}
    assert FINDING_DERIVATIVES_ADVISORY in finding_types_advisory
