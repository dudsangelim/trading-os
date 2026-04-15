from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading.core.monitoring.operational_readiness import (
    DERIV_READINESS_ADVISORY_READY,
    DERIV_READINESS_NO_VALUE_YET,
    READINESS_NOT_READY,
    READINESS_PAPER_STABLE_WITH_WARNINGS,
    READINESS_READY_FOR_PHASE_F,
    build_operational_readiness_report,
)
from trading.core.monitoring.operational_reviewer import SEVERITY_CRITICAL


def _base_system_status(now: datetime) -> dict:
    return {
        "status": "ok",
        "ts": now.isoformat(),
        "contract_version": "C.1",
        "postgres": True,
        "worker": {
            "status": "alive",
            "last_loop_completed_age_seconds": 20.0,
            "heartbeat_check": {"status": "ok"},
        },
        "inputs": {
            "status": "ok",
            "symbols": [
                {"symbol": "BTCUSDT", "quality": "FRESH"},
                {"symbol": "ETHUSDT", "quality": "FRESH"},
                {"symbol": "SOLUSDT", "quality": "FRESH"},
            ],
        },
        "engines": [
            {
                "engine_id": "m1_eth",
                "heartbeat_status": "OK",
                "last_run_at": (now - timedelta(minutes=1)).isoformat(),
            },
            {
                "engine_id": "m2_btc",
                "heartbeat_status": "OK",
                "last_run_at": (now - timedelta(minutes=1)).isoformat(),
            },
            {
                "engine_id": "m3_sol",
                "heartbeat_status": "OK",
                "last_run_at": (now - timedelta(minutes=1)).isoformat(),
            },
            {
                "engine_id": "m3_eth_shadow",
                "heartbeat_status": "OK",
                "last_run_at": (now - timedelta(minutes=1)).isoformat(),
            },
        ],
        "derivatives": {
            "status": "ok",
            "mode": "ADVISORY_ONLY",
            "fallback_active": False,
            "summary": {"available_symbols": 3},
        },
    }


def _base_recovery_state() -> dict:
    return {
        "active_pauses_count": 0,
        "pending_setups_count": 0,
    }


def _base_reviewer(now: datetime) -> dict:
    return {
        "active_alerts": [],
        "briefing": {
            "timestamp": now.isoformat(),
            "overall_status": "INFO",
            "top_issues": ["healthy steady-state"],
            "engine_summary": {"total": 4},
            "operator_recommendations": ["continue monitoring"],
        },
    }


def _base_signals(now: datetime) -> list[dict]:
    return [
        {"engine_id": "m1_eth", "created_at": (now - timedelta(hours=1)).isoformat()},
        {"engine_id": "m2_btc", "created_at": (now - timedelta(hours=1)).isoformat()},
        {"engine_id": "m3_sol", "created_at": (now - timedelta(hours=1)).isoformat()},
    ]


def test_readiness_not_ready_on_critical_alert():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    reviewer = _base_reviewer(now)
    reviewer["active_alerts"] = [{"severity": SEVERITY_CRITICAL, "finding_type": "missing heartbeat"}]

    report = build_operational_readiness_report(
        system_status=_base_system_status(now),
        state_transitions=[{"recorded_at": (now - timedelta(minutes=2)).isoformat(), "to_status": "OK"}],
        recovery_state=_base_recovery_state(),
        reviewer_payload=reviewer,
        risk_events=[],
        signals=_base_signals(now),
        now=now,
    )
    assert report["current_readiness_status"] == READINESS_NOT_READY


def test_readiness_with_warnings_when_silent_degradation_gate_fails():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    report = build_operational_readiness_report(
        system_status=_base_system_status(now),
        state_transitions=[{"recorded_at": (now - timedelta(hours=2)).isoformat(), "to_status": "OK"}],
        recovery_state=_base_recovery_state(),
        reviewer_payload=_base_reviewer(now),
        risk_events=[],
        signals=_base_signals(now),
        now=now,
    )
    assert report["current_readiness_status"] == READINESS_PAPER_STABLE_WITH_WARNINGS


def test_readiness_ready_for_phase_f_when_all_gates_pass():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    report = build_operational_readiness_report(
        system_status=_base_system_status(now),
        state_transitions=[{"recorded_at": (now - timedelta(minutes=2)).isoformat(), "to_status": "OK"}],
        recovery_state=_base_recovery_state(),
        reviewer_payload=_base_reviewer(now),
        risk_events=[],
        signals=_base_signals(now),
        now=now,
    )
    assert report["current_readiness_status"] == READINESS_READY_FOR_PHASE_F
    assert report["metrics"]["derivatives_mode"] == "ADVISORY_ONLY"
    assert report["derivatives_readiness"]["status"] in {
        DERIV_READINESS_ADVISORY_READY,
        DERIV_READINESS_NO_VALUE_YET,
    }


def test_derivatives_readiness_no_value_when_unavailable():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    system = _base_system_status(now)
    system["derivatives"]["mode"] = "DISCONNECTED"
    system["derivatives"]["fallback_active"] = True
    system["derivatives"]["symbols"] = [
        {"symbol": "BTCUSDT", "available": False, "freshness": "ABSENT", "available_features": []},
        {"symbol": "ETHUSDT", "available": False, "freshness": "ABSENT", "available_features": []},
    ]
    report = build_operational_readiness_report(
        system_status=system,
        state_transitions=[{"recorded_at": (now - timedelta(minutes=2)).isoformat(), "to_status": "OK"}],
        recovery_state=_base_recovery_state(),
        reviewer_payload=_base_reviewer(now),
        risk_events=[],
        signals=_base_signals(now),
        now=now,
    )
    assert report["derivatives_readiness"]["status"] == DERIV_READINESS_NO_VALUE_YET


# ---------------------------------------------------------------------------
# Phase G — shadow filter metrics in readiness report
# ---------------------------------------------------------------------------

def _shadow_event(engine_id: str, verdict: str, now: datetime) -> dict:
    event_type = (
        "DERIVATIVE_SHADOW_FILTER_BLOCK" if verdict == "BLOCK" else "DERIVATIVE_SHADOW_FILTER_PASS"
    )
    return {
        "engine_id": engine_id,
        "event_type": event_type,
        "description": f"shadow_filter:{verdict}:BTCUSDT",
        "payload": {
            "engine_id": engine_id,
            "verdict": verdict,
            "triggered_rules": ["OI_DELTA_ADVERSE", "FUNDING_RATE_EXTREME"] if verdict == "BLOCK" else [],
            "data_quality": "GOOD",
        },
        "created_at": now.isoformat(),
    }


def test_shadow_filter_metrics_in_readiness_report():
    from trading.core.monitoring.operational_readiness import DERIV_READINESS_SHADOW_FILTER_ACTIVE
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    shadow_events = (
        [_shadow_event("m2_btc", "BLOCK", now) for _ in range(4)]
        + [_shadow_event("m2_btc", "PASS", now) for _ in range(10)]
    )
    report = build_operational_readiness_report(
        system_status=_base_system_status(now),
        state_transitions=[{"recorded_at": (now - timedelta(minutes=2)).isoformat(), "to_status": "OK"}],
        recovery_state=_base_recovery_state(),
        reviewer_payload=_base_reviewer(now),
        risk_events=shadow_events,
        signals=_base_signals(now),
        now=now,
    )
    metrics = report["metrics"]
    assert metrics["shadow_filter_evaluated_count"] == 14
    assert metrics["shadow_filter_block_count"] == 4
    assert metrics["shadow_filter_pass_count"] == 10
    assert abs(metrics["shadow_filter_block_rate"] - round(4 / 14, 4)) < 0.001
    assert "m2_btc" in metrics["shadow_filter_by_engine"]
    assert metrics["shadow_filter_by_engine"]["m2_btc"]["evaluated"] == 14

    dr = report["derivatives_readiness"]
    assert dr["shadow_filter_evaluated_count"] == 14
    assert dr["shadow_filter_block_rate"] == metrics["shadow_filter_block_rate"]

    # 14 evaluations >= 10 threshold → SHADOW_FILTER_ACTIVE
    assert dr["status"] == DERIV_READINESS_SHADOW_FILTER_ACTIVE


def test_shadow_filter_zero_when_no_events():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    report = build_operational_readiness_report(
        system_status=_base_system_status(now),
        state_transitions=[{"recorded_at": (now - timedelta(minutes=2)).isoformat(), "to_status": "OK"}],
        recovery_state=_base_recovery_state(),
        reviewer_payload=_base_reviewer(now),
        risk_events=[],
        signals=_base_signals(now),
        now=now,
    )
    assert report["metrics"]["shadow_filter_evaluated_count"] == 0
    assert report["metrics"]["shadow_filter_block_rate"] == 0.0
    assert report["derivatives_readiness"]["shadow_filter_evaluated_count"] == 0
