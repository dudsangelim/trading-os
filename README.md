# Trading OS

Sistema de trading algoritmico para criptomoedas com 9 engines independentes, pipeline de dados derivativos, e gestao de risco automatizada.

## Arquitetura

```
el_candles_1m (dados 1m) --> trading_worker (loop 60s)
                              |-> gera sinais por engine
                              |-> valida (circuit breakers, filtros)
                              |-> abre/fecha posicoes
                              |-> registra em tr_paper_trades
                              |-> alerta via Telegram
                              
trading_worker sinais ------> overlay_worker
                              |-> snapshot de liquidez (L2 orderbook)
                              |-> ghost trades (counterfactual)
                              |-> comparacao base vs ghost

trading_api (read-only) ----> endpoints de status, health, operator repair
```

### Servicos

| Servico | Container | Porta | Descricao |
|---------|-----------|-------|-----------|
| trading-worker | `trading_worker` | - | Loop principal — 9 engines, 60s/ciclo |
| trading-carry-worker | `trading_carry_worker` | - | Carry neutral BTC — loop autonomo 5min, funding capture delta-neutral |
| trading-overlay-worker | `trading_overlay_worker` | - | Avaliacao de liquidez + ghost trades |
| trading-api | `trading_api` | 8091 | FastAPI read-only + operator endpoints |
| PostgreSQL 16 | `trading_postgres` | 5432 | Storage principal (tabelas `tr_*`) |
| Redis 7 | `trading_redis` | - | Cache/queue |

### Servico externo (opcional)

| Servico | Porta | Descricao |
|---------|-------|-----------|
| derivatives-collector | 5433 | Coleta OI, funding, liquidacoes, taker aggression (Binance + Coinalyze) |

## Engines

O papel de cada engine e declarado explicitamente em `core/config/settings.py → ENGINE_ROLES`.
Roles: `ACTIVE` (abre paper trades), `SIGNAL_ONLY` (gera sinais apenas), `EXPERIMENTAL` (desabilitada).

### ACTIVE (abrem paper trades)

| Engine | Par | Estrategia | Timeout | Notas |
|--------|-----|------------|---------|-------|
| **m3_sol** | SOLUSDT | Retest com trend_ok EMA | 64 bars | Spec frozen |
| **m3_eth_shadow** | ETHUSDT | Direct ajustado (lookback 8) | 64 bars | Promovida 2026-04-17 |
| **carry_neutral_btc** | BTCUSDT spot+perp | Delta-neutral funding capture | 168h | Worker dedicado (trading-carry-worker) |

### SIGNAL_ONLY (geram sinais, nao abrem trades — para analise e shadow filter)

| Engine | Estrategia | Proposito |
|--------|------------|-----------|
| **cn1_oi_divergence** | OI-price divergence | Shadow dataset para derivatives filter |
| **cn2_taker_momentum** | Taker buy/sell ratio | Shadow dataset |
| **cn3_funding_reversal** | Funding rate extremo | Shadow dataset (relacionado ao carry) |
| **cn4_liquidation_cascade** | Proximidade de zonas | Shadow dataset |
| **cn5_squeeze_zone** | BB squeeze + metricas cripto | Shadow dataset |

### ARCHIVED (codigo preservado, engine nao executada)

| Engine | Par | Motivo | Evidencia |
|--------|-----|--------|-----------|
| **m1_eth** | ETHUSDT | Expectancy negativa — custo round-trip aniquila gross | `research_log/m1_eth_autopsy_2026-04-17.md` |
| **m2_btc** | BTCUSDT | Expectancy negativa — custo round-trip aniquila gross | `research_log/m2_btc_autopsy_2026-04-17.md` |

Classic engines: **$1000 capital, 3% risco/trade, 10x leverage, $300 stake maximo**.
Carry: stake fixo $300 por posicao, sem leverage aplicada (delta-neutral).

## Gestao de Risco

### Circuit Breakers (avaliados em ordem)

1. **KILL_STALE_DATA** — candles sem atualizacao >10min bloqueia tudo
2. **KILL_HEARTBEAT** — worker silencioso >15min, alerta critico
3. **CIRCUIT_DD_DIARIO** — drawdown diario >=5% pausa a engine ate meia-noite UTC
4. **CIRCUIT_BANKROLL_MIN** — bankroll <$200 pausa indefinidamente
5. **ONE_POSITION_PER_ENGINE** — max 1 posicao aberta por engine

### Position Sizing

```
stake_usd = bankroll * risk_per_trade_pct * leverage
# $1000 * 0.03 * 10 = $300 por trade
```

### Ciclo de Vida da Posicao

1. Sinal gerado -> `tr_signals`
2. Sinal validado -> `tr_signal_decisions` (ENTER / SKIP / SIGNAL_ONLY)
3. Posicao aberta -> `tr_paper_trades` (status=OPEN)
4. Break-even: lucro >= 0.5R -> stop movido para entrada
5. Fechamento: Stop Loss / Take Profit / Timeout
6. PnL registrado -> `tr_equity` atualizado

### Modelo de Execucao

- **Entry**: preco do sinal (sem slippage no trigger bar)
- **Stop Loss**: market order com slippage (5 bps BTC/ETH, 8 bps SOL)
- **Take Profit**: limit order sem slippage
- **Timeout**: market order com slippage
- **Fee**: 10 bps por lado (taker Binance)

## Pipeline de Dados

### Candles

- Fonte: tabela `el_candles_1m` (OHLCV 1min para BTC, ETH, SOL)
- Agregacao: 1m -> 15m (engines) e 1m -> 4h (regime HMM do M1)

### Dados Derivativos (opcional)

DB separado em porta 5433 com features:
- `orderbook_imbalance`, `funding_rate`, `funding_rate_zscore`
- `oi_delta_1h`, `oi_delta_4h`, `taker_aggression`
- `liquidation_intensity`, `squeeze_score`, `trend_conviction`

Freshness: <600s = FRESH, >1800s = STALE (fallback automatico)

### Overlay de Liquidez

- Snapshots do orderbook L2 da Binance
- Zonas de suporte/resistencia computadas por sinal
- Ghost trades comparam resultado com e sem ajuste de liquidez

## Monitoramento

### Endpoints

```
GET /health                    # conectividade simples
GET /system/status             # snapshot operacional completo
POST /operator/clear-pause/{k} # resume engine pausada (auth)
POST /operator/reset-pending/{e} # limpa pending setup (auth)
GET /operator/recovery-state   # diagnostico completo (auth)
GET /operator/reviewer-briefing # revisao operacional (auth)
```

### Telegram Alerts

Alertas automaticos para:
- Sinal aceito/rejeitado
- Trade aberto/fechado com PnL
- Dados stale (candles atrasadas)
- Drawdown diario atingido
- Divergencia do overlay de liquidez

### Scripts Cron

| Script | Schedule | Descricao |
|--------|----------|-----------|
| `scripts/trading_daily_review.py` | 07:00 UTC | Report diario: PnL, win rate, custos, skips, alertas |
| `scripts/trading_watcher.py` | */10 min | Health check: worker status, derivativos, regressoes |

## Banco de Dados (tabelas principais)

### Trading Core
- `tr_signals` — sinais gerados por engine
- `tr_signal_decisions` — decisao de validacao (ENTER/SKIP/SIGNAL_ONLY)
- `tr_paper_trades` — posicoes (open/closed, entry/exit, PnL)
- `tr_equity` — snapshots diarios de bankroll por engine
- `tr_engine_heartbeat` — ultimo ciclo completado

### Risk & Monitoring
- `tr_risk_events` — log append-only (sinais, circuit breakers, pausas)
- `tr_runtime_pauses` — pausas ativas (DD limit, bankroll min)

### Overlay
- `tr_liquidity_snapshots` — zonas de orderbook por sinal
- `tr_overlay_decisions` — comparacoes ghost trade
- `tr_ghost_trades` — trades counterfactuais
- `tr_trade_comparisons` — delta metrics base vs ghost

## Tech Stack

- **Python 3.10+** — asyncio loop
- **AsyncPG 0.29** — PostgreSQL async
- **FastAPI 0.111** — API REST
- **hmmlearn 0.3** — HMM regime classifier (M1)
- **NumPy 1.26+** / **scikit-learn 1.4** — calculo numerico
- **Httpx 0.27** — HTTP async (Telegram)
- **PostgreSQL 16** — storage principal
- **Redis 7** — cache

## Setup

```bash
# 1. Clone
git clone https://github.com/dudsangelim/trading-os.git
cd trading-os

# 2. Configurar
cp .env.example .env
# Editar .env com suas credenciais

# 3. Subir
docker compose up -d

# 4. Verificar
curl http://127.0.0.1:8091/health
curl http://127.0.0.1:8091/system/status
```

## Fases do Projeto

- [x] **A-C**: Engines classicas (M1/M2/M3)
- [x] **D**: Overlay de liquidez (ghost trades + comparacoes)
- [x] **E**: Operational reviewer + readiness gates
- [x] **F**: Integracao de derivativos (OI, funding, liquidacoes, taker)
- [x] **G**: Shadow filter (avaliacao counterfactual)
- [ ] **H**: Promocao para trading ao vivo
