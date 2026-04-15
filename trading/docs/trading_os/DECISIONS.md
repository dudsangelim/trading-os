# Decisions

Updated: 2026-04-14

## 2026-04-14 — Phase G: shadow filter requires 2+ rules to BLOCK (corroboration)

- Decision: verdict is `BLOCK` only when ≥2 filter rules fire simultaneously.
- Why: a single adverse signal (e.g. low taker aggression alone) can occur in normal markets without being genuinely predictive. Requiring corroboration reduces false positives in the shadow dataset.
- Consequence: block rate will be conservative initially; tune threshold after ≥50 samples are collected.

## 2026-04-14 — Phase G: shadow filter is a counterfactual observer, not an executor

- Decision: shadow filter evaluates the verdict and emits a risk event but never blocks the trade.
- Why: the filter's predictive value is unproven. The shadow phase exists to build the counterfactual dataset.
- Consequence: trading PnL is unaffected during Phase G. The outcome correlation in `/operator/shadow-filter-report` is the primary deliverable of this phase.

## 2026-04-14 — asyncpg jsonb codec must be registered explicitly on pool init

- Decision: register json/jsonb codecs in `_init_conn` callback on every pool creation.
- Why: asyncpg 0.29.0 returns jsonb columns as Python strings by default — no automatic deserialization without explicit codec. Defensive json.loads fallbacks added as secondary protection.
- Consequence: all payload fields across the system are now consistently dicts, not strings.

## 2026-04-14 — Phase F.5 validates value before enabling filters

- Decision: evaluate derivatives through operational metrics and advisory evidence first.
- Why: plumbing alone is insufficient to justify stricter strategy coupling.
- Consequence: progression is tied to measured availability/freshness/fallback/advisory usage, not intuition.

## 2026-04-14 — Phase F.5 defines explicit derivatives readiness states

- Decision: classify derivatives value using `NO_VALUE_YET`, `INFRA_READY_DATA_POOR`, `ADVISORY_READY`, `CANDIDATE_FOR_FILTER_EXPERIMENT`.
- Why: provide a concrete answer on whether to continue toward a controlled filter experiment.
- Consequence: readiness reporting can now state explicitly if derivatives are operationally useful or still only plumbing.

## 2026-04-14 — Phase F derivatives integration is optional and non-blocking

- Decision: integrate derivatives through an explicit read-only adapter with fallback, never as a hard requirement.
- Why: preserve paper-stable runtime behavior while adding incremental context.
- Consequence: core continues operating on candle baseline when derivatives are missing, stale or degraded.

## 2026-04-14 — Phase F keeps derivatives usage behind explicit flags

- Decision: gate all derivatives behavior with `DERIVATIVES_ENABLED`, `DERIVATIVES_FEATURE_ADAPTER_ENABLED`, `ENGINE_DERIVATIVES_ADVISORY_ENABLED`, `ENGINE_DERIVATIVES_HARD_FILTER_ENABLED`.
- Why: integration must be reversible and operationally controlled.
- Consequence: hard filter remains disabled by default in this phase and no strategy path becomes derivatives-dependent.

## 2026-04-14 — Phase F consolidates derivatives mode in status

- Decision: expose one canonical derivatives mode in `/system/status`: `DISCONNECTED | ADVISORY_ONLY | DEGRADED | AVAILABLE`.
- Why: operators/OpenClaw need a stable, explicit control-plane signal for derivatives health and usage level.
- Consequence: reviewer/readiness can mention derivatives state and fallback behavior without mutating execution.

## 2026-04-14 — Phase E.5 readiness is gate-driven and objective

- Decision: readiness classification must be computed from explicit acceptance gates, not free-form operator opinion.
- Why: promotion to Phase F needs repeatable and auditable criteria.
- Consequence: every readiness status is explainable through gate pass/fail records.

## 2026-04-14 — Phase E.5 reuses existing telemetry instead of new storage

- Decision: build readiness from `/system/status`, `/state-transitions`, `/operator/recovery-state`, recent risk events and recent signals.
- Why: keeps implementation simple and robust, avoiding schema drift and extra runtime coupling.
- Consequence: readiness quality depends on the quality of current observability surfaces.

## 2026-04-14 — Phase E.5 keeps promotion states separate from reviewer severity

- Decision: use dedicated readiness states (`NOT_READY`, `PAPER_STABLE`, `PAPER_STABLE_WITH_WARNINGS`, `READY_FOR_PHASE_F`) instead of mapping directly from reviewer severity.
- Why: reviewer severity captures incident urgency; readiness captures promotion maturity.
- Consequence: system can be operationally stable with warnings and still be blocked from promotion when promotion gates fail.

## 2026-04-14 — Phase E reviewer is analytical, not decisional

- Decision: implement the reviewer as a read-only analytical layer.
- Why: operational briefing is needed now without increasing execution risk.
- Consequence: reviewer can classify and recommend, but cannot mutate trading runtime.

## 2026-04-14 — Phase E reuses canonical operational surfaces

- Decision: reviewer consumes the same canonical data surfaces already exposed to operators (`/system/status`, `/state-transitions`, `/operator/recovery-state`).
- Why: avoid parallel telemetry contracts and keep OpenClaw integration low-friction.
- Consequence: reviewer behavior remains bounded by existing observability quality and schema stability.

## 2026-04-14 — Phase E delivery format is a stable briefing endpoint

- Decision: expose `GET /operator/reviewer-briefing` from the API layer.
- Why: simplest robust integration path for OpenClaw and operators; no background worker or new storage needed.
- Consequence: briefing freshness follows API call cadence; no push/stream semantics yet.

## 2026-04-14 — Phase A uses existing persistence only

- Decision: persist worker loop and blocker audit data in `tr_risk_events` and keep engine service state in `tr_engine_heartbeat`.
- Why: Phase A requires high operational impact with minimal schema risk.
- Consequence: worker-level history is available immediately, but long-term service-state modeling still belongs to Phase B.

## 2026-04-14 — `/system/status` is the canonical status endpoint

- Decision: keep `GET /status` available, but treat `GET /system/status` as the canonical operational status surface.
- Why: the new endpoint needs a stable contract that is broader than the legacy heartbeat list.
- Consequence: downstream consumers should migrate to `/system/status`.

## 2026-04-14 — Missing valid input is treated as an explicit blocker

- Decision: aggregate TF candle absence and missing closed-input context are recorded as `NO_VALID_INPUT`.
- Why: silent skips were operationally ambiguous and hid real data path problems.
- Consequence: some situations previously visible only in logs now surface in heartbeats and risk events.

## 2026-04-14 — Global worker status is derived, not stored separately

- Decision: worker status (`alive`, `processing`, `blocked_risk`, `degraded`, `slow`, `stopped`) is derived from persisted loop events, engine heartbeats and freshness checks.
- Why: avoids adding a new schema object before Phase B.
- Consequence: correctness depends on continued emission of loop events each minute.

## 2026-04-14 — Phase B adds minimal dedicated runtime tables

- Decision: introduce `tr_engine_runtime_state`, `tr_pending_setups`, `tr_runtime_pauses` and `tr_worker_runtime_state`.
- Why: Phase B objective is to remove critical dependence on volatile memory without broad refactor.
- Consequence: recovery now has first-class persisted state, while strategy logic remains mostly unchanged.

## 2026-04-14 — Pending setup persistence stays engine-specific

- Decision: persist only the real pending states already present in engines (`pending_sweep`, `pending_retest`, `pending_direct`), not a generalized workflow framework.
- Why: user requested simple, robust and auditable persistence with low break risk.
- Consequence: if new engines add new pending logic later, they must opt in explicitly.

## 2026-04-14 — Phase D: state transitions are best-effort (never block worker)

- Decision: wrap all `insert_state_transition` calls in `try/except` and never raise.
- Why: the audit log is diagnostic infrastructure; a DB error there must never stop the trading loop.
- Consequence: there may be gaps in `tr_state_transitions` under DB pressure, but the worker remains operational. Gaps are identifiable by comparing with `tr_risk_events` chronology.

## 2026-04-14 — Phase D: OPERATOR_TOKEN empty = no auth (dev default)

- Decision: `OPERATOR_TOKEN` defaults to empty string, which disables auth on operator endpoints.
- Why: the service runs in a VPS with no public exposure; the worker and API are on localhost. Mandatory auth would add friction in dev without security benefit.
- Consequence: anyone with network access to the API port can call operator endpoints unless `OPERATOR_TOKEN` is set. Operators should set this before exposing the API beyond localhost.

## 2026-04-14 — Phase D: KILL_STALE_DATA pause auto-resumed at startup if feed is fresh

- Decision: at startup, if an active KILL_STALE_DATA pause exists but the candle feed is currently fresh, resume it automatically.
- Why: the worker was stopped while the feed was stale. By the time it restarts, the feed may have recovered. Making the operator clear the pause manually each restart is unnecessary friction.
- Consequence: the first loop iteration no longer inherits a stale block when the feed is healthy. `STARTUP_PAUSE_AUTO_RESUMED` provides the audit trail.

## 2026-04-14 — Phase C: contract_version is additive and broadcasted

- Decision: add `contract_version: "C.1"` to the root of every `/system/status` response.
- Why: downstream consumers (OpenClaw) had no way to detect schema changes. An additive version field solves this without breaking anything.
- Consequence: consumers should check `contract_version` and alert on unexpected values. Additive fields do not require a version bump.

## 2026-04-14 — Phase C: DEGRADED is advisory-only (CONTINUE policy)

- Decision: coverage degradation (`DEGRADED` quality) does not block trading; only `STALE` and `ABSENT` block.
- Why: sparse-but-alive feed is operationally distinct from dead feed. Blocking on degradation would increase false positives.
- Consequence: `DEGRADED` is surfaced in `/system/status` for human review but the worker ignores it. The circuit breaker (`check_stale_data`) still uses the freshness-only criterion.

## 2026-04-14 — Phase C: interfaces are Protocols, not ABCs

- Decision: use `typing.Protocol` for storage interface groups rather than abstract base classes.
- Why: TradingRepository already works correctly; making it inherit from ABCs would require code changes. Protocols are structural and require no changes to the implementation.
- Consequence: type-checkers can verify protocol compliance. Runtime `isinstance()` checks work for `@runtime_checkable` protocols. No implementation change needed.

## 2026-04-14 — Startup inconsistency handling is fail-safe

- Decision: on startup, inconsistent pending state is invalidated and recorded as `STARTUP_INCONSISTENCY` instead of being trusted.
- Why: recovery must not operate blindly on stale or contradictory state.
- Consequence: some recoverable-but-ambiguous setups are dropped rather than guessed.
