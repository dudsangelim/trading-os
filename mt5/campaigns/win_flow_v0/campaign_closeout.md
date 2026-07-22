# win_flow_v0 — Closeout (2026-07-22)

**Status: `premise_refuted` na Fase 0b** — para OFI simples (tick-rule,
agregação 1-min, janelas 5/15/30min) prevendo retornos 5/15/30min no WIN.

## O que a campanha ENTREGOU (além do resultado nulo)

1. **Infra validada**: 256 pregões de flow-bars 1min (143.664 barras,
   0% NaN) a partir de 2.81 GB de ticks; pipeline reproduzível.
2. **Tick-rule VALIDADO contra agressor real** (G4): 80.85% por trade /
   84.95% por volume, estável em 9 dias — na faixa da literatura (75-85%).
   O classificador serve pra qualquer pesquisa futura de fluxo na contínua.
3. Descoberta e correção do bug de timezone da coleta (epoch = wall-clock
   B3 rotulado UTC) ANTES de contaminar qualquer análise.

## Resultado do mapa (Fase 0b)

36 combos (2 sinais × k∈{5,15,30} × h∈{5,15,30} × 2 metades). DISCOVERY
jul/2025-mar/2026 (180 dias, H1/H2), HOLDOUT abr-jul/2026 intocado.

- Maior |spread Q5−Q1|: **1.43 bps** (LARGE_OFI k5 h30, H2) — gate era 6.
- 5/36 combos com IC excluindo 0, todos |spread| < 1.5 bps: um leve e
  consistente viés de **reversão** ao OFI de 5min (~1 bp) — assinatura de
  bid-ask bounce/ruído de microestrutura, economicamente morto vs custo
  taker 2-6 bps. Nem `maker_only_candidate` (≥2 bps) apareceu.
- Direção instável entre metades na maioria dos combos.

## O que isto NÃO refuta (campanhas futuras legítimas, cada uma com nome novo)

- OFI com **agressor real** (WINQ26+ acumulando via coletor diário — reavaliar
  com ≥2-3 meses; acurácia 81% do tick-rule sugere ganho marginal, não milagre).
- Horizontes sub-5min / execução maker — exigem dados de **book/fila**
  (MT5 `market_book_*` dá DOM em tempo real; seria coleta forward-only,
  processo contínuo — upgrade de infra a decidir).
- Fluxo condicionado a contexto (níveis D-1, eventos macro) — interação,
  não sinal isolado.
- VPIN/toxicidade, trade-size clustering.

## Code review da fase

0a: bug de sessão (TimedeltaIndex → ndarray) corrigido; offset de timezone
corrigido no review. 0b: forward por relógio exato verificado; desvio único
documentado (thresholds de quintil por combo — conservador). Sem lookahead.

## Decisão

Campanha fechada. Linha tick continua VIVA via coleta diária (agressor
real acumulando para `win_flow_v1_realflags` futura).
