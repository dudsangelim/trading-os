# b3_mtf_swinghold_v0 — Fase 0 — Sumário

Total de configs: 56 (esperado 56 pelo pré-registro)

## (a) Sanity — rolagem por calendário

### WIN

- Total de janelas: 21 (3 pulada(s) por falta de histórico no início da série)
- Janelas por ano:
  - 2021: 3
  - 2022: 6
  - 2023: 6
  - 2024: 6
- Primeiras 5 janelas (data de vencimento -> pregões da janela):
  - vencimento 2021-08-18: [2021-08-12, 2021-08-13, 2021-08-16, 2021-08-17, 2021-08-18]
  - vencimento 2021-10-13: [2021-10-06, 2021-10-07, 2021-10-08, 2021-10-11, 2021-10-13]
  - vencimento 2021-12-15: [2021-12-09, 2021-12-10, 2021-12-13, 2021-12-14, 2021-12-15]
  - vencimento 2022-02-16: [2022-02-10, 2022-02-11, 2022-02-14, 2022-02-15, 2022-02-16]
  - vencimento 2022-04-13: [2022-04-07, 2022-04-08, 2022-04-11, 2022-04-12, 2022-04-13]

### WDO

- Total de janelas: 41 (1 pulada(s) por falta de histórico no início da série)
- Janelas por ano:
  - 2021: 5
  - 2022: 12
  - 2023: 12
  - 2024: 12
- Primeiras 5 janelas (data de vencimento -> pregões da janela):
  - vencimento 2021-08-02: [2021-07-28, 2021-07-29, 2021-07-30, 2021-08-02]
  - vencimento 2021-09-01: [2021-08-27, 2021-08-30, 2021-08-31, 2021-09-01]
  - vencimento 2021-10-01: [2021-09-28, 2021-09-29, 2021-09-30, 2021-10-01]
  - vencimento 2021-11-01: [2021-10-27, 2021-10-28, 2021-10-29, 2021-11-01]
  - vencimento 2021-12-01: [2021-11-26, 2021-11-29, 2021-11-30, 2021-12-01]

## (b) Contagem de configs passando cada gate (DISCOVERY, informativo — não é veredito)

| Gate | Descrição | Passam / Total |
|---|---|---|
| 1 | PF_net6 >= 1.10 discovery | 0 / 56 |
| 2 | n_trades >= 200(A)/120(B,C) discovery | 48 / 56 |
| 3 | Expectancy net6 > 0 discovery E confirm | 0 / 56 |
| 4 | PF_net6 >= 1.05 confirm | 2 / 56 |
| 5 | PF_net6 ex-top2(A,C)/ex-top3(B) >= 1.0 discovery | 0 / 56 |
| 6 | Nenhum ano com PF_net6 < 0.85 discovery | 2 / 56 |
| 7 | Replicação estrutural (sinal não inverte no outro ativo) | 54 / 56 |

**Configs que passam TODOS os 7 gates: 0 / 56**

## (c) Top-15 configs por PF_net6 no DISCOVERY (com CONFIRM ao lado), agrupado por arm

### Arm A

| config_id | asset | n_disc | pf_net6_disc | exp_net6_disc | n_conf | pf_net6_conf | exp_net6_conf |
|---|---|---|---|---|---|---|---|
| WIN_A_G2_T1 | WIN | 285 | 0.842 | -4.59 | 119 | 0.703 | -5.80 |
| WIN_A_G3_T2 | WIN | 100 | 0.833 | -6.19 | 48 | 0.590 | -9.34 |
| WIN_A_G3_T1 | WIN | 284 | 0.808 | -5.92 | 130 | 0.777 | -3.99 |
| WIN_A_G2_T2 | WIN | 100 | 0.789 | -7.74 | 47 | 0.851 | -3.35 |
| WIN_A_G4_T2 | WIN | 78 | 0.781 | -8.20 | 43 | 1.081 | 1.69 |
| WIN_A_G1_T4 | WIN | 305 | 0.770 | -8.35 | 129 | 0.625 | -9.91 |
| WIN_A_G3_T4 | WIN | 261 | 0.767 | -9.36 | 117 | 0.652 | -8.40 |
| WIN_A_G4_T4 | WIN | 248 | 0.761 | -8.71 | 94 | 0.745 | -6.53 |
| WIN_A_G1_T1 | WIN | 257 | 0.759 | -6.98 | 110 | 0.792 | -3.93 |
| WIN_A_G2_T4 | WIN | 270 | 0.745 | -9.78 | 121 | 0.559 | -11.55 |
| WIN_A_G1_T2 | WIN | 76 | 0.742 | -8.81 | 35 | 1.341 | 5.48 |
| WIN_A_G1_T3 | WIN | 498 | 0.690 | -8.42 | 171 | 0.688 | -5.09 |
| WIN_A_G3_T3 | WIN | 577 | 0.680 | -9.07 | 210 | 0.853 | -2.22 |
| WDO_A_G2_T2 | WDO | 111 | 0.678 | -7.33 | 38 | 0.773 | -4.86 |
| WIN_A_G4_T1 | WIN | 284 | 0.677 | -9.97 | 114 | 0.783 | -4.44 |

### Arm B

| config_id | asset | n_disc | pf_net6_disc | exp_net6_disc | n_conf | pf_net6_conf | exp_net6_conf |
|---|---|---|---|---|---|---|---|
| WDO_B_B2_m15_ema21x100 | WDO | 425 | 1.042 | 1.56 | 179 | 0.852 | -4.62 |
| WDO_B_B3_h1_ema9x21 | WDO | 389 | 1.037 | 1.46 | 162 | 0.897 | -3.27 |
| WIN_B_B3_h1_ema9x21 | WIN | 329 | 0.993 | -0.39 | 148 | 0.670 | -14.72 |
| WDO_B_B1_m15_ema9x100 | WDO | 570 | 0.992 | -0.25 | 229 | 0.851 | -3.98 |
| WIN_B_B2_m15_ema21x100 | WIN | 371 | 0.954 | -2.47 | 152 | 0.774 | -9.13 |
| WDO_B_B5_h1_macd | WDO | 569 | 0.940 | -2.20 | 245 | 0.885 | -3.13 |
| WIN_B_B5_h1_macd | WIN | 540 | 0.902 | -5.00 | 230 | 0.808 | -6.65 |
| WDO_B_B4_h1_ema21x50 | WDO | 248 | 0.849 | -7.95 | 107 | 0.750 | -11.28 |
| WIN_B_B1_m15_ema9x100 | WIN | 506 | 0.842 | -7.62 | 204 | 0.762 | -8.33 |
| WIN_B_B6_d1_ema5x20 | WIN | 127 | 0.783 | -20.85 | 52 | 0.918 | -5.10 |
| WIN_B_B4_h1_ema21x50 | WIN | 198 | 0.752 | -20.49 | 86 | 0.866 | -7.05 |
| WDO_B_B6_d1_ema5x20 | WDO | 187 | 0.661 | -24.34 | 74 | 0.953 | -2.32 |

### Arm C

| config_id | asset | n_disc | pf_net6_disc | exp_net6_disc | n_conf | pf_net6_conf | exp_net6_conf |
|---|---|---|---|---|---|---|---|
| WDO_C_E1_h1_ema21x50_X2_0915_close | WDO | 492 | 0.727 | -6.04 | 206 | 0.489 | -11.61 |
| WDO_C_E2_d1_close_vs_ema20_X2_0915_close | WDO | 491 | 0.689 | -7.05 | 206 | 0.523 | -10.53 |
| WIN_C_E1_h1_ema21x50_X2_0915_close | WIN | 537 | 0.677 | -10.37 | 220 | 0.646 | -7.55 |
| WIN_C_E2_d1_close_vs_ema20_X2_0915_close | WIN | 536 | 0.654 | -11.32 | 220 | 0.455 | -13.50 |
| WDO_C_E3_h1_macd_X2_0915_close | WDO | 492 | 0.627 | -8.89 | 206 | 0.647 | -7.11 |
| WIN_C_E3_h1_macd_X2_0915_close | WIN | 537 | 0.615 | -12.91 | 220 | 0.702 | -6.25 |
| WIN_C_E3_h1_macd_X1_next_open | WIN | 537 | 0.595 | -11.51 | 220 | 0.623 | -6.16 |
| WIN_C_E1_h1_ema21x50_X1_next_open | WIN | 537 | 0.592 | -11.61 | 220 | 0.500 | -9.10 |
| WDO_C_E1_h1_ema21x50_X1_next_open | WDO | 492 | 0.581 | -7.77 | 206 | 0.537 | -7.70 |
| WIN_C_E2_d1_close_vs_ema20_X1_next_open | WIN | 536 | 0.558 | -12.91 | 220 | 0.363 | -13.04 |
| WDO_C_E2_d1_close_vs_ema20_X1_next_open | WDO | 491 | 0.558 | -8.34 | 206 | 0.501 | -8.50 |
| WDO_C_E3_h1_macd_X1_next_open | WDO | 492 | 0.463 | -10.90 | 206 | 0.523 | -7.80 |

## (d) Mediana de PF_gross e PF_net6 por arm e split

| arm | split | mediana pf_gross | mediana pf_net6 | n configs |
|---|---|---|---|---|
| A | discovery | 0.864 | 0.666 | 32 |
| A | confirm | 0.981 | 0.673 | 32 |
| B | discovery | 1.046 | 0.921 | 12 |
| B | confirm | 0.997 | 0.851 | 12 |
| C | discovery | 0.833 | 0.605 | 12 |
| C | confirm | 0.837 | 0.523 | 12 |

## (e) Spot-checks manuais

```

Exemplo de alinhamento MTF (WIN):
  Barra M15 de sinal: open_ts=2021-07-28 10:45:00 term_ts(=open+15min)=2021-07-28 11:00:00
  Barra H1 usada pelo gate: open_ts=2021-07-28 10:00:00 term_ts(=open+60min)=2021-07-28 11:00:00
  Confirmação: term_ts H1 (2021-07-28 11:00:00) <= term_ts M15 (2021-07-28 11:00:00) => H1 já estava COMPLETA quando o sinal M15 fechou.
  h1_ema9 anexado na barra M15 = 125271.78 (igual ao ema9 H1 na barra usada: 125271.78)

[WIN_B_B3_h1_ema9x21] Trade 'flip multi-dia': dir=1 entry_ts=2021-07-20 09:00:00 entry_px=125680.0 exit_ts=2021-07-23 14:00:00 exit_px=125050.0 reason=flip pnl_gross_bps=-50.13 hold_bars=128
--- barras ao redor da ENTRADA (2021-07-20 09:00:00) ---
                 ts       date     open     high      low    close       h1_ema9      h1_ema21
2021-07-20 09:00:00 2021-07-20 125680.0 125805.0 125195.0 125250.0 124457.677363 124433.813155
2021-07-20 09:15:00 2021-07-20 125245.0 125325.0 125130.0 125205.0 124457.677363 124433.813155
2021-07-20 09:30:00 2021-07-20 125200.0 125240.0 124825.0 124845.0 124457.677363 124433.813155
2021-07-20 09:45:00 2021-07-20 124845.0 124880.0 124660.0 124785.0 124523.141891 124465.739232
2021-07-20 10:00:00 2021-07-20 124780.0 124790.0 124040.0 124165.0 124523.141891 124465.739232
2021-07-20 10:15:00 2021-07-20 124165.0 124385.0 123990.0 124220.0 124523.141891 124465.739232
2021-07-20 10:30:00 2021-07-20 124220.0 124285.0 123905.0 124115.0 124523.141891 124465.739232
--- barras ao redor da SAÍDA (2021-07-23 14:00:00) ---
                 ts       date     open     high      low    close       h1_ema9      h1_ema21
2021-07-23 12:30:00 2021-07-23 125580.0 125640.0 125375.0 125450.0 126238.420934 126067.131227
2021-07-23 12:45:00 2021-07-23 125450.0 125520.0 125365.0 125485.0 126087.736748 126014.210206
2021-07-23 13:00:00 2021-07-23 125485.0 125610.0 125450.0 125545.0 126087.736748 126014.210206
2021-07-23 13:15:00 2021-07-23 125540.0 125555.0 125410.0 125495.0 126087.736748 126014.210206
2021-07-23 13:30:00 2021-07-23 125500.0 125515.0 125325.0 125340.0 126087.736748 126014.210206
2021-07-23 13:45:00 2021-07-23 125340.0 125355.0 125010.0 125045.0 125879.189398 125926.100187
2021-07-23 14:00:00 2021-07-23 125050.0 125210.0 124885.0 125085.0 125879.189398 125926.100187
2021-07-23 14:15:00 2021-07-23 125090.0 125330.0 125080.0 125280.0 125879.189398 125926.100187
2021-07-23 14:30:00 2021-07-23 125285.0 125435.0 125230.0 125405.0 125879.189398 125926.100187
2021-07-23 14:45:00 2021-07-23 125405.0 125430.0 125110.0 125295.0 125762.351518 125868.727443
2021-07-23 15:00:00 2021-07-23 125295.0 125385.0 125245.0 125320.0 125762.351518 125868.727443
2021-07-23 15:15:00 2021-07-23 125320.0 125330.0 125100.0 125145.0 125762.351518 125868.727443
2021-07-23 15:30:00 2021-07-23 125150.0 125295.0 124710.0 124910.0 125762.351518 125868.727443

[WIN_B_B3_h1_ema9x21] Trade 'saida forcada por rolagem': dir=-1 entry_ts=2021-08-10 17:00:00 entry_px=122265.0 exit_ts=2021-08-11 17:45:00 exit_px=122130.0 reason=rollover_forced pnl_gross_bps=11.04 hold_bars=39
--- barras ao redor da ENTRADA (2021-08-10 17:00:00) ---
                 ts       date     open     high      low    close       h1_ema9      h1_ema21
2021-08-10 15:30:00 2021-08-10 122570.0 122655.0 122420.0 122635.0 123074.805056 122986.176315
2021-08-10 15:45:00 2021-08-10 122635.0 122675.0 122375.0 122605.0 122980.844045 122951.523923
2021-08-10 16:00:00 2021-08-10 122610.0 122610.0 122410.0 122560.0 122980.844045 122951.523923
2021-08-10 16:15:00 2021-08-10 122560.0 122680.0 122470.0 122500.0 122980.844045 122951.523923
2021-08-10 16:30:00 2021-08-10 122500.0 122565.0 122265.0 122290.0 122980.844045 122951.523923
2021-08-10 16:45:00 2021-08-10 122285.0 122360.0 122175.0 122265.0 122837.675236 122889.112657
2021-08-10 17:00:00 2021-08-10 122265.0 122445.0 122230.0 122420.0 122837.675236 122889.112657
2021-08-10 17:15:00 2021-08-10 122425.0 122425.0 122150.0 122210.0 122837.675236 122889.112657
2021-08-10 17:30:00 2021-08-10 122205.0 122400.0 122205.0 122270.0 122837.675236 122889.112657
2021-08-10 17:45:00 2021-08-10 122270.0 122500.0 122215.0 122450.0 122760.140189 122849.193325
--- barras ao redor da SAÍDA (2021-08-11 17:45:00) ---
                 ts       date     open     high      low    close       h1_ema9      h1_ema21
2021-08-11 16:15:00 2021-08-11 122530.0 122560.0 122180.0 122360.0 122422.718168 122557.058977
2021-08-11 16:30:00 2021-08-11 122360.0 122450.0 122285.0 122360.0 122422.718168 122557.058977
2021-08-11 16:45:00 2021-08-11 122360.0 122395.0 122105.0 122225.0 122383.174534 122526.871798
2021-08-11 17:00:00 2021-08-11 122220.0 122225.0 122030.0 122070.0 122383.174534 122526.871798
2021-08-11 17:15:00 2021-08-11 122070.0 122085.0 121930.0 122005.0 122383.174534 122526.871798
2021-08-11 17:30:00 2021-08-11 122000.0 122180.0 121980.0 122110.0 122383.174534 122526.871798
2021-08-11 17:45:00 2021-08-11 122115.0 122210.0 122015.0 122130.0 122332.539627 122490.792543
```

## Sanity checks gerais

- Configs com 0 trades no discovery: 0 
- Validações Arm A (cruzar dia) e Arm B (intersectar janela de rolo) e Arm C (>1 trade/noite) rodadas inline no loop principal — ver stdout da execução para [WARN]/[ASSERT-FAIL].

## Ressalvas / decisões de implementação

- **D1 term_ts**: definido como o term_ts da ÚLTIMA barra M15 de cada pregão (não como open+1440min). Isso faz com que (i) qualquer barra M15 intraday de um dia D nunca veja o D1 do próprio dia D — sempre D-1, satisfazendo literalmente a regra do pré-registro para os gates de Arm A (G3) e para o sinal D1 de Arm B (B6) durante o dia — e (ii) a avaliação feita EXATAMENTE na última barra M15 do pregão (Arm C, estado E2) já vê o D1 do PRÓPRIO dia, porque nesse instante exato o pregão (e portanto a barra D1) acabou de fechar — não é lookahead, é o mesmo evento temporal. Isto foi uma decisão de design para reconciliar a regra 'nunca D1 do dia corrente' (explícita para Arm A intraday) com a definição de Arm C ('estado no fechamento... close vs EMA20 D1'), que naturalmente inclui o fechamento do próprio dia. Ambos os comportamentos emergem do MESMO mecanismo de merge_asof por term_ts, sem branch especial por arm.
- **H1 term_ts nominal**: sempre open+60min, mesmo quando a última barra H1 do pregão é parcial (pregão não termina em hora cheia — ex.: última M15 abre 18:15, termina 18:30; última H1 abre 18:00, term_ts nominal=19:00 > 18:30). Consequência mecânica: a última barra H1 do dia NUNCA é usada para gatear/avaliar as últimas barras M15 do mesmo dia (inclusive Arm C E1/E3) — o alinhamento cai para a penúltima barra H1 (aberta 17:00, term_ts=18:00). Isso é conservador (nunca usa dado ainda não fechado) mas significa que Arm C E1/E3 sistematicamente ignora a última hora de dados H1 do pregão.
- **T4 (Bollinger breakout M15)**: 'cross oposto do trigger' interpretado literalmente como o evento de rompimento na banda OPOSTA (cross_down(close,bb_lo) encerra um long aberto por cross_up(close,bb_up), e vice-versa) — não como reversão à média (sma20). Isso é bem mais raro que EOD, então espera-se que a maioria dos trades T4 feche por EOD.
- **Arm B, cold start**: a primeiríssima vez que um sinal fica definido (fim do warm-up de EMA/MACD) é tratada como um 'flip' a partir de posição zero, executado no open de t+1 — não há estado anterior conhecido para entrar no open da própria barra (evita lookahead), custando um atraso de 1 barra apenas nesse evento único por config.
- **Arm B, dias de janela de rolo**: entradas/reversões INTRADAY continuam permitidas em dias que pertencem à janela de rolo (o pré-registro só proíbe segurar OVERNIGHT nesses dias) — a posição é forçada a fechar no close de qualquer dia bloqueado (`blocked_overnight`), podendo reabrir na manhã seguinte se ainda dentro/ao redor da janela, e fechar de novo à noite, se aplicável. Isso pode gerar sequências de trades intraday curtos durante janelas multi-dia (WIN: 5 pregões) — comportamento intencional, documentado, não filtro pós-hoc.
- **Arm C, preço de entrada**: literal ao pré-registro, a entrada usa o CLOSE da última barra M15 do pregão (não o open da barra seguinte) — mais agressivo que as demais mecânicas desta campanha, que sempre adiam execução para o open da barra seguinte. Risco de slippage de leilão de fechamento não modelado além do custo de 6 bps, conforme já anotado no pré-registro.
- **hold_bars do Arm C**: calculado como (exit_ts - entry_ts) / 15min (aproximação por tempo decorrido), não por contagem de barras via loop — Arm C não itera bar-a-bar (é vetorizado a partir de um agregado diário), então não há um índice de barra nativo para diferença exata; a aproximação é exata em minutos de qualquer forma (15min = 1 barra M15 nominal), só não conta barras 'puladas' se houver gap de dados intraday.
- **Gate 7 (replicação estrutural)**: comparação feita por config_id com o prefixo do asset removido (ex.: 'A_G1_T1', 'B_B3_h1_ema9x21', 'C_E1_h1_ema21x50_X1_next_open'), cruzando WIN<->WDO. Passa se o sinal do expectancy_net6 discovery no outro ativo não é invertido (ou se o próprio config já não é positivo, caso em que gate7 é irrelevante — reportado como False para não inflar contagem).
- **Split por entry_ts (todos os arms)**: DISCOVERY = entry_ts < 2024-01-01, CONFIRM = 2024-01-01 <= entry_ts < 2025-01-01. Para o Arm B isso significa que um trade aberto em 2023 e fechado em 2024 conta inteiro no DISCOVERY (mesma convenção usada nos outros arms e na campanha anterior) — comportamento esperado e pré-registrado explicitamente.
- **Snap de vencimento para dia de pregão real**: a quarta-feira mais próxima do dia 15 (WIN) é calculada em cima do calendário civil e depois ajustada ('snap') para o pregão real mais próximo presente na série D1, caso a quarta calculada seja feriado/não-pregão. Em empate de distância, prefere a data mais cedo (critério de desempate arbitrário, raramente acionado).