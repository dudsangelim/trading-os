# t3_b3_ny_open_v0 — Closeout (2026-07-21)

**Status: `premise_refuted` na Fase 0, no V-gate (estrutura).** P-gate nem
foi avaliado (regra pré-registrada). Nenhum backtest rodado.

## O que foi testado

Tese T3: a abertura da NYSE (10:30 local em EDT / 11:30 em EST, alinhado
por DST calculado em código) muda o regime de vol da sessão B3 e cria
interação pré/pós exploratável. WIN e WDO, M5 IS 2022-12→2024-12
(508/500 dias válidos), placebo 14:30, perfil em event time EDT vs EST.

## Resultado

- V1 (mediana vol_post/vol_pre ≥ 1.15): WIN 1.042, WDO 1.004 — FAIL.
- V2 (≥1.10 por ano): 0.95-1.05 em todos — FAIL.
- V3 (NY > placebo): WIN passa (1.04 vs 0.94), WDO empata — misto.
- V4 (pico event-time pós-T0 em EDT E EST): FAIL — o "pico" da janela é
  a borda esquerda (a manhã B3), em ambos os subsets DST. O perfil de vol
  NÃO migra com o horário NY: é o decaimento monotônico desde a abertura
  B3 09:00, dominante sobre qualquer efeito NY.

**Falsificação limpa da premissa causal**: se o NY open comandasse o
regime, o pico deveria seguir o relógio de NY na troca EDT↔EST. Não segue.

## Coerência com o resto do corpo de evidência

Mesmo desenho e mesmo destino do premise-check de
btc_london_asian_compression_v0. E consistente com o perfil de vol do WDO
achado na T2: pico único na abertura B3, decaimento o dia todo. A B3
intraday é dominada pela própria abertura, não por eventos externos
visíveis em OHLCV.

## Code review da fase

DST (nth_sunday/is_edt) correto; convenção de barra correta; filtro de fim
de semana e OOS ok. Sem bugs materiais.

## Não revisitar

Sem post-hoc. Reformulações (ex.: âncora em releases 8:30 ET, interação
com dias de payroll/CPI) exigiriam calendário macro externo — hipótese
NOVA, campanha nova, só se houver literatura + dado de evento confiável.
