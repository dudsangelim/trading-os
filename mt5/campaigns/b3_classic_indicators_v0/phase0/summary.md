# b3_classic_indicators_v0 — Fase 0 — Sumário

Total de configs (asset x tf x familia): 88

## Dias de rolagem flagados por (asset, tf)

- WIN_M5: 12 eventos flagados
- WIN_M15: 23 eventos flagados
- WDO_M5: 8 eventos flagados
- WDO_M15: 23 eventos flagados

## Contagem de configs que passam cada gate (DISCOVERY, informativo — não é veredito)

| Gate | Descrição | Passam / Total |
|---|---|---|
| 1 | PF_net6 >= 1.10 discovery | 2 / 88 |
| 2 | n_trades >= 200 discovery | 76 / 88 |
| 3 | Expectancy net6 > 0 discovery E confirm | 0 / 88 |
| 4 | PF_net6 >= 1.05 confirm | 1 / 88 |
| 5 | PF_net6 ex-top2 >= 1.0 discovery | 1 / 88 |
| 6 | Nenhum ano com PF_net6 < 0.85 discovery | 3 / 88 |

## Top-15 configs por PF_net6 no DISCOVERY (com valores de CONFIRM ao lado)

| config_id | asset | tf | n_disc | pf_net6_disc | exp_net6_disc | n_conf | pf_net6_conf | exp_net6_conf |
|---|---|---|---|---|---|---|---|---|
| WIN_M15_F6i_adxgt25_ema21x50 | WIN | M15 | 74 | 1.137 | 4.95 | 35 | 0.994 | -0.13 |
| WDO_M15_F1_ema21x100 | WDO | M15 | 209 | 1.110 | 2.27 | 95 | 0.710 | -5.35 |
| WIN_M15_F1_ema21x100 | WIN | M15 | 220 | 1.031 | 0.91 | 99 | 0.694 | -7.27 |
| WIN_M15_F1_ema9x100 | WIN | M15 | 320 | 0.962 | -1.19 | 126 | 0.881 | -2.50 |
| WIN_M15_F1_ema21x50 | WIN | M15 | 303 | 0.930 | -2.39 | 137 | 0.879 | -2.85 |
| WDO_M15_F6i_adxgt25_ema21x50 | WDO | M15 | 90 | 0.913 | -2.15 | 34 | 1.460 | 6.64 |
| WDO_M5_F6ii_adxlt20_rsi14_70_30 | WDO | M5 | 57 | 0.858 | -1.69 | 42 | 0.492 | -6.49 |
| WDO_M15_F1_ema9x100 | WDO | M15 | 311 | 0.853 | -3.30 | 135 | 0.758 | -3.99 |
| WIN_M15_F3b_bb20_breakout | WIN | M15 | 734 | 0.841 | -4.93 | 327 | 0.693 | -7.15 |
| WIN_M15_F1_ema9x50 | WIN | M15 | 429 | 0.840 | -5.06 | 194 | 0.924 | -1.60 |
| WIN_M15_F6iii_ema200filter_ema9x21 | WIN | M15 | 365 | 0.834 | -4.78 | 159 | 0.660 | -7.26 |
| WIN_M15_F6iii_ema200filter_ema9x50 | WIN | M15 | 235 | 0.830 | -5.40 | 115 | 1.025 | 0.49 |
| WIN_M15_F1_ema9x21 | WIN | M15 | 710 | 0.817 | -5.41 | 311 | 0.748 | -5.17 |
| WDO_M15_F6iii_ema200filter_ema9x50 | WDO | M15 | 269 | 0.804 | -4.58 | 112 | 0.794 | -4.20 |
| WIN_M5_F6ii_adxlt20_rsi14_70_30 | WIN | M5 | 54 | 0.802 | -3.11 | 46 | 0.522 | -7.16 |

## Sanity checks

- Configs com 0 trades no discovery: 0 
- Nenhum trade com entry_ts==exit_ts / cruzando dia (validado no loop principal, ver stdout).

## Decisões de implementação / ressalvas

- Entradas de famílias baseadas em cruzamento (F1, F2, F4c, F5a) disparam apenas no EVENTO de flip (cross), não em 'seguir estado continuamente'. Isso significa que, após um fechamento forçado por EOD, a posição só é reaberta no dia seguinte quando um NOVO cruzamento ocorrer — não há reentrada automática mantendo o estado do dia anterior. Leitura literal do pré-registro ('entra no flip... sai no cross oposto ou EOD').
- F4b (RSI2 Connors): saída definida como condição de nível (close > SMA5 para long, close < SMA5 para short), não como cruzamento — texto do pré-registro diz literalmente 'sai quando close > SMA5'.
- F5b (VWAP stretch fade): saída simétrica por toque (low <= vwap <= high), igual para long e short, conforme especificado. Execução da saída no open de t+1, como as demais.
- Reversão (sinal oposto com posição aberta): saída no open de t+1 e reabertura na direção oposta na MESMA barra de execução, sujeita à mesma janela 09:15-16:00 e ao bloqueio de dia de rolagem que uma entrada nova teria. Se a reversão cair fora da janela ou em dia bloqueado, o resultado é apenas ficar flat (sem reversão).
- Rolagem: detectada por dia de pregão (não dia calendário) via gap absoluto de abertura > 3×std OU retorno overnight > 1.5% (WIN) / 1.0% (WDO); bloqueio cobre o dia flagado + o dia de pregão seguinte.
- real_volume está 100% populado em todos os 4 parquets (WIN/WDO x M5/M15) — não foi necessário fallback para tick_volume no VWAP.
- M5 (WIN e WDO) cobre apenas dez/2022 em diante (arquivo truncado no teto de 100k linhas do terminal MT5, conforme já registrado em memória do projeto). DISCOVERY em M5 é portanto ~1 ano (dez/2022-2023), bem mais curto que M15 (~2.5 anos). Isso já reduz a potência estatística esperada em configs M5, especialmente as com poucos trades/ano.
- PF com denominador zero (nenhum trade perdedor) reportado como inf; configs com 0 trades reportam NaN em todas as métricas derivadas.
- Anomalia: contagem de dias de rolagem ficou abaixo do esperado para WDO (~4-6.7/ano observado vs ~12/ano esperado pelo calendário de vencimentos mensais). Investigação: gap_pct overnight do WDO tem mediana ~0.22% e 75º percentil ~0.36% — a maioria das rolagens reais produz gap MENOR que o threshold de 1.0% (custo de carrego cambial é pequeno), então o critério (gap>3σ OU retorno>1%) não capta todas as rolagens mensais, só as maiores. Para WIN a contagem bateu com a expectativa (~6/ano) porque os gaps overnight do índice são tipicamente maiores. Isso é uma limitação conhecida do critério pré-registrado, não um bug — registrado aqui para o review decidir se precisa de detecção por calendário de vencimento em campanha futura.
- Total de configs implementados: 88 (22 por combinação asset×tf × 4 combinações), abaixo da estimativa aproximada '~140' do pré-registro. A estimativa era um número redondo ('~35' por asset×tf); a grade detalhada no prompt de execução (F1: 5 pares EMA válidos com fast<slow, F2: 2, F3: 2, F4: 3, F5: 3, F6: 7) soma 22 por combinação. Nenhuma variante da grade especificada foi omitida.

## Spot-check manual (F1 EMA9x21, WIN M15) — 2 trades com barras ao redor

```
Spot-check config: WIN_M15_F1_ema9x21, n_trades=1021

Trade #0: dir=-1 entry_ts=2021-07-19 09:30:00 entry_px=124365.0 exit_ts=2021-07-19 17:00:00 exit_px=124730.0 reason=reversal pnl_gross_bps=-29.35
                 ts     open     high      low    close          ema9         ema21
2021-07-19 09:00:00 124750.0 124945.0 124505.0 124940.0 124940.000000 124940.000000
2021-07-19 09:15:00 124940.0 124990.0 124365.0 124365.0 124825.000000 124887.727273
2021-07-19 09:30:00 124365.0 124570.0 124250.0 124555.0 124771.000000 124857.479339
2021-07-19 09:45:00 124560.0 124690.0 124375.0 124480.0 124712.800000 124823.163035
2021-07-19 10:00:00 124475.0 124750.0 124395.0 124555.0 124681.240000 124798.784578
2021-07-19 10:15:00 124555.0 124710.0 124415.0 124690.0 124682.992000 124788.895071
2021-07-19 10:30:00 124695.0 125015.0 124445.0 125010.0 124748.393600 124808.995519
2021-07-19 10:45:00 125010.0 125080.0 124530.0 124735.0 124745.714880 124802.268653
2021-07-19 11:00:00 124735.0 124750.0 124210.0 124380.0 124672.571904 124763.880594
2021-07-19 11:15:00 124385.0 124490.0 124060.0 124320.0 124602.057523 124723.527813
2021-07-19 11:30:00 124325.0 124695.0 123985.0 124595.0 124600.646019 124711.843466
2021-07-19 11:45:00 124595.0 124670.0 124050.0 124065.0 124493.516815 124653.039515
2021-07-19 12:00:00 124060.0 124450.0 123980.0 124245.0 124443.813452 124615.945013
2021-07-19 12:15:00 124250.0 124625.0 124190.0 124255.0 124406.050762 124583.131830
2021-07-19 12:30:00 124255.0 124540.0 124170.0 124440.0 124412.840609 124570.119846
2021-07-19 12:45:00 124440.0 124735.0 124335.0 124570.0 124444.272487 124570.108951
2021-07-19 13:00:00 124570.0 124570.0 124205.0 124460.0 124447.417990 124560.099046
2021-07-19 13:15:00 124460.0 124555.0 124230.0 124245.0 124406.934392 124531.453678
2021-07-19 13:30:00 124240.0 124245.0 123815.0 123855.0 124296.547514 124469.957889
2021-07-19 13:45:00 123860.0 124000.0 123800.0 123935.0 124224.238011 124421.325354
2021-07-19 14:00:00 123935.0 124185.0 123870.0 124060.0 124191.390409 124388.477594
2021-07-19 14:15:00 124060.0 124125.0 123710.0 123875.0 124128.112327 124341.797813
2021-07-19 14:30:00 123875.0 124085.0 123730.0 123845.0 124071.489862 124296.634376
2021-07-19 14:45:00 123845.0 124005.0 123660.0 123750.0 124007.191889 124246.940341
2021-07-19 15:00:00 123750.0 124150.0 123725.0 124070.0 124019.753511 124230.854856
2021-07-19 15:15:00 124070.0 124280.0 124020.0 124140.0 124043.802809 124222.595323
2021-07-19 15:30:00 124135.0 124160.0 123845.0 123950.0 124025.042247 124197.813930
2021-07-19 15:45:00 123950.0 124170.0 123910.0 124060.0 124032.033798 124185.285391
2021-07-19 16:00:00 124055.0 124265.0 123990.0 124170.0 124059.627038 124183.895810
2021-07-19 16:15:00 124170.0 124190.0 124000.0 124140.0 124075.701631 124179.905282
2021-07-19 16:30:00 124135.0 124395.0 124040.0 124290.0 124118.561304 124189.913893
2021-07-19 16:45:00 124295.0 124745.0 124245.0 124730.0 124240.849044 124239.012630
2021-07-19 17:00:00 124730.0 124980.0 124605.0 124960.0 124384.679235 124304.556936
2021-07-19 17:15:00 124960.0 125030.0 124860.0 124965.0 124500.743388 124364.597215
2021-07-19 17:30:00 124965.0 125020.0 124870.0 124920.0 124584.594710 124415.088377
2021-07-19 17:45:00 124925.0 125190.0 124905.0 125060.0 124679.675768 124473.716706

Trade #1: dir=-1 entry_ts=2021-07-20 10:45:00 entry_px=124110.0 exit_ts=2021-07-20 11:00:00 exit_px=124955.0 reason=reversal pnl_gross_bps=-68.08
                 ts     open     high      low    close          ema9         ema21
2021-07-20 10:00:00 124780.0 124790.0 124040.0 124165.0 124715.268156 124597.421225
2021-07-20 10:15:00 124165.0 124385.0 123990.0 124220.0 124616.214525 124563.110204
2021-07-20 10:30:00 124220.0 124285.0 123905.0 124115.0 124515.971620 124522.372913
2021-07-20 10:45:00 124110.0 124985.0 124035.0 124960.0 124604.777296 124562.157194
2021-07-20 11:00:00 124955.0 125170.0 124820.0 124975.0 124678.821837 124599.688358
2021-07-20 11:15:00 124970.0 125480.0 124930.0 125445.0 124832.057469 124676.534871
2021-07-20 11:30:00 125445.0 125480.0 124990.0 125105.0 124886.645975 124715.486246
2021-07-20 11:45:00 125105.0 125375.0 124975.0 125205.0 124950.316780 124759.987496
```