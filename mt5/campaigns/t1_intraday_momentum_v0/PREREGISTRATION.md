# t1_intraday_momentum_v0 — Pré-registro Fase 0 (2026-07-21)

**Tese**: T1 do thesis_registry_2026-07 — retorno da primeira meia hora
prevê o da última meia hora (Gao/Han/Li/Zhou JFE 2018 e replicações).

## Dados e splits

- Fonte: `B3 Futuros/mt5_history/WIN_cont_N_M15.parquet` e `WDO_cont_N_M15.parquet`
  (contínuas não ajustadas, wall-clock B3 UTC-3).
- **IS**: 2021-01-01 → 2024-12-31. **OOS**: 2025-01-01+ — NÃO TOCAR na Fase 0.
- Dias válidos: sessão com barras cobrindo 09:00-09:30 E a janela final;
  descartar dias de sessão curta (véspera de feriado etc.) e reportar quantos.

## Definições (fixadas antes de rodar)

- `ret_first` = close(09:30) / open(09:00) − 1 (variante A, intraday puro)
- `ret_on_first` = close(09:30) / close_prev_day − 1 (variante B, Gao original)
- `ret_on` = open(09:00) / close_prev_day − 1 (variante C, Liu & Tse)
- `ret_last` = close(18:15) / close(17:45) − 1 (última meia hora praticável;
  se a grade real do parquet diferir, usar as 2 últimas barras M15 completas
  ANTES das 18:20 e documentar)
- Regime de vol: terciles do range (high-low)/open da sessão D-1.

## Análises da Fase 0 (descritivo, zero trades simulados)

1. OLS ret_last ~ predictor (cada variante), com t-stat Newey-West.
2. Hit rate: sign(predictor) == sign(ret_last), com IC 95% bootstrap 1000x.
3. Média de ret_last condicional ao sinal do predictor (bps), IC bootstrap.
4. Breakdown por ano (2021/22/23/24), por tercil de vol, por dia da semana.
5. Robustez: excluir top/bottom 1% de ret_first (dependência de outliers).
6. Tudo em WIN E WDO (replicação cruzada).

## Gates da premissa (TODOS pré-registrados; falhou = premise_refuted/weak)

Para declarar **premise_confirmed** numa variante × mercado (IS):

- G1: t-stat NW do slope ≥ 2.0 e slope positivo
- G2: hit rate ≥ 52% com IC95 bootstrap inferior > 50%
- G3: |média condicional de ret_last| ≥ 4 bps gross (viabilidade vs custo
  2-6 bps RT + slippage)
- G4: sinal do efeito consistente (slope > 0) em ≥ 3 dos 4 anos IS
- G5: efeito sobrevive à exclusão de outliers (G1-G3 ainda passam)

**Regra de decisão**: ≥1 variante passa G1-G5 em WIN → Fase 1 (mecânica) só
nessa(s) variante(s). Replicação em WDO conta como evidência extra, não
requisito (mercados diferentes). Nenhuma passa → campanha fecha como
premise_refuted, sem tentar variantes novas post-hoc.

## Custos (para fases futuras, registrado agora)

WIN: tick 5 pts; assumir slippage 1 tick por perna + emolumentos → cenários
2/4/6 bps RT. Entrada/saída da última meia hora compete com leilão de
fechamento — fase 1 usará close(18:15) como último preço praticável, nunca
o ajuste.
