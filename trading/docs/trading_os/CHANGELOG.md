# Changelog

## 2026-04-14 — Phase G: Shadow Hard-Filter Experiment

### Code changes

- `core/engines/derivatives_filter.py` (new):
  - `ShadowFilterVerdict` dataclass: `verdict`, `triggered_rules`, `features_used`, `data_quality`, `reason`, `feature_values`.
  - `evaluate_shadow_filter(direction, derivatives_ctx)` — rules: `FUNDING_RATE_EXTREME`, `OI_DELTA_ADVERSE`, `TAKER_AGGRESSION_LOW`, `OB_IMBALANCE_ADVERSE`. Verdict `BLOCK` requires ≥2 rules.
  - Never raises — errors degrade to `PASS` with `data_quality=INSUFFICIENT`.
- `core/config/settings.py`: `ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED` flag (default false).
- `apps/worker/main.py`: shadow filter hook on ENTER signals → `DERIVATIVE_SHADOW_FILTER_PASS/BLOCK` events. `import json` added.
- `core/monitoring/operational_readiness.py`: `DERIV_READINESS_SHADOW_FILTER_ACTIVE` state; shadow metrics in report.
- `apps/api/main.py`: `GET /operator/shadow-filter-report` with outcome correlation.
- Tests: `tests/test_derivatives_filter.py` (14 tests) + 2 new assertions in `test_operational_readiness.py`.

### Bug Fix (Phase F.5 regression — asyncpg jsonb codec)

- `apps/api/main.py` + `apps/worker/main.py`: `_init_conn` callback registers json/jsonb codecs on pool.
- Root cause: asyncpg 0.29.0 returns jsonb as strings → `payload.get()` raised `AttributeError` on `/operator/derivatives-readiness`.
- `core/monitoring/operational_readiness.py` + `healthcheck.py`: defensive `json.loads` fallback added.

### No-change boundaries preserved

- Shadow filter never blocks trades.
- No live execution changes.
- `ENGINE_DERIVATIVES_HARD_FILTER_ENABLED` remains false.

## 2026-04-14 — Phase F.5: Validação de Valor Operacional das Features Derivativas

### Code changes

- `core/storage/repository.py`:
  - Added `canonical_snapshot` in derivatives adapter payload (`timestamp`, `source_status`, `available_features`, `freshness`, `quality`, `fallback_active`, `fallback_reason`).
- `apps/worker/main.py`:
  - Added advisory evidence events:
    - `DERIVATIVE_CONTEXT_AVAILABLE`
    - `DERIVATIVE_CONTEXT_UNAVAILABLE`
    - `DERIVATIVE_CONTEXT_DEGRADED`
    - `DERIVATIVES_FALLBACK_ACTIVE`
    - `DERIVATIVES_DEGRADED`
- `core/monitoring/operational_reviewer.py`:
  - Reviewer summary/briefing now includes derivatives context note (`available`, `unavailable`, `degraded`) and available symbols.
- `core/monitoring/operational_readiness.py`:
  - Added derivatives value metrics:
    - `derivative_availability_rate`
    - `derivative_fresh_rate`
    - `derivative_degraded_rate`
    - `derivative_fallback_rate`
    - `advisory_usage_count`
    - `symbols_with_real_feature_coverage`
    - `derivative_degraded_reason_frequency`
  - Added `derivatives_readiness` block with states:
    - `NO_VALUE_YET`
    - `INFRA_READY_DATA_POOR`
    - `ADVISORY_READY`
    - `CANDIDATE_FOR_FILTER_EXPERIMENT`
- `apps/api/main.py`:
  - Added `GET /operator/derivatives-snapshot`.
  - Added `GET /operator/derivatives-readiness`.
- Tests:
  - Updated `tests/test_operational_readiness.py` for derivatives readiness assertions.

### Operational delta

- Trading OS can now answer whether derivatives features are arriving with enough quality/coverage to justify next advisory experiments.
- Lack of value is now explicitly surfaced (`NO_VALUE_YET`) instead of implied.

### No-change boundaries preserved

- No hard filter activation.
- No change to primary trade decision path.
- No live execution changes.

## 2026-04-14 — Phase F: Integração Derivativa Opcional e Controlada

### Code changes

- `core/config/settings.py`:
  - Added explicit derivatives flags and thresholds:
    - `DERIVATIVES_ENABLED`
    - `DERIVATIVES_FEATURE_ADAPTER_ENABLED`
    - `ENGINE_DERIVATIVES_ADVISORY_ENABLED`
    - `ENGINE_DERIVATIVES_HARD_FILTER_ENABLED`
    - `DERIVATIVES_DATABASE_URL`
    - freshness/quality thresholds.
- `core/storage/repository.py`:
  - Added formal read-only adapter method `get_derivatives_feature_snapshot(symbols, now)`.
  - Added normalized contract for first feature set:
    - `orderbook_imbalance`
    - `orderbook_imbalance_delta`
    - `funding_rate`
    - `oi_delta_1h`
    - `oi_delta_4h`
    - `taker_aggression`
    - `liquidation_intensity`
  - Added explicit fallback payload for unavailable/degraded derivatives path.
- `core/monitoring/healthcheck.py` and `core/monitoring/status_model.py`:
  - `/system/status` now includes top-level `derivatives` block.
  - Engine status now includes derivatives availability summary.
- `apps/api/main.py`:
  - Added optional derivatives DB pool lifecycle.
  - `/health`, `/status`, `/system/status`, reviewer and readiness endpoints now use optional derivatives context.
- `apps/worker/main.py`:
  - Added optional derivatives DB pool lifecycle.
  - Engine context now receives optional derivatives snapshot in `ctx.extra`.
  - Added explicit `derivatives_context` and fallback observability logs.
- `core/monitoring/operational_reviewer.py`:
  - Reviewer now reports derivatives availability/degradation/advisory/fallback outcomes.
- `core/monitoring/operational_readiness.py`:
  - Readiness now includes derivatives mode/fallback metrics in report payload.
- Tests:
  - `tests/test_operational_reviewer.py` extended for derivatives degraded/advisory/fallback coverage.
  - `tests/test_operational_readiness.py` now asserts derivatives metrics presence.

### Operational delta

- Derivatives integration is now optional, explicit and reversible.
- Core runtime remains candle-baseline operable when derivatives path is disconnected or degraded.
- No engine hard-filter behavior is enabled by default in this phase.

### No-change boundaries preserved

- No live trading execution.
- No IA decisora.
- No mandatory derivatives dependency.
- No deep strategy rewrite.

## 2026-04-14 — Phase E.5: Stabilização Operacional e Gating de Promoção

### New files

- `core/monitoring/operational_readiness.py` — readiness synthesis with objective gates/metrics and promotion classification.
- `tests/test_operational_readiness.py` — unit tests covering readiness state transitions.
- `docs/trading_os/OPERATIONS_ACCEPTANCE.md` — canonical paper acceptance gates, metrics, and validation scenarios.

### Code changes

- `apps/api/main.py`:
  - Added `GET /operator/readiness-report`.
  - Added `_build_risk_events_payload` and `_build_signals_payload` helpers.
  - Refactored `GET /signals` and `GET /risk-events` to shared payload helpers.
  - Readiness endpoint composes canonical surfaces and reviewer output to produce gate-based promotion status.

### Operational delta

- Operators now have an explicit promotion gating surface for paper stabilization.
- Readiness status is reproducible (`NOT_READY | PAPER_STABLE_WITH_WARNINGS | PAPER_STABLE | READY_FOR_PHASE_F`) and backed by measurable gates.
- Noise is reduced by separating incident severity (reviewer) from promotion maturity (readiness).

### No-change boundaries preserved

- No derivatives integration.
- No live execution changes.
- No strategy/engine behavior changes.
- No loop-critical mutation.

## 2026-04-14 — Phase E: Reviewer IA Operacional

### New files

- `core/monitoring/operational_reviewer.py` — read-only analytical reviewer synthesizer.
  - Produces: `system_summary`, `active_alerts`, `degraded_components`, `recovery_findings`, `engine_highlights`, `operator_actions`.
  - Emits canonical briefing block: `timestamp`, `overall_status`, `top_issues`, `engine_summary`, `operator_recommendations`.
  - Severity levels: `CRITICAL`, `WARNING`, `INFO`.
  - Finding taxonomy: `stale input`, `degraded coverage`, `runtime inconsistency`, `recovery anomaly`, `prolonged blocked risk`, `missing heartbeat`, `no recent transitions`, `healthy steady-state`.

### Code changes

- `apps/api/main.py`:
  - Added shared helper `_build_state_transitions_payload`.
  - Added shared helper `_build_recovery_state_payload`.
  - Refactored `GET /state-transitions` and `GET /operator/recovery-state` to reuse shared payload builders.
  - Added `GET /operator/reviewer-briefing` — read-only review/briefing endpoint for OpenClaw/operator use.
- `tests/test_operational_reviewer.py`:
  - Added reviewer unit tests for critical stale/heartbeat path, prolonged blocked risk, and healthy steady-state.

### Operational delta

- Operators now have a short and stable operational briefing surface without touching worker/engine execution.
- OpenClaw can consume a single reviewer payload instead of manually composing multiple endpoint reads.

### No-change boundaries preserved

- No loop-critical execution changes.
- No strategy/engine behavior changes.
- No risk mutation behavior changes.
- No derivatives integration.
- No live execution path.

## 2026-04-14 — Phase D: Recovery Maturity

### Schema changes

- Added `tr_state_transitions` — append-only table recording every engine and worker status transition.
  - Columns: `entity_type, entity_id, from_status, to_status, detail, payload, recorded_at`
  - Indexed on `(entity_type, entity_id, recorded_at DESC)` and `(recorded_at DESC)`.

### Code changes

- `core/storage/migrations.py` — added `tr_state_transitions` DDL (idempotent).
- `core/storage/repository.py` — added `insert_state_transition`, `get_state_transitions`.
- `core/storage/interfaces.py` — added `IStateTransitions` Protocol; added to `IFullRepository`.
- `core/config/settings.py` — added `OPERATOR_TOKEN` env var (empty = no auth in dev).
- `apps/worker/main.py`:
  - Added `_worker_runtime_status` global — tracks worker status for transition detection.
  - `_persist_worker_runtime_state`: records a `tr_state_transitions` row when worker status changes (best-effort, never blocks).
  - `_set_engine_heartbeat`: records a `tr_state_transitions` row when engine status changes (best-effort, never blocks).
  - `_recover_runtime_state`: fetches candle times at startup; auto-resumes `KILL_STALE_DATA` pause if data is fresh, recording `STARTUP_PAUSE_AUTO_RESUMED` event.
  - `main()`: orphaned open positions (engine_id not in registry) now produce `ORPHANED_POSITION` risk events instead of silent skips.
- `apps/api/main.py`:
  - Added `GET /state-transitions?entity_id=&entity_type=&limit=` — audit log endpoint.
  - Added `POST /operator/clear-pause/{pause_key}` — manual pause clearance (requires `X-Operator-Token` if `OPERATOR_TOKEN` is set).
  - Added `POST /operator/reset-pending/{engine_id}` — manual pending setup reset.
  - Added `GET /operator/recovery-state` — diagnostic dump for operator-assisted recovery.

### Operational delta

- Status transitions are now auditable without scraping logs or risk events.
- KILL_STALE_DATA pauses no longer persist across restarts when the feed has recovered.
- Orphaned positions (from removed engines) are now explicitly flagged at startup.
- Operators can clear stuck pauses and reset pending setups via API without DB access.
- `OPERATOR_TOKEN` controls access to write endpoints (`POST /operator/*`). Empty = open in dev.

### No-change boundaries preserved

- No derivatives integration.
- No engine strategy rewrites.
- No autonomous repair — all operator endpoints require explicit human action.
- Transition recording is best-effort (try/except); worker loop is never blocked by it.

## 2026-04-14 — Phase C: Interfaces Formais Mínimas do Core

### New files

- `core/data/candle_contract.py` — formal contract between candle-collector and Trading Core.
  - `CANDLE_SOURCE_TABLE`, `CANDLE_COLUMNS`, `FRESHNESS_THRESHOLD_MINUTES`, `COVERAGE_WINDOW_MINUTES`, `MINIMUM_COVERAGE_IN_WINDOW`, `REQUIRED_SYMBOLS` make all implicit expectations explicit.
  - `InputQuality` enum: `FRESH | DEGRADED | STALE | ABSENT` — richer than the previous binary fresh/stale.
  - `DegradedInputPolicy` enum documents the policy decision (CONTINUE) for advisory-only coverage degradation.
  - `SymbolInputStatus` and `CandleInputValidation` dataclasses represent observed input state.
  - `validate_candle_inputs(reader, symbols, now)` — async validator used by healthcheck.

- `core/monitoring/status_model.py` — typed contract for the runtime state exposed by the Trading Core.
  - `CONTRACT_VERSION = "C.1"` — schema version broadcasted in every `/system/status` response.
  - Dataclasses: `WorkerStatusSnapshot`, `EngineStatusSnapshot`, `InputSymbolStatus`, `InputStatus`, `RiskSnapshot`, `SystemStatusSnapshot`.
  - Canonical status value constants for worker loop and engine heartbeat states.
  - This file IS the OpenClaw contract — changes here require a CONTRACT_VERSION bump.

- `core/storage/interfaces.py` — explicit Protocol definitions grouping TradingRepository by concern.
  - `IOperationalEvents` — risk event audit log (tr_risk_events).
  - `IEngineRuntimeState` — engine snapshot persistence (tr_engine_runtime_state).
  - `IWorkerRuntimeState` — worker loop snapshot persistence (tr_worker_runtime_state).
  - `IPendingSetups` — multi-bar setup slot persistence (tr_pending_setups).
  - `IRuntimePauses` — active pause persistence (tr_runtime_pauses).
  - `IEngineHeartbeat` — legacy last-run-at table (tr_engine_heartbeat).
  - `ITradeRepository` — paper trade lifecycle (tr_paper_trades).
  - `IEquityRepository` — daily equity snapshots (tr_daily_equity).
  - `IFullRepository` — composite of all above; satisfied by TradingRepository.

### Code changes

- `core/monitoring/healthcheck.py`:
  - Imports `validate_candle_inputs` from `candle_contract` and `CONTRACT_VERSION` from `status_model`.
  - Replaces inline freshness-only per-symbol loop with `validate_candle_inputs()`.
  - `last_candle_times` is derived from `candle_validation.symbols` instead of separate DB loop.
  - `input_symbols` list now includes `quality`, `coverage_in_window`, `coverage_window_minutes`.
  - `status` field remains "ok" | "stale" | "absent" for backward compatibility.
  - Adds `contract_version: CONTRACT_VERSION` to the response root.

- `apps/api/main.py`:
  - `GET /system/status` is now annotated as the stable operations contract endpoint.
  - Docstring references `status_model.py` as the schema source and `contract_version` as the signal for schema changes.

### Operational delta

- `/system/status` response now includes `contract_version: "C.1"` at the root.
- Per-symbol input data now includes `quality` (richer than binary fresh/stale) and coverage metrics.
- Coverage degradation (sparse but not stale feed) is now visible in `/system/status` without blocking trading.
- OpenClaw consumers can now verify schema version and alert on unexpected changes.

### No-change boundaries preserved

- No DDL changes.
- No new persistence tables.
- No changes to circuit breaker logic (`check_stale_data` still used for breaker result).
- No derivatives integration.
- No engine strategy rewrites.
- No changes to API response structure beyond additive fields.

## 2026-04-14 — Phase A: Hardening do Core

### Code changes

- Reworked worker operational state handling in [apps/worker/main.py](/home/agent/agents/stack/trading/apps/worker/main.py).
- Replaced superficial health synthesis in [core/monitoring/healthcheck.py](/home/agent/agents/stack/trading/core/monitoring/healthcheck.py).
- Extended breaker metadata in [core/risk/circuit_breakers.py](/home/agent/agents/stack/trading/core/risk/circuit_breakers.py).
- Added canonical status exposure in [apps/api/main.py](/home/agent/agents/stack/trading/apps/api/main.py).
- Added `health_snapshot` helper in [core/monitoring/structured_log.py](/home/agent/agents/stack/trading/core/monitoring/structured_log.py).

### Operational delta

- Worker loop start/completion is now queryable across API restarts.
- Engine blocked/resumed transitions are now explicit.
- Stale input and missing valid input are distinguishable.
- Health now reflects real loop recency instead of just table presence.

### No-change boundaries preserved

- No DDL changes.
- No new persistence tables.
- No derivatives integration.
- No engine strategy rewrites.

## 2026-04-14 — Phase B: Persistência e Recovery

### Schema changes

- Added `tr_engine_runtime_state`.
- Added `tr_pending_setups`.
- Added `tr_runtime_pauses`.
- Added `tr_worker_runtime_state`.

### Code changes

- Added engine serialization hooks in [core/engines/base.py](/home/agent/agents/stack/trading/core/engines/base.py).
- Added pending/runtime persistence for [core/engines/m2_btc.py](/home/agent/agents/stack/trading/core/engines/m2_btc.py), [core/engines/m3_sol.py](/home/agent/agents/stack/trading/core/engines/m3_sol.py) and [core/engines/m3_eth_shadow.py](/home/agent/agents/stack/trading/core/engines/m3_eth_shadow.py).
- Added repository and migration support in [core/storage/repository.py](/home/agent/agents/stack/trading/core/storage/repository.py) and [core/storage/migrations.py](/home/agent/agents/stack/trading/core/storage/migrations.py).
- Added startup recovery and consistency checks in [apps/worker/main.py](/home/agent/agents/stack/trading/apps/worker/main.py).
- Extended status synthesis to read persisted runtime state in [core/monitoring/healthcheck.py](/home/agent/agents/stack/trading/core/monitoring/healthcheck.py).

### Operational delta

- Restart no longer loses `last_bar_seen`.
- Restart no longer loses pending multi-bar setups for the engines that actually use them.
- Restart preserves active DD and bankroll pauses, plus worker/global stale pause state.
- Worker startup validates persisted state against current candle context before resuming operation.
