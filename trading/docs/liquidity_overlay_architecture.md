## Liquidity Overlay Architecture

### Objetivo

Adicionar uma camada paralela de observacao e comparacao baseada em zonas de
liquidez da Binance sem alterar o comportamento dos bots principais.

O runtime atual continua sendo a fonte canonica de:

- `tr_signals`
- `tr_signal_decisions`
- `tr_paper_trades`
- `tr_daily_equity`
- `tr_engine_heartbeat`

O overlay de liquidez nao bloqueia, nao aprova e nao reescreve trades reais.
Ele observa os sinais/trades do bot, calcula um contrafactual e grava um
"ghost trade" para comparacao futura.

### Principios

1. `Base runtime first`
O worker atual continua executando os engines normalmente.

2. `Parallel only`
O overlay nao entra no path critico de abertura/fechamento do trade real.

3. `Deterministic linkage`
Cada avaliacao paralela deve apontar para um `signal_id` e, quando existir,
para um `trade_id` real.

4. `Comparable outcomes`
O overlay precisa responder objetivamente:
`same`, `skip`, `enter_earlier`, `enter_later`, `adjust_stop`,
`adjust_target`, `reverse`, `size_down`, `size_up`.

5. `Replayable`
As features e decisoes do overlay devem ser persistidas para reprocessamento.

## Escopo funcional

Para cada sinal real emitido por um engine:

1. O bot principal gera e persiste o sinal normalmente.
2. O overlay carrega o contexto de liquidez Binance para o mesmo timestamp.
3. O overlay decide:
   - aceitaria igual
   - rejeitaria
   - entraria antes
   - entraria depois
   - manteria a entrada e mudaria `stop`
   - manteria a entrada e mudaria `target`
4. Se a decisao for operacionalizavel, o overlay cria um `ghost trade`.
5. No fechamento do trade real ou do ghost trade, o sistema calcula o delta.

## O que muda na arquitetura atual

### Mantem igual

- `trading/apps/worker/main.py`
- `trading/core/engines/*`
- `trading/core/execution/*`
- `trading/core/risk/*`

### Adiciona

```text
trading/
  core/
    liquidity/
      zone_models.py
      binance_liquidity_reader.py
      overlay_evaluator.py
      ghost_trade_manager.py
    comparison/
      delta_metrics.py
  apps/
    overlay_worker/
      main.py
```

### Separacao correta de responsabilidades

- `worker/main.py`
  executa bots reais e continua gravando o fluxo canonico.

- `overlay_worker/main.py`
  observa sinais/trades reais e roda a camada paralela.

- `core/liquidity/binance_liquidity_reader.py`
  carrega zonas e features de liquidez.

- `core/liquidity/overlay_evaluator.py`
  transforma `signal + liquidity_context` em decisao paralela.

- `core/liquidity/ghost_trade_manager.py`
  abre/fecha o ghost trade e calcula PnL paralelo.

- `core/comparison/delta_metrics.py`
  gera comparativos entre base e overlay.

## Fluxo de eventos

### 1. Fluxo base

O worker atual faz:

1. gerar sinal
2. validar sinal
3. abrir trade real quando aplicavel
4. fechar trade real por `SL`, `TP` ou `TIMEOUT`

Isso permanece intocado.

### 2. Fluxo paralelo do overlay

O `overlay_worker` roda em loop proprio:

1. busca `tr_signals` ainda nao avaliados pelo overlay
2. para cada sinal:
   - carrega contexto do signal
   - carrega features de liquidez Binance
   - executa `overlay_evaluator`
   - persiste a recomendacao paralela
3. se a recomendacao implicar trade hipotetico:
   - abre `ghost trade`
4. monitora `ghost trades` abertos
5. fecha `ghost trades` com a mesma logica de simulacao
6. consolida comparativos contra o trade real

### 3. Fechamento comparativo

Quando houver trade real e ghost trade para o mesmo sinal:

- comparar `entry_price`
- comparar `entry_delay_bars`
- comparar `stop_price`
- comparar `target_price`
- comparar `exit_reason`
- comparar `pnl_usd`
- comparar `pnl_pct`
- comparar `max_adverse_excursion`
- comparar `max_favorable_excursion`

## Contrato do overlay

### Entrada

`overlay_evaluator` recebe:

- `signal`
- `base_decision`
- `base_trade` opcional
- `recent_candles`
- `liquidity_snapshot`
- `engine_id`
- `symbol`

### Saida

Estrutura sugerida:

```python
{
    "action": "SAME|SKIP|ENTER|ADJUST",
    "comparison_label": "same|skip|earlier|later|stop_wider|stop_tighter|tp_higher|tp_lower",
    "reason": "LIQUIDITY_REJECTION_ABOVE|STOP_BELOW_SWEEP_LOW|TARGET_AT_NEXT_POOL",
    "entry_price": 123.45,
    "stop_price": 120.10,
    "target_price": 129.80,
    "stake_usd": 8.50,
    "trigger_bar_timestamp": "2026-04-13T14:15:00+00:00",
    "features": {}
}
```

## Schema proposto

### 1. Snapshot de liquidez

```sql
CREATE TABLE IF NOT EXISTS tr_liquidity_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    tf              TEXT NOT NULL,
    snapshot_ts     TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL DEFAULT 'binance',
    zones           JSONB NOT NULL,
    features        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, tf, snapshot_ts, source)
);
```

Uso:

- guardar pools de liquidez acima/abaixo
- sweep highs/lows
- equal highs/lows
- imbalance/fvg
- distancia ate zona mais proxima
- score agregado

### 2. Avaliacao paralela por sinal

```sql
CREATE TABLE IF NOT EXISTS tr_overlay_decisions (
    id                  BIGSERIAL PRIMARY KEY,
    signal_id           BIGINT NOT NULL REFERENCES tr_signals(id),
    engine_id           TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    overlay_name        TEXT NOT NULL DEFAULT 'binance_liquidity_v1',
    action              TEXT NOT NULL,
    comparison_label    TEXT NOT NULL,
    reason              TEXT,
    entry_price         NUMERIC(18,8),
    stop_price          NUMERIC(18,8),
    target_price        NUMERIC(18,8),
    stake_usd           NUMERIC(18,2),
    trigger_bar_ts      TIMESTAMPTZ,
    features            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (signal_id, overlay_name)
);
```

Uso:

- guardar a resposta do overlay para cada sinal real
- comparar versoes futuras do overlay sem quebrar historico

### 3. Ghost trades

```sql
CREATE TABLE IF NOT EXISTS tr_ghost_trades (
    id                  BIGSERIAL PRIMARY KEY,
    overlay_decision_id BIGINT NOT NULL REFERENCES tr_overlay_decisions(id),
    signal_id           BIGINT NOT NULL REFERENCES tr_signals(id),
    base_trade_id       BIGINT REFERENCES tr_paper_trades(id),
    engine_id           TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,
    entry_price         NUMERIC(18,8) NOT NULL,
    exit_price          NUMERIC(18,8),
    stop_price          NUMERIC(18,8),
    target_price        NUMERIC(18,8),
    stake_usd           NUMERIC(18,2) NOT NULL,
    pnl_usd             NUMERIC(18,2),
    pnl_pct             NUMERIC(10,6),
    status              TEXT NOT NULL DEFAULT 'OPEN',
    opened_at           TIMESTAMPTZ NOT NULL,
    closed_at           TIMESTAMPTZ,
    close_reason        TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
```

Uso:

- representar o trade contrafactual da camada de liquidez

### 4. Tabela consolidada de comparacao

```sql
CREATE TABLE IF NOT EXISTS tr_trade_comparisons (
    id                      BIGSERIAL PRIMARY KEY,
    signal_id               BIGINT NOT NULL REFERENCES tr_signals(id),
    base_trade_id           BIGINT REFERENCES tr_paper_trades(id),
    ghost_trade_id          BIGINT REFERENCES tr_ghost_trades(id),
    overlay_decision_id     BIGINT NOT NULL REFERENCES tr_overlay_decisions(id),
    engine_id               TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    comparison_label        TEXT NOT NULL,
    entry_delta_bps         NUMERIC(10,4),
    stop_delta_bps          NUMERIC(10,4),
    target_delta_bps        NUMERIC(10,4),
    pnl_delta_usd           NUMERIC(18,2),
    pnl_delta_pct           NUMERIC(10,6),
    base_close_reason       TEXT,
    ghost_close_reason      TEXT,
    summary                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (signal_id, overlay_decision_id)
);
```

Uso:

- relatorio final e analytics

## Integracao com o schema atual

### Ponto de acoplamento minimo

O melhor gatilho para o overlay e `tr_signals`.

Motivo:

- todo engine ja grava sinal
- o overlay precisa avaliar o setup do bot, nao o loop inteiro
- evita acoplamento com detalhes internos do engine

### Vinculo com trade real

Quando o sinal real gerar trade:

- `tr_paper_trades.signal_id` continua sendo o vinculo canonico
- `tr_ghost_trades.base_trade_id` aponta para o trade real correspondente

### Vinculo sem trade real

Tambem precisa existir comparacao quando:

- o bot fez `SKIP`
- o overlay faria `ENTER`

Nesse caso:

- existe `tr_overlay_decisions`
- pode existir `tr_ghost_trades`
- `base_trade_id` fica `NULL`

Isso responde ao caso:
"o bot nao entraria, mas a camada de liquidez entraria".

## Coleta de zonas de liquidez

### Fase 1

Sem order book e sem websocket obrigatorio.
Comecar com zonas derivadas de OHLCV Binance:

- swing highs/lows
- equal highs/lows
- liquidity sweeps
- displacement candles
- fair value gaps
- prior day high/low
- prior week high/low
- session high/low

Essa fase e suficiente para validar valor incremental do overlay.

### Fase 2

Se a fase 1 mostrar edge:

- adicionar order book snapshots
- volume profile local
- open interest
- liquidation heatmaps externos, se houver fonte confiavel

## Regras de decisao sugeridas

### Exemplo de filtros iniciais

1. `SKIP_IF_ENTRY_INTO_OPPOSING_LIQUIDITY`
Nao entrar se a entrada estiver colada em piscina contraria.

2. `STOP_BEYOND_SWEEP_EXTREME`
Mover stop para alem da varredura relevante.

3. `TARGET_AT_NEXT_LIQUIDITY_POOL`
Definir take na proxima zona relevante.

4. `DELAY_ENTRY_UNTIL_RECLAIM`
Nao entrar no breakout seco; esperar reclaim.

5. `ENTER_EARLIER_ON_PRE_BREAK_COMPRESSION`
Permitir ghost trade antecipado quando a leitura de liquidez justificar.

## Como encaixar sem quebrar o runtime atual

### Nao fazer

- nao alterar `generate_signal()` dos engines atuais
- nao misturar overlay em `validate_signal()`
- nao enfiar logica de liquidez em `worker/main.py`
- nao reutilizar `tr_signal_decisions` para guardar decisao paralela

### Fazer

- criar `overlay_worker` separado
- usar tabelas novas com prefixo `tr_`
- consumir `tr_signals` e `tr_paper_trades`
- expor endpoints read-only novos na API

## Endpoints novos sugeridos

Na API read-only:

- `GET /overlay/decisions?engine_id=&limit=`
- `GET /overlay/ghost-trades?engine_id=&limit=`
- `GET /overlay/comparisons?engine_id=&limit=`
- `GET /overlay/summary?engine_id=`

## Metricas que importam

As metricas certas para decidir se junta ou nao junta a camada:

- `% signals skipped by overlay`
- `% overlay entries better than base by bps`
- `% overlay trades with lower MAE`
- `% overlay trades with higher expectancy`
- `% overlay stop adjustments that reduce loss`
- `% overlay target adjustments that improve realized R`
- `pnl_delta_usd` agregado por engine
- `pnl_delta_pct` por 30d, 60d, 90d

## Plano de implementacao

### Etapa 1. Schema e contratos

- adicionar migrations das 4 tabelas novas
- adicionar models/repositorio do overlay
- adicionar docstrings e tipos

### Etapa 2. Overlay evaluator v1

- features OHLCV-based
- decisoes `same|skip|adjust`
- sem order book ainda

### Etapa 3. Ghost trade engine

- abrir/fechar trades paralelos
- usar o mesmo fill model do runtime base
- gravar comparativos

### Etapa 4. API e relatorios

- endpoints read-only
- resumo por engine
- drill-down por signal/trade

### Etapa 5. Decisao de integracao

So depois de amostra suficiente:

- promover regra especifica do overlay para engine real
- ou manter como camada analitica

## Decisao recomendada

O melhor caminho agora e:

1. preservar os bots atuais
2. criar `overlay_worker` paralelo
3. usar `tr_signals` como gatilho canonico
4. persistir `overlay_decision + ghost_trade + comparison`
5. medir edge antes de fundir qualquer regra ao runtime principal

Essa abordagem respeita exatamente o caso de uso:

- "o bot fez o trade abc"
- "com liquidez eu faria x/y/z"
- "ou nao faria"
- "ou faria antes"

sem contaminar a serie historica do bot principal.
