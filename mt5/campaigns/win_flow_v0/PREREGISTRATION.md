# win_flow_v0 — Pré-registro Fase 0 (2026-07-21)

**Tese**: order flow imbalance (OFI) prevê retornos de curto horizonte —
literatura de microestrutura estabelecida (Cont/Kukanov/Stoikov 2014,
price impact de eventos de fluxo; Lee-Ready 1991 p/ classificação;
literatura VPIN/agressão). Primeira campanha da linha tick B3.

## Dados e universo

- `B3 Futuros/tick_history/WINSN/` — 256 pregões de ticks de negócio
  WIN$N (2025-07-14 → 2026-07-21), campos time/time_msc/bid/ask/last/
  volume/flags (flags de agressor = placeholder, NÃO usar).
- `tick_history/WINQ26/` — 9 pregões com flags de agressor REAIS
  (uso EXCLUSIVO na validação de classificação, nunca em estatística de
  retorno).

## Splits (universo novo; não toca o OOS OHLCV)

- **DISCOVERY**: 2025-07-14 → 2026-03-31, dividido em
  **H1** (2025-07→2025-11) e **H2** (2025-12→2026-03) p/ replicação interna.
- **HOLDOUT**: 2026-04-01 → 2026-07-21 — NÃO TOCAR na Fase 0; será usado
  UMA vez, só se uma mecânica completa da Fase 1 chegar lá.
- Exceção pré-registrada: os dias 2026-07-09→21 do WINQ26 entram SÓ no
  QA de classificação (acurácia tick-rule vs flag real) — nenhuma
  estatística de retorno é extraída deles.

## Fase 0a — QA e construção (mecânico)

1. QA: cobertura de bid/ask nos ticks WIN$N (% ticks com bid>0 e ask>0);
   se cobertura ≥90%, classificar por **Lee-Ready** (last≥ask→buy,
   last≤bid→sell, senão tick-rule); senão, **tick-rule puro** (uptick→buy,
   downtick→sell, zero-tick→herda última classe).
2. **Validação contra verdade**: nos dias de overlap, aplicar o MESMO
   classificador aos ticks do WINQ26 (ignorando flags), comparar com as
   flags BUY/SELL reais. Reportar acurácia por dia (ponderada por volume
   e por trade). **Gate G4: acurácia média ≥70%** (literatura: 75-85%).
   G4 falhou → campanha vira `premise_blocked_data_quality` (esperar
   acumulação de agressor real; NÃO é refutação da tese).
3. Construir flow-bars de 1min por dia: n_trades, vol_total, buy_vol,
   sell_vol, ofi = (buy−sell)/(buy+sell), large_ofi (idem, só trades com
   volume ≥ p90 do próprio dia), close_last. Salvar parquet único.

## Fase 0b — Mapa preditivo (pré-fixado)

- Grade de amostragem: a cada 5min, 09:05→17:30, dias válidos.
- Sinais (2, sem post-hoc): OFI_k e LARGE_OFI_k trailing, k ∈ {5,15,30}min.
- Alvos: ret forward h ∈ {5,15,30}min (close_last→close_last),
  ESTRITAMENTE à frente (sem sobreposição sinal/alvo).
- Estatística: quintis do sinal (thresholds por split-half), média do ret
  forward por quintil em bps, spread Q5−Q1 com IC95 por bootstrap de
  BLOCO-DIA (1000x, seed 42); Spearman quintil→média; por hora do dia
  (manhã 09-12 vs tarde 13-17:30); H1 e H2 separados.
- Contar total de testes.

## Gates da premissa

- **G1**: ≥1 combinação (sinal, k, h) com h≥15min e |spread Q5−Q1| ≥ 6 bps,
  IC95 excluindo 0, mesmo sinal em H1 E H2.
- **G2**: monotonicidade (|Spearman| ≥ 0.9) na combinação de G1, em H1 e H2.
- **G3**: efeito sobrevive à remoção dos 5% dias de maior |contribuição|.
- **G4**: acurácia de classificação ≥70% (Fase 0a).
- Combos com h=5min e spread 2-6 bps: reportar como
  `maker_only_candidate` (não passam gate; fila/queue não modelável com
  tick de negócio — só viram mecânica com dados de book futuros).

**Decisão**: G1-G4 → Fase 1 (mecânica pré-registrada, custos 2-6 bps RT,
depois holdout uma vez). Falhou G1-G3 com G4 ok → `premise_refuted` para
OFI tick-rule em WIN (variações VPIN/trade-size viram campanhas novas,
não post-hoc aqui).

## Custos de referência

Taker RT 2-6 bps + slippage. Horizonte h≥15min é o mínimo tradeável
realista via taker; h=5min só faria sentido maker.
