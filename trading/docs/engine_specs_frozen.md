# Portfólio Trading OS — Estado Canônico

**Atualizado:** 2026-04-20

## Engines ACTIVE (paper trading)
- M3 SOL: retest, TF 15m com signal em 1h, trend_ok EMA
- M3 ETH Shadow: direct breakout ajustado (lookback 8, max_width 2.0) — promovida 2026-04-17
- Carry Neutral BTC: delta-neutral, TF 8h (funding cycle), multi-dia

> **carry_neutral_btc** roda em worker dedicado (`trading-carry-worker`), não passa pelo
> registry do `trading_worker` principal. A mensagem `ENGINE_ROLE_LOADED` não é emitida
> para esta engine — o startup log equivalente é `carry_enabled: true` no carry-worker.

## Engines ARCHIVED (código preservado, engine não chamada pelo worker)
- M1 ETH: compression breakout + HMM regime — arquivada 2026-04-17 (expectancy negativa, ver research_log/)
- M2 BTC: sweep+reclaim — arquivada 2026-04-17 (expectancy negativa, ver research_log/)

## Engines SIGNAL_ONLY (dataset apenas)
- CN1-5: experimentos crypto-native shadow

### Como mudar o role de uma engine
Edite `core/config/settings.py` → `ENGINE_ROLES`.
Valores válidos: `ACTIVE`, `SIGNAL_ONLY`, `EXPERIMENTAL`, `ARCHIVED`.
Reinicie o worker. Status é logado no startup e aparece em `/system/status` (campo `role`).

## Decisões de design vigentes
- HMM em produção: Opção B (modelo fixo, treinamento único), pendente resolução
- Carry variant: `sell_accrual` (Presto Sharpe 6.71)
- Overlay aplicado apenas a engines direcionais (m1/m2/m3, não carry)
- Todas engines `SIGNAL_ONLY` alimentam shadow filter da Fase G

---

# Engine Specs — SPEC FREEZE
## Sistema B: Trading em Corretoras / Jarvis Capital Exchange
**Versão:** 3.0-frozen
**Data:** 2026-03-24
**Status:** FROZEN — blockers B2–B6 resolvidos via `motor3_validation.py`. Apenas B1 (estratégia HMM em produção) permanece aberto.

---

> **FONTE DE VERDADE (ordem de prioridade)**
> 1. `motor3_validation.py` — código operacional M3 (zona, trend_ok, execução)
> 2. `eth_btc_portfolio.py` — código operacional reconciliado do portfólio M1+M2
> 3. `ETH_REGIME_FOLLOWUP_REPORT.md` — estudo IS/OOS M1 ETH
> 4. `BTC_REGIME_FOLLOWUP_REPORT.md` — estudo IS/OOS M2 BTC
> 5. `MOTOR3_REPORT.md` — validação M3 SOL (direct/retest/halfpull)
> 6. `block_a_standalone.py` — código de estudo setup sweep/reclaim
> 7. `motor3_trades_SOL_retest.csv` — trades M3 SOL retest (dados de estudo)
> 8. `briefs/trading_paper_v1.md` + `briefs/trading_runtime_v1.md` — arquitetura V1
>
> **PROIBIDO como fonte para este documento:**
> qualquer lógica, padrão ou threshold do Sistema A (EdgeLab / Polymarket / contratos binários).

---

## PORTFÓLIO CONGELADO

| Motor | engine_id | Ativo | Estratégia | Papel |
|-------|-----------|-------|-----------|-------|
| M1 | `m1_eth` | ETHUSDT | Trend-Drift (compression+breakout) | ACTIVE — paper trade |
| M2 | `m2_btc` | BTCUSDT | Compressed Balance (sweep+reclaim) | ACTIVE — paper trade |
| M3 principal | `m3_sol` | SOLUSDT | Retest | ACTIVE — paper trade |
| M3 sombra | `m3_eth_shadow` | ETHUSDT | Direct ajustado (lookback 8, max_width 2.0) | SIGNAL_ONLY — sem posição |

---

---

# MOTOR 1 — ETH / Trend-Drift (`m1_eth`)

## 1. Identidade
- **engine_id:** `m1_eth`
- **Ativo:** ETHUSDT
- **Papel:** M1 — braço ETH intradiário principal, ACTIVE (abre posições paper)

## 2. Timeframes
- **TF de execução (setup + entry):** 15 minutos
- **TF de regime/contexto:** 4 horas (HMM GaussianHMM, 4 estados)
- **TF de sinal:** 15m — sinal gerado após fechamento da barra de breakout
- **TF de filtro:** 4h — regime deve ser "Trend / drift" na barra 4h atual

> **CONFIRMADO** — `eth_btc_portfolio.py` linha 159: `"""ETH: ONLY Trend/Drift, 13-18 window, full stop, 1R, timeout=8."""`

## 3. Regime / contexto

### Regime requerido
- **Nome:** "Trend / drift"
- **Modelo:** GaussianHMM, n_components=4, covariance_type="full", n_iter=200
- **Features (4h):** log_ret, range_pct, body_ratio, roll_vol (rolling 6 barras), vol_z (rolling 24 barras), dir_eff
- **Mapeamento de estado:** greedy por score composto (dir_eff × 0.6 + log_ret_abs × 0.4 para "Trend/drift")

### Obrigatoriedade
- **OBRIGATÓRIO** — se regime 4h atual ≠ "Trend / drift", barra é ignorada completamente
- Código: `if df.iloc[i]["macro_family"] != "Trend / drift": continue`

### Ausência de contexto
- Se regime 4h indisponível (dados stale, HMM não inicializado): **SKIP — não entrar**
- Tratamento: `SignalDecision(action=SKIP, reason="SKIP_REGIME_UNAVAILABLE")`

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 169, 114–127

## 4. Setup

### Definição do padrão
Compression + Breakout em 15m dentro do regime Trend/Drift.

**Compressão:**
1. Janela de 4 velas consecutivas: bars `[i-3 : i+1]` (inclusive — 4 barras)
2. `ch = max(high)` e `cl = min(low)` dessas 4 barras
3. Condição de largura: `(ch - cl) / close[i] < atrp35 × 2.5`
   - `atrp35` = percentil 35 do ATR relativo das últimas 48 barras 15m (= 12h)
   - ATR relativo de cada barra: `(high - low) / close`
4. Condição de corpo: nenhuma das 4 barras pode ter `|close - open| / close > 0.003` (0.3%)

**Breakout (gatilho de sinal):**
- Barra `i+1` fecha acima de `ch` → LONG
- Barra `i+1` fecha abaixo de `cl` → SHORT
- Se ambos (raro): lado com maior desvio vence

### Parâmetros congelados
| Parâmetro | Valor | Origem |
|-----------|-------|--------|
| `compression_bars` | 4 | código `cb = df.iloc[i-3:i+1]` |
| `atr_lookback_bars` | 48 (= 12h de 15m) | código `rolling(48, min_periods=12)` |
| `atr_percentile` | 35 | código `.quantile(0.35)` |
| `width_multiplier` | 2.5 | código `cr > th * 2.5` → skip |
| `max_body_pct` | 0.003 (0.3%) | código `bp > 0.003` → skip |
| `entry_window_utc` | 13:00–17:59 UTC (hours 13,14,15,16,17) | código `hours.isin({13,14,15,16,17})` |

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 162–181

## 5. Entrada
- **Gatilho:** fechamento da barra de breakout (bar `i+1`) fora da zona de compressão
- **Tipo:** breakout direto — sem esperar retest
- **Preço de referência:** `close` da barra de breakout (bar `i+1`)
- **Preço de entrada paper:** `close × (1 + slippage_bps/10_000)` para LONG, `close × (1 - slippage_bps/10_000)` para SHORT
- **Janela de validade:** apenas 1 barra — ou entra no fechamento do breakout bar, ou descarta
- **Barra de sinal gerado:** bar `i` (última barra de compressão)
- **Barra de execução:** bar `i+1` (breakout bar)

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 177–199: `e = bo["close"]` onde `bo = df.iloc[i+1]`

## 6. Stop
- **Definição LONG:** `stop = cl` (mínimo da zona de compressão)
- **Definição SHORT:** `stop = ch` (máximo da zona de compressão)
- **Sem buffer adicional** (ao contrário do M2 que usa 1bp)
- **Acionamento LONG:** `candle.low ≤ stop_price`
- **Acionamento SHORT:** `candle.high ≥ stop_price`
- **Prioridade de saída:** Stop > Target > Timeout

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 185–187

## 7. Target
- **Definição:** 1R fixo (full)
- **Cálculo:** `R = entry - stop` (LONG) ou `R = stop - entry` (SHORT)
- **Target LONG:** `target = entry + R`
- **Target SHORT:** `target = entry - R`

> **CONFIRMADO** — código `tgt = e + (e - cl)` → 1R | ETH report Q3: "full_1R melhor OOS"

## 8. Timeout
- **Definição:** 8 barras de 15m após a barra de entrada
- **Contagem:** bars `i+2` até `i+9` (8 barras avaliadas após o breakout)
- **Saída:** ao `close` da 8ª barra se stop e target não forem atingidos antes
- **Unidade:** barras 15m

> **CONFIRMADO** — `eth_btc_portfolio.py` linha 190: `range(i+2, min(i+10, n))`
> ETH report Seção 7: "timeout=8c → IS +0.1639R, OOS +0.2094R (melhor)"

## 9. Invalidações
- Regime 4h ≠ "Trend / drift" → barra ignorada
- Compressão muito larga (`cr > atrp35 × 2.5`) → barra ignorada
- Qualquer barra da compressão com corpo > 0.3% → barra ignorada
- Bar `i+1` não rompe nenhum lado da compressão → sem sinal
- Bar `i+1` está fora da janela 13:00–17:59 UTC → sem sinal
- Motor já tem posição OPEN → circuit breaker ONE_POSITION_PER_ENGINE
- DD diário atingido → CIRCUIT_DD_DIARIO (pausa até meia-noite UTC)
- Bankroll < $50 → CIRCUIT_BANKROLL_MIN
- Stale data > 10min → KILL_STALE_DATA

## 10. Sizing / risco
| Parâmetro | Valor |
|-----------|-------|
| `risk_per_trade_pct` | 1.0% da banca atual |
| `initial_capital_usd` | $200 |
| `max_daily_dd_pct` | 3.0% |
| `min_bankroll_usd` | $50 |
| `slippage_bps` | 5 (entrada + saída) |

**Fórmula de sizing:**
`stake_usd = bankroll × risk_per_trade_pct / (|entry_price - stop_price| / entry_price)`

> **CONFIRMADO** — `settings.py` + `briefs/trading_runtime_v1.md`

## 11. Campos de auditoria (`signal_data` JSONB)
```json
{
  "setup_label": "compression_breakout",
  "regime_label": "Trend / drift",
  "compression_high": "<float>",
  "compression_low": "<float>",
  "atrp35": "<float>",
  "compression_width_pct": "<float>",
  "entry_window_utc": "13-18"
}
```

## 12. Ambiguidades e divergências

| # | Item | Material A | Material B | Decisão |
|---|------|-----------|-----------|---------|
| 1 | Target | ETH report Q3: "full_1.5R melhor IS" | Código `run_eth()`: 1R (linha 185-186: `tgt = e+(e-cl)`) | **1R** — código é a implementação final aceita; relatório explorou mas código não mudou |
| 2 | Timeout | ETH report Seção 7: 8c melhor IS e OOS | Scaffold padrão: 6 barras | **8 barras** — valor do estudo confirmado pelo código `range(i+2, min(i+10,n))` |
| 3 | Janela 13-18 vs 15-18 | ETH report Q2: "15-18 melhor (IS +0.1300R)" | Código: `hours.isin({13,14,15,16,17})` = 13-18 | **13-18** — código é a implementação final |
| 4 | HMM em runtime | Código usa `sklearn.preprocessing.StandardScaler + GaussianHMM` treinado on-the-fly | Scaffold: nenhum modelo HMM implementado | **BLOQUEADOR B1** — ver seção final |

---

---

# MOTOR 2 — BTC / Compressed Balance (`m2_btc`)

## 1. Identidade
- **engine_id:** `m2_btc`
- **Ativo:** BTCUSDT
- **Papel:** M2 — braço BTC intradiário, ACTIVE (abre posições paper)

## 2. Timeframes
- **TF de execução (setup + entry):** 15 minutos
- **TF de regime/contexto:** 4 horas (HMM GaussianHMM, 4 estados)
- **TF de sinal:** 15m — sinal gerado após fechamento da barra de sweep
- **TF de filtro:** 4h — regime deve ser "Compressed balance"

> **CONFIRMADO** — `eth_btc_portfolio.py` linha 209: `"""BTC: ONLY Compressed Balance, 13-14:30 window, 8h lookback, vol filter, 1R, t=6."""`

## 3. Regime / contexto

### Regime requerido
- **Nome:** "Compressed balance"
- **Modelo:** idêntico ao M1 — GaussianHMM 4 estados em barras 4h
- **Features:** mesmas 6 features do M1 (log_ret, range_pct, body_ratio, roll_vol, vol_z, dir_eff)
- **Mapeamento de estado:** score `sc = (1 - norm(roll_vol)) × 0.5 + (1 - norm(range_pct)) × 0.5`

### Obrigatoriedade
- **OBRIGATÓRIO** — se regime 4h atual ≠ "Compressed balance", barra é ignorada
- Código: `if bar["macro_family"] != "Compressed balance": continue`

### Ausência de contexto
- Regime indisponível → **SKIP** com `reason="SKIP_REGIME_UNAVAILABLE"`

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 222, 96–128

## 4. Setup

### Definição do padrão
Sweep de nível de 8h + reclaim na próxima barra (next-candle reclaim).

**Nível de referência (8h rolling):**
- `low_ref` = mínimo do low das últimas 32 barras 15m **excluindo a barra atual** (`.shift(1).rolling(32)`)
- `high_ref` = máximo do high das últimas 32 barras 15m **excluindo a barra atual** (`.shift(1).rolling(32)`)
- Lookback efetivo: 8 horas de barras 15m (32 × 15min = 480min = 8h)
- `min_periods=16` (8 barras = 2h mínimo)

**Filtro de volume:**
- `volmed20` = mediana do volume das últimas 20 barras 15m **excluindo a atual** (`.shift(1).rolling(20)`)
- Condição: `bar["volume"] > volmed20` — barra deve ter volume acima da mediana
- Se volume ≤ mediana: barra ignorada

**Setup bull (LONG sweep):**
- `bar["low"] < low_ref` AND `bar["close"] > low_ref`
- Interpretação: barra varreu abaixo do nível e reclamou (fechou acima)

**Setup bear (SHORT sweep):**
- `bar["high"] > high_ref` AND `bar["close"] < high_ref`
- Interpretação: barra varreu acima do nível e reclamou (fechou abaixo)

**Confirmação na próxima barra (reclaim):**
- LONG: `bar[i+1]["close"] > low_ref` (próxima barra também fecha acima)
- SHORT: `bar[i+1]["close"] < high_ref` (próxima barra também fecha abaixo)
- Se a confirmação falha: sem entrada

### Parâmetros congelados
| Parâmetro | Valor | Origem |
|-----------|-------|--------|
| `lookback_bars` | 32 (= 8h de 15m) | código `rolling(32, min_periods=16)` |
| `vol_lookback` | 20 barras | código `rolling(20, min_periods=5)` |
| `vol_filter` | mediana | código `.median()` |
| `entry_window_utc` | 13:00–14:30 UTC | código `hour==13 OR (hour==14 AND minute<=30)` |
| `stop_buffer` | 0.01% (1bp) | código `bar["low"] × (1 - 0.0001)` |

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 212–254

## 5. Entrada
- **Gatilho:** fechamento da barra de confirmação (bar `i+1`) dentro do nível reclamado
- **Tipo:** next-candle reclaim — não é entrada direta, é o candle seguinte à barra sweep
- **Preço de referência:** `close` de bar `i+1` (reclaim candle)
- **Preço de entrada paper:** `close × (1 ± slippage_bps/10_000)`
- **Janela de validade:** apenas 1 barra — a barra de sweep determina a janela
- **Barra de sinal:** bar `i` (sweep candle)
- **Barra de execução:** bar `i+1` (reclaim candle)

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 232–243: `e = nxt["close"]` onde `nxt = df.iloc[i+1]`

## 6. Stop
- **Definição LONG:** `stop = bar_i["low"] × (1 - 0.0001)` (extremo do sweep candle, buffer 1bp)
- **Definição SHORT:** `stop = bar_i["high"] × (1 + 0.0001)` (extremo do sweep candle, buffer 1bp)
- **Acionamento LONG:** `candle.low ≤ stop_price`
- **Acionamento SHORT:** `candle.high ≥ stop_price`
- **Prioridade:** Stop > Target > Timeout

> **CONFIRMADO** — `eth_btc_portfolio.py` linhas 236–241

## 7. Target
- **Definição:** 1R fixo
- **Cálculo:** `R = entry - stop` (LONG) ou `R = stop - entry` (SHORT)
- **Target LONG:** `target = entry + R`
- **Target SHORT:** `target = entry - R`

> **CONFIRMADO** — código `tgt = e + R` (linha 237) | BTC report Q3: "target 1.5R → NEUTRO; OOS -0.027R"

## 8. Timeout
- **Definição:** 6 barras de 15m após a barra de entrada
- **Contagem:** bars `i+2` até `i+7` (6 barras avaliadas)
- **Saída:** `close` da 6ª barra se stop/target não atingidos
- **Unidade:** barras 15m

> **CONFIRMADO** — `eth_btc_portfolio.py` linha 244: `range(i+2, min(i+8, n))`
> BTC report Seção 7: "timeout=6c → IS +0.0345R, OOS +0.2630R"

## 9. Invalidações
- Regime 4h ≠ "Compressed balance" → barra ignorada
- Volume da barra ≤ mediana 20 barras → barra ignorada
- Barra fora da janela 13:00–14:30 UTC → ignorada
- Barra `i+1` não confirma reclaim (fecha do lado errado) → sem entrada
- Lookback insuficiente (< 16 barras disponíveis) → sem sinal
- Circuit breakers padrão: ONE_POSITION_PER_ENGINE, CIRCUIT_DD_DIARIO, CIRCUIT_BANKROLL_MIN, KILL_STALE_DATA

## 10. Sizing / risco
| Parâmetro | Valor |
|-----------|-------|
| `risk_per_trade_pct` | 0.5% da banca atual |
| `initial_capital_usd` | $200 |
| `max_daily_dd_pct` | 2.0% |
| `min_bankroll_usd` | $50 |
| `slippage_bps` | 5 |

## 11. Campos de auditoria (`signal_data` JSONB)
```json
{
  "setup_label": "sweep_reclaim",
  "regime_label": "Compressed balance",
  "sweep_low_ref": "<float>",
  "sweep_high_ref": "<float>",
  "sweep_candle_extreme": "<float>",
  "volmed20": "<float>",
  "entry_window_utc": "13:00-14:30"
}
```

## 12. Ambiguidades e divergências

| # | Item | Material A | Material B | Decisão |
|---|------|-----------|-----------|---------|
| 1 | **Target 1R vs 1.5R** | BTC report "Melhor IS sensitivity: 8h/1.5R/t=8c → +0.1043R" | Código `run_btc()` usa 1R (linha 237: `tgt = e + R`) | **1R** — código final; relatório Q3 diz "NEUTRO (OOS: -0.027R)" → portfólio manteve 1R |
| 2 | **Timeout 6 vs 8** | BTC report Seção 7: t=8c dá IS +0.0518R (t=6: +0.0345R) | Código: `range(i+2, min(i+8, n))` = 6 barras | **6 barras** — código final aceito |
| 3 | **Lookback 8h vs 12h** | BTC report: "IS: 12h melhor; OOS: 8h melhor" | Código: `rolling(32)` = 8h | **8h (32 barras)** — código final; OOS favorece 8h |
| 4 | **⚠️ DIVERGÊNCIA PRINCIPAL** | "Melhor OOS: 8h/1.5R/t=8c → net=+0.3112R" | Código congelado: 8h/1R/t=6 → OOS net=+0.2630R | **Código (1R/t=6)** — delta OOS é real (+0.048R), mas decisão explícita ao implementar. N OOS estreito (n=47). Não reotimizar. |

---

---

# MOTOR 3 PRINCIPAL — SOL / Retest (`m3_sol`)

## 1. Identidade
- **engine_id:** `m3_sol`
- **Ativo:** SOLUSDT
- **Papel:** M3 principal — braço SOL, ACTIVE (abre posições paper)
- **Status no estudo:** "FORTE CANDIDATO A MOTOR 3" (MOTOR3_REPORT Parte 8)

## 2. Timeframes
- **TF de zona (compressão):** 1 hora — `comp_high` e `comp_low` são calculados em barras 1h
- **TF de regime:** 4 horas (HMM GaussianHMM, 4 estados — mesmo pipeline M1/M2)
- **TF de tendência:** 4 horas (EMA20 vs EMA50 — filtro adicional separado do HMM)
- **TF de execução:** 15 minutos — aguarda retest, simula trade bar a bar
- **TF de sinal:** 1h — sinal gerado após fechamento da 1h barra de breakout; executável após +1h

> **CONFIRMADO** — `motor3_validation.py` linhas 243–308: zona em `df1h`, execução em `df15m`
> MOTOR3_REPORT Parte 2: "Motor 3 multi-TF (4h trend | 1h compression | 15m execution)"

## 3. Regime / contexto

### Camada 1 — HMM 4h (mesmo modelo M1/M2)
- Régimes disponíveis: "Compressed Balance", "High Participation Rotation", "Trend / Drift", "Volatile Chop"
- SOL HMM aplicado com fresh fit na mesma pipeline
- MOTOR3_REPORT Parte 6: SOL × regime (direct execution OOS):
  - "Compressed Balance": OOS n=1, exp=-0.1772R → **bloqueio justificado**
  - "Volatile Chop": OOS n=24, exp=+0.2649R → **sem bloqueio** (positivo)
  - "Trend / Drift": OOS n=26, exp=-0.2105R → **atenção: negativo OOS**
  - "High Participation": OOS n=13, exp=+0.7491R → **melhor regime**
- **Decisão de filtro HMM para M3 SOL:** INFERÊNCIA — o estudo baseline não filtra por regime, aplica `trend_ok` como filtro principal. A decomposição por regime é exploratória. Para paper, não filtrar por regime HMM além de trend_ok — aceitar todos exceto **se quiser ser conservador**, bloquear apenas "Compressed Balance" (pior resultado). **Marcar como INFERÊNCIA** — sem regime_filter explícito no código operacional.

### Camada 2 — trend_ok (4h EMA — OBRIGATÓRIO)
- **Definição exata** (código `motor3_validation.py` linhas 339–352):
  ```python
  ema_short = EMA(close_4h, span=20)   # EMA20 em barras 4h
  ema_long  = EMA(close_4h, span=50)   # EMA50 em barras 4h
  slope_s   = ema_short - ema_short.shift(1)

  # LONG trend_ok = True se:
  ema_short > ema_long AND slope_s > 0

  # SHORT trend_ok = True se:
  ema_short < ema_long AND slope_s < 0
  ```
- **OBRIGATÓRIO** — código aplica `df_sigs[df_sigs["trend_ok"]]` antes de simular trades
- Se EMAs indisponíveis (lookback insuficiente): trend_ok = False → SKIP

### Ausência de contexto
- HMM indisponível → aplicar apenas trend_ok; logar ausência de regime
- trend_ok não computável (dados 4h insuficientes) → SKIP

> **CONFIRMADO** — `motor3_validation.py` linhas 315–353 (attach_4h_context + trend_ok)

## 4. Setup

### Definição da zona de balanço (1h compression)

**Cálculo da zona (barras 1h):**
```python
# Para cada barra 1h (bar atual = i):
comp_high = max(high[i-lookback : i])    # shift(1).rolling(lookback).max()
comp_low  = min(low[i-lookback  : i])    # shift(1).rolling(lookback).min()
comp_width = comp_high - comp_low

# ATR simplificado (mean of high-low das últimas atr_period barras 1h):
atr14 = mean(high[i-14:i] - low[i-14:i])  # shift(1).rolling(14).mean()

# Compressão válida se:
comp_width <= max_width × atr14
```

**Parâmetros SOL (baseline do estudo):**
| Parâmetro | Valor | Unidade | Status |
|-----------|-------|---------|--------|
| `lookback` | 12 | barras 1h (= 12h de janela) | **CONFIRMADO** — `BASELINE = dict(lookback=12, ...)` em `motor3_validation.py` linha 40 |
| `max_width` | 2.5 | × ATR14 (1h) | **CONFIRMADO** — `BASELINE = dict(max_width=2.5, ...)` linha 41 |
| `atr_period` | 14 | barras 1h | **CONFIRMADO** — `agg_compute_1h_compression(df1h, atr_period=14)` linha 243 |
| `min_periods` | lookback // 2 = 6 | barras 1h | **CONFIRMADO** — `rolling(lookback, min_periods=lookback // 2)` linha 256 |

**Breakout de sinal (1h bar):**
- LONG: `close > comp_high` AND `comp_width <= max_width × atr14`
- SHORT: `close < comp_low` AND `comp_width <= max_width × atr14`
- `signal_time = ts + 1h` (disponível no fechamento da próxima 1h bar)

> **CONFIRMADO** — `motor3_validation.py` linhas 267–308 (`detect_signals_1h`)

### Execução — Retest

**Mecanismo de retest (15m bars):**
```
Após signal_time (= abertura da 1h bar seguinte ao breakout):
  Aguardar até retest_window barras 15m para preço tocar entry_level:
    LONG:  entry_level = comp_high
           touched se bar["low"] <= comp_high
    SHORT: entry_level = comp_low
           touched se bar["high"] >= comp_low

  Se tocou: entra em entry_price = entry_level (limite simulado)
  Se não tocou em retest_window barras: sem trade
```

| Parâmetro | Valor | Unidade | Status |
|-----------|-------|---------|--------|
| `retest_window` | 8 | barras 15m (= 2h máximo de espera) | **CONFIRMADO** — `BASELINE = dict(retest_window=8, ...)` linha 48 |
| `entry_type` | "retest" | — | **CONFIRMADO** — motor escolhido: retest (MOTOR3_REPORT Parte 8) |

> **CONFIRMADO** — `motor3_validation.py` linhas 448–473 (bloco `exec_type == "retest"`)

## 5. Entrada
- **Gatilho:** primeiro bar 15m onde `low ≤ comp_high` (LONG) ou `high ≥ comp_low` (SHORT), dentro da janela de retest
- **Tipo:** retest — espera retorno ao nível após breakout
- **Preço:** `entry_price = entry_level` (comp_high para LONG, comp_low para SHORT) — limite simulado
- **Janela de validade:** 8 barras 15m (2 horas) após `signal_time`
- **Se nenhum retest em 2h:** sinal descartado, sem trade

> **CONFIRMADO** — `motor3_validation.py` linhas 449, 460–471

## 6. Stop
- **LONG:** `stop = comp_low` (extremo oposto da zona)
- **SHORT:** `stop = comp_high` (extremo oposto da zona)
- **R = |entry_price - stop|** = largura completa da zona de balanço
- **Acionamento LONG:** `candle.low ≤ stop_price`
- **Acionamento SHORT:** `candle.high ≥ stop_price`
- **Prioridade:** Stop > Target > Timeout

> **CONFIRMADO** — `motor3_validation.py` linha 450: `stop = comp_low if direction == 1 else comp_high`
> CSV `motor3_trades_SOL_retest.csv`: `stop == comp_low` confirmado nos trades LONG

## 7. Target
- **Target = 1.5R**
- **R_dist = |entry_level - stop|**
- **Target LONG:** `target = entry_level + 1.5 × R_dist`
- **Target SHORT:** `target = entry_level - 1.5 × R_dist`

> **RECUPERADO** — `motor3_validation.py` linha 454: `target = entry_level + direction * target_r * R_dist`
> BASELINE: `target_r=1.5` | CSV: entry=16.83, stop=15.90, alvo≈18.22 → R=0.93, target=16.83+1.5×0.93=18.225 ✓

## 8. Timeout
- **timeout_bars = 64 barras de 15m = 16 horas**
- **Contagem:** barras 15m a partir da barra de entrada
- **Saída:** `close` da 64ª barra se stop/target não atingidos
- **Unidade:** barras 15m

> **CONFIRMADO** — BASELINE: `timeout=64` | Motor3 robustez (direct SOL): `timeout=64 → OOS exp=0.1632R` (melhor)

## 9. Invalidações
- trend_ok = False → sinal descartado
- comp_width > max_width × atr14 → zona muito larga, descartado
- lookback insuficiente (< 6 barras 1h disponíveis) → sem zona
- breakout não ocorre → sem sinal
- Nenhum retest em 8 barras 15m → sem trade
- **INFERÊNCIA:** regime "Compressed Balance" 4h → SKIP (pior OOS)
- Circuit breakers padrão: ONE_POSITION_PER_ENGINE, CIRCUIT_DD_DIARIO, CIRCUIT_BANKROLL_MIN, KILL_STALE_DATA

## 10. Sizing / risco
| Parâmetro | Valor |
|-----------|-------|
| `risk_per_trade_pct` | 0.5% |
| `initial_capital_usd` | $200 |
| `max_daily_dd_pct` | 2.0% |
| `min_bankroll_usd` | $50 |
| `slippage_bps` | 8 (SOL tem spread maior) |
| `timeout_bars` | 64 (15m bars = 16h) — **ajustar em settings.py** |
| `timeframe_minutes` | 15 (execução) — mas o sinal é gerado em 1h |

## 11. Campos de auditoria (`signal_data` JSONB)
```json
{
  "setup_label": "retest",
  "regime_label": "<macro_family>",
  "trend_ok": "<bool>",
  "ema_short_4h": "<float>",
  "ema_long_4h": "<float>",
  "ema_slope_4h": "<float>",
  "comp_high": "<float>",
  "comp_low": "<float>",
  "comp_width": "<float>",
  "atr14_1h": "<float>",
  "zone_width_atr_multiple": "<float>",
  "retest_window_bars": 8,
  "signal_tf": "1h"
}
```

## 12. Ambiguidades e divergências

| # | Item | Material disponível | Decisão |
|---|------|---------------------|---------|
| 1 | **Filtro de regime HMM para SOL retest** | Código não aplica regime_filter no baseline; tabela Part 6 é exploratória | **Não filtrar por regime HMM além do trend_ok** — usar todos os regimes. Bloquear "Compressed Balance" é inferência conservadora opcional. |
| 2 | Janela de horário UTC | Sem entry_window_utc no código M3 | **Sem filtro de horário** — M3 roda 24h (diferente de M1 e M2) |
| 3 | Target 1.5R para retest | CSV confirma (1 cálculo) + BASELINE + robustez SOL direct (melhor OOS=1.5) | **1.5R** — convergência de múltiplas fontes |
| 4 | Timeout 64 para retest | BASELINE = 64; robustez direct SOL = 64 (OOS melhor) | **64 barras 15m** — BASELINE confirmado por robustez |
| 5 | **⚠️ OOS Trend/Drift negativo** | SOL Parte 6: regime "Trend/Drift" → OOS exp=-0.2105R (n=26) | **Não bloquear** — filtro é trend_ok (EMA), não HMM regime. OOS ruim é na análise exploratória sem trend_ok. No backtest completo (com trend_ok), SOL OOS=+0.2189R. |

---

---

# MOTOR 3 SOMBRA — ETH / Direct ajustado (`m3_eth_shadow`)

## 1. Identidade
- **engine_id:** `m3_eth_shadow`
- **Ativo:** ETHUSDT
- **Papel:** M3 sombra — SIGNAL_ONLY, sem posição financeira; calibração antes de promoção
- **Parâmetros congelados:** lookback=8, max_width=2.0

## 2. Timeframes
- **TF de zona (compressão):** 1 hora — mesma lógica do M3 SOL
- **TF de regime:** 4 horas (HMM + trend_ok — mesmo pipeline M3 SOL)
- **TF de execução:** 15 minutos
- **TF de sinal:** 1h — breakout na 1h bar, executável após +1h

> **CONFIRMADO** — portfólio congelado: "ETH direct ajustado (lookback 8, max_width 2.0)"
> `motor3_validation.py`: zona em df1h (linha 638), execução em df15m (linha 629)
> MOTOR3_REPORT Parte 8: "ETH direct: PROMISSOR (OOS N=57, exp=0.0811R, wr=49.1%)"

## 3. Regime / contexto
- **Mesma pipeline de trend_ok do M3 SOL:** EMA20(4h) vs EMA50(4h) + slope
- **MOTOR3_REPORT Parte 6 (ETH direct) por regime:**
  - "Compressed balance": OOS WR=60.0%, Exp=+0.3454R (n=10 — amostra pequena)
  - "Trend / drift": OOS WR=47.6%, Exp=+0.0227R
  - "High Participation": OOS WR=60.0%, Exp=+0.2778R
  - "Volatile chop": OOS WR=37.5%, Exp=-0.1304R
- **Sem filtro de regime HMM explícito** — trend_ok é o filtro operacional
- **INFERÊNCIA:** "Volatile chop" é o único regime negativo; bloquear é defensável mas não está no código

## 4. Setup

### Definição da zona (1h compression — mesma função do M3 SOL)
```python
comp_high = max(high[i-8 : i])    # shift(1).rolling(8).max() em barras 1h
comp_low  = min(low[i-8  : i])    # shift(1).rolling(8).min()
atr14     = mean((high-low)[i-14:i])  # shift(1).rolling(14).mean()

# Compressão válida:
comp_width <= 2.0 × atr14
```

**Parâmetros congelados:**
| Parâmetro | Valor | Unidade | Status |
|-----------|-------|---------|--------|
| `lookback` | 8 | barras 1h (= 8h de janela) | **CONFIRMADO** — portfólio + MOTOR3_REPORT Parte 5 top OOS |
| `max_width` | 2.0 | × ATR14 (1h) | **CONFIRMADO** — portfólio + Part 5: ETH/8/2.0 domina top 20 OOS |
| `atr_period` | 14 | barras 1h | **CONFIRMADO** — mesma função `compute_1h_compression` linha 243 |
| `target_r` | 1.50 | R | **RECUPERADO** — top OOS: ETH/8/2.0/1.50/96/8bps → exp=0.2146R |
| `timeout_bars` | 96 | barras 15m (= 24h) | **RECUPERADO** — top OOS ETH/8/2.0: timeout=96 → exp=0.2146R (melhor) |
| `signal_only` | True | — | **CONFIRMADO** — portfólio e settings.py |

> **RECUPERADO** — MOTOR3_REPORT Parte 5 Tabela top 20 OOS:
> `ETH 8 2.0 1.50 96 8 → OOS exp=0.2146R, wr=52.6%, oos_n=97`
> `ETH 8 2.0 1.50 64 8 → OOS exp=0.2075R, wr=53.6%, oos_n=97`

### Execução — Direct
```
Após signal_time (1h close + 1h):
  Primeira barra 15m disponível:
    entry_price = open da primeira 15m bar (ref_open)
    stop  = comp_low (LONG) ou comp_high (SHORT)
    R_dist = |entry_price - stop|
    target = entry_price + direction × 1.5 × R_dist
```

> **CONFIRMADO** — `motor3_validation.py` linhas 436–444 (bloco `exec_type == "direct"`)

## 5. Entrada
- **Gatilho:** close 1h acima de comp_high (LONG) ou abaixo de comp_low (SHORT), AND comp_width ≤ 2.0 × ATR14
- **Tipo:** direct — entrada na abertura da primeira barra 15m disponível após signal_time
- **Preço:** `open` da primeira barra 15m após signal_time (= open do bar em idx0)
- **Janela de validade:** 1 barra 15m — ou entra na abertura imediata, ou não
- **SIGNAL_ONLY:** sinal e decisão registrados nas trilhas de auditoria; posição nunca aberta

## 6. Stop
- **LONG:** `stop = comp_low` (extremo oposto da zona)
- **SHORT:** `stop = comp_high`
- **R_dist = |entry_price - stop|**

> **RECUPERADO** — `motor3_validation.py` linha 438: `stop = comp_low if direction == 1 else comp_high`
> Por analogia exata com M3 SOL — mesma função, mesma lógica

## 7. Target
- **Target = 1.5R**
- **Target LONG:** `target = entry_price + 1.5 × R_dist`
- **Target SHORT:** `target = entry_price - 1.5 × R_dist`

> **RECUPERADO** — top OOS Part 5: target_r=1.50 dominante para ETH/8/2.0

## 8. Timeout
- **timeout_bars = 96 barras 15m = 24 horas**
- **Alternativa viável:** 64 barras (OOS exp=0.2075R — diferença de 0.007R)

> **RECUPERADO** — MOTOR3_REPORT Parte 5; timeout=96 dá melhor exp OOS para ETH/8/2.0

## 9. Invalidações
- trend_ok = False → sinal descartado
- comp_width > 2.0 × atr14 → zona muito larga, descartado
- `signal_only=True` → mesmo com ENTER, nunca abre tr_paper_trades
- Circuit breakers não aplicáveis (sem capital alocado)

## 10. Sizing / risco
| Parâmetro | Valor |
|-----------|-------|
| `risk_per_trade_pct` | 0.5% (simulado, sem capital real alocado) |
| `signal_only` | True — nunca abre tr_paper_trades |
| `slippage_bps` | 5 |
| `timeout_bars` | 96 (15m bars = 24h) — **ajustar em settings.py** |

## 11. Campos de auditoria (`signal_data` JSONB)
```json
{
  "setup_label": "direct_breakout",
  "regime_label": "<macro_family>",
  "trend_ok": "<bool>",
  "ema_short_4h": "<float>",
  "ema_long_4h": "<float>",
  "ema_slope_4h": "<float>",
  "comp_high": "<float>",
  "comp_low": "<float>",
  "comp_width": "<float>",
  "atr14_1h": "<float>",
  "lookback": 8,
  "max_width": 2.0,
  "signal_tf": "1h"
}
```

## 12. Ambiguidades e divergências

| # | Item | Material disponível | Decisão |
|---|------|---------------------|---------|
| 1 | Filtro de regime HMM | Part 6: "Volatile chop" único negativo, sem filtro explícito no código | **Sem filtro HMM** — trend_ok é o filtro operacional. Bloquear "Volatile chop" é inferência opcional. |
| 2 | Timeout 64 vs 96 | Part 5: 96 → 0.2146R; 64 → 0.2075R | **96 barras** — ligeiramente melhor, diferença de 0.007R |
| 3 | Stop sem buffer | M3 SOL não tem buffer de 1bp; M2 BTC tem buffer 1bp | **Sem buffer** — por consistência com a lógica do estudo M3 |

---

---

# COMPONENTE TRANSVERSAL — Regime HMM

## Problema arquitetural crítico (BLOQUEADOR B1)

Todos os 4 motores exigem:
1. **M1, M2:** Regime HMM 4h ativo e classificado — bloqueador direto (regime é obrigatório)
2. **M3 SOL, M3 ETH shadow:** trend_ok baseado em EMA20/EMA50 4h — não requer HMM diretamente para a condição principal. HMM é contextual/opcional.

### Features necessárias (confirmadas)
Para cada barra 4h (mesma pipeline M1+M2+M3):
```python
log_ret    = log(close / close.shift(1))
range_pct  = (high - low) / close
body_ratio = abs(close - open) / (high - low)  # 0 se high==low
roll_vol   = log_ret.rolling(6, min_periods=3).std()    # 6 barras 4h = 24h
vol_z      = (volume - volume.rolling(24, min_periods=6).mean()) / \
              volume.rolling(24, min_periods=6).std()
dir_eff    = abs(log_ret) / range_pct.replace(0, nan)
```

### Opções para produção (não decidir aqui — apontar o problema)

**Opção A — Re-treino periódico (ex: semanal)**
- Worker treina HMM a cada N dias com dados históricos + recentes
- Persiste modelo (pickle/joblib) no banco ou filesystem do container
- Vantagem: modelo atualizado | Risco: deriva de regime não detectável

**Opção B — Modelo fixo (congelado na data de corte)**
- Treina uma vez com dados históricos IS+OOS, persiste, não re-treina
- Mais simples, mais estável | Risco: deriva se distribuição mudar
- **Vantagem prática:** elimina complexidade de re-treino em produção

**Opção C — Inferência online (barra a barra)**
- Mais complexo; exige janela de warm-up de pelo menos 200 barras 4h (~33 dias)

> **Esta decisão é BLOQUEADORA para M1 e M2 — precisa de confirmação antes de implementar `generate_signal()`**
> Para M3: trend_ok pode ser implementado sem HMM (requer apenas EMA20/50 em barras 4h).

### EMA para trend_ok (M3 — implementável sem HMM)
```python
ema_short = close_4h.ewm(span=20, adjust=False).mean()
ema_long  = close_4h.ewm(span=50, adjust=False).mean()
slope_s   = ema_short - ema_short.shift(1)

# LONG signal valid se trend_ok:
trend_ok_long  = (ema_short > ema_long) and (slope_s > 0)

# SHORT signal valid se trend_ok:
trend_ok_short = (ema_short < ema_long) and (slope_s < 0)
```
Requer: `valid_from = bar_4h_ts + 4h` (shift para usar barra fechada)

---

---

# AJUSTES OBRIGATÓRIOS EM `settings.py` (fazer antes de qualquer build)

| Motor | Parâmetro atual | Valor correto | Fonte |
|-------|----------------|---------------|-------|
| `m1_eth` | `timeout_bars=6` | `timeout_bars=8` | Código `eth_btc_portfolio.py` |
| `m3_sol` | `timeout_bars=6` | `timeout_bars=64` | BASELINE `motor3_validation.py` |
| `m3_eth_shadow` | `timeout_bars=6` | `timeout_bars=96` | Parte 5 OOS top ETH/8/2.0 |
| `m3_sol` | `timeframe_minutes=5` | Manter 5m? | **Ver nota abaixo** |
| `m3_eth_shadow` | `timeframe_minutes=5` | Manter 5m? | **Ver nota abaixo** |

**Nota sobre `timeframe_minutes` de M3:** O worker usa `timeframe_minutes` para detectar fechamento de barra e como unidade de timeout. Para M3:
- O sinal é gerado em barras 1h (not 15m)
- A execução e contagem de timeout é em barras 15m
- Sugestão: `timeframe_minutes=15` para M3 (o worker agrega de 1m → 15m via candle_reader)
- O sinal 1h requer lógica adicional de detecção de barra 1h fechada (every 4 ciclos de 15m)

---

---

# RESUMO EXECUTIVO

## O que está sólido (CONFIRMADO ou RECUPERADO)

| Item | Status |
|------|--------|
| Infraestrutura scaffold (worker, API, migrations, circuit breakers) | ✅ FROZEN |
| Schema tr_* (6 tabelas) | ✅ FROZEN |
| Risk defaults M1/M2/M3/M3-shadow | ✅ FROZEN |
| M1 ETH — setup completo (compression+breakout 15m, regime 4h HMM, 13-18 UTC, 1R, t=8) | ✅ CONFIRMADO |
| M2 BTC — setup completo (sweep+reclaim 15m, regime 4h HMM, 8h lb, vol filter, 13-14:30 UTC, 1R, t=6) | ✅ CONFIRMADO |
| M3 SOL — definição zona: 1h bars, lookback=12, max_width=2.5×ATR14(1h) | ✅ CONFIRMADO |
| M3 SOL — trend_ok: EMA20(4h) vs EMA50(4h) + slope — OBRIGATÓRIO | ✅ CONFIRMADO |
| M3 SOL — retest: entry=comp_high/comp_low, window=8 barras 15m, target=1.5R, t=64 | ✅ CONFIRMADO |
| M3 shadow ETH — lookback=8 (1h bars), max_width=2.0×ATR14(1h), direct, 1.5R, t=96 | ✅ RECUPERADO |
| M3 shadow ETH — stop=extremo oposto da zona (confirmado no código) | ✅ CONFIRMADO |
| Portfólio congelado (4 engines, papéis, modos) | ✅ CONFIRMADO |
| Divergência M2 BTC (1R/t=6 vs 1.5R/t=8c) documentada e resolvida | ✅ |
| SOL não promovido a "regime oficial consolidado" — candidato operacional validado para paper | ✅ |
| Filtro de regime M3: trend_ok EMA-based (não apenas HMM) | ✅ CONFIRMADO |
| M3 sem entry_window_utc — roda 24h | ✅ CONFIRMADO |

---

## O que depende de confirmação antes de codar

### BLOQUEADOR ÚNICO RESTANTE

| # | Item | Motor afetado | Pergunta exata |
|---|------|--------------|----------------|
| B1 | **Estratégia do HMM em produção** | M1, M2 (e M3 para regime_label de auditoria) | Re-treino periódico? Modelo congelado na data de corte? Com qual frequência e dados? Onde persistir (pickle no filesystem do container, banco, volume)? |

> **Nota:** Para M3 SOL e M3 ETH shadow, a implementação pode começar sem o HMM — trend_ok (EMA) não depende do HMM. Apenas o `regime_label` no JSONB de auditoria ficará como "unknown" até o HMM estar disponível.

### INFERÊNCIAS CODIFICÁVEIS COM RESSALVA

| # | Item | Motor | Confiança | Risco se errado |
|---|------|-------|-----------|----------------|
| I1 | Bloquear "Compressed Balance" para M3 SOL | M3 SOL | Média (OOS n=1 → inconclusivo) | Muito baixo — perda de trades raros nesse regime |
| I2 | Bloquear "Volatile chop" para M3 shadow ETH | M3 ETH shadow | Média (OOS n=16, exp=-0.1304R) | Baixo — mais conservador |
| I3 | `timeframe_minutes=15` para M3 (vs 5m atual no settings.py) | M3 SOL + shadow | Alta | Médio — muda frequência de detecção de barra |

---

## Histórico de revisões

| Data | Autor | Alteração |
|------|-------|-----------|
| 2026-03-24 v1.0 | Claude | Criação — infraestrutura FROZEN, engines aguardando spec |
| 2026-03-24 v2.0 | Claude | SPEC FREEZE completo — M1/M2 confirmados do código; M3/M3-shadow recuperados do estudo; divergências documentadas; bloqueadores listados |
| 2026-03-24 v3.0 | Claude | Resolução de B2–B6 via `motor3_validation.py`: zona 1h confirmada, lookback/max_width/ATR14 recuperados, trend_ok EMA definido com código exato, retest_window=8 barras 15m, sem entry_window_utc. Único bloqueador restante: B1 (estratégia HMM em produção). |
