# m3_eth_shadow — Spec

**Status:** ACTIVE (paper) desde 2026-04-17
**Promovida de:** SIGNAL_ONLY
**Base estatística da promoção:** Backtest conservador 5bps, N=77, WR 64.9%, edge +17 p.p.

## Lógica de entrada (conservative fill)

O sinal só dispara quando o preço varre a zona por ≥ 5bps no primeiro bar 15m após o breakout 1h:

- **LONG:** `bar.low ≤ comp_high × (1 - 5bps)` E `bar.close > comp_high`
- **SHORT:** `bar.high ≥ comp_low × (1 + 5bps)` E `bar.close < comp_low`

Motivo: o modelo "direct entry" original (optimistic) assumia fill garantido no primeiro bar sem verificar retorno à zona. Validação mostrou que 79% dos 370 trades do backtest original não teriam executado em produção como limit orders. O subset conservador (N=77) tem qualidade superior (WR 64.9% vs 60.3%) e é a base real da promoção.

Se a condição não for atendida: `CONSERVATIVE_FILL_NOT_MET` skip registrado no `tr_skip_events`. Pending expirado.

## Parâmetros de zona

- `LOOKBACK = 8` — janela 1h para definição da zona (8h)
- `MAX_WIDTH = 2.0 × ATR14` — filtro de largura da zona
- Gate de tendência: EMA20 > EMA50 E slope > 0 no 4h (LONG); espelhado (SHORT)
- RR = 1.5, Timeout = 96 bars (24h)

## Sizing inicial (cauteloso)

- `risk_per_trade_pct = 0.015` (metade do padrão de 0.03 usado pelo m3_sol)
- `leverage = 10`
- Effective notional: $150 em bankroll $1000 (15% vs 30% do m3_sol)

**Razão do sizing reduzido:** engine acumula N ≥ 30 trades reais antes de validar WR backtest vs real. Nenhuma outra engine da família "retest em zona" tem histórico real suficiente para calibrar o gap.

## Critério de revisão automática

### Revisão 1 — Primeira validação
**Gatilho:** N ≥ 30 trades reais paper

Ações:
- Comparar WR real vs 64.9% do backtest conservador
- Se WR real ≥ 55%: manter engine ACTIVE, considerar aumentar `risk_per_trade_pct` para 0.03
- Se WR real entre 45–55%: manter sizing reduzido, aguardar N = 50
- Se WR real < 45%: rebaixar para SIGNAL_ONLY, investigar gap real vs backtest

### Revisão 2 — Validação completa
**Gatilho:** N ≥ 50 trades reais paper OU 2026-10-31 (o que vier primeiro)

Ações:
- Análise completa: WR, expectancy, cost/gross, sl_distance real vs backtest
- Comparar frequência de `CONSERVATIVE_FILL_NOT_MET` skip vs sinal gerado (taxa de miss)
- Decisão: ACTIVE em stake padrão / ACTIVE em stake reduzido / SIGNAL_ONLY / ARCHIVED

### Kill switch
**Gatilho:** DD > 15% no equity atribuído à engine

Ação: pausar engine imediatamente (worker pause via `CIRCUIT_DD_DIARIO`), revisar antes de retomar. Não reiniciar automaticamente.

## Referências

- Validação fill: `~/m3_validation/results/VALIDATION_REPORT.md`
- Backtest conservador: `~/m3_validation/results/m3_eth_shadow/trades_conservative_5.csv`
- Commit lógica conservadora: 096cddc
- Commit sizing/signal_only: 9381345
- Commit role ACTIVE: (ver commit 4)
