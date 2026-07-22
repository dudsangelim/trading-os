# win_stock_leadlag_v0 — Closeout Phase 0A (2026-07-22)

**Status: `premise_refuted`.** Todos os gates mecânicos falharam.
OOS 2025+ intacto. Não haverá Phase 0B.

## O que foi testado

Tese do Eduardo: VALE3/PETR4/ITUB4 (maiores pesos do Ibov) antecipam o
WIN em minutos. Coleta nova via MT5 (M5 2021-12→hoje, ~44k barras
pareadas/ação pré-2025; M1 inutilizável — ver ressalva). DISCOVERY
≤2023 / CONFIRM 2024. Quatro testes descritivos pré-registrados.

## Resultados

- **T1 (sanity)**: corr contemporânea 0.43-0.68 ✓ — dados bons; a
  relação existe, mas contemporânea (não exploratável).
- **T2 (lead-lag linear)**: |corr| < 0.02 em TODOS os lags k=1..6 M5,
  nas duas direções, nas 3 ações, nos dois splits. **Achado
  informativo**: nem o espelho WIN→ação aparece — em granularidade de
  5min a arbitragem já está completa nos DOIS sentidos. O lead do
  futuro documentado na literatura vive abaixo de 5min.
- **T3 (choque idiossincrático — central)**: 612 eventos. Forward WIN
  15min: +0.96 bps disc / +0.42 bps conf, ICs cruzando zero. Único IC
  fora de zero (ITUB4 conf fwd30 +4.4 bps, n=97) não tem contrapartida
  no discovery = ruído de comparações múltiplas. Mesmo que fosse real,
  4.4 bps < custo 6 bps.
- **T4 (divergência de cesta)**: sem monotonicidade; extremo +0.51 bps
  no disc; sinais invertem no confirm.

Gate 1 (T2∨T3∨T4): FALHA · Gate 2 (≥2 ações): FALHA (0 hits).

## Code review da fase (Fable)

Aprovado. Z-score T3 por horário com shift(1) intra-bucket (sem
lookahead); cortes T4 só no discovery; bootstrap com seed. Um desvio da
spec, registrado: forward T3 medido do close da barra do evento (não da
seguinte) — viés PRÓ-efeito; o nulo com esse viés fortalece a refutação.

## Ressalvas estruturais (pra campanhas futuras)

1. **M1 do MT5 é inutilizável pra pesquisa com OOS 2025+**: o teto de
   ~100k barras alcança só ~9-11 meses → 100% do M1 cai dentro do
   embargo. Qualquer campanha que precise de M1 histórico exige outra
   fonte ou acúmulo próprio (como o tick collector).
2. `copy_rates_from_pos` falha com count=100000 exato; usar 99999.
3. Pesos da cesta = atuais aplicados ao passado (viés conhecido,
   irrelevante dado o nulo).

## Leitura

A informação de VALE/PETR/ITAU está no WIN dentro da mesma barra de
5min — não há atraso exploratável nessa granularidade, nem no caso mais
favorável (choque idiossincrático com WIN parado). Consistente com o
precedente BTC→ETH (cross-asset em barras = ruído). Se a tese for
revisitada um dia, só faz sentido em sub-minuto (tick data das ações +
WIN tick, ambos exigiriam coleta própria de meses) — anotar como
hipótese de baixa prioridade, não como pendência.
