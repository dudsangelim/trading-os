# Derivatives Readiness (Phase F.5)

Updated: 2026-04-14

## Purpose

Evaluate whether derivatives features provide operational value in advisory-only mode, without introducing hard dependencies in Trading OS.

## Canonical Surfaces

- `GET /system/status` → `derivatives` block
- `GET /operator/derivatives-snapshot`
- `GET /operator/derivatives-readiness`
- `GET /operator/readiness-report` → `derivatives_readiness` block

## Canonical Snapshot Fields

Per symbol:
- `timestamp`
- `source_status`
- `available_features`
- `freshness`
- `quality`
- `fallback_active`
- `fallback_reason`

## Operational Value Metrics

- `derivative_availability_rate`
- `derivative_fresh_rate`
- `derivative_degraded_rate`
- `derivative_fallback_rate`
- `advisory_usage_count`
- `symbols_with_real_feature_coverage`
- `derivative_degraded_reason_frequency`

## Readiness States

- `NO_VALUE_YET`
  - low availability and/or no advisory usage evidence
- `INFRA_READY_DATA_POOR`
  - adapter reachable but freshness/quality/fallback still weak
- `ADVISORY_READY`
  - enough availability/freshness for continued advisory evaluation
- `CANDIDATE_FOR_FILTER_EXPERIMENT`
  - strong availability/freshness with repeated advisory evidence

## Guardrails

- No hard filter activation in this phase.
- No strategy/risk/sizing mutation.
- No live execution.
