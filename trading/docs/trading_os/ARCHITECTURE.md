# Trading OS Architecture

Updated: 2026-04-14

## Canonical Runtime

- Core: `agents/stack/trading`
- Official 1m market data source: `candle-collector` via `el_candles_1m`
- Operational supervision: OpenClaw
- Derivatives path: optional read-only adapter (Phase F), no hard runtime dependency

## Phase B Operational Topology

1. `apps/worker/main.py` runs the minute loop.
2. `core/data/candle_reader.py` reads `el_candles_1m` and optional `tr_candles_4h`.
3. `core/risk/circuit_breakers.py` evaluates stale data, daily DD, bankroll minimum and one-position constraints.
4. `core/storage/repository.py` persists heartbeats, runtime state, pending setups, pauses, trades, equity and risk events.
5. `core/monitoring/healthcheck.py` synthesizes runtime status from Postgres, runtime snapshots, heartbeats, candles, positions and risk events.
6. `apps/api/main.py` exposes read-only operational visibility, now including `GET /system/status`.
7. `core/monitoring/operational_reviewer.py` synthesizes operational review + operator briefing from canonical read surfaces.
8. `core/monitoring/operational_readiness.py` synthesizes stabilization metrics and promotion gates from canonical read surfaces.
9. `core/storage/repository.py` includes a formal derivatives adapter (`get_derivatives_feature_snapshot`) that reads isolated derivatives DB in read-only mode and emits optional feature snapshots.

## Persistence Model

- `tr_engine_runtime_state`
  - one row per engine
  - holds `last_bar_seen`, `last_processed_at`, minimal serialised engine state and status/detail
- `tr_pending_setups`
  - one active pending setup slot per engine
  - stores simple serialised pending multi-bar setup data with reference and expiry timestamps
- `tr_runtime_pauses`
  - stores active/resumed pause state for worker-global and per-engine blockers
- `tr_worker_runtime_state`
  - stores last loop start/completion, last freshness check and summarised worker state

## Recovery Design

- Worker loop lifecycle remains persisted in `tr_risk_events` for audit chronology.
- Engine service state remains visible in `tr_engine_heartbeat`, but recovery now reads `tr_engine_runtime_state`.
- Operational blockers are auditably described through both `tr_risk_events.payload` and `tr_runtime_pauses`.
- API status is synthesized from persisted state, not from in-process worker memory.
- Startup recovery order is:
  1. load worker runtime snapshot
  2. initialize engines
  3. rehydrate open positions
  4. rehydrate engine runtime state
  5. rehydrate pending setups
  6. rehydrate active pauses
  7. validate consistency against current candle context

## Phase C Formal Interfaces

### candle-collector → Trading Core (candle_contract.py)

- Contract file: `core/data/candle_contract.py`
- Source table: `el_candles_1m` (read-only from Trading Core perspective)
- Expected columns: `symbol, open_time, open, high, low, close, volume`
- Freshness requirement: last candle within `FRESHNESS_THRESHOLD_MINUTES` (= `STALE_DATA_THRESHOLD_MINUTES`)
- Coverage requirement: `MINIMUM_COVERAGE_IN_WINDOW` candles in last `COVERAGE_WINDOW_MINUTES`
- Input quality states: `FRESH | DEGRADED | STALE | ABSENT`
- Degraded policy: `CONTINUE` (advisory only — does not block trading)
- Validator: `validate_candle_inputs(reader, symbols, now)` used by healthcheck

### worker → API/status (status_model.py)

- Contract file: `core/monitoring/status_model.py`
- Schema version: `CONTRACT_VERSION` (broadcasted in every `/system/status` response)
- Endpoint: `GET /system/status` — stable operations contract
- `build_system_status()` in `healthcheck.py` returns data conforming to `SystemStatusSnapshot`
- OpenClaw consumers must parse `contract_version` and alert on unexpected values

### worker → storage (interfaces.py)

- Interface file: `core/storage/interfaces.py`
- Logical groups: `IOperationalEvents | IEngineRuntimeState | IWorkerRuntimeState | IPendingSetups | IRuntimePauses | IEngineHeartbeat | ITradeRepository | IEquityRepository`
- `TradingRepository` satisfies `IFullRepository` (composite of all groups)
- Components should declare only the narrower sub-protocol they actually need

## Phase D Recovery Infrastructure

### State transition audit trail

- Table: `tr_state_transitions` — append-only log of status changes.
- Written by: `_set_engine_heartbeat` (engine transitions) and `_persist_worker_runtime_state` (worker transitions).
- Read by: `GET /state-transitions`.
- Best-effort: errors are swallowed; the worker loop is never blocked.

### Startup recovery — pause validation

- At startup, `_recover_runtime_state` fetches current candle times and checks stale-data.
- Active `KILL_STALE_DATA` pauses are auto-resumed if the feed is now fresh.
- Active `CIRCUIT_DD_DIARIO` pauses are auto-resumed if they started on a previous date.
- Orphaned open positions produce `ORPHANED_POSITION` risk events.

### Operator repair surface

- `POST /operator/clear-pause/{pause_key}` — manually resume any active pause.
- `POST /operator/reset-pending/{engine_id}` — manually clear an active pending setup.
- `GET /operator/recovery-state` — full diagnostic view combining pauses, pending setups, engine states, open positions, and recent recovery/operator events.
- `GET /operator/reviewer-briefing` — analytical review and short briefing for operator/OpenClaw consumption.
- `GET /operator/readiness-report` — objective readiness report for stabilization and Phase F promotion gating.
- Auth: `X-Operator-Token: Bearer <OPERATOR_TOKEN>` required if `OPERATOR_TOKEN` env var is set.

## Phase E Reviewer IA Operacional

### Reviewer role (strict)

- Analytical only; never mutates runtime.
- No order routing.
- No risk mutation.
- No engine/parameter mutation.
- No automated pause clearance.
- No trade decision authority.

### Reviewer inputs (canonical priority)

- `GET /system/status` (via `build_system_status`)
- `GET /state-transitions` (via `_build_state_transitions_payload`)
- `GET /operator/recovery-state` (via `_build_recovery_state_payload`)
- Optional support evidence from persisted recent events/state already included in recovery/status payloads.

### Reviewer outputs

- Model blocks:
  - `system_summary`
  - `active_alerts`
  - `degraded_components`
  - `recovery_findings`
  - `engine_highlights`
  - `operator_actions`
- Severity: `CRITICAL | WARNING | INFO`.
- Finding types: `stale input`, `degraded coverage`, `runtime inconsistency`, `recovery anomaly`, `prolonged blocked risk`, `missing heartbeat`, `no recent transitions`, `healthy steady-state`.
- Canonical briefing:
  - `timestamp`
  - `overall_status`
  - `top_issues`
  - `engine_summary`
  - `operator_recommendations`

### OpenClaw integration mode

- Loose coupling through API contract only (`GET /operator/reviewer-briefing`).
- OpenClaw remains consumer/reviewer layer; Trading Core remains source of truth and execution authority.
- No back-channel from reviewer to worker loop.

## Phase E.5 Stabilization And Promotion Gating

### Readiness role (strict)

- Analytical and gate-based only.
- No mutation of worker loop, strategy logic, runtime pauses, or risk state.
- Uses existing data surfaces; introduces no new persistence model.

### Readiness inputs

- `GET /system/status` snapshot.
- `GET /state-transitions` audit window.
- `GET /operator/recovery-state` recovery diagnostics.
- Recent `risk_events` and `signals` for stabilization metrics.
- Reviewer output (`build_operational_review`) for alert-quality signal.

### Readiness outputs

- `current_readiness_status`
- `blocking_issues`
- `recent_incidents`
- `recovery_health`
- `signal_health`
- `service_health`
- `metrics`
- `acceptance_gates`
- `recommendation`

### Promotion state model

- `NOT_READY` — at least one hard gate failed.
- `PAPER_STABLE_WITH_WARNINGS` — hard gates pass, soft gates fail.
- `PAPER_STABLE` — hard + soft pass, promotion gates fail.
- `READY_FOR_PHASE_F` — all gates pass.

## Guardrails

- No direct derivative feed hard dependency (adapter fallback keeps baseline operational).
- No exchange/live order implementation.
- No broad package refactor in this phase.
- No reviewer-to-execution coupling.
- No readiness-to-execution coupling.

## Phase F Optional Derivatives Path

- Feature contract (initial set):
  - `orderbook_imbalance`
  - `orderbook_imbalance_delta`
  - `funding_rate`
  - `oi_delta_1h`
  - `oi_delta_4h`
  - `taker_aggression`
  - `liquidation_intensity`
- Adapter responsibilities:
  - read-only fetch by symbol from `derived_features` + `coinalyze_funding_rate`
  - normalize per-symbol payload + timestamp
  - compute freshness/quality/availability
  - expose fallback state without breaking engine loop
- Flags:
  - `DERIVATIVES_ENABLED`
  - `DERIVATIVES_FEATURE_ADAPTER_ENABLED`
  - `ENGINE_DERIVATIVES_ADVISORY_ENABLED`
  - `ENGINE_DERIVATIVES_HARD_FILTER_ENABLED` (kept off by default in Phase F)
- Status integration:
  - `/system/status` includes top-level `derivatives` block
  - each engine status includes derivatives availability summary

## Phase F.5 Derivatives Value Validation

- Objective: measure operational value of derivatives features without changing trade decisions.
- Canonical artifacts:
  - `GET /operator/derivatives-snapshot` — normalized per-symbol derivatives snapshot.
  - `GET /operator/derivatives-readiness` — derivatives value metrics + derived readiness state.
- Advisory evidence events (worker):
  - `DERIVATIVE_CONTEXT_AVAILABLE`
  - `DERIVATIVE_CONTEXT_UNAVAILABLE`
  - `DERIVATIVE_CONTEXT_DEGRADED`
  - `DERIVATIVES_FALLBACK_ACTIVE`
  - `DERIVATIVES_DEGRADED`
- Derived readiness states (metrics-driven):
  - `NO_VALUE_YET`
  - `INFRA_READY_DATA_POOR`
  - `ADVISORY_READY`
  - `CANDIDATE_FOR_FILTER_EXPERIMENT`
- Guardrail preserved:
  - no hard filter activation
  - no sizing/risk mutation
  - no live execution path
