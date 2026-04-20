# Roadmap

Updated: 2026-04-20

## Phase A — Hardening do Core

- Status: completed in code on 2026-04-14.
- Goal met: improve operational robustness without changing runtime essence.

## Phase B — Persistent Service State And Recovery

- Status: completed in code on 2026-04-14.
- Delivered:
  - persisted engine runtime state
  - persisted pending setups
  - persisted runtime pauses
  - persisted worker runtime state
  - deterministic startup recovery with consistency checks

## Phase C — Interfaces Formais Mínimas do Core

- Status: completed in code on 2026-04-14.
- Delivered:
  - `core/data/candle_contract.py` — formal candle-collector → Core input contract
  - `core/monitoring/status_model.py` — typed schema + CONTRACT_VERSION for `/system/status`
  - `core/storage/interfaces.py` — explicit Protocol groups for TradingRepository
  - `/system/status` enriched with `contract_version`, `quality`, `coverage_in_window`
  - healthcheck now uses formal `validate_candle_inputs()` instead of inline loop

## Phase D — Recovery Maturity

- Status: completed in code on 2026-04-14.
- Delivered:
  - `tr_state_transitions` — append-only audit trail of status changes
  - State transition recording in worker (`_set_engine_heartbeat`, `_persist_worker_runtime_state`)
  - Startup KILL_STALE_DATA pause validation — auto-resumes if feed is fresh
  - Orphaned position detection at startup → `ORPHANED_POSITION` risk event
  - `GET /state-transitions` — audit log endpoint
  - `POST /operator/clear-pause/{pause_key}` — manual pause clearance
  - `POST /operator/reset-pending/{engine_id}` — manual pending setup reset
  - `GET /operator/recovery-state` — diagnostic dump for operators
  - `OPERATOR_TOKEN` env var for access control on write endpoints

## Phase E — Reviewer IA Operacional

- Status: completed in code on 2026-04-14.
- Delivered:
  - `core/monitoring/operational_reviewer.py` — analytical reviewer synthesis layer (read-only)
  - `GET /operator/reviewer-briefing` — canonical short briefing endpoint for operators/OpenClaw
  - Severity model: `CRITICAL | WARNING | INFO`
  - Finding taxonomy for stale/degraded/runtime/recovery/heartbeat/transition/healthy states
  - Stable briefing payload: `timestamp`, `overall_status`, `top_issues`, `engine_summary`, `operator_recommendations`

## Phase E.5 — Stabilização Operacional e Readiness

- Status: completed in code on 2026-04-14.
- Delivered:
  - `core/monitoring/operational_readiness.py` — objective readiness synthesis layer (read-only)
  - `GET /operator/readiness-report` — canonical readiness and promotion-gating endpoint
  - Explicit acceptance gates (`hard`, `soft`, `promotion`) for paper stability
  - Canonical readiness metrics for worker/recovery/reviewer/service/signal health
  - Promotion states: `NOT_READY`, `PAPER_STABLE`, `PAPER_STABLE_WITH_WARNINGS`, `READY_FOR_PHASE_F`
  - Operational acceptance playbook in `docs/trading_os/OPERATIONS_ACCEPTANCE.md`

## Phase F — Integração Derivativa Opcional e Controlada

- Status: completed in code on 2026-04-14.
- Delivered:
  - Optional read-only derivatives adapter in `core/storage/repository.py`
  - Formalized first derivatives feature contract and per-symbol normalized snapshot
  - Freshness/quality/availability checks with explicit fallback behavior
  - `/system/status` derivatives mode and observability expansion
  - Optional engine context enrichment (advisory path only)
  - Explicit feature flags for adapter and engine usage
  - Reviewer and readiness awareness of derivatives state/fallback
  - Canonical docs updated for Phase F boundaries and behavior

## Phase F.5 — Validação de Valor Operacional Derivativo

- Status: completed in code on 2026-04-14.
- Delivered:
  - canonical derivatives snapshot endpoint
  - derivatives readiness endpoint with metrics-derived state
  - advisory evidence events in worker runtime
  - explicit derivatives value metrics and degraded reason frequency
  - explicit next-step classification (`NO_VALUE_YET` → `CANDIDATE_FOR_FILTER_EXPERIMENT`)

## Phase G — Shadow Hard-Filter Experiment

- Status: completed in code on 2026-04-14.
- Delivered:
  - `core/engines/derivatives_filter.py` — shadow filter rules (FUNDING_RATE_EXTREME, OI_DELTA_ADVERSE, TAKER_AGGRESSION_LOW, OB_IMBALANCE_ADVERSE)
  - `ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED` flag — off by default, explicitly enabled on VPS
  - Worker shadow filter hook on ENTER signals — evaluates verdict, emits `DERIVATIVE_SHADOW_FILTER_PASS` / `DERIVATIVE_SHADOW_FILTER_BLOCK` risk events, never blocks trade
  - `SHADOW_FILTER_ACTIVE` readiness state (promotes from `CANDIDATE_FOR_FILTER_EXPERIMENT` when ≥10 evaluations)
  - `GET /operator/shadow-filter-report` — counterfactual analysis endpoint with trade outcome correlation
  - 14 unit tests in `tests/test_derivatives_filter.py`

## Fase 1 (Liquidity Zones) — Equal Levels + Sweeps

- Status: completed.
- `EqualLevelsProvider`, `SweepDetector` — OHLCV-based.

## Fase 1.5 (Liquidity Zones) — Swing, FVG, Prior Levels

- Status: completed.
- `SwingProvider`, `FairValueGapProvider`, `PriorLevelsProvider` — OHLCV-based.
- `LiquiditySnapshot` extended with `typed_zones` and `sweep_events`.

## Fase 2 V2 (Liquidity Zones) — LiquidationHeatmapProvider

- Status: completed 2026-04-17.
- Delivered:
  - `LiquidationHeatmapBin` dataclass + `get_liquidation_heatmap_snapshot()` in repository
  - `LiquidationHeatmapProvider` — async, fault-tolerant, reads pre-computed table from derivatives-collector
  - `LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED` flag + 3 tuning flags
  - 10 unit tests (mocks only), smoke script
- Original V1 approach (LiquidationEstimator with OI-delta projection) was replaced after diagnostic confirmed that the derivatives-collector already performs this calculation in `liquidation_heatmap` (updated every 5 min, calibrated with real liquidation events at 3× weight).

## Fase 3 (Liquidity Zones) — Volume Profile

- Status: planned. Scope: HVN/LVN detection from candle volume distribution.

## Fase 4 (Liquidity Zones) — Aggregator + Overlay Integration

- Status: planned. Integrates all providers (including LiquidationHeatmapProvider) into `BinanceLiquidityReader` and `overlay_evaluator`.

## Fase 5 (Liquidity Zones) — Validação Estatística

- Status: planned. Statistical evaluation of zone prediction accuracy.

## Carry Neutral BTC — Delta-Neutral Funding Capture

- Status: completed 2026-04-20. CARRY_NEUTRAL_ENABLED=false (ativar quando quiser iniciar paper trading).
- Delivered:
  - `core/engines/carry_neutral_btc.py` — `CarryNeutralBtcEngine` with `evaluate_carry()` interface
  - `core/execution/carry_fill_model.py` — open/funding/close simulation
  - `apps/carry_worker/main.py` — autonomous 5-min loop (independent of standard worker)
  - `GET /operator/carry-status` — status endpoint
  - `tr_paper_trades` extended with carry columns (idempotent migration)
  - `ENGINE_ROLES` dict in `settings.py` — explicit role declaration for all engines
  - Worker enforces ENGINE_ROLES at architectural level (SIGNAL_ONLY guard, EXPERIMENTAL skip)
  - `/system/status` exposes `role` per engine
  - docker-compose: `trading-carry-worker` service (disabled by default)
  - 26 unit tests across fill model and engine

## Cleanup de Narrativa — 2026-04-20

- Status: completed 2026-04-20.
- Delivered:
  - `ENGINE_ROLES` dict in `settings.py` as single source of truth for engine roles
  - Worker startup logs role of each engine; enforces SIGNAL_ONLY/EXPERIMENTAL at loop level
  - `/system/status` `role` field on each engine
  - README.md engines section rewritten to show ACTIVE/SIGNAL_ONLY clearly
  - `engine_specs_frozen.md` canonical state section added at top
  - DECISIONS.md 3 new entries (keep SIGNAL_ONLY engines, carry worker, ENGINE_ROLES)
  - `cn1-cn5` dead code tagged `# Reserved for future ACTIVE role`
  - docker-compose comments on service roles

## Next Phase (Planned)

- Phase H: analysis of shadow filter outcome correlation after ≥50 evaluations; optional threshold tuning and progression criteria for controlled hard-filter activation (still paper-only, no live execution).

## Later Phases

- Separate derivatives path once the spot/paper core is operationally stable.
- Broader architecture refactor only after review layer and derivatives path are proven.
- Any live execution or more autonomous AI remains explicitly deferred.
