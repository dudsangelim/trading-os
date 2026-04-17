# Trading OS State

Updated: 2026-04-17 (m3_eth_shadow promoted to ACTIVE)

## Runtime State After m3_eth_shadow Promotion (2026-04-17)

- `m3_eth_shadow` promoted from SIGNAL_ONLY → ACTIVE.
- Conservative fill logic added: signal fires only when `bar.low ≤ comp_high × (1-5bps) AND bar.close > comp_high` (LONG); espelhado (SHORT).
- `risk_per_trade_pct = 0.015` (50% do padrão) até N ≥ 30 trades reais.
- Spec e critérios de revisão: `trading/docs/engine_specs/m3_eth_shadow_SPEC.md`.
- Base estatística: backtest conservador N=77, WR 64.9%, edge +17 p.p.

## Runtime State After Engine Archival (2026-04-17)

- `m1_eth` and `m2_btc` archived (role: ARCHIVED) — structural negative expectancy confirmed by backtest audit.
  - m1_eth: 502 trades, WR 30.9% vs 49.2% break-even, Net PnL -$287.78
  - m2_btc: 3395 trades, WR 24.5% vs 61.1% break-even, Net PnL -$961.62
  - Autópsias: `trading/research_log/m1_eth_autopsy_2026-04-17.md`, `trading/research_log/m2_btc_autopsy_2026-04-17.md`
- Added ARCHIVED role to ENGINE_ROLES; worker skips ARCHIVED engines (same guard as EXPERIMENTAL).
- Promotion criteria for TF ≤ 15m setups formalized in DECISIONS.md D-01.

### Engines por role (2026-04-17)

| Engine | Role |
|--------|------|
| m1_eth | ARCHIVED |
| m2_btc | ARCHIVED |
| m3_sol | ACTIVE |
| m3_eth_shadow | ACTIVE (conservative fill, stake 50%) |
| cn1_oi_divergence | SIGNAL_ONLY |
| cn2_taker_momentum | SIGNAL_ONLY |
| cn3_funding_reversal | SIGNAL_ONLY |
| cn4_liquidation_cascade | SIGNAL_ONLY |
| cn5_squeeze_zone | SIGNAL_ONLY |
| carry_neutral_btc | ACTIVE (CARRY_NEUTRAL_ENABLED=false) |

## Runtime State After Carry Neutral + Cleanup (2026-04-17)

- `carry_neutral_btc` engine added. CARRY_NEUTRAL_ENABLED=false by default.
- `carry_worker` is a new autonomous loop (5-min interval, 8h funding cycle logic).
- `ENGINE_ROLES` is the canonical engine role registry in `settings.py`.
- `/system/status` now exposes `role` per engine.
- Worker enforces SIGNAL_ONLY and EXPERIMENTAL roles at the architectural loop level.
- Engines cn1-cn5 and m3_eth_shadow remain registered as SIGNAL_ONLY.
- `tr_paper_trades` extended with carry columns (trade_type, spot/perp prices, funding fields).

## Canonical Baseline

- `agents/stack/trading` is the canonical runtime core.
- `candle-collector` remains the official `el_candles_1m` supplier.
- `derivatives-collector` remains isolated and is integrated via optional read-only adapter (no hard dependency).
- OpenClaw remains the operational and monitoring consumer of runtime telemetry.

## Runtime State After Phase E

- Phase E added an analytical reviewer layer in [core/monitoring/operational_reviewer.py](/home/agent/agents/stack/trading/core/monitoring/operational_reviewer.py).
- Reviewer API exposure is available at [apps/api/main.py](/home/agent/agents/stack/trading/apps/api/main.py) via `GET /operator/reviewer-briefing`.
- Reviewer is read-only and non-decision:
  - does not send orders
  - does not change risk
  - does not alter engines
  - does not alter params
  - does not auto-clear pauses
  - does not decide trades
- Reviewer consumes canonical operational sources:
  - `GET /system/status`
  - `GET /state-transitions`
  - `GET /operator/recovery-state`

## Runtime State After Phase E.5

- Phase E.5 added operational readiness synthesis in [core/monitoring/operational_readiness.py](/home/agent/agents/stack/trading/core/monitoring/operational_readiness.py).
- Readiness API exposure is available via `GET /operator/readiness-report` in [apps/api/main.py](/home/agent/agents/stack/trading/apps/api/main.py).
- Readiness report is objective and gate-driven (not opinion-driven):
  - `current_readiness_status`
  - `blocking_issues`
  - `recent_incidents`
  - `recovery_health`
  - `signal_health`
  - `service_health`
  - `recommendation`
- Promotion classification states are now canonical:
  - `NOT_READY`
  - `PAPER_STABLE`
  - `PAPER_STABLE_WITH_WARNINGS`
  - `READY_FOR_PHASE_F`

## Runtime State After Phase B

- Worker loop hardening is active in [apps/worker/main.py](/home/agent/agents/stack/trading/apps/worker/main.py).
- API health/status hardening is active in [apps/api/main.py](/home/agent/agents/stack/trading/apps/api/main.py).
- Monitoring status synthesis is centralized in [core/monitoring/healthcheck.py](/home/agent/agents/stack/trading/core/monitoring/healthcheck.py).
- Circuit breaker metadata is richer in [core/risk/circuit_breakers.py](/home/agent/agents/stack/trading/core/risk/circuit_breakers.py).
- Runtime persistence and recovery are backed by:
  - `tr_engine_runtime_state`
  - `tr_pending_setups`
  - `tr_runtime_pauses`
  - `tr_worker_runtime_state`

## What Is Now Observable

- Real worker loop markers persisted via `tr_risk_events`:
  - `WORKER_LOOP_STARTED`
  - `WORKER_LOOP_COMPLETED`
  - `WORKER_LOOP_DEGRADED`
- Worker runtime snapshot persisted in `tr_worker_runtime_state`.
- Engine operational transitions reflected in `tr_engine_heartbeat.status`:
  - `PROCESSING`
  - `OK`
  - `BLOCKED_RISK`
  - `DEGRADED`
  - `STOPPED`
- Engine recovery state persisted in `tr_engine_runtime_state`:
  - `last_bar_seen`
  - `last_processed_at`
  - minimal serialized engine runtime markers
- Multi-bar setups for engines that need them are persisted in `tr_pending_setups`.
- Active pauses are persisted in `tr_runtime_pauses`.
- Explicit blocker events persisted via `tr_risk_events`:
  - `KILL_STALE_DATA`
  - `CIRCUIT_DD_DIARIO`
  - `CIRCUIT_BANKROLL_MIN`
  - `NO_VALID_INPUT`
  - `ENGINE_BLOCKED`
  - `ENGINE_RESUMED`

## What `/system/status` Now Consolidates

- Worker loop start/completion/degraded timestamps and loop age.
- Worker persisted runtime snapshot and freshness-check marker.
- DB connectivity status.
- Freshness of required symbols from `el_candles_1m`.
- Engine heartbeat state and detail.
- Engine persisted runtime state and pending setup when active.
- Open position per engine when present.
- Recent risk and operational blocker events plus active persisted pauses.

## Recovery Behavior

- Worker startup loads persisted worker state, engine runtime state, active pending setups and active pauses before entering the minute loop.
- Open positions are still rehydrated from `tr_paper_trades`.
- Startup invalidates pending setups that are expired, inconsistent with open positions or inconsistent with current candle context.
- Startup records `STARTUP_INCONSISTENCY` and degrades safely instead of operating on unverifiable saved state.

## What Phase D Added

- `tr_state_transitions` table — append-only audit log of engine and worker status transitions.
- State transition recording in `_set_engine_heartbeat` and `_persist_worker_runtime_state` (best-effort, status-change only).
- Startup KILL_STALE_DATA pause validation — auto-resumes when candle feed is fresh at restart.
  - Records `STARTUP_PAUSE_AUTO_RESUMED` risk event when auto-resumed.
- Orphaned position detection — open positions for unregistered engines produce `ORPHANED_POSITION` risk events.
- `OPERATOR_TOKEN` env var — controls auth on write-side operator endpoints.
- Operator repair API: `POST /operator/clear-pause/{pause_key}`, `POST /operator/reset-pending/{engine_id}`, `GET /operator/recovery-state`.
- `GET /state-transitions` — public audit log endpoint.

## What Phase E Added

- Operational reviewer model outputs:
  - `system_summary`
  - `active_alerts`
  - `degraded_components`
  - `recovery_findings`
  - `engine_highlights`
  - `operator_actions`
- Severity contract:
  - `CRITICAL`
  - `WARNING`
  - `INFO`
- Finding type coverage:
  - `stale input`
  - `degraded coverage`
  - `runtime inconsistency`
  - `recovery anomaly`
  - `prolonged blocked risk`
  - `missing heartbeat`
  - `no recent transitions`
  - `healthy steady-state`
- Canonical briefing block emitted by reviewer:
  - `timestamp`
  - `overall_status`
  - `top_issues`
  - `engine_summary`
  - `operator_recommendations`

## What Phase E.5 Added

- Explicit acceptance gates for paper stabilization and promotion gating.
- Canonical readiness metrics set:
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
- Readiness endpoint remains read-only and does not touch the critical trading loop.
- Operational acceptance process documented in `docs/trading_os/OPERATIONS_ACCEPTANCE.md`.

## What Phase C Added

- `core/data/candle_contract.py` — formal candle-collector → Core contract with freshness and coverage validation.
- `core/monitoring/status_model.py` — typed schema for `/system/status`; `CONTRACT_VERSION = "C.1"` is now broadcasted in every response.
- `core/storage/interfaces.py` — explicit Protocol groups for TradingRepository (IOperationalEvents, IEngineRuntimeState, IWorkerRuntimeState, IPendingSetups, IRuntimePauses, IEngineHeartbeat, ITradeRepository, IEquityRepository).
- `/system/status` now includes `contract_version`, richer per-symbol input quality (`quality`, `coverage_in_window`, `coverage_window_minutes`).

## Interfaces Status After Phase C

| Interface | Status | File |
|-----------|--------|------|
| candle-collector → Core | Formalized | `core/data/candle_contract.py` |
| worker → storage | Formalized | `core/storage/interfaces.py` |
| worker → API/status | Formalized | `core/monitoring/status_model.py` |
| OpenClaw → Core | Formalized | `GET /system/status` + `status_model.py` |
| worker → monitoring | Implicit (via healthcheck) | Remains implicit |
| derivatives-collector → Core | Optional + formalized | `core/storage/repository.py#get_derivatives_feature_snapshot` |

## Runtime State After Phase G

- Phase G added shadow hard-filter evaluation in [core/engines/derivatives_filter.py](/home/agent/agents/stack/trading/core/engines/derivatives_filter.py).
- Worker emits shadow filter evidence events on every ENTER signal when `ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED=true`:
  - `DERIVATIVE_SHADOW_FILTER_PASS` — signal passed the filter criteria
  - `DERIVATIVE_SHADOW_FILTER_BLOCK` — signal would have been blocked
- Shadow filter never affects the trading loop — it is a counterfactual observer only.
- New readiness state `SHADOW_FILTER_ACTIVE` (in `operational_readiness.py`) promotes from `CANDIDATE_FOR_FILTER_EXPERIMENT` once ≥10 evaluations are observed.
- New endpoint `GET /operator/shadow-filter-report` provides:
  - evaluation counts, block rate, triggered rules frequency
  - per-engine breakdown
  - trade outcome correlation (avg PnL / win rate for shadow-blocked vs shadow-passed)
- asyncpg jsonb codec fix applied globally to both `trading_api` and `trading_worker` pools.

## Runtime State After Fase 2 V2 (Liquidity Zones — LiquidationHeatmapProvider)

- `LiquidationHeatmapProvider` delivers `LIQUIDATION_CLUSTER` zones from the pre-computed `liquidation_heatmap` table in the derivatives-collector DB.
- Provider is async, tolerant to adapter failures, and never blocks the snapshot.
- New repository method `get_liquidation_heatmap_snapshot()` follows the same guard pattern as `get_derivatives_feature_snapshot()`:
  - Returns `None` if `DERIVATIVES_ENABLED=False` or `_derivatives_pool` unavailable
  - Returns `None` on any query error (logged, not raised)
  - Returns `None` if no snapshot within freshness window
- Risk event `LIQUIDATION_HEATMAP_UNAVAILABLE` emitted (to `tr_risk_events`) when derivatives is enabled but snapshot returns empty.
- New flags in `settings.py` (all default off):
  - `LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED`
  - `LIQUIDATION_HEATMAP_MIN_INTENSITY` (0.3)
  - `LIQUIDATION_HEATMAP_MAX_ZONES_PER_SIDE` (5)
  - `LIQUIDATION_HEATMAP_MAX_AGE_MINUTES` (10)
- Integration into `BinanceLiquidityReader` and `overlay_evaluator` is deferred to Fase 4.

## Runtime State After Fase 4 (Liquidity Zone Aggregator + Ghost Trade Variants)

- `LiquidityZoneAggregator` (`core/liquidity/zone_aggregator.py`) consolidates zones from all providers — sync (OHLCV) + async (derivatives). Provider failures are silenced; snapshot always returned.
- `BinanceLiquidityReader` routes through aggregator when `LIQUIDITY_AGGREGATOR_ENABLED=True`; legacy OHLCV path unchanged when disabled.
- `LiquidityOverlayEvaluator.evaluate()` now returns `List[OverlayDecision]` (1 or 3 elements):
  - `legacy` — existing behavior, byte-identical when variants disabled
  - `zones_nearest` — stop/target at nearest qualifying zone above/below entry
  - `zones_weighted` — stop/target at highest-intensity qualifying zone
- `OVERLAY_GHOST_VARIANTS_ENABLED=False` (default) → single-element list, byte-identical behavior
- Ghost trades now have `variant_label` column; dedup is by `(signal_id, variant_label)` — allows 3 ghost trades per signal
- New repository methods:
  - `get_ghost_trade_by_signal_variant(signal_id, variant_label)` — O(1) dedup lookup
  - `get_zone_comparison_stats(since_days, engine_id, min_trades_per_variant)` — per-variant Sharpe/win rate/PnL with bootstrap p-value
- New endpoint `GET /operator/zone-comparison-report` — read-only, operator-auth gated
- Schema migrations (idempotent): `tr_ghost_trades` gets `variant_label`, `zone_sources_used`, `stop_zone_info`, `target_zone_info`
- New flags in `settings.py` (all default off unless noted):
  - `LIQUIDITY_AGGREGATOR_ENABLED` (default off)
  - `LIQUIDITY_PROVIDER_SWING_ENABLED` (default on)
  - `LIQUIDITY_PROVIDER_FVG_ENABLED` (default on)
  - `LIQUIDITY_PROVIDER_PRIOR_LEVELS_ENABLED` (default on)
  - `OVERLAY_GHOST_VARIANTS_ENABLED` (default off)
  - `OVERLAY_VARIANTS_MIN_INTENSITY` (default 0.3)

## Explicitly Not Added Yet

- No hard dependency on derivatives feed.
- No derivatives hard filter enabled by default.
- No recovery workflow redesign.
- No engine refactor.
- No AI decision layer.
- No live execution changes.
## Runtime State After Phase F

- Phase F added an optional derivatives adapter in [core/storage/repository.py](/home/agent/agents/stack/trading/core/storage/repository.py) via `get_derivatives_feature_snapshot()`.
- Integration mode is explicit and reversible through flags in [core/config/settings.py](/home/agent/agents/stack/trading/core/config/settings.py):
  - `DERIVATIVES_ENABLED`
  - `DERIVATIVES_FEATURE_ADAPTER_ENABLED`
  - `ENGINE_DERIVATIVES_ADVISORY_ENABLED`
  - `ENGINE_DERIVATIVES_HARD_FILTER_ENABLED` (kept disabled by default in this phase)
- `/system/status` now exposes `derivatives` with:
  - adapter state and mode (`DISCONNECTED | ADVISORY_ONLY | DEGRADED | AVAILABLE`)
  - per-symbol availability/freshness/quality
  - feature availability by symbol
  - fallback state and reason
- Engine contexts now carry optional derivatives snapshot in `ctx.extra["derivatives"]`; engines remain fully operable without derivatives.
- Reviewer/readiness now mention derivatives availability/degradation/advisory/fallback without becoming hard blockers.

## Runtime State After Phase F.5

- Derivatives value validation is now explicit and metric-driven (advisory-only).
- Canonical snapshot endpoint:
  - `GET /operator/derivatives-snapshot`
  - emits per-symbol records with `timestamp`, `source_status`, `available_features`, `freshness`, `quality`, `fallback_active`, `fallback_reason`.
- Derivatives value/readiness endpoint:
  - `GET /operator/derivatives-readiness`
  - emits metrics and a readiness classification for derivatives usage progression.
- New metrics in readiness synthesis:
  - `derivative_availability_rate`
  - `derivative_fresh_rate`
  - `derivative_degraded_rate`
  - `derivative_fallback_rate`
  - `advisory_usage_count`
  - `symbols_with_real_feature_coverage`
  - `derivative_degraded_reason_frequency`
- Derived states for derivatives readiness:
  - `NO_VALUE_YET`
  - `INFRA_READY_DATA_POOR`
  - `ADVISORY_READY`
  - `CANDIDATE_FOR_FILTER_EXPERIMENT`
- Worker now records advisory evidence events:
  - `DERIVATIVE_CONTEXT_AVAILABLE`
  - `DERIVATIVE_CONTEXT_UNAVAILABLE`
  - `DERIVATIVE_CONTEXT_DEGRADED`
  - `DERIVATIVES_FALLBACK_ACTIVE`
  - `DERIVATIVES_DEGRADED`
