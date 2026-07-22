# win_stock_leadlag_v0 -- Phase 0A -- summary

Premise check descritivo (sem entry/exit/PnL). Ver PREREGISTRATION.md.

## Log completo de execucao

```
==============================================================================
COBERTURA DE DADOS (apos filtro OOS 2025+ sagrado)
==============================================================================
  VALE3        M5 : total=  99999  pre-2025=  66516  range_pre2025=[2021-12-06 .. 2024-12-30]
  PETR4        M5 : total=  99999  pre-2025=  66487  range_pre2025=[2021-12-03 .. 2024-12-30]
  ITUB4        M5 : total=  99999  pre-2025=  66517  range_pre2025=[2021-12-06 .. 2024-12-30]
  WIN_cont_N   M5 : total= 100000  pre-2025=  56611  range_pre2025=[2022-12-14 .. 2024-12-30]

M1 robustez (k=1..10, sem gate):
  VALE3        M1 : total=  99999  pre-2025=      0
  PETR4        M1 : total=  99999  pre-2025=      0
  ITUB4        M1 : total=  99999  pre-2025=      0
  WIN_cont_N   M1 : total= 100000  pre-2025=      0
  ==> TODAS as series M1 (acoes e WIN) tem 0 barras pre-2025.
  ==> Causa: teto de ~100k barras do terminal MT5 pra M1 alcanca apenas
      ~9-11 meses pra tras a partir de hoje (2026-07-22), caindo
      inteiramente dentro do periodo OOS 2025+ embargado.
  ==> T2 M1 robustez NAO EXECUTAVEL. Reportado como dado ausente, nao
      simulado nem aproximado.

Anos presentes no M5 das acoes (pre-2025): [2021, 2022, 2023, 2024]
M5 das acoes cobre 2021-12->2024-12 (2022 e 2023 completos). DISCOVERY = tudo <=2023 (inclui a franja de 2021-12). CONFIRM = 2024. OOS = 2025+ (ja removido).

==============================================================================
ALINHAMENTO (interseccao exata de timestamps acao x WIN, M5)
==============================================================================
  VALE3: 44322 barras pareadas | barras c/ real_volume==0 descartadas: 0
      2022: n=  1094  hora_min=10.00  hora_max=18.08
      2023: n= 21479  hora_min=10.00  hora_max=18.08
      2024: n= 21749  hora_min=10.00  hora_max=18.08
  PETR4: 44254 barras pareadas | barras c/ real_volume==0 descartadas: 0
      2022: n=  1089  hora_min=10.00  hora_max=18.08
      2023: n= 21432  hora_min=10.00  hora_max=18.08
      2024: n= 21733  hora_min=10.00  hora_max=18.08
  ITUB4: 44311 barras pareadas | barras c/ real_volume==0 descartadas: 0
      2022: n=  1088  hora_min=10.00  hora_max=18.08
      2023: n= 21470  hora_min=10.00  hora_max=18.08
      2024: n= 21753  hora_min=10.00  hora_max=18.08

==============================================================================
T1 -- correlacao contemporanea M5, por acao x ano (sanity, nao e gate)
==============================================================================
  VALE3 2022: n=  1080  corr=0.5556
  VALE3 2023: n= 21222  corr=0.4835
  VALE3 2024: n= 21489  corr=0.4309
  PETR4 2022: n=  1076  corr=0.6175
  PETR4 2023: n= 21173  corr=0.5719
  PETR4 2024: n= 21467  corr=0.4513
  ITUB4 2022: n=  1075  corr=0.6793
  ITUB4 2023: n= 21218  corr=0.6366
  ITUB4 2024: n= 21493  corr=0.5466

==============================================================================
T2 -- lead-lag linear M5, k=1..6, por acao x split (+ espelho WIN->acao)
==============================================================================
  VALE3 DISCOVERY k=1: acao->WIN n= 22042 corr=-0.0106   | WIN->acao n= 22300 corr= 0.0144
  VALE3 DISCOVERY k=2: acao->WIN n= 21782 corr=-0.0122   | WIN->acao n= 22298 corr=-0.0143
  VALE3 DISCOVERY k=3: acao->WIN n= 21531 corr=-0.0100   | WIN->acao n= 22296 corr=-0.0070
  VALE3 DISCOVERY k=4: acao->WIN n= 21273 corr= 0.0172   | WIN->acao n= 22294 corr=-0.0108
  VALE3 DISCOVERY k=5: acao->WIN n= 21014 corr= 0.0161   | WIN->acao n= 22292 corr=-0.0011
  VALE3 DISCOVERY k=6: acao->WIN n= 20755 corr= 0.0055   | WIN->acao n= 22290 corr=-0.0044
  VALE3 CONFIRM k=1: acao->WIN n= 21237 corr= 0.0057   | WIN->acao n= 21488 corr=-0.0119
  VALE3 CONFIRM k=2: acao->WIN n= 20985 corr= 0.0035   | WIN->acao n= 21487 corr=-0.0134
  VALE3 CONFIRM k=3: acao->WIN n= 20741 corr=-0.0001   | WIN->acao n= 21486 corr= 0.0089
  VALE3 CONFIRM k=4: acao->WIN n= 20489 corr=-0.0083   | WIN->acao n= 21485 corr=-0.0023
  VALE3 CONFIRM k=5: acao->WIN n= 20237 corr=-0.0010   | WIN->acao n= 21484 corr=-0.0012
  VALE3 CONFIRM k=6: acao->WIN n= 19985 corr=-0.0122   | WIN->acao n= 21483 corr=-0.0104
  PETR4 DISCOVERY k=1: acao->WIN n= 21986 corr=-0.0179   | WIN->acao n= 22248 corr=-0.0039
  PETR4 DISCOVERY k=2: acao->WIN n= 21723 corr=-0.0090   | WIN->acao n= 22246 corr=-0.0192
  PETR4 DISCOVERY k=3: acao->WIN n= 21468 corr= 0.0005   | WIN->acao n= 22244 corr= 0.0030
  PETR4 DISCOVERY k=4: acao->WIN n= 21206 corr=-0.0088   | WIN->acao n= 22242 corr= 0.0005
  PETR4 DISCOVERY k=5: acao->WIN n= 20944 corr= 0.0002   | WIN->acao n= 22240 corr=-0.0079
  PETR4 DISCOVERY k=6: acao->WIN n= 20682 corr=-0.0045   | WIN->acao n= 22238 corr=-0.0067
  PETR4 CONFIRM k=1: acao->WIN n= 21211 corr=-0.0099   | WIN->acao n= 21466 corr=-0.0104
  PETR4 CONFIRM k=2: acao->WIN n= 20955 corr=-0.0039   | WIN->acao n= 21465 corr= 0.0032
  PETR4 CONFIRM k=3: acao->WIN n= 20708 corr= 0.0074   | WIN->acao n= 21464 corr=-0.0029
  PETR4 CONFIRM k=4: acao->WIN n= 20452 corr=-0.0081   | WIN->acao n= 21463 corr= 0.0052
  PETR4 CONFIRM k=5: acao->WIN n= 20196 corr= 0.0004   | WIN->acao n= 21462 corr=-0.0077
  PETR4 CONFIRM k=6: acao->WIN n= 19942 corr=-0.0054   | WIN->acao n= 21461 corr=-0.0077
  ITUB4 DISCOVERY k=1: acao->WIN n= 22034 corr= 0.0038   | WIN->acao n= 22292 corr= 0.0037
  ITUB4 DISCOVERY k=2: acao->WIN n= 21775 corr=-0.0072   | WIN->acao n= 22290 corr=-0.0164
  ITUB4 DISCOVERY k=3: acao->WIN n= 21521 corr= 0.0027   | WIN->acao n= 22288 corr= 0.0056
  ITUB4 DISCOVERY k=4: acao->WIN n= 21262 corr= 0.0035   | WIN->acao n= 22286 corr= 0.0068
  ITUB4 DISCOVERY k=5: acao->WIN n= 21003 corr=-0.0058   | WIN->acao n= 22284 corr=-0.0030
  ITUB4 DISCOVERY k=6: acao->WIN n= 20744 corr=-0.0063   | WIN->acao n= 22282 corr=-0.0030
  ITUB4 CONFIRM k=1: acao->WIN n= 21242 corr=-0.0004   | WIN->acao n= 21492 corr= 0.0195
  ITUB4 CONFIRM k=2: acao->WIN n= 20991 corr=-0.0123   | WIN->acao n= 21491 corr=-0.0004
  ITUB4 CONFIRM k=3: acao->WIN n= 20749 corr= 0.0134   | WIN->acao n= 21490 corr= 0.0094
  ITUB4 CONFIRM k=4: acao->WIN n= 20498 corr=-0.0100   | WIN->acao n= 21489 corr=-0.0124
  ITUB4 CONFIRM k=5: acao->WIN n= 20247 corr=-0.0063   | WIN->acao n= 21488 corr= 0.0006
  ITUB4 CONFIRM k=6: acao->WIN n= 19996 corr= 0.0034   | WIN->acao n= 21487 corr=-0.0102

T2 robustez M1 (k=1..10): NAO EXECUTAVEL -- 0 barras pre-2025 em todas as series M1

==============================================================================
T3 -- choque idiossincratico (teste central)
==============================================================================
Escolha metodologica: z por horario-do-dia (bucket HH:MM), rolling media/desvio sobre as ultimas 20 ocorrencias PASSADAS do mesmo horario (shift(1), sem lookahead).
  VALE3: ret15 validos=64181, z validos (pos-warmup 20d)=63211 (98.5%) -- OK, nao rala
  PETR4: ret15 validos=64100, z validos (pos-warmup 20d)=63144 (98.5%) -- OK, nao rala
  ITUB4: ret15 validos=64194, z validos (pos-warmup 20d)=63240 (98.5%) -- OK, nao rala
  WIN: ret15 validos=55078, z validos=53977 (98.0%)
  Decisao final: por horario

Total eventos T3 (todas acoes, todos splits, pos-dedupe 30min): 612
  VALE3  DISCOVERY fwd_ 5m: n= 110  mean=  +0.53bps  median=  +0.44bps  IC95=[-1.16,+2.28]bps  WR=53.6%
  VALE3  DISCOVERY fwd_15m: n= 110  mean=  +1.45bps  median=  +0.43bps  IC95=[-2.14,+4.74]bps  WR=50.9%
  VALE3  DISCOVERY fwd_30m: n= 110  mean=  +0.65bps  median=  +0.44bps  IC95=[-3.74,+4.90]bps  WR=50.0%
  VALE3  CONFIRM   fwd_ 5m: n= 125  mean=  +0.39bps  median=  -0.38bps  IC95=[-0.60,+1.49]bps  WR=45.6%
  VALE3  CONFIRM   fwd_15m: n= 125  mean=  -0.50bps  median=  -1.18bps  IC95=[-2.38,+1.47]bps  WR=47.2%
  VALE3  CONFIRM   fwd_30m: n= 125  mean=  -0.06bps  median=  +0.38bps  IC95=[-2.73,+2.52]bps  WR=50.4%
  PETR4  DISCOVERY fwd_ 5m: n=  93  mean=  -1.09bps  median=  -1.70bps  IC95=[-2.57,+0.25]bps  WR=43.0%
  PETR4  DISCOVERY fwd_15m: n=  93  mean=  -2.09bps  median=  -0.84bps  IC95=[-5.30,+0.59]bps  WR=44.1%
  PETR4  DISCOVERY fwd_30m: n=  93  mean=  -5.35bps  median=  -2.57bps  IC95=[-10.34,-1.16]bps  WR=41.9%
  PETR4  CONFIRM   fwd_ 5m: n= 126  mean=  +0.12bps  median=  -0.19bps  IC95=[-1.31,+1.51]bps  WR=48.4%
  PETR4  CONFIRM   fwd_15m: n= 126  mean=  +0.43bps  median=  +0.20bps  IC95=[-1.77,+2.70]bps  WR=50.0%
  PETR4  CONFIRM   fwd_30m: n= 126  mean=  +1.26bps  median=  +1.94bps  IC95=[-1.96,+4.28]bps  WR=53.2%
  ITUB4  DISCOVERY fwd_ 5m: n=  61  mean=  +3.05bps  median=  +0.85bps  IC95=[-2.00,+11.63]bps  WR=55.7%
  ITUB4  DISCOVERY fwd_15m: n=  61  mean=  +4.74bps  median=  +0.92bps  IC95=[-0.97,+11.07]bps  WR=52.5%
  ITUB4  DISCOVERY fwd_30m: n=  61  mean=  +3.23bps  median=  +3.39bps  IC95=[-2.39,+10.11]bps  WR=57.4%
  ITUB4  CONFIRM   fwd_ 5m: n=  97  mean=  +1.23bps  median=  +0.80bps  IC95=[-0.07,+2.67]bps  WR=58.8%
  ITUB4  CONFIRM   fwd_15m: n=  97  mean=  +1.60bps  median=  +0.00bps  IC95=[-0.90,+4.18]bps  WR=48.5%
  ITUB4  CONFIRM   fwd_30m: n=  97  mean=  +4.36bps  median=  +3.51bps  IC95=[+0.56,+8.08]bps  WR=58.8%
  ALL    DISCOVERY fwd_ 5m: n= 264  mean=  +0.54bps  median=  +0.40bps  IC95=[-1.04,+2.59]bps  WR=50.4%
  ALL    DISCOVERY fwd_15m: n= 264  mean=  +0.96bps  median=  +0.00bps  IC95=[-1.10,+3.31]bps  WR=48.9%
  ALL    DISCOVERY fwd_30m: n= 264  mean=  -0.87bps  median=  -0.19bps  IC95=[-3.65,+1.84]bps  WR=48.9%
  ALL    CONFIRM   fwd_ 5m: n= 348  mean=  +0.53bps  median=  +0.38bps  IC95=[-0.25,+1.31]bps  WR=50.3%
  ALL    CONFIRM   fwd_15m: n= 348  mean=  +0.42bps  median=  -0.38bps  IC95=[-0.87,+1.80]bps  WR=48.6%
  ALL    CONFIRM   fwd_30m: n= 348  mean=  +1.65bps  median=  +1.15bps  IC95=[-0.18,+3.57]bps  WR=53.7%

Breakdown por ano:
  ITUB4 2022: n=   1  mean_fwd15=  -6.25bps  WR=0.0%
  ITUB4 2023: n=  60  mean_fwd15=  +4.93bps  WR=53.3%
  ITUB4 2024: n=  97  mean_fwd15=  +1.60bps  WR=48.5%
  PETR4 2023: n=  93  mean_fwd15=  -2.09bps  WR=44.1%
  PETR4 2024: n= 126  mean_fwd15=  +0.43bps  WR=50.0%
  VALE3 2022: n=   1  mean_fwd15= +19.73bps  WR=100.0%
  VALE3 2023: n= 109  mean_fwd15=  +1.28bps  WR=50.5%
  VALE3 2024: n= 125  mean_fwd15=  -0.50bps  WR=47.2%

==============================================================================
T4 -- divergencia de cesta ponderada
==============================================================================
Pesos renormalizados: {'VALE3': 0.4285714285714286, 'PETR4': 0.34285714285714286, 'ITUB4': 0.2285714285714286}
Barras com cesta+WIN validos (ret30, interseccao das 3 acoes): 40939
  DISCOVERY Q1(mais neg): n= 4168  spread_mean=-21.56bps  fwd30=+0.45bps  fwd60=+1.11bps
  DISCOVERY Q2: n= 4167  spread_mean=-6.74bps  fwd30=-0.03bps  fwd60=-0.00bps
  DISCOVERY Q3: n= 4167  spread_mean=+0.08bps  fwd30=+0.16bps  fwd60=+0.62bps
  DISCOVERY Q4: n= 4167  spread_mean=+6.81bps  fwd30=-0.16bps  fwd60=-0.06bps
  DISCOVERY Q5(mais pos): n= 4167  spread_mean=+20.78bps  fwd30=+0.51bps  fwd60=+0.37bps
  CONFIRM Q1(mais neg): n= 3556  spread_mean=-20.12bps  fwd30=-0.47bps  fwd60=-0.86bps
  CONFIRM Q2: n= 4495  spread_mean=-6.69bps  fwd30=+0.14bps  fwd60=+0.10bps
  CONFIRM Q3: n= 4488  spread_mean=+0.05bps  fwd30=+0.09bps  fwd60=+0.40bps
  CONFIRM Q4: n= 4106  spread_mean=+6.70bps  fwd30=-0.53bps  fwd60=-0.59bps
  CONFIRM Q5(mais pos): n= 3457  spread_mean=+20.05bps  fwd30=-0.88bps  fwd60=-1.46bps

==============================================================================
GATES MECANICOS (reporte passa/falha -- SEM veredito, revisao e do Fable)
==============================================================================
  Gate1a T2 k=1 VALE3: DISCOVERY=-0.0106 CONFIRM=0.0057 -> falha
  Gate1a T2 k=1 PETR4: DISCOVERY=-0.0179 CONFIRM=-0.0099 -> falha
  Gate1a T2 k=1 ITUB4: DISCOVERY=0.0038 CONFIRM=-0.0004 -> falha
  Gate1b T3 fwd15 VALE3: DISCOVERY mean=+1.45bps n=110 IC_excl_zero=False CONFIRM_mean=-0.50bps -> falha
  Gate1b T3 fwd15 PETR4: DISCOVERY mean=-2.09bps n=93 IC_excl_zero=False CONFIRM_mean=+0.43bps -> falha
  Gate1b T3 fwd15 ITUB4: DISCOVERY mean=+4.74bps n=61 IC_excl_zero=False CONFIRM_mean=+1.60bps -> falha
  Gate1c T4 DISCOVERY: extremo=0.51bps monotonic=False replica_CONFIRM=False -> falha

GATE 1 (T2 OR T3 OR T4): FALHA
GATE 2 (efeito em >=2 acoes ou na cesta): acoes_com_hit(T2)=0 acoes_com_hit(T3)=0 cesta_T4=False -> FALHA

GATE 3 (regua T2-espelho, k=1 M5 DISCOVERY):
  VALE3: |WIN->acao|/|acao->WIN| = 1.36 -> nao confirma 3x
  PETR4: |WIN->acao|/|acao->WIN| = 0.22 -> nao confirma 3x
  ITUB4: |WIN->acao|/|acao->WIN| = 0.98 -> nao confirma 3x
GATE 3: nao aplicavel / nao confirmado

RESUMO GATES:
  Gate 1 (T2 OR T3 OR T4): FALHA
  Gate 2 (>=2 acoes/cesta): FALHA
  Gate 3 (regua espelho, veto): sem veto
  TODOS OS GATES MECANICOS: FALHA
  (Nota: decisao de promocao/closeout e da revisao humana/Fable, nao deste script.)
```
