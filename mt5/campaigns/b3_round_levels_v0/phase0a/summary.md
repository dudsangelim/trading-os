# b3_round_levels_v0 — Phase 0A — Summary

Premise check descritivo (sem entry/exit/PnL) para niveis redondos em WIN/WDO. Hipoteses simetricas H_bounce (T1) e H_cascade (T2) no intraday; T3 magnetismo; braco swing D1 com T4 barreira, T5 cruzamento, T6 rejeicao. Toda estatistica medida CONTRA GRADE PLACEBO (meia-grade). Bootstrap 1000x, seed fixa (20260722). DISCOVERY <2024-01-01 / CONFIRM 2024 / OOS 2025+ filtrado no load, sagrado.

## Metodologia efetiva (thresholds em ATR20d)

- Banda de toque / magnetismo (T1/T3): 0.05×ATR20d
- Distancia minima de aproximacao (T1 'vem de longe'): 0.3×ATR20d
- Reversao que confirma 'segura' (T1): 0.15×ATR20d
- Confirmacao de quebra/cruzamento (T1 cross-out, T2): 0.1×ATR20d
- T4 barreira: fracao de closes com distancia <= 0.1× meia-grade (ver ressalva sobre normalizacao) + KS-test na distancia normalizada por ATR20d
- ATR20d: media movel de 20 dias do True Range diario, com shift(1) — o dia D usa ATR calculado com dados ate D-1 (sem lookahead).

## Contagem de eventos por celula (discovery)

### T1_bounce

| asset | grid | tf | n_round | n_placebo |
|---|---|---|---|---|
| WIN | g500 | M5 | 620 | 637 |
| WIN | g1000 | M5 | 296 | 324 |
| WIN | g500 | M15 | 1310 | 1304 |
| WIN | g1000 | M15 | 646 | 664 |
| WIN | g500 | H1 | 725 | 726 |
| WIN | g1000 | H1 | 356 | 369 |
| WDO | g5 | M5 | 1736 | 1759 |
| WDO | g10 | M5 | 859 | 877 |
| WDO | g5 | M15 | 4205 | 4189 |
| WDO | g10 | M15 | 2110 | 2095 |
| WDO | g5 | H1 | 2121 | 2099 |
| WDO | g10 | H1 | 1071 | 1050 |

### T2_cascade

| asset | grid | tf | n_round | n_placebo |
|---|---|---|---|---|
| WIN | g500 | M5 | 564 | 573 |
| WIN | g1000 | M5 | 287 | 277 |
| WIN | g500 | M15 | 1330 | 1324 |
| WIN | g1000 | M15 | 667 | 663 |
| WIN | g500 | H1 | 1054 | 1042 |
| WIN | g1000 | H1 | 534 | 520 |
| WDO | g5 | M5 | 1559 | 1552 |
| WDO | g10 | M5 | 780 | 779 |
| WDO | g5 | M15 | 4125 | 4122 |
| WDO | g10 | M15 | 2053 | 2072 |
| WDO | g5 | H1 | 2981 | 2967 |
| WDO | g10 | H1 | 1478 | 1503 |

### T5_cross

| asset | grid | tf | n_round | n_placebo |
|---|---|---|---|---|
| WIN | g5000 | D1 | 119 | 98 |
| WDO | g50 | D1 | 247 | 251 |

### T6_reject

| asset | grid | tf | n_round | n_placebo |
|---|---|---|---|---|
| WIN | g5000 | D1 | 86 | 95 |
| WDO | g50 | D1 | 294 | 294 |

### T3_magnet (n_bars discovery)

| asset | grid | tf | n_bars | n_days | near_round | near_placebo |
|---|---|---|---|---|---|---|
| WIN | g500 | M5 | 24971 | 229 | 10056 | 10169 |
| WIN | g1000 | M5 | 24971 | 229 | 5109 | 4947 |
| WIN | g500 | M15 | 19018 | 519 | 8465 | 8520 |
| WIN | g1000 | M15 | 19018 | 519 | 4315 | 4150 |
| WIN | g500 | H1 | 4846 | 519 | 2161 | 2182 |
| WIN | g1000 | H1 | 4846 | 519 | 1076 | 1085 |
| WDO | g5 | M5 | 22030 | 201 | 21838 | 21844 |
| WDO | g10 | M5 | 22030 | 201 | 13846 | 13728 |
| WDO | g5 | M15 | 17494 | 477 | 17428 | 17435 |
| WDO | g10 | M15 | 17494 | 477 | 13417 | 13481 |
| WDO | g5 | H1 | 4461 | 477 | 4442 | 4442 |
| WDO | g10 | H1 | 4461 | 477 | 3436 | 3393 |

### T4_barrier (n dias discovery)

| asset | grid | n_dias |
|---|---|---|
| WIN | g5000 | 519 |
| WDO | g50 | 477 |

## Tabelas principais redondo vs placebo (todas as celulas, discovery + confirm)

### T1_bounce (efeito = forward horizonte 'mid' assinado, bps [30min M5/M15, 120min H1]; direcao = lado de aproximacao)

| asset | grid | tf | split | n_round | n_placebo | efeito_round | efeito_placebo | diff | ci_lo | ci_hi | primary_horizon_min | hold_rate_round | hold_rate_placebo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WIN | g500 | M5 | discovery | 620 | 637 | 0.744 | 1.071 | -0.326 | -3.720 | 3.334 | 30 | 0.432 | 0.449 |
| WIN | g500 | M5 | confirm | 508 | 495 | -0.197 | 1.787 | -1.984 | -4.628 | 0.529 | 30 | 0.435 | 0.448 |
| WIN | g1000 | M5 | discovery | 296 | 324 | 0.645 | 0.835 | -0.189 | -4.685 | 4.713 | 30 | 0.412 | 0.451 |
| WIN | g1000 | M5 | confirm | 261 | 247 | -0.280 | -0.109 | -0.171 | -3.406 | 3.497 | 30 | 0.456 | 0.413 |
| WIN | g500 | M15 | discovery | 1310 | 1304 | -0.560 | -0.630 | 0.070 | -2.507 | 2.553 | 30 | 0.382 | 0.395 |
| WIN | g500 | M15 | confirm | 410 | 400 | 0.084 | 0.798 | -0.713 | -3.394 | 2.051 | 30 | 0.395 | 0.378 |
| WIN | g1000 | M15 | discovery | 646 | 664 | 0.294 | -1.392 | 1.685 | -1.644 | 4.913 | 30 | 0.372 | 0.392 |
| WIN | g1000 | M15 | confirm | 207 | 203 | 0.786 | -0.631 | 1.417 | -2.299 | 5.032 | 30 | 0.449 | 0.340 |
| WIN | g500 | H1 | discovery | 725 | 726 | -1.344 | -2.570 | 1.226 | -3.349 | 6.124 | 120 | 0.276 | 0.282 |
| WIN | g500 | H1 | confirm | 233 | 227 | -1.398 | 1.176 | -2.573 | -7.660 | 2.708 | 120 | 0.296 | 0.278 |
| WIN | g1000 | H1 | discovery | 356 | 369 | -1.999 | -0.712 | -1.287 | -8.224 | 5.430 | 120 | 0.275 | 0.276 |
| WIN | g1000 | H1 | confirm | 118 | 115 | 0.257 | -3.095 | 3.352 | -4.275 | 10.357 | 120 | 0.314 | 0.278 |
| WDO | g5 | M5 | discovery | 1736 | 1759 | 0.591 | 0.956 | -0.365 | -1.648 | 1.023 | 30 | 0.442 | 0.439 |
| WDO | g5 | M5 | confirm | 1845 | 1831 | -0.601 | -0.486 | -0.115 | -1.553 | 1.137 | 30 | 0.449 | 0.434 |
| WDO | g10 | M5 | discovery | 859 | 877 | 0.582 | 0.600 | -0.018 | -1.814 | 1.914 | 30 | 0.431 | 0.453 |
| WDO | g10 | M5 | confirm | 915 | 930 | -0.723 | -0.480 | -0.242 | -2.034 | 1.661 | 30 | 0.443 | 0.455 |
| WDO | g5 | M15 | discovery | 4205 | 4189 | 0.203 | 0.417 | -0.214 | -1.114 | 0.727 | 30 | 0.403 | 0.402 |
| WDO | g5 | M15 | confirm | 1462 | 1442 | -0.518 | 0.069 | -0.587 | -1.911 | 0.696 | 30 | 0.394 | 0.399 |
| WDO | g10 | M15 | discovery | 2110 | 2095 | 0.073 | 0.334 | -0.260 | -1.613 | 1.062 | 30 | 0.394 | 0.412 |
| WDO | g10 | M15 | confirm | 727 | 735 | -0.443 | -0.592 | 0.149 | -1.741 | 2.220 | 30 | 0.387 | 0.401 |
| WDO | g5 | H1 | discovery | 2121 | 2099 | 1.515 | 1.301 | 0.214 | -1.701 | 2.178 | 120 | 0.299 | 0.305 |
| WDO | g5 | H1 | confirm | 762 | 732 | -0.958 | -0.422 | -0.536 | -3.570 | 2.116 | 120 | 0.293 | 0.280 |
| WDO | g10 | H1 | discovery | 1071 | 1050 | 1.538 | 1.491 | 0.046 | -2.685 | 2.737 | 120 | 0.308 | 0.290 |
| WDO | g10 | H1 | confirm | 369 | 393 | 0.226 | -2.069 | 2.295 | -1.994 | 6.534 | 120 | 0.287 | 0.298 |

### T2_cascade (efeito = forward horizonte 'mid' assinado, bps [30min M5/M15, 120min H1]; direcao = lado da quebra)

| asset | grid | tf | split | n_round | n_placebo | efeito_round | efeito_placebo | diff | ci_lo | ci_hi | primary_horizon_min | range_v_atr_round | range_v_atr_placebo | range_v_atr_diff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WIN | g500 | M5 | discovery | 564 | 573 | -2.631 | -1.684 | -0.947 | -4.440 | 3.040 | 30 | 0.269 | 0.263 | 0.005 |
| WIN | g500 | M5 | confirm | 450 | 447 | -0.690 | 0.229 | -0.919 | -3.784 | 1.993 | 30 | 0.272 | 0.259 | 0.013 |
| WIN | g1000 | M5 | discovery | 287 | 277 | -4.939 | -0.239 | -4.700 | -10.062 | 0.957 | 30 | 0.265 | 0.272 | -0.007 |
| WIN | g1000 | M5 | confirm | 224 | 226 | -0.793 | -0.587 | -0.206 | -4.586 | 3.969 | 30 | 0.272 | 0.273 | -0.001 |
| WIN | g500 | M15 | discovery | 1330 | 1324 | -0.294 | 0.507 | -0.800 | -3.204 | 1.618 | 30 | 0.321 | 0.325 | -0.004 |
| WIN | g500 | M15 | confirm | 404 | 416 | -0.953 | -0.438 | -0.514 | -3.092 | 2.443 | 30 | 0.338 | 0.337 | 0.001 |
| WIN | g1000 | M15 | discovery | 667 | 663 | -0.032 | -0.557 | 0.525 | -3.141 | 4.130 | 30 | 0.323 | 0.319 | 0.004 |
| WIN | g1000 | M15 | confirm | 199 | 205 | -1.464 | -0.457 | -1.007 | -5.299 | 3.194 | 30 | 0.349 | 0.327 | 0.022 |
| WIN | g500 | H1 | discovery | 1054 | 1042 | 1.659 | 1.623 | 0.037 | -4.714 | 4.157 | 120 | 0.552 | 0.556 | -0.004 |
| WIN | g500 | H1 | confirm | 300 | 304 | -1.760 | 1.026 | -2.786 | -7.595 | 1.715 | 120 | 0.531 | 0.536 | -0.005 |
| WIN | g1000 | H1 | discovery | 534 | 520 | -0.332 | 3.705 | -4.037 | -10.017 | 2.020 | 120 | 0.553 | 0.551 | 0.003 |
| WIN | g1000 | H1 | confirm | 143 | 157 | -1.838 | -1.689 | -0.149 | -6.454 | 6.180 | 120 | 0.537 | 0.525 | 0.013 |
| WDO | g5 | M5 | discovery | 1559 | 1552 | -1.257 | -1.445 | 0.188 | -1.246 | 1.580 | 30 | 0.274 | 0.275 | -0.001 |
| WDO | g5 | M5 | confirm | 1682 | 1674 | 0.217 | 0.167 | 0.049 | -1.276 | 1.434 | 30 | 0.290 | 0.293 | -0.003 |
| WDO | g10 | M5 | discovery | 780 | 779 | -0.951 | -1.563 | 0.613 | -1.375 | 2.538 | 30 | 0.273 | 0.275 | -0.002 |
| WDO | g10 | M5 | confirm | 841 | 841 | 0.036 | 0.397 | -0.360 | -2.319 | 1.611 | 30 | 0.289 | 0.291 | -0.002 |
| WDO | g5 | M15 | discovery | 4125 | 4122 | -0.730 | -0.851 | 0.121 | -0.996 | 1.131 | 30 | 0.331 | 0.330 | 0.001 |
| WDO | g5 | M15 | confirm | 1477 | 1477 | 0.440 | 0.649 | -0.209 | -1.526 | 1.099 | 30 | 0.354 | 0.354 | -0.001 |
| WDO | g10 | M15 | discovery | 2053 | 2072 | -1.024 | -0.438 | -0.586 | -2.055 | 0.872 | 30 | 0.332 | 0.329 | 0.003 |
| WDO | g10 | M15 | confirm | 743 | 734 | 0.500 | 0.380 | 0.121 | -1.740 | 2.165 | 30 | 0.350 | 0.357 | -0.007 |
| WDO | g5 | H1 | discovery | 2981 | 2967 | -1.167 | -1.119 | -0.048 | -1.873 | 1.770 | 120 | 0.532 | 0.532 | -0.000 |
| WDO | g5 | H1 | confirm | 1063 | 1058 | 1.579 | 1.867 | -0.288 | -3.079 | 2.393 | 120 | 0.554 | 0.557 | -0.003 |
| WDO | g10 | H1 | discovery | 1478 | 1503 | -1.048 | -1.284 | 0.236 | -2.307 | 2.796 | 120 | 0.534 | 0.530 | 0.004 |
| WDO | g10 | H1 | confirm | 536 | 527 | 1.456 | 1.704 | -0.247 | -3.941 | 3.870 | 120 | 0.557 | 0.552 | 0.005 |

### T3_magnet (efeito = fracao de barras a <=0.05xATR do nivel)

| asset | grid | tf | split | n_round | n_placebo | efeito_round | efeito_placebo | diff | ci_lo | ci_hi |
|---|---|---|---|---|---|---|---|---|---|---|
| WIN | g500 | M5 | discovery | 10056 | 10169 | 0.403 | 0.407 | -0.005 | -0.026 | 0.020 |
| WIN | g500 | M5 | confirm | 8172 | 8266 | 0.327 | 0.331 | -0.004 | -0.029 | 0.021 |
| WIN | g1000 | M5 | discovery | 5109 | 4947 | 0.205 | 0.198 | 0.006 | -0.021 | 0.032 |
| WIN | g1000 | M5 | confirm | 4089 | 4083 | 0.164 | 0.164 | 0.000 | -0.024 | 0.025 |
| WIN | g500 | M15 | discovery | 8465 | 8520 | 0.445 | 0.448 | -0.003 | -0.020 | 0.014 |
| WIN | g500 | M15 | confirm | 2741 | 2785 | 0.326 | 0.332 | -0.005 | -0.032 | 0.020 |
| WIN | g1000 | M15 | discovery | 4315 | 4150 | 0.227 | 0.218 | 0.009 | -0.009 | 0.027 |
| WIN | g1000 | M15 | confirm | 1385 | 1356 | 0.165 | 0.161 | 0.003 | -0.022 | 0.030 |
| WIN | g500 | H1 | discovery | 2161 | 2182 | 0.446 | 0.450 | -0.004 | -0.029 | 0.023 |
| WIN | g500 | H1 | confirm | 706 | 744 | 0.319 | 0.337 | -0.017 | -0.052 | 0.019 |
| WIN | g1000 | H1 | discovery | 1076 | 1085 | 0.222 | 0.224 | -0.002 | -0.024 | 0.018 |
| WIN | g1000 | H1 | confirm | 343 | 363 | 0.155 | 0.164 | -0.009 | -0.038 | 0.021 |
| WDO | g5 | M5 | discovery | 21838 | 21844 | 0.991 | 0.992 | -0.000 | -0.002 | 0.001 |
| WDO | g5 | M5 | confirm | 22239 | 22228 | 0.949 | 0.948 | 0.000 | -0.005 | 0.006 |
| WDO | g10 | M5 | discovery | 13846 | 13728 | 0.629 | 0.623 | 0.005 | -0.012 | 0.021 |
| WDO | g10 | M5 | confirm | 14404 | 14957 | 0.615 | 0.638 | -0.024 | -0.043 | -0.005 |
| WDO | g5 | M15 | discovery | 17428 | 17435 | 0.996 | 0.997 | -0.000 | -0.002 | 0.001 |
| WDO | g5 | M15 | confirm | 7429 | 7408 | 0.951 | 0.948 | 0.003 | -0.004 | 0.009 |
| WDO | g10 | M15 | discovery | 13417 | 13481 | 0.767 | 0.771 | -0.004 | -0.014 | 0.007 |
| WDO | g10 | M15 | confirm | 4823 | 4977 | 0.617 | 0.637 | -0.020 | -0.041 | 0.001 |
| WDO | g5 | H1 | discovery | 4442 | 4442 | 0.996 | 0.996 | 0.000 | -0.003 | 0.003 |
| WDO | g5 | H1 | confirm | 1950 | 1953 | 0.948 | 0.950 | -0.001 | -0.016 | 0.012 |
| WDO | g10 | H1 | discovery | 3436 | 3393 | 0.770 | 0.761 | 0.010 | -0.009 | 0.029 |
| WDO | g10 | H1 | confirm | 1270 | 1294 | 0.618 | 0.629 | -0.012 | -0.049 | 0.026 |

### T4_barrier (efeito = fracao de closes a <=0.10x meia-grade; ks_stat/ks_pvalue na distancia/ATR)

| asset | grid | tf | split | n_round | n_placebo | efeito_round | efeito_placebo | diff | ci_lo | ci_hi | ks_stat | ks_pvalue |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WIN | g5000 | D1 | discovery | 519 | 519 | 0.094 | 0.104 | -0.010 | -0.048 | 0.029 | 0.067 | 0.189 |
| WIN | g5000 | D1 | confirm | 221 | 221 | 0.050 | 0.154 | -0.104 | -0.163 | -0.050 | 0.190 | 0.001 |
| WDO | g50 | D1 | discovery | 477 | 477 | 0.113 | 0.132 | -0.019 | -0.063 | 0.025 | 0.088 | 0.049 |
| WDO | g50 | D1 | confirm | 206 | 206 | 0.112 | 0.102 | 0.010 | -0.053 | 0.073 | 0.073 | 0.647 |

### T5_cross (efeito = drift D+1 assinado, bps; ver fwd3/fwd5 no CSV p/ acumulado)

| asset | grid | tf | split | n_round | n_placebo | efeito_round | efeito_placebo | diff | ci_lo | ci_hi | n_discarded_rollover_round |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WIN | g5000 | D1 | discovery | 119 | 98 | -58.110 | -25.120 | -32.990 | -111.350 | 39.637 | 19 |
| WIN | g5000 | D1 | confirm | 24 | 37 | 10.761 | -36.781 | 47.542 | -37.244 | 125.383 | 19 |
| WDO | g50 | D1 | discovery | 247 | 251 | -16.008 | -17.045 | 1.037 | -37.821 | 40.776 | 143 |
| WDO | g50 | D1 | confirm | 92 | 98 | 2.010 | -5.713 | 7.722 | -43.601 | 58.382 | 143 |

### T6_reject (efeito = reversao D+1 assinada, bps; ver fwd3 no CSV p/ D+1..D+3 acumulado)

| asset | grid | tf | split | n_round | n_placebo | efeito_round | efeito_placebo | diff | ci_lo | ci_hi | n_discarded_rollover_round |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WIN | g5000 | D1 | discovery | 86 | 95 | 49.317 | 18.841 | 30.476 | -27.823 | 95.338 | 19 |
| WIN | g5000 | D1 | confirm | 20 | 32 | -16.943 | 14.189 | -31.132 | -87.715 | 29.146 | 19 |
| WDO | g50 | D1 | discovery | 294 | 294 | 7.678 | -0.045 | 7.723 | -18.713 | 35.283 | 149 |
| WDO | g50 | D1 | confirm | 117 | 101 | -0.658 | 1.919 | -2.576 | -39.663 | 32.747 | 149 |

## Resultado mecanico dos gates por celula

Gate 1 (efeito liquido >= 6bps intraday / 25bps swing, IC exclui zero no discovery, mesmo sinal no confirm) so se aplica a testes com metrica 'forward' (T1,T2,T5,T6) — marcado N/A para T3/T4. Gate 2 = replicacao WIN<->WDO na grade equivalente, mesmo sinal. Gate 3 = n minimo no discovery (150 intraday / 60 swing). Gate 4 = coerencia — flag se T1 e T2 (ou T6 e T5) derem AMBAS positivas na mesma celula.

| test | asset | grid | tf | n_round | gate1 | gate2 | gate3 | gate4_coherent |
|---|---|---|---|---|---|---|---|---|
| T1_bounce | WIN | g500 | M5 | 620 | False | True | True | True |
| T1_bounce | WIN | g1000 | M5 | 296 | False | True | True | True |
| T1_bounce | WIN | g500 | M15 | 1310 | False | False | True | True |
| T1_bounce | WIN | g1000 | M15 | 646 | False | False | True | False |
| T1_bounce | WIN | g500 | H1 | 725 | False | True | True | False |
| T1_bounce | WIN | g1000 | H1 | 356 | False | False | True | True |
| T1_bounce | WDO | g5 | M5 | 1736 | False | True | True | True |
| T1_bounce | WDO | g10 | M5 | 859 | False | True | True | True |
| T1_bounce | WDO | g5 | M15 | 4205 | False | False | True | True |
| T1_bounce | WDO | g10 | M15 | 2110 | False | False | True | True |
| T1_bounce | WDO | g5 | H1 | 2121 | False | True | True | True |
| T1_bounce | WDO | g10 | H1 | 1071 | False | False | True | False |
| T2_cascade | WIN | g500 | M5 | 564 | False | False | True | True |
| T2_cascade | WIN | g1000 | M5 | 287 | False | False | True | True |
| T2_cascade | WIN | g500 | M15 | 1330 | False | False | True | True |
| T2_cascade | WIN | g1000 | M15 | 667 | False | False | True | False |
| T2_cascade | WIN | g500 | H1 | 1054 | False | False | True | False |
| T2_cascade | WIN | g1000 | H1 | 534 | False | False | True | True |
| T2_cascade | WDO | g5 | M5 | 1559 | False | False | True | True |
| T2_cascade | WDO | g10 | M5 | 780 | False | False | True | True |
| T2_cascade | WDO | g5 | M15 | 4125 | False | False | True | True |
| T2_cascade | WDO | g10 | M15 | 2053 | False | False | True | True |
| T2_cascade | WDO | g5 | H1 | 2981 | False | False | True | True |
| T2_cascade | WDO | g10 | H1 | 1478 | False | False | True | False |
| T3_magnet | WIN | g500 | M5 | 10056 | N/A | True | True | True |
| T3_magnet | WIN | g1000 | M5 | 5109 | N/A | True | True | True |
| T3_magnet | WIN | g500 | M15 | 8465 | N/A | True | True | True |
| T3_magnet | WIN | g1000 | M15 | 4315 | N/A | False | True | True |
| T3_magnet | WIN | g500 | H1 | 2161 | N/A | False | True | True |
| T3_magnet | WIN | g1000 | H1 | 1076 | N/A | False | True | True |
| T3_magnet | WDO | g5 | M5 | 21838 | N/A | True | True | True |
| T3_magnet | WDO | g10 | M5 | 13846 | N/A | True | True | True |
| T3_magnet | WDO | g5 | M15 | 17428 | N/A | True | True | True |
| T3_magnet | WDO | g10 | M15 | 13417 | N/A | False | True | True |
| T3_magnet | WDO | g5 | H1 | 4442 | N/A | False | True | True |
| T3_magnet | WDO | g10 | H1 | 3436 | N/A | False | True | True |
| T4_barrier | WIN | g5000 | D1 | 519 | N/A | True | True | True |
| T4_barrier | WDO | g50 | D1 | 477 | N/A | True | True | True |
| T5_cross | WIN | g5000 | D1 | 119 | False | False | True | True |
| T5_cross | WDO | g50 | D1 | 247 | False | False | True | False |
| T6_reject | WIN | g5000 | D1 | 86 | False | True | True | True |
| T6_reject | WDO | g50 | D1 | 294 | False | True | True | False |

**Celulas com gate1=True: 0. Celulas passando gate1+gate2+gate3+gate4 simultaneamente: 0.**

## Breakdown por ano das celulas que passaram algum gate

Nenhuma celula passou gate1+gate2+gate3+gate4 simultaneamente no discovery — sem breakdown por ano a reportar (regra: nao adicionar analise pos-hoc para 'salvar' celula reprovada).

## Sanity — rolagem por calendario

- WIN: 21 janelas de rolo (105 dias de pregao bloqueados p/ eventos), 3 pulada(s) por falta de historico, 20 dias iniciais sem ATR20d (warm-up de 20 dias).
- WDO: 41 janelas de rolo (164 dias de pregao bloqueados p/ eventos), 1 pulada(s) por falta de historico, 20 dias iniciais sem ATR20d (warm-up de 20 dias).

## Ressalvas

- **M5 truncado**: os parquets M5 de WIN e WDO tem teto de 100.000 linhas do terminal MT5 e comecam apenas em 2022-12 (WIN) / 2022-12 (WDO), nao em 2021-07 como M15/H1/D1. Isso significa que o braco intraday principal (M5) tem DISCOVERY efetivamente reduzido a ~2022-12..2023-12 (~1 ano), nao os ~2.5 anos nominais do periodo <2024. M15/H1 (robustez) cobrem o periodo completo desde 2021-07 e sao a fonte mais confiavel de n_trades para os anos 2021-2022.
- **T4 'distancia normalizada'**: o pre-registro nao especifica explicitamente a unidade de normalizacao para T4 (a regra geral do briefing pede ATR20d para 'todos os thresholds', mas a instrucao especifica de T4 pede 'diferenca de fracao a <=0.1 DA MEIA-GRADE'). Decisao: reportar AMBAS as metricas — (a) fracao de closes com distancia <= 0.10x meia-grade (efeito_round/efeito_placebo/diff/CI no CSV, conforme pedido explicito para T4) e (b) distancia media normalizada por ATR20d + KS-test nessa distribuicao (ks_stat/ks_pvalue), para nao violar a regra geral de nunca usar pontos fixos. As duas sao reportadas lado a lado, sem esconder nenhuma.
- **T1, resolucao 'segura' vs 'atravessa'**: quando nem a reversao de 0.15xATR nem o cross-out de 0.10xATR ocorrem ate o fim do pregao (M5/M15) apos o toque, o evento fica com outcome='unresolved' (nao conta nem como hold nem como cross na taxa hold_rate — afeta o denominador). Nao ha timeout explicito no pre-registro; a resolucao e limitada ao proprio pregao (sem overnight), decisao de implementacao documentada aqui.
- **T1/T2, horizontes de forward adaptados por TF**: os horizontes 15/30/60min do pre-registro sao degenerados em H1 (barra=60min faz 15/30min colapsarem no proprio evento, retorno sempre 0 -- bug detectado e corrigido durante a execucao desta fase). Solucao: horizontes reescalados por TF em multiplos da propria barra -- M5/M15 mantêm 15/30/60min (short/mid/long); H1 usa 60/120/240min (1/2/4 barras). O horizonte PRIMARIO ('mid', usado em efeito_round/efeito_placebo/diff/CI) e sempre o do meio da lista -- 30min em M5/M15, 120min em H1 -- reportado explicitamente na coluna primary_horizon_min de cada linha. T2 usa 4 horizontes (vshort/short/mid/long); a metrica de velocidade (range) usa o horizonte 'short' de cada TF (15min M5/M15, 60min H1), coluna range_horizon_min.
- **T2, direcao do evento e velocidade**: a metrica de velocidade (|range| pos-quebra, horizonte 'short' por TF) e reportada em pontos absolutos (range_v) e normalizada por ATR20d (range_v_atr, usada no efeito/CI das colunas range_v_atr_*) — NAO e signed, e uma comparacao de magnitude (redondo vs placebo), nao de direcao.
- **T5/T6, ausencia de threshold de confirmacao**: seguindo a redacao literal do pre-registro ('close diario cruza nivel' / 'high/low diario toca nivel... sem close atravessar'), T5 e T6 usam comparacao de SINAL pura (sem magnitude minima de cruzamento em ATR) — a unica excecao intencional a regra geral de escala por ATR20d, porque o proprio pre-registro nao define uma magnitude minima para estes dois testes especificos (diferente de T1/T2/T3, onde a magnitude em ATR e explicita). Isso pode incluir cruzamentos 'raspando' o nivel por pontos irrisorios como eventos validos; anotado como risco de ruido, nao corrigido pos-hoc para nao introduzir grau de liberdade nao pre-registrado.
- **T5/T6, multiplos niveis por dia**: se o movimento diario atravessar/tocar mais de um nivel da grade no mesmo dia (raro dado grid 5000/50 vs ATR tipico ~2000pts WIN / ~70pts WDO, mas possivel em dias de gap extremo), cada nivel gera um evento separado nesse dia — sem dedupe adicional alem do que o pre-registro pede explicitamente so para T1/T2 (intraday).
- **Janela de rolagem e braco swing**: dias na janela de rolo NUNCA geram evento (T4/T5/T6, mesmo T4 que nao e um 'evento' de crossing/touch — excluido por seguranca, pois o preco continuo (N) pode ter salto artificial de rolagem que contamina a distancia ao nivel). Para T5/T6, eventos cujo horizonte forward (D+1..D+5 ou D+1..D+3) atravessa QUALQUER dia de janela de rolo sao descartados e contados em n_discarded_rollover_round/placebo no CSV.
- **Gate 1 aplicado so a T1/T2/T5/T6**: T3 e T4 nao tem uma metrica 'forward' equivalente (sao estatisticas de posicao/tempo, nao de retorno subsequente) — o texto do gate1 do pre-registro se refere explicitamente a 'diferenca ... de forward', entao gate1 foi marcado N/A para essas duas celulas, mecanicamente, sem inferir um substituto nao pre-registrado.
- **W1 (robustez swing) nao implementado nesta rodada**: o pre-registro cita 'W1 como robustez' no braco swing mas nao ha parquet W1 disponivel no diretorio de dados; D1 (principal) foi executado integralmente. Anotado como lacuna, nao preenchida com resample ad-hoc para nao introduzir metodologia nao pre-registrada.
- **T3, saturacao em grades finas (WDO g5, parcialmente WDO g10)**: a fracao 'near' de T3 se aproxima de min(1, 2×banda/grid) quando o preco e aproximadamente uniforme dentro do intervalo entre niveis (banda=0.05×ATR20d). Para WDO g5 (meia-grade=2.5pts) com ATR tipico ~60-90pts, banda~3-4.5pts JA EXCEDE a meia-grade — TODO ponto do espaco de precos fica, por construcao, dentro da banda do nivel redondo mais proximo (e, simetricamente, do placebo mais proximo tambem), saturando near_round e near_placebo perto de 99-100% (ver t3_magnet.csv) e tornando a DIFERENCA estruturalmente proxima de zero independente de haver clustering real. WDO g10 (meia-grade=5pts, banda~3.5-4.5pts) fica na fronteira (~60-77%, nao totalmente saturado mas com pouca margem). WIN g500/g1000 e WDO g10 tem meia-grade suficientemente maior que a banda e nao sofrem desse problema. Isto NAO e um resultado de 'sem clustering' para WDO g5 — e uma limitacao de POTENCIA do desenho (banda >= meia-grade), analoga a um teste com n baixo: a celula T3 WDO g5 (M5/M15/H1) deve ser lida como 'sem potencia para detectar magnetismo', nao como evidencia contra H_bounce/magnetismo.
- **Sem alavancagem/custos**: fase 0A e puramente descritiva (toques, cruzamentos, drift, magnetismo) — nenhum PnL, fee ou slippage e modelado. Gate1 usa bps de PRECO, nao de PnL liquido.