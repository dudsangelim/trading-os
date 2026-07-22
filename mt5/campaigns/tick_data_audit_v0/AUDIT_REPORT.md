# tick_data_audit_v0 — Relatório (2026-07-21)

Probe read-only via `copy_ticks_range` (COPY_TICKS_ALL) na XP demo
(XPMT5-DEMO), 12 datas-amostra de D-2 a D-730. JSON completo em
`tick_audit_report.json`.

## Achados

### 1. Contrato vigente (front) tem tick COMPLETO com agressor real
- **WINQ26**: ~1M ticks/dia, `last`/`volume` 100%, **flags BUY 48% / SELL
  49% / LAST 95%** — marcação de agressor REAL e balanceada, no período em
  que é front (~últimos 30-45 dias). Antes de virar front: dados esparsos
  e flags zeradas.
- **WDOQ26**: ~65-85k ticks/dia, BUY 43-49% / SELL 47-49% — idem, no
  período front (~último mês).
- **Implicação**: OFI/fluxo de agressão verdadeiro EXISTE, mas só é
  servido pelo contrato vigente enquanto vigente. Coleta tem que ser
  contínua e rolar junto com o contrato.

### 2. Contínua WIN$N: ~1 ANO de ticks de negócio, sem agressor
- Ticks até ~D-365 (2025-07): 600k-1.7M/dia, `last`/`volume` 100%.
- **Flags inúteis** (BUY 100%/SELL 0% em tudo — placeholder do feed).
- **Implicação**: dá pra reconstruir fluxo por **tick-rule** (classificar
  agressor pela direção do trade vs trade anterior — método clássico
  Lee-Ready/tick test) para ~jul/2025→hoje. Aproximação, não verdade.

### 3. WDO$N contínua: cache com buracos
- Dias presentes (D-2, D-7, D-270, D-365) e ausentes (D-14 a D-120)
  intercalados — cache de servidor não confiável pra backfill sistemático.

### 4. Janela de disponibilidade é ROLANTE (risco de perda)
- D-540/D-730 vazios em tudo: o servidor serve ~1 ano. O que não for
  coletado agora pode sumir. Backfill é urgência, não conveniência.

## Nuance metodológica importante

Tick data só existe 2025-07+, que é exatamente o período OOS sagrado das
campanhas OHLCV. Não há conflito direto (universo de dados novo, campanhas
novas), mas uma linha de pesquisa tick precisará de split próprio (ex.:
research 2025-07→2026-03, holdout 2026-04+, walk-forward) e NÃO poderá
reusar o OOS OHLCV como se fosse virgem para mecânicas híbridas.

## Recomendações (ordem)

1. **Backfill imediato** de WIN$N ticks (last/volume) dos ~250 pregões
   disponíveis → parquet diário (~10-20MB/dia comprimido, ~3-5GB total).
2. **Coleta diária recorrente** do front WIN + WDO com flags de agressor
   (scheduled task local ou bot na VPS) — constrói o dataset OFI real
   daqui pra frente, rolando de contrato automaticamente.
3. Campanha OFI só depois de ≥2-3 meses de agressor real acumulado
   (ou usando tick-rule no backfill, com a ressalva de aproximação).
