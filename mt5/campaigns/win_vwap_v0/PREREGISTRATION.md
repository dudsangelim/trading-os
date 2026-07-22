# win_vwap_v0 — Pré-registro Fase 0 / Bateria D (2026-07-21)

**Tese**: VWAP de sessão como referência de valor intradiário no WIN —
(H_rev) preço esticado vs VWAP reverte; (H_magnet) VWAP atrai o preço;
(H_trend) lado do preço vs VWAP + inclinação do VWAP tem drift. Última
família OHLCV+volume não varrida (atlas v0 esgotou o resto).

## Disciplina (herda PROTOCOL.md do atlas)

- DISCOVERY 2021-07→2023-12 / CONFIRM 2024 / **OOS 2025+ intocado**.
- Bootstrap 1000x seed 42; reportar n, bps, IC95 nos dois splits; contar
  total de testes.
- Promoção: |efeito| ≥ 6 bps DISCOVERY com IC95 excl. 0, n≥100, mesmo
  sinal e ≥3 bps no CONFIRM, robusto a trim 1%. Alvos SEMPRE forward
  (nunca contendo a janela do sinal — lição do atlas C1).

## Dados e definições

- WIN_cont_N_M15 (principal, tem real_volume); M5 como robustez 2023+.
- Sessões que abrem 09:00; fim real por dia; Cinzas fora.
- VWAP de sessão: cumsum(TP×real_volume)/cumsum(real_volume), TP=(H+L+C)/3,
  começando na barra 09:00. dev(t) = close(t)/VWAP(t) − 1.
- **Normalização (vol encolhendo — lição do atlas)**: dev_n = dev /
  ATR20, onde ATR20 = média móvel 20 sessões do range diário (high-low)/open
  em bps, calculada SÓ com dias ≤ t (sem lookahead). Buckets pré-fixados
  de dev_n: (−inf,−0.15], (−0.15,−0.05], (−0.05,0.05), [0.05,0.15),
  [0.15,+inf).
- Checkpoints horários: 10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 16:00.
  (17:00 fora: a sessão acaba 17:45/18:00 em parte da amostra — janela
  forward de 60min não cabe de forma uniforme.)

## Análises

D1. Distribuição de dev (bps e dev_n) por checkpoint × split (mediana,
    quartis) — mapa de quão longe o preço anda do VWAP.
D2. **H_rev**: ret forward 30min e 60min (close→close) condicional ao
    bucket de dev_n × checkpoint. Célula: n, média bps, IC95, DISCOVERY
    e CONFIRM. Convenção de leitura: reversão = sinal do ret oposto ao
    sinal do dev.
D3. **H_magnet**: p/ |dev_n| ≥ 0.05 no checkpoint, % que toca o VWAP
    (low≤VWAP≤high de alguma barra) em até 60min / até o fim do dia;
    tempo mediano até toque. Por checkpoint × lado × split.
D4. **H_trend**: sinal(dev) × sinal(inclinação VWAP últimos 30min) →
    ret forward 60min. 4 células × checkpoint × split.
D5. Robustez: D2 com trim 1% dos dev_n; D2 recalculado em M5 (2023
    discovery-parcial, 2024 confirm) p/ checar granularidade.

## Gates de leitura

Sem gate de campanha único (é bateria descritiva): candidatos promovidos
individualmente pelo critério acima. Se ZERO células passarem → família
VWAP OHLCV encerrada, registrar e não revisitar sem tick data.

## Custos de referência

2-6 bps RT + slippage. Célula útil precisa ≥ 6 bps líquido-viável.
