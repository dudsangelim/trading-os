# win_vwap_v0 — Closeout (2026-07-21)

**Status: `premise_refuted`.** Última família OHLCV intraday do WIN.

## O que foi testado

VWAP de sessão (TP×real_volume cumulativo) como referência de valor:
H_rev (esticado reverte), H_magnet (VWAP atrai), H_trend (lado+inclinação
tem drift). 7 checkpoints (10h-16h) × 5 buckets de desvio normalizado por
ATR20 (sem lookahead) × forward 30/60min. DISCOVERY 2021-23 (610 dias) /
CONFIRM 2024 (250) / OOS 2025+ intocado. M5 como robustez de grade.
196 testes de efeito + 280 de robustez.

## Resultado

- **H_rev**: 3 células passaram o gate no discovery; **0/3 replicaram**
  em 2024 (duas inverteram sinal). As 3 eram do bucket "perto do VWAP" —
  nem eram reversão de esticamento; os buckets esticados (|dev_n|≥0.15)
  deram 0-4 bps com IC cruzando zero em toda parte.
- **H_trend**: nenhuma das 28 células chegou perto (melhor: −10.4 bps com
  IC cruzando 0, n=25).
- **H_magnet**: taxa de toque do VWAP em 60min cai de ~90% (10h — artefato:
  VWAP jovem anda colado no preço) para 17-25% (15h-16h), sem assimetria
  lado comprado/vendido. Sem estrutura exploratável além do trivial.

## Leitura

Mesmo padrão das outras famílias: efeito aparente no discovery que não
sobrevive a holdout. Com este fechamento, o corpo OHLCV intraday WIN cobre:
OR, momentum 1ª→última meia hora, NY open, gaps, níveis D-1, autocorrelação
horária, tipologia, preditores de direção e VWAP. **Família OHLCV intraday
WIN encerrada** — não revisitar sem dado novo (tick/OFI, já em coleta desde
2026-07-21) ou âncora externa (calendário macro US).

## Code review da fase

ATR20 com shift(1) correto; VWAP cumulativo por sessão; forwards
estritamente à frente; fallback de volume nunca disparou (real_volume
100% presente); artefato do D3 nos checkpoints iniciais identificado
pelo próprio executor e descontado na leitura.
