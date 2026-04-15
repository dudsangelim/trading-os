# Operations Acceptance (Phase E.5)

Updated: 2026-04-14

## Purpose

Define objective paper-runtime stabilization criteria before Phase F.
This process is read-only and does not alter execution or strategy logic.

## Canonical Source Surfaces

- `GET /system/status`
- `GET /state-transitions`
- `GET /operator/recovery-state`
- `GET /risk-events`
- `GET /signals`
- `GET /operator/reviewer-briefing`
- `GET /operator/readiness-report` (consolidated output)

## Acceptance Gates

### Hard Gates (blockers)

- `worker_uptime_observed`:
  - Pass when worker is not stopped, heartbeat is not blocked, and worker loop gap is within threshold.
- `system_status_stability`:
  - Pass when Postgres is reachable, `/system/status` is not `error`, and `contract_version` is present.
- `recovery_consistency_after_restart`:
  - Pass when no recovery inconsistency events are observed in lookback window.
- `absence_unhandled_critical_error`:
  - Pass when reviewer critical alerts are zero.
- `heartbeat_consistency_by_service`:
  - Pass when all engines have valid heartbeat and `last_run_at`.

### Soft Gates (warnings, not hard blockers)

- `reviewer_briefing_quality`:
  - Pass when briefing has canonical keys.
- `no_silent_degradation_prolonged`:
  - Pass when at least one transition was observed in the recent window.
- `degraded_coverage_not_prolonged`:
  - Pass when degraded coverage proxy stays below threshold.

### Promotion Gates (for Phase F readiness)

- `paper_runtime_noise_low`:
  - Pass when stale-input and degraded-service event counts are zero in window.
- `signal_health_minimum`:
  - Pass when active (non-shadow) engines emitted signal in the last 12h.
- `reviewer_alert_budget`:
  - Pass when reviewer alert count stays within budget.

## Canonical Metrics

- `worker_loop_latency`
- `worker_loop_gap`
- `stale_input_events`
- `engine_block_events`
- `recovery_success_count`
- `recovery_inconsistency_count`
- `reviewer_alert_count`
- `service_degraded_count`
- `no_signal_duration` (per engine)
- `coverage_degraded_frequency`

## Promotion Classification

- `NOT_READY`:
  - At least one hard gate failed.
- `PAPER_STABLE_WITH_WARNINGS`:
  - Hard gates pass, but one or more soft gates failed.
- `PAPER_STABLE`:
  - Hard + soft gates pass, but one or more promotion gates failed.
- `READY_FOR_PHASE_F`:
  - All hard, soft, and promotion gates pass.

## What Blocks Phase F

- Any hard-gate failure.
- Persistent stale input / degraded-service noise beyond promotion thresholds.
- Missing minimum signal recency on active engines.

## Warning vs Real Blocker

- Warning tolerable:
  - Soft-gate failures with hard gates passing.
  - Readiness may remain `PAPER_STABLE_WITH_WARNINGS`.
- Real blocker:
  - Any hard-gate failure (`NOT_READY`).
  - Promotion-gate failure keeps system in paper stabilization (`PAPER_STABLE`).

## Operator Interpretation Guide

- `NOT_READY`: keep in paper, resolve blockers before considering promotion.
- `PAPER_STABLE_WITH_WARNINGS`: paper can continue; reduce warnings and reassess.
- `PAPER_STABLE`: system is stable in paper; still not promoted to Phase F.
- `READY_FOR_PHASE_F`: eligible to start controlled Phase F planning.

## Minimal Operational Validation Scenarios

1. Worker restart:
   - Restart worker process.
   - Check `worker_uptime_observed`, recent transitions, and readiness status.
2. Restart with pending setup:
   - Ensure a pending setup exists, restart worker, verify recovery consistency and no unexpected inconsistency events.
3. Restart with active pause:
   - Keep active pause, restart worker, verify pause state consistency and recovery findings.
4. Input stale:
   - Stop/update candle feed temporarily.
   - Verify stale-input events, reviewer alerts, and readiness downgrade.
5. Service degraded:
   - Simulate degraded worker state (slow/degraded loop condition).
   - Verify `service_degraded_count` and gate impact.
6. Reviewer under anomaly:
   - Inject a condition that triggers reviewer CRITICAL.
   - Verify `absence_unhandled_critical_error` fails and status becomes `NOT_READY`.
7. Prolonged absence of transitions:
   - Keep runtime without transitions beyond threshold.
   - Verify `no_silent_degradation_prolonged` soft-gate failure.

## Why Phase F Depends On E.5

Derivatives integration increases runtime coupling and failure modes. Phase E.5
ensures the current paper runtime has measurable stability, low silent degradation
risk, and clear operator interpretation before adding new complexity.
