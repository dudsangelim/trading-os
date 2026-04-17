# Decisions

Updated: 2026-04-17

## D-01 (2026-04-17): Setups micro em TF ≤ 15m — critérios de promoção

**Contexto:** m1_eth e m2_btc foram arquivados por expectancy estruturalmente negativa. Ambos seguiam o padrão "compression breakout em TF ≤ 15m com SL estrutural < 1% do preço". Backtests em 502 e 3.395 trades respectivamente confirmaram que o custo round-trip (32bps) aniquila o gross positivo gerado pelo sinal.

**Decisão:** Setups em timeframe ≤ 15m cujo SL médio fique abaixo de 1.5× ATR do próprio TF **não podem receber role: ACTIVE** sem cumprir, cumulativamente:

1. Backtest com N ≥ 300 trades em período ≥ 18 meses
2. Expectancy líquida (após fees+slippage modelados realisticamente) positiva com margem ≥ 2× o custo round-trip
3. WR empírico ≥ break-even WR + 10 p.p.
4. Stop em ≤ 2 barras < 10% dos trades
5. Cost/gross < 50%

**Rationale:** Esses critérios garantem que o edge observado sobreviva a degradação de execução real (slippage variável, fills parciais, mudança de regime de liquidez).

**Aplicabilidade retroativa:** Aplicar a qualquer engine em status SIGNAL_ONLY ou GHOST antes de considerar promoção para ACTIVE.

**Evidências de origem:**
- Autópsia m1_eth: `trading/research_log/m1_eth_autopsy_2026-04-17.md`
- Autópsia m2_btc: `trading/research_log/m2_btc_autopsy_2026-04-17.md`

## 2026-04-17 — Decision: keep SIGNAL_ONLY engines registered, not delete

- Decision: engines `cn1-cn5` and `m3_eth_shadow` remain in registry as `SIGNAL_ONLY` despite never opening paper trades. Not removed from codebase.
- Why:
  1. They emit signals that feed the `shadow_filter` dataset (Phase G)
  2. Removing them would delete historical signal data from `tr_signals`
  3. Any can be promoted to `ACTIVE` by editing `ENGINE_ROLES` in `settings.py` without code changes
  4. Dead code review performed: `build_order_plan` and `manage_open_position` retained as `# Reserved for future ACTIVE role` where non-trivial; pure stubs removed where present
- How to change role: edit `core/config/settings.py → ENGINE_ROLES`, restart worker. Role is logged at startup and visible in `/system/status`.

## 2026-04-17 — Carry Neutral BTC: autonomous loop, not standard worker

- Decision: `carry_neutral_btc` engine runs via a dedicated `carry_worker` (5-min loop) instead of the standard `trading_worker` (1-min bar-based loop).
- Why: carry operates on 8h funding cycles, not 15m bars. Running it through the standard worker would require complex special-casing. A dedicated worker is simpler and isolates failure domains.
- Consequence: carry_worker has its own heartbeat (`carry_neutral_btc` in `tr_engine_heartbeat`). The standard worker's registry does not include carry. CARRY_NEUTRAL_ENABLED=false by default.

## 2026-04-17 — ENGINE_ROLES as authoritative role registry

- Decision: add `ENGINE_ROLES` dict to `settings.py` as the authoritative source for engine roles (`ACTIVE`, `SIGNAL_ONLY`, `EXPERIMENTAL`).
- Why: previously `signal_only` was implicit (engines returning SIGNAL_ONLY in validate_signal). Bug-prone: a code change in any engine could accidentally open trades. Explicit config is robust.
- Consequence: worker startup logs each engine's role. Worker loop enforces SIGNAL_ONLY at the architectural level even if validate_signal returns ENTER. `/system/status` exposes `role` per engine.

## 2026-04-17 — Fase 2 V2: consumir liquidation_heatmap diretamente em vez de reimplementar o estimador

- Decision: `LiquidationHeatmapProvider` lê a tabela `liquidation_heatmap` do derivatives-collector em vez de reimplementar projeção de liquidações a partir de OI bruto.
- Why: diagnóstico confirmou que o derivatives-collector já executa o mesmo cálculo (OI série × leverage distribution × long/short ratio × calibração com liquidações reais em 3×). Reimplementar seria duplicação sem ganho.
- Consequence: Fase 2 V2 é ~4× mais simples que V1, sem nova lógica de estimativa. Risco de divergência de modelo é eliminado. A tabela é atualizada a cada 5 min com TTL de 48h — fresher que qualquer snapshot OHLCV.

## 2026-04-17 — Fase 2 V2: LiquidationHeatmapBin como modelo do adapter, não de zona

- Decision: `LiquidationHeatmapBin` fica em `repository.py` (adapter layer), não em `zone_types.py`.
- Why: é o modelo interno do adapter, não um modelo de domínio de liquidity zone. O output do provider é `LiquidityZone` — essa é a fronteira de domínio.
- Consequence: `zone_types.py` permanece limpo; provider converte de bin para zona.

## 2026-04-17 — Fase 2 V2: risk event só emitido quando DERIVATIVES_ENABLED=True

- Decision: `LIQUIDATION_HEATMAP_UNAVAILABLE` risk event é emitido apenas quando `DERIVATIVES_ENABLED=True` e o snapshot retorna vazio.
- Why: quando derivatives está desabilitado por design (default), emitir um risk event a cada ciclo seria spam na tabela `tr_risk_events` e levaria a falsos alertas operacionais.
- Consequence: eventos de risco refletem falhas inesperadas, não estado intencional de feature flag.

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
