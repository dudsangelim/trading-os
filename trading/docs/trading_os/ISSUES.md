# Known Issues

Updated: 2026-04-14

## Remaining Risks After Phase F.5

- Derivatives value metrics are computed from bounded lookback windows and event samples.
  - Impact: long-horizon behavior may differ from near-term readiness classification.
  - Mitigation: run periodic longer-window audits before any filter experiment.

- `advisory_usage_count` depends on worker risk-event write health.
  - Impact: temporary DB pressure can undercount usage evidence.
  - Mitigation: correlate with worker logs during incident windows.

- `CANDIDATE_FOR_FILTER_EXPERIMENT` indicates data quality readiness, not proven PnL improvement.
  - Impact: still possible to pass readiness and fail value in later experiments.
  - Mitigation: keep next step as controlled advisory experiment first; no hard gating yet.

## Remaining Risks After Phase F

- Derivatives adapter depends on a separate DB URL (`DERIVATIVES_DATABASE_URL`) and separate pool lifecycle.
  - Impact: wrong/missing DSN keeps mode in `DISCONNECTED`.
  - Mitigation: keep flags explicit and monitor `/system/status.derivatives.mode`.

- Feature freshness thresholds are static defaults.
  - Impact: markets/sources with different cadence may classify as `DEGRADED` earlier/later than ideal.
  - Mitigation: tune `DERIVATIVES_FRESHNESS_THRESHOLD_SECONDS` and `DERIVATIVES_STALE_THRESHOLD_SECONDS` after observing live telemetry.

- Collector quality proxy uses recent `collection_log` status ratio.
  - Impact: short noisy windows can transiently mark derivatives path degraded.
  - Mitigation: treat as advisory signal; keep runtime fallback to candle baseline.

- Phase F does not enable derivatives hard filters by default.
  - Impact: derivatives features are visible/advisory, but not enforcing entry blocking.
  - Mitigation: evaluate advisory behavior first; hard filter remains gated for a later phase.

## Remaining Risks After Phase E.5

- Readiness metrics use bounded windows (`lookback_hours`, `limit`) from pull APIs.
  - Impact: very long-tail incidents may be outside the sampled window.
  - Mitigation: increase lookback/limits for deeper audits; keep default lightweight for routine operations.

- `coverage_degraded_frequency` is snapshot-proxy based (current degraded symbol ratio), not a full historical timeseries.
  - Impact: short-lived degradation bursts may be underrepresented.
  - Mitigation: treat it as readiness signal, not forensic evidence.

- `signal_health_minimum` gate may fail for low-signal regimes even when engine is functioning.
  - Impact: false promotion blocks in quiet market conditions.
  - Mitigation: operators should validate with reviewer context before changing gate thresholds.

- Readiness endpoint is pull-based and not event-streaming.
  - Impact: readiness changes are observed on poll cadence.
  - Mitigation: OpenClaw should define a fixed polling SLO and alert on stale readiness timestamp.

## Remaining Risks After Phase E

- Reviewer quality is bounded by source freshness/accuracy (`/system/status`, `/state-transitions`, `/operator/recovery-state`).
  - Impact: false positives/negatives are possible when underlying signals are delayed or incomplete.
  - Mitigation: keep reviewer advisory-only; operators validate before manual action.

- `GET /operator/reviewer-briefing` is pull-based, not streaming.
  - Impact: OpenClaw/operator receives updates only on poll cadence.
  - Mitigation: define polling SLO in OpenClaw; consider streaming only in a later phase if needed.

- Finding heuristics are intentionally simple thresholds.
  - Impact: some borderline cases may classify as WARNING/INFO differently than human judgement.
  - Mitigation: tune thresholds only after observing real VPS telemetry.

## Remaining Risks After Phase A

- Worker loop history shares `tr_risk_events` with risk telemetry.
  - Impact: operational and risk streams are mixed in one table.
  - Phase B follow-up: decide whether worker/service state deserves a dedicated persistence model.

- `tr_engine_heartbeat` is still per-engine only.
  - Impact: worker-global state is derived rather than first-class persisted state.
  - Phase B follow-up: add a dedicated worker/service heartbeat model if needed.

- Block duration continuity across process restarts is partial.
  - Impact: `ENGINE_RESUMED` duration is exact only within a continuous worker lifetime.
  - Phase B follow-up: persist blocker start state explicitly.

- `/system/status` depends on current event naming contract in the worker.
  - Impact: future worker event changes must remain backward-compatible or update the monitoring builder.

- No deep recovery or reconciliation was added.
  - Impact: phase handles visibility and hardening, not autonomous repair.

## Remaining Risks After Phase D

- `tr_state_transitions` recording is best-effort; gaps possible under DB pressure.
  - Impact: audit gaps in `tr_state_transitions` — not in `tr_risk_events` or logs.
  - Acceptable: diagnostic tool, not a safety mechanism.

- OPERATOR_TOKEN defaults to empty; no auth in dev.
  - Impact: operator write endpoints are unprotected unless OPERATOR_TOKEN is set.
  - Operator action required: set `OPERATOR_TOKEN` before exposing API beyond localhost.

- `POST /operator/clear-pause` modifies DB but not worker in-memory state.
  - Impact: operator clears a pause in DB; worker picks it up on next loop iteration (up to 60s delay).
  - Expected behavior: the endpoint header documents this. No workaround needed.

- Startup candle time fetch in `_recover_runtime_state` adds 3 extra DB queries (one per symbol).
  - Impact: marginal startup latency increase.
  - Acceptable: startup path is not time-critical.

## Remaining Risks After Phase C

- `validate_candle_inputs` adds two DB queries per symbol (get_last_candle_time + get_latest_candles).
  - Impact: each call to `build_system_status` now makes ~6 extra queries (3 symbols × 2).
  - Acceptable: `build_system_status` is not on a hot path; called only on API requests.
  - Future mitigation: cache coverage result if query volume becomes a concern.

- Coverage threshold (`MINIMUM_COVERAGE_IN_WINDOW = 10`) is not yet validated against observed feed behavior.
  - Impact: the `DEGRADED` quality state may be triggered by normal feed patterns (e.g. brief gaps).
  - Mitigation: monitor `quality` field in `/system/status` for a few days before treating `DEGRADED` as actionable.

- `status_model.py` defines the contract but `build_system_status()` does not yet return a typed `SystemStatusSnapshot`.
  - Impact: type-checker cannot verify conformance of the dict with the model automatically.
  - Phase D follow-up: optionally migrate `build_system_status` to build and return a typed snapshot.

- Storage Protocol groups are structural (duck-typed).
  - Impact: type-checkers may not catch missing methods at import time unless mypy is run.
  - Acceptable for now; Protocols are documented and available for stricter checking when needed.

## Remaining Risks After Phase B

- Runtime snapshots are latest-state records, not append-only history.
  - Impact: recovery is deterministic, but historical debugging of every state transition still depends on `tr_risk_events` and logs.

- Pending setup persistence only covers engines with known in-memory pending state today.
  - Impact: any future engine with new volatile workflow state must explicitly implement persistence hooks.

- Startup consistency checks are intentionally conservative.
  - Impact: ambiguous pending setups may be invalidated even when a human operator could potentially salvage them.

- Bankroll-min pause recovery still assumes the existing bankroll model.
  - Impact: if bankroll is manually repaired outside normal trade flow, the pause record may need operator review.
