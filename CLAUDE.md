# Trading OS — CLAUDE.md

## What this is

Eduardo's self-hosted trading OS ("Jarvis Capital") running on a VPS.
Two layers: **standalone paper traders** (Docker, no DB) + **core worker** (asyncio, PostgreSQL/Redis).

## Active strategies

| Container | Port | Strategy | Capital |
|---|---|---|---|
| `trading_ny_open_paper` | 8094 | NY Open 2C fade, BTCUSDT 5m | $100 / 1x |
| `trading_asian_dema_paper` | 8095 | R021-B Asian DEMA 1h (DEMA 13/34), SOLUSDT 1h | $1000 / 1x |
| `trading_rsi_reversion_paper` | 8093 | R021-A C1 RSI Reversion 5m (RSI14 os=10 ob=85), SOLUSDT 5m | $1000 / 1x |
| `trading_sol_burst_paper` | 8101 | R012 SOL Extreme Burst Reversal 5m (\|ret\|>2% fade), SOLUSDT 5m | $1000 / 1x |
| `trading_btc_lead_paper` | 8106 | BTC→ETH lead-lag 4h (btc_lead roc40/ema100), ETHUSDT | $1000 / 1x |
| `trading_taker_cap_paper` | 8107 | Taker-capitulation LONG 4h (z(taker LSR)<-2), BTC/ETH/SOL 1h | $1000 / 1x (3×$333) |
| `trading_vrp_paper` | 8108 | VRP: short straddle ATM semanal Deribit BTC, delta-hedged diário | $1000 / 1x vol-scaled |
| `trading_worker` | — | m3_eth_shadow (ETHUSDT SIGNAL_ONLY) | $200 |

Health check: `GET /health` or `/healthz` on each container's port.

### ny_open_mom_paper — APOSENTADO 2026-07-01 (Eduardo)
Look-ahead na entrada + re-backtest 30d sem edge. Container, engine, watcher de drawdown e bloco do compose removidos. Não reviver.

## Standalone paper traders (`trading/ny_open_paper/`, `trading/dow_3legs_paper/`, `trading/asian_dema_paper/`, `trading/rsi_reversion_paper/`, `trading/sol_burst_paper/`)

- No auth, no DB. REST feed from Binance public endpoints.
- State persisted to `/data/state.json` (volume-mounted).
- CSVs: `trades.csv`, `decisions.csv`, `equity_curve.csv`.
- Telegram notifications via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
- Each has a `--once` flag for smoke tests and `--replay-days N` for quick backtesting.

### dow_3legs_paper specifics — APOSENTADO 2026-06-14 (audit: sangria -8,8%, $50→$45,6)
Container removido, código em `trading/_archived/dow_3legs_paper_archived_20260614/`, serviço comentado no compose. Detalhes históricos abaixo.
- Trigger windows: Sun/Mon/Tue/Wed/Thu 23:55 UTC (±10 min tolerance).
- ATR gate refreshes daily 00:30–01:00 UTC. `DOW_DISABLE_GATE=true` to bypass.
- State machine: `flat → long_mon → flat → long_wed → [flat | short_thu] → flat`.
- Stale-position guard: force-closes any position open >30h (missed-close recovery).

### ny_open_paper specifics
- Session: 13:30–20:00 UTC weekdays. Decision bar at 14:00 UTC.
- Frozen model in `assets/2C_v1_frozen.json` — do NOT retrain without full validation.
- **Live execution (`EXECUTION_MODE`)**: default `shadow` (fills simulated only — safe).
  Set `NY_OPEN_EXECUTION_MODE=live` in `.env` to mirror the engine's position + native
  protective stop onto a REAL Binance (sub)account via `LiveBroker` (`live_broker.py`)
  + `BinanceLiveExecutor` (`executor_live.py`). Requires `BINANCE_<LIVE_ACCOUNT>_API_KEY/SECRET`
  (default account label `NY_OPEN`). Fixed real notional `NY_OPEN_LIVE_NOTIONAL_USD`
  (default $240, hard-capped $300), leverage `NY_OPEN_LIVE_LEVERAGE` (default 2x).
  The paper/shadow track keeps running in parallel; real fills logged to `data/live_fills.csv`.
  v1: single-unit position, stop follows engine incl. move-to-BE at TP1; does NOT bank the
  50% TP1 partial (Binance ~$100 min notional). Startup flattens any residual live position.

### asian_dema_paper specifics (R021-B Phase 0)
- Trigger: polls every minute, processes new closed 1h bars.
- Entry: DEMA(13) crosses DEMA(34) + slope(DEMA_slow, 3h) confirms, during Asian hours 00:00–07:59 UTC only.
- Exit: reverse crossover, any session (positions can close during London/NY).
- Data: asyncpg → `el_candles_1m` (jarvis_postgres 172.18.0.2:5432) → resample 1h.
- Fee: 13 bps RT. Notional: $1000/trade at 1x.
- Phase 0 gate: halts if WR < 22% after 30 trades; alerts at 4 consecutive losses.
- Status: `docker exec trading_asian_dema_paper python3 -m trading.asian_dema_paper.main --status`
- Replay: `docker exec trading_asian_dema_paper python3 -m trading.asian_dema_paper.main --replay-days 30`

### sol_burst_paper specifics (R012 Phase 0)
- Trigger: polls every 60s, detects new closed 5m bars from 1m candles.
- Entry: |ret_5m| > 2% → fade direction. SHORT se ret>+2%, LONG se ret<-2%. Entry at open of first 1m candle in next 5m bar.
- Exit: TP=0.5% / SL=0.25% / Timeout=30min from entry. Checked tick-by-tick on every 1m candle high/low.
- One position at a time (cooldown == full timeout).
- Data: asyncpg → `el_candles_1m` (jarvis_postgres 172.18.0.2:5432). No 5m resample stored — aggregated on the fly per tick.
- Fee: 13 bps RT. Notional: $1000/trade at 1x. PnL tracked in bps (matches R012 backtest convention).
- Phase 0 gate: halts if WR<42% after 50 trades; alerts at 4 consecutive losses.
- Status: `docker exec trading_sol_burst_paper python -m trading.sol_burst_paper.main --status`
- Replay: `docker exec trading_sol_burst_paper python -m trading.sol_burst_paper.main --replay-days 30`
- Migrado em 2026-05-12 do processo nu em `/home/agent/backtests/edge_factory/sol_extreme_burst_reversal_5m/phase0/` (PID 2773418). Zero trades disparados nos 7 dias anteriores.

### swing_wf_paper — APOSENTADO 2026-07-04 (Eduardo, pós-auditoria)
Revalidação com engine corrigido derrubou a expectativa p/ ~1,4%/mês / Sharpe 0,59 / maxDD -38%
(números do deploy estavam inflados pelo bug de shorts) e a tese 4h é redundante com o
btc_lead_paper, que a domina (~4,9%/mês / DD -33%). Encerrado com equity $978,74 (-2,1% em 15d,
5 trades). Container/compose/watcher removidos; código + data em
`trading/_archived/swing_wf_paper_archived_20260704/`. Não reviver sem edge novo. Detalhes históricos abaixo.

### swing_wf_paper specifics (4h walk-forward portfolio)
- Universe: BTC/ETH/SOL, 4h closed candles via Binance REST (no DB). $1000 total = 3×$333.33 sleeves, 1x.
- Walk-forward: every REOPT_BARS (540 ≈ 90d) closed bars each sleeve re-selects the best config (by train Sharpe, ≥10 trades, PF≥1.05) over the trailing TRAIN_BARS (2160 ≈ 360d) window.
- Config space: **signal-exit configs only** — the live state machine reproduces the research `run_backtest` EXACTLY for these (proven in `verify_fidelity.py`: 100% side-match, corr 1.0000). Stop-based exits excluded (unavoidable 1-bar live offset).
- Execution: signal on just-closed bar → fill at the **next bar's real open**, tick-rounded with adverse slippage. Costs 4+2 bps/side + real Binance funding per holding window (long pays positive).
- Researched in `/home/agent/research/swing_study/`. ⚠️ 2026-07-02: bug no P&L de shorts do backtest.py corrigido (era `entry/exit-1`, inflava shorts; agora `1-exit/entry`, USDT-M qty fixa). Engine ao vivo (engine.py) sempre esteve correto. **Revalidação 2026-07-04 (engine corrigido, dados até 04/07)**: expectativa real do portfólio só-sinal = **~1,4%/mês, PF mensal 1,57, Sharpe 0,59, maxDD -38%** (pior caso custos 2x+funding: 1,27-1,47%/mês). Metas antigas (2,7%/mo, PF 2,2) eram infladas pelo bug; mensal >2% NÃO se sustenta. Picks do otimizador corrigido = idênticos aos vivos (6/6 janelas testadas) — reopt forçada desnecessária. Ver adendo em `research/swing_study/REPORT.md`. Não alocar capital com base nos números pré-fix.
- Status: `docker exec trading_swing_wf_paper python -m trading.swing_wf_paper.main --status`
- Smoke / force reopt: `... main --once` / `... main --once --reopt` (only when the live container is stopped — health-port clash otherwise).

### btc_lead_paper specifics (single-asset study round-2 winner)
- Trades **ETHUSDT 4h in the direction of BTC's trend+momentum**: long when ROC(40) of BTC > 0 AND BTC > EMA(100) of BTC; short mirrored; exit when the condition breaks. Fixed config (no reopt), signal-exit only, $1000 / 1x.
- Validated 2023+ net of costs+funding (corrected engine): PF 1.53, 4.93%/mo, Sharpe 1.23, maxDD -33%, every year positive, MC p=0.013. Family walk-forward: 5.94%/mo 100% OOS. See `/home/agent/research/single_asset_2023/REPORT.md`.
- Structural clone of swing_wf_paper (same execution state machine, fidelity-proven); data via Binance REST (klines spot, funding fapi), no DB. Cycle ~2 min after each 4h UTC close.
- Telegram tag `BTCLEAD` (startup/open/close/daily 08:00 UTC/DD>20%/5 losses). Monitored by `trading_watcher.py`.
- Status: `docker exec trading_btc_lead_paper python -m trading.btc_lead_paper.main --status` | health `curl localhost:8106/healthz` (`/status` shows the current BTC roc/ema signal).

### taker_cap_paper specifics (H3 liqflow phase-2)
- **LONG-only, hold fixo de 4 barras 1h** após capitulação de taker: z do rolling-24h de log(taker buy/sell volume ratio) vs norma de 7 dias < -2. BTC/ETH/SOL, $1000 total = 3×$333.33 sleeves, 1x, config fixa (sem reopt).
- Validado 3,5 anos pré-registrado (fase 2 liqflow, 03/07/2026): +17,6bps bruto/trade, t=2,8, n=801, hit 58%, excesso sobre drift positivo nos 4 anos, 3/3 símbolos, dose-resposta monotônica. Standalone: CAGR 7-10%, maxDD -12%, Sharpe 0,9-1,25. Corr ≈ 0 com todos os sleeves do stable5. Ver `/home/agent/research/liqflow_study/REPORT.md`.
- Estrutura: clone do btc_lead_paper (mesmo Sleeve state machine); engine usa só o branch max_hold. Não-sobreposição fiel à pesquisa: sinal mascarado no bar em que havia posição no início (main.py `was_holding`).
- Dados via Binance REST fapi (klines 1h + `/futures/data/takerlongshortRatio` 5m paginado, ~30d de histórico — warm-up de 192h necessário). **Gotcha de timestamp**: o endpoint REST rotula buckets 5m pelo INÍCIO; a Binance Vision (fonte da pesquisa) pelo FIM — `exchange.fetch_taker_5m` soma +5min p/ replicar a convenção da pesquisa (corr 1.0 verificada no overlap jun/2026).
- Ciclo ~3 min após cada fechamento 1h UTC (buffer p/ o bucket 5m publicar). Custos 4+2bps/lado + funding real.
- Telegram tag `TAKERCAP` (startup/open/close/daily 08:00 UTC/DD>10%/6 losses). Monitorado por `trading_watcher.py`.
- Status: `docker exec trading_taker_cap_paper python -m trading.taker_cap_paper.main --status` | health `curl localhost:8107/healthz` (`/status` mostra o z atual por símbolo).

### vrp_paper specifics (short-vol Deribit — options study 04/07/2026)
- **Vende straddle ATM BTC toda sexta ~08:05 UTC** (vencimento sexta seguinte 08:00) no best
  bid REAL via API pública Deribit (sem chave); **hedge de delta diário** (ciclo 08:05) via perp
  paper ao index price, 6bps/ajuste; settle no delivery price oficial (fallback index após 3h).
  MtM horário com tickers reais. Sizing vol-scaled: `capital/S × min(0.55/DVOL, 2)`.
- Validado em `/home/agent/research/options_study/` (23,7M candles de opções 2022→2026-05,
  **pós-fix de look-ahead de DVOL/spot**, 2 trades verificados contra dados crus):
  2,56%/mês, PF trade 1,80, Sharpe 1,73, maxDD -17,9% @1x; OOS 2025-26 3,38%/mês; custo 2x → 1,9%/mês.
  Strangles OTM/condor/put-write = NEGATIVOS (custos comem prêmio OTM) — não "melhorar" pra OTM.
- Papel: perna de 60% do portfólio-alvo 60/30/10 (c/ btc_lead 8106 e taker_cap 8107):
  3,40%/mês, PFmo 5,79, Sharpe 2,07, maxDD -10,7% (2023→2026-05).
- Paper usa contratos fracionários; real exige mín. Deribit 0,1 contrato (~US$13k+ na perna).
- Status: `docker exec trading_vrp_paper python -m trading.vrp_paper.main --status` | health
  `curl localhost:8108/healthz` | Telegram tag `VRP`. Monitorado pelo `trading_watcher.py`.

### rsi_reversion_paper specifics (R021-A C1 Phase 0)
- Trigger: polls every minute, processes new closed 5m bars (30s grace after bar close).
- Entry: RSI(14 Wilder) crosses below 10 → LONG; crosses above 85 → SHORT. At bar close.
- Exit: RSI crosses 50, any session.
- Blacklist: hours 13–15 UTC and Tuesdays (permanent from R020/R021 research).
- Data: asyncpg → `el_candles_1m` → resample 5m. DB same as asian_dema_paper.
- Fee: 13 bps RT. Notional: $1000/trade at 1x.
- Phase 0 gate: halts if WR < 30% after 30 trades; alerts at 4 consecutive losses.
- Status: `docker exec trading_rsi_reversion_paper python3 -m trading.rsi_reversion_paper.main --status`
- Replay: `docker exec trading_rsi_reversion_paper python3 -m trading.rsi_reversion_paper.main --replay-days 30`

## Core worker (`trading/core/`)

- Runs `trading_worker` container. Reads `el_candles_1m` from `jarvis_edgelab` DB.
- Active engines: `m3_eth_shadow` (SIGNAL_ONLY).
- cn1–cn5 removed 2026-04-28 (zero signals ever, missing derivative data permanently); heartbeats limpos do DB em 2026-06-14.
- m3_sol APOSENTADO em 2026-06-14 (audit): negativo (-$5,43 / 5 trades em 3 meses), frequência baixíssima. Removido de registry + ENGINE_CONFIGS, marcado em tr_runtime_pauses. Código `m3_sol.py` mantido para reativação.
- m1_eth, m2_btc deleted — replaced by m3 family.
- `carry_neutral_btc` roda em container separado `trading_carry_worker` (não dispara: funding sempre abaixo do limiar).

## Key rules

- Closes always execute regardless of ATR gate — gate only blocks new opens.
- `FEE_RT_PCT = 0.10` is the roundtrip fee constant; defined in `config.py`, imported by engine.
- Never mock the DB in core worker tests — use real asyncpg connections.
- Don't touch `assets/2C_v1_frozen.json` — it's the production-frozen NY Open model.

## Common commands

```bash
# Health check
curl localhost:8094/health
curl localhost:8093/healthz
curl localhost:8095/healthz
curl localhost:8101/healthz
curl localhost:8104/healthz

# Smoke test (one tick)
docker exec trading_rsi_reversion_paper python3 -m trading.rsi_reversion_paper.main --once

# Replay last 14 days
docker exec trading_rsi_reversion_paper python3 -m trading.rsi_reversion_paper.main --replay-days 14

# Rebuild and restart a paper trader
docker compose build rsi_reversion_paper && docker compose up -d rsi_reversion_paper

# View live logs
docker logs -f trading_ny_open_paper
```
