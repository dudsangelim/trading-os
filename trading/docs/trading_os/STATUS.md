# Trading OS Status

Updated: 2026-04-14

## Done

- Phase A core hardening remains active on worker, monitoring, risk and API.
- Phase B persistence and recovery implemented on worker, monitoring, storage and affected engines.
- Phase C formal interfaces remain active (`candle_contract`, `status_model`, storage Protocols).
- Phase D observability/recovery maturity remains active (`state-transitions`, `operator/recovery-state`, operator repair endpoints).
- Phase E Reviewer IA Operacional implemented:
  - read-only reviewer synthesis in `core/monitoring/operational_reviewer.py`
  - endpoint `GET /operator/reviewer-briefing`
  - canonical briefing output (`timestamp`, `overall_status`, `top_issues`, `engine_summary`, `operator_recommendations`)
  - finding taxonomy + severity (`CRITICAL`, `WARNING`, `INFO`)
  - no change to worker critical loop, no strategy/risk/live execution mutation
- Phase E.5 Stabilização Operacional e Gating de Promoção implemented:
  - readiness synthesis in `core/monitoring/operational_readiness.py`
  - endpoint `GET /operator/readiness-report`
  - objective acceptance gates (hard/soft/promotion)
  - canonical promotion states (`NOT_READY`, `PAPER_STABLE`, `PAPER_STABLE_WITH_WARNINGS`, `READY_FOR_PHASE_F`)
  - canonical readiness metrics for worker/recovery/reviewer/service/signal health
- Phase F Integração Derivativa Opcional e Controlada implemented:
  - read-only derivatives adapter in `core/storage/repository.py`
  - explicit mode/status in `GET /system/status` (`DISCONNECTED`, `ADVISORY_ONLY`, `DEGRADED`, `AVAILABLE`)
  - optional engine context enrichment with derivatives snapshot in `apps/worker/main.py`
  - explicit feature flags in `core/config/settings.py`
  - reviewer/readiness awareness of derivatives availability/degradation/fallback
  - no hard dependency and no live execution change
- Phase F.5 Validação de Valor Operacional das Features Derivativas implemented:
  - canonical snapshot endpoint `GET /operator/derivatives-snapshot`
  - derivatives value/readiness endpoint `GET /operator/derivatives-readiness`
  - operational value metrics:
    - `derivative_availability_rate`
    - `derivative_fresh_rate`
    - `derivative_degraded_rate`
    - `derivative_fallback_rate`
    - `advisory_usage_count`
    - `symbols_with_real_feature_coverage`
  - derived readiness states:
    - `NO_VALUE_YET`
    - `INFRA_READY_DATA_POOR`
    - `ADVISORY_READY`
    - `CANDIDATE_FOR_FILTER_EXPERIMENT`
  - advisory context events in worker:
    - `DERIVATIVE_CONTEXT_AVAILABLE`
    - `DERIVATIVE_CONTEXT_UNAVAILABLE`
    - `DERIVATIVE_CONTEXT_DEGRADED`

## Done (continued)

- Phase G Shadow Filter implemented:
  - `core/engines/derivatives_filter.py` — shadow filter evaluation (PASS/BLOCK, rules, feature values)
  - `core/config/settings.py` — `ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED` flag
  - `apps/worker/main.py` — shadow filter hook on ENTER (never blocks, emits risk events)
  - `core/monitoring/operational_readiness.py` — shadow filter metrics + `SHADOW_FILTER_ACTIVE` state
  - `apps/api/main.py` — `GET /operator/shadow-filter-report` endpoint
  - `tests/test_derivatives_filter.py` — 14 unit tests
  - `tests/test_operational_readiness.py` — 2 new Phase G assertions

## Bug Fix (F.5)

- `apps/api/main.py` + `apps/worker/main.py` — asyncpg jsonb codec registered on pool init
  (asyncpg 0.29.0 returns jsonb as string without explicit codec registration)
- `core/monitoring/operational_readiness.py` — defensive json.loads fallback for payload field
- `core/monitoring/healthcheck.py` — same defensive fallback

## Engine Archival (2026-04-17)

- `m1_eth` archived: role changed to ARCHIVED. Backtest audit (502 trades) confirmed structural negative expectancy. See `trading/research_log/m1_eth_autopsy_2026-04-17.md`.
- `m2_btc` archived: role changed to ARCHIVED. Backtest audit (3395 trades) confirmed structural negative expectancy. See `trading/research_log/m2_btc_autopsy_2026-04-17.md`.
- ARCHIVED role added to ENGINE_ROLES; worker skips ARCHIVED engines at architectural loop level.
- Promotion criteria for TF ≤ 15m setups formalized in DECISIONS.md D-01.

## In Progress

- No active implementation in progress.

## Pending

- Accumulate shadow filter samples over real trading (evaluate_count ≥ 50) before tuning thresholds.
- Analysis of outcome_correlation in `/operator/shadow-filter-report` after sufficient samples.
- Any live/autonomous AI discussion remains explicitly out of current scope.
