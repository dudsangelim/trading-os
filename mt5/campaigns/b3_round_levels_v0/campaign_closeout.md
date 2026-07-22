# b3_round_levels_v0 — Closeout Phase 0A (2026-07-22)

**Status: `premise_refuted`.** 0/42 células passaram os gates. As
hipóteses de Osler (bounce em nível redondo; cascata pós-rompimento)
não aparecem em WIN/WDO em granularidade de barra, nem intraday nem
swing. OOS 2025+ intacto. Não haverá Phase 0B.

## O que foi testado

Grades redondas (WIN 500/1000/5000; WDO 5/10/50) vs grades PLACEBO de
meia-grade, mesmíssimo algoritmo nas duas — todo efeito reportado é a
diferença redondo−placebo com bootstrap 1000×. Thresholds em frações de
ATR20d (shift 1). Rolagem por calendário excluída dos eventos. M5/M15/H1
(intraday) + D1 (swing). DISCOVERY ≤2023 / CONFIRM 2024. 6 testes:
T1 bounce, T2 cascata+velocidade, T3 magnetismo, T4 barreira,
T5 cruzamento swing, T6 rejeição swing. n amplo (T1/T2: 280-4200
eventos/célula; T5/T6: 86-294).

## Resultado

- **T1/T2/T5/T6 (forwards)**: TODOS os ICs da diferença redondo−placebo
  cruzam zero. Nem bounce, nem cascata, nem drift, nem rejeição.
- **T3 (magnetismo)**: WIN ±0.5pp, ICs cruzando zero em todas as
  grades/TFs. WDO g5 saturado por construção (banda 0.05×ATR > meia-
  grade de 2.5 pts — sem potência, documentado). Única célula com IC
  fora de zero (WDO g10 M5 confirm −2.4pp) inverte o sinal do discovery
  = ruído.
- **T4 (barreira)**: nada no discovery. WIN g5000 mostra efeito
  barreira forte SÓ no confirm (−10.4pp, KS p=0.0007) — ordem errada
  pelo protocolo (discovery→confirm), 1 célula entre as comparações,
  descartado como falso positivo. Anotado como watch-item de baixíssima
  prioridade (se reaparecer em campanha futura com outra motivação).
- **Gate 4 (coerência)**: várias células com bounce E cascata "positivos"
  simultâneos — confirmando que os pequenos desvios são artefato, não
  mecanismo.
- **Intraday vs swing ("qual o melhor")**: nenhum. Os dois braços nulos.

## Code review da fase (Fable)

Aprovado. Simetria redondo/placebo verificada (mesma função, só muda
offset); direção assinada pelo lado de aproximação; ATR sem lookahead;
eventos deduplicados por dia×nível; splits por ts do evento. Adaptações
do executor aceitas e documentadas: horizontes reescalados em H1 (os
literais 15/30min degeneravam) e forward medido do close do evento
(viés PRÓ-efeito — nulo ainda mais forte). Bug de potência do WDO g5
identificado pelo próprio executor.

## Leitura

O mecanismo de Osler (clustering de stops em redondos) foi documentado
em FX 1996-98 com dados de ordens de dealer. Em WIN/WDO 2021-24, em
barras de 5min+, não há assinatura: nem estática (magnetismo/barreira)
nem dinâmica (bounce/cascata). Ou o clustering não existe nesses
contratos (mercado eletrônico moderno, stops algorítmicos dispersos),
ou vive em escala sub-barra. Com isso, o ÚNICO primitivo SMC com lastro
acadêmico também está refutado nos nossos dados em barra — a família
SMC/liquidez em OHLCV está encerrada para WIN/WDO.

## O que fica

- Caminho tick (assinatura de rajada de agressão em níveis, sub-minuto):
  hipótese legítima mas de baixa prioridade; exige a base tick (1a WIN)
  e campanha nova.
- Watch-item: T4 barreira WIN g5000 (confirm-only) — não perseguir
  ativamente.
