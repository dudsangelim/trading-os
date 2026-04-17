# Trading OS — Carry Delta-Neutral Engine Spec

## Objetivo estratégico
Adicionar uma estratégia de carry delta-neutral que captura funding rate em regime de funding extremo, operando long spot + short perp simultaneamente.

Esta é a única estratégia do portfólio com evidência pública robusta (Presto Labs reportou Sharpe 6.71 em variante com venda imediata de accrual).

**Escopo:** paper trading puro. Segue padrão das engines existentes (`m1_eth`, `m2_btc`, `m3_sol`). Nenhuma integração com Binance API real. Simulação de execução via preços de candle, reconciliação em `tr_paper_trades` com extensões.

## Princípio arquitetural
Carry é uma estratégia de horizonte multi-dia, não intraday.

- Não compete com as engines existentes no mesmo universo — vive em paralelo.
- Não usa o overlay de liquidez.
- Não consome aggregator.
- É autônoma.

## Contexto de implementação

### O que essa engine faz
1. Monitora funding rate de BTCUSDT (extensível a ETH/SOL em fase 2)
2. Quando funding anualizado cruza threshold superior (default 15%), abre carry:
   - Simula compra spot equivalente a stake
   - Simula short em perp de mesmo notional
   - Posição é delta-neutral: mudanças de preço afetam os dois lados de forma oposta
3. A cada ciclo de funding (8h), acumula funding recebido em USD no PnL da posição
4. Variante “vende accrual”: a cada funding, “converte” o accrual para USDT imediatamente (simplificação da variante Presto Sharpe 6.71)
5. Fecha quando funding anualizado cai abaixo de threshold inferior (default 5%) OU após `max_hold_hours` (default 168h = 7 dias)
6. PnL total = funding acumulado − fees de entrada/saída − slippage modelado

### Por que usa derivatives-collector
O collector já coleta `binance_funding_rate` com granularidade de 8h. A engine consulta esse dado via adapter read-only. Sem isso não há sinal. Fase 2 V2 já validou que o adapter funciona.

## Diferenças fundamentais vs engines existentes

| Aspecto | Engines clássicas (m1/m2/m3) | Carry Neutral |
|---|---|---|
| Timeframe | 15m | 8h (ciclo de funding) |
| Horizonte | Horas | Dias |
| Direção | LONG ou SHORT direcional | Delta-neutral (ambos) |
| Exit | SL/TP/timeout em barras | Threshold funding ou max_hold |
| Fonte de PnL | Movimento de preço | Funding accrual |
| Overlay aplicável | Sim (ghost trades) | Não (delta-neutral não tem zonas) |

Essa diferença é importante: carry não precisa passar pelo `overlay_worker`. É path próprio.

## Entregáveis

### 1 — Nova engine `trading/core/engines/carry_neutral_btc.py`
Classe `CarryNeutralBtcEngine(BaseEngine)`:

```python
class CarryNeutralBtcEngine(BaseEngine):
    engine_id = "carry_neutral_btc"
    symbol = "BTCUSDT"

    # Não usa BaseEngine.generate_signal pattern diretamente
    # porque funding é contínuo, não "bar close"
    interface customizada:
    async def evaluate_carry(self, ctx: CarryContext) -> CarryDecision
```

Retorna:

- `OPEN_CARRY` : abre nova posição (se bankroll permite e nenhuma aberta)
- `CLOSE_CARRY` : fecha posição existente
- `HOLD` : mantém posição (ou aguarda abertura)
- `SKIP` : nada a fazer

`CarryContext` inclui:

- Funding rate atual (annualized)
- Funding rate histórico últimas 24h (para stability check)
- Preço spot BTC
- Preço perp BTC (para basis)
- Bankroll disponível
- Posição aberta (se houver)

### 2 — Novo tipo de posição em `tr_paper_trades`
Schema atual de `tr_paper_trades` é orientado a single-leg direcional. Carry precisa representar 2 legs (spot + perp).

#### Opção A (escolhida)
Adicionar colunas opcionais na tabela existente.

Para engines existentes:
- `trade_type='directional'` (default)
- colunas carry ficam `NULL`
- comportamento intocado

Para carry:
- `trade_type='carry_neutral'`
- `entry_price` e `exit_price` passam a representar o net PnL da posição agregada (spot + perp + funding)

```sql
ALTER TABLE tr_paper_trades ADD COLUMN IF NOT EXISTS trade_type TEXT DEFAULT 'directional',
ADD COLUMN IF NOT EXISTS spot_entry_price NUMERIC,
ADD COLUMN IF NOT EXISTS perp_entry_price NUMERIC,
ADD COLUMN IF NOT EXISTS spot_exit_price NUMERIC,
ADD COLUMN IF NOT EXISTS perp_exit_price NUMERIC,
ADD COLUMN IF NOT EXISTS funding_accrued_usd NUMERIC DEFAULT 0,
ADD COLUMN IF NOT EXISTS funding_events_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS carry_variant TEXT; -- 'hold_accrual' | 'sell_accrual'

CREATE INDEX IF NOT EXISTS idx_paper_trades_type ON tr_paper_trades(trade_type, engine_id, status);
```

### 3 — Novo módulo `trading/core/execution/carry_fill_model.py`
Simulação de execução para carry.

Padrão do `fill_model.py` existente:

```python
class CarryFillModel:
    def simulate_open(
        self,
        spot_price: float,
        perp_price: float,
        stake_usd: float,
        slippage_bps: float = 5.0,
        fee_bps: float = 10.0,
    ) -> CarryOpenFill:
        ...

    def simulate_funding_event(
        self,
        position: CarryPosition,
        funding_rate_annualized: float,
        variant: str,  # 'hold_accrual' | 'sell_accrual'
    ) -> CarryFundingFill:
        ...

    def simulate_close(
        self,
        position: CarryPosition,
        current_spot: float,
        current_perp: float,
        slippage_bps: float = 5.0,
        fee_bps: float = 10.0,
    ) -> CarryCloseFill:
        ...
```

#### Fórmula de funding por evento
Funding rate é por 8h, não anualizado:

```text
funding_per_event_pct = funding_rate_annualized / (365 * 3)
funding_usd = notional_short_perp * funding_per_event_pct
```

Se funding positivo: longs pagam shorts. Como carry é short perp, recebe funding positivo. Se funding negativo, paga.

#### Variante `sell_accrual`
A cada evento, funding_accrued do ciclo é “realizado” para USDT.
Reduz exposição a variação de preço do BTC recebido.

#### Variante `hold_accrual`
Funding acumula em BTC virtual (multiplicado por preço spot atual para contabilização).

### 4 — Novo worker loop `trading/apps/carry_worker/main.py`

