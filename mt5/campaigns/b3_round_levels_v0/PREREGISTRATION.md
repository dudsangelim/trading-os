# b3_round_levels_v0 — Pré-registro Phase 0A (2026-07-22)

## Tese

Números redondos concentram ordens (stops logo além, limits no nível) —
documentado com dados reais de ordens por Osler (2000, 2003/2005, FX):
(a) **H_bounce**: o nível segura o primeiro toque com frequência acima
do acaso; (b) **H_cascade**: rompido o nível, o movimento acelera
(cascata de stops) — continuação, não reversão. As duas hipóteses têm
sinais OPOSTOS e são testadas simetricamente. Aplicação em WIN/WDO
nunca feita. Prior: moderado (mecanismo documentado em FX; mas corpo
intraday B3 é cemitério, e cascatas podem viver abaixo da barra de 5m).

## Desenho anti-tautologia (núcleo do pré-registro)

Todo efeito é medido CONTRA GRADE PLACEBO: a mesma estatística computada
em níveis deslocados de meia-grade. Efeito real = diferença
redondo−placebo com IC bootstrap excluindo zero. Sem placebo, taxa de
bounce/fill é artefato de construção.

**Grades** (redondo | placebo):
- WIN intraday: múltiplos de 500 pts | grade +250; múltiplos de 1000 |
  grade +500 (só os ímpares de 500, p/ não colidir).
- WIN swing: múltiplos de 5000 | grade +2500.
- WDO intraday: múltiplos de 5 pts | grade +2.5; múltiplos de 10 |
  grade +5 (ímpares); WDO swing: múltiplos de 50 | grade +25.

## Dados e splits

M5/M15/H1/D1 de WIN_cont_N e WDO_cont_N. DISCOVERY ≤2023 / CONFIRM
2024 / **OOS 2025+ sagrado** (filtrado no load). Dias de janela de
rolagem por calendário (regra da b3_mtf_swinghold_v0) excluídos dos
EVENTOS (toques/quebras) — gap de rolo desloca o preço através de
níveis sem pregão.

## Testes Phase 0A (descritivos; sem entry/exit)

### Braço intraday (M5 principal; M15 e H1 como robustez de grade)
- **T1 (H_bounce)**: evento = primeira aproximação do dia a um nível
  (preço vem de ≥0.3×ATR20d de distância e toca banda de ±0.05×ATR20d
  do nível). Medir: taxa de "segura" (reverte ≥0.15×ATR antes de
  atravessar ≥0.10×ATR) e forward return assinado 15/30/60min.
  Redondo vs placebo, por ativo × grade × split.
- **T2 (H_cascade)**: evento = cruzamento confirmado do nível (close
  M5 além de ≥0.10×ATR após estar do outro lado). Medir: forward na
  direção da quebra 5/15/30/60min + |range| dos próximos 30min
  (velocidade, proxy da cascata de Osler). Redondo vs placebo.
- **T3 (magnetismo)**: fração do tempo a ≤0.05×ATR de nível redondo vs
  placebo (paridade esperada se nada existe; excesso = clustering).

### Braço swing (D1; W1 como robustez)
- **T4 (barreira)**: distribuição da distância do CLOSE diário ao nível
  redondo grande mais próximo vs placebo (efeito barreira clássico:
  déficit de closes colados no redondo).
- **T5 (cruzamento swing)**: close diário cruza nível grande (WIN 5000
  / WDO 50) → drift D+1..D+5 na direção do cruzamento, vs placebo.
- **T6 (rejeição swing)**: high/low diário toca nível grande sem close
  atravessar → reversão D+1..D+3, vs placebo.

Bootstrap 1000× em toda diferença redondo−placebo. Reportar n, efeito,
IC, por ano. Nenhum evento em dia de janela de rolo.

## Gates Phase 0A → 0B (mecânica)

1. **Efeito líquido de placebo**: alguma célula (hipótese × grade × TF)
   com |diferença redondo−placebo| de forward ≥ 6 bps (intraday) ou
   ≥ 25 bps (swing D+1..D+5 acumulado), IC bootstrap excluindo zero no
   DISCOVERY, mesmo sinal no CONFIRM.
2. **Replicação entre ativos**: mesma célula (mesma hipótese e grade
   equivalente) com mesmo sinal em WIN E WDO (mecanismo de clustering
   não é idiossincrático de um ativo).
3. **n ≥ 150** eventos no discovery na célula candidata (intraday) /
   ≥ 60 (swing).
4. Coerência de mecanismo: se H_bounce e H_cascade derem AMBAS
   positivas na mesma grade/TF (contraditório), tratar como suspeita de
   artefato e não promover sem diagnóstico.

Falhou tudo: closeout `premise_refuted`, sem iteração de thresholds ou
grades. Passou: Phase 0B define mecânica (lado vencedor: bounce-fade OU
cascade-momentum; intraday OU swing — "qual o melhor" sai da comparação
direta das células vencedoras).

## Fases
- **0A** (esta): descritivo intraday+swing. Executor Sonnet, review Fable.
- **0B**: mecânica com custos (6 bps gate intraday; swing com overnight
  usa regras de rolagem da b3_mtf_swinghold_v0).
- Closeout obrigatório.

## Riscos anotados
- Cascata de Osler é fenômeno de segundos/minutos — M5 pode ser grosso
  demais; se T2 falhar mas T3 acusar clustering, anotar tick data (1a
  de WIN com agressor) como caminho de re-teste, campanha separada.
- ATR20d como escala (vol encolhendo — lição do atlas): thresholds
  todos relativos, nunca em pontos fixos.
- WDO tem tick mínimo 0.5 pt e níveis psicológicos em R$/mil — grade de
  5/10 pts pode não ser onde os stops moram; a comparação de grades é
  parte do teste, não iteração livre.
