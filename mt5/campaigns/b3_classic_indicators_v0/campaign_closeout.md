# b3_classic_indicators_v0 — Closeout (2026-07-22)

**Status: `premise_refuted` nos custos pré-registrados.** 0/88 configs
passaram os gates combinados. OOS 2025+ permanece intacto.

## O que foi testado

Indicadores clássicos como mecânica intraday (F1 EMAs cross, F2 MACD,
F3 Bollinger MR+breakout, F4 RSI 14/2/cross50, F5 VWAP cross+stretch,
F6 combos ADX/EMA200) × {WIN, WDO} × {M5, M15} = 88 configs, DISCOVERY
≤2023 / CONFIRM 2024, custos 2/4/6 bps RT, gate a 6 bps. Sem lookahead
(sinal t → exec open t+1), flat EOD, entradas 09:15–16:00, dias de
rolagem bloqueados. Grade e gates pré-registrados antes de rodar.

## Resultado contra os gates (@6 bps)

| Gate | Passam /88 |
|---|---|
| PF_net6 ≥ 1.10 discovery | 2 |
| n ≥ 200 discovery | 76 |
| Expectancy net6 > 0 nos dois splits | **0** |
| PF_net6 ≥ 1.05 confirm | 1 |
| ex-top2 ≥ 1.0 | 1 |
| estabilidade anual | 3 |

Mediana PF_net6 discovery: 0.67. Os dois que passaram o gate 1
(`WIN_M15_F6i_adxgt25_ema21x50` 1.137, `WDO_M15_F1_ema21x100` 1.110)
caíram pra 0.994 e 0.710 no confirm. O melhor confirm isolado
(`WDO_M15_F6i` PF 1.46, n=34) tinha discovery 0.913 — ordem errada,
n pequeno: ruído.

## Achado honesto: existe sinal bruto, morto por custo

20/88 configs têm expectancy BRUTA positiva nos dois splits, e não é
aleatório — é um cluster coerente: **trend-following de EMAs lentas em
M15** (ema9x100, ema21x50, ema21x100), replicando em WIN E WDO, mais o
MACD zero-filter WDO (mesma família funcional). Magnitude: +0.5 a +5 bps
gross/trade. No custo otimista de 2 bps o melhor caso limpo é
`WIN_M15_F1_ema9x100` (PF_net2 1.096 disc / 1.080 conf) — positivo mas
marginal, e 2 bps pressupõe execução quase perfeita. A 6 bps, tudo morre.

Mesmo padrão de `directional_macro_window_v0` (BTC): sinal estatístico
real com magnitude insuficiente pro custo. Isso é informação, não edge.

## Leitura estrutural

- Mean-reversion clássica (BB MR, RSI fade, VWAP fade) não tem NEM sinal
  bruto consistente — pior família que momentum nos dois ativos.
- O pouco de estrutura intraday que existe em WIN/WDO é continuação lenta
  em M15, ~2-4 bps/trade — só exploratável com custo efetivo <2 bps
  (execução maker com fila modelada, fora do alcance de OHLCV).
- Consistente com o corpo anterior: atlas (0 candidatos), T1/T2/T3, OR,
  VWAP descritivo. **Família "indicadores clássicos OHLCV intraday"
  encerrada para WIN e WDO.**

## Code review da fase (Fable)

Aprovado. Spot-check de 2 trades validado manualmente contra as EMAs;
sem lookahead; splits estanques; 2025+ nunca computado. Ressalvas
menores documentadas no summary.md (subcontagem de rolagem WDO —
imaterial sem overnight; entrada em dia seguinte após dia curto — raro;
label `reversal` cosmético). Nenhuma afeta o veredito.

## Não fazer depois

- Não "salvar" o cluster EMA-M15 com filtros extras (overfitting).
- Hipótese futura legítima e SEPARADA: mesma família com execução
  maker/limit modelada com dados de fila (tick/book), se um dia houver.
