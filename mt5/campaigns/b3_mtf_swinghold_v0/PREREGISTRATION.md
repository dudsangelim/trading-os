# b3_mtf_swinghold_v0 — Pré-registro (2026-07-22)

## Tese

Sequela direta de `b3_classic_indicators_v0`: o cluster de sinal bruto
(trend-following EMAs lentas M15, +2-5 bps/trade, replicado em WIN↔WDO e
discovery↔confirm) morreu por custo, não por ausência de sinal. Duas
rotas testáveis pra escapar do pedágio de 6 bps:

- **A (MTF)**: gate de timeframe superior (H1/D1) concentra o sinal M15
  e eleva o gross/trade acima do custo.
- **B (swing-hold)**: remover a saída EOD e segurar a posição entre
  sessões multiplica a magnitude por trade (o corte EOD trunca
  exatamente o movimento que EMA lenta captura).
- **C (overnight puro)**: o intervalo entre sessões, sozinho, carrega
  drift condicionado ao estado de tendência no fechamento.

**Riscos declarados**: (i) isto beira "filtro pra salvar mecânica" — por
isso grade fechada AQUI, sem iteração pós-confirm; (ii) campanhas swing
daily anteriores mostraram edges trend morrendo em 2025+ — o OOS sagrado
2025+ é o juiz final; (iii) B/C dependem de rolagem por CALENDÁRIO
(gap-based subconta WDO; com overnight isso é crítico, não cosmético).

## Dados

M15/H1/D1 de `WIN_cont_N` e `WDO_cont_N` (M5 excluído: histórico curto e
custo relativo pior). DISCOVERY ≤2023 / CONFIRM 2024 / **OOS 2025+
sagrado** (filtrado no load). VWAP não entra (irrelevante p/ MTF/overnight).

## Rolagem por calendário (regra fixa)

- **WIN**: vencimento na quarta-feira mais próxima do dia 15 de meses
  pares. Janela de rolo = 5 pregões: os 4 anteriores ao vencimento + o
  próprio.
- **WDO**: vencimento no 1º pregão do mês. Janela de rolo = últimos 3
  pregões do mês anterior + o 1º pregão do mês.
- Arm A: sem entradas em dias da janela. Arms B/C: **proibido segurar
  overnight ENTRANDO em qualquer noite cujo pregão seguinte esteja na
  janela** — saída forçada no close anterior; re-entrada permitida no
  open seguinte à janela se o estado persistir.
- Sanity obrigatório: ~6 janelas/ano WIN, ~12/ano WDO.

## Mecânicas (grade fixa)

Sem lookahead: sinal no close da barra t → execução no open de t+1.
Gate de TF superior usa APENAS a última barra H1/D1 **completada** antes
do fechamento da barra de sinal (alinhamento por tempo de término, não
de abertura — MT5 timestampa no open).

### Arm A — MTF intraday (M15, flat EOD, janela de entrada 09:15–16:00)
Gates H1/D1 (estado na última barra completada):
G1 = lado de EMA9x21 H1; G2 = lado de EMA21x50 H1; G3 = close D-1 vs
EMA20 D1; G4 = ADX14 H1 > 25 (sem direção — só habilita).
Triggers M15 (só a favor do gate; G4 combina com a direção do próprio
trigger): T1 = cross EMA9x21; T2 = cross EMA21x50; T3 = cross MACD
12/26/9; T4 = breakout Bollinger 20/2σ a favor.
Saída: cross oposto do trigger ou EOD. 4 gates × 4 triggers × 2 ativos
= **32 configs**.

### Arm B — swing-hold SAR (sem saída EOD)
Estado-seguidor (state, não evento): mantém posição no lado do
indicador; troca no flip; força flat só na regra de rolagem.
Sinais: M15 EMA9x100, M15 EMA21x100, H1 EMA9x21, H1 EMA21x50,
H1 MACD 12/26/9 (lado do MACD vs signal), D1 EMA5x20.
Entradas/re-entradas só no open do pregão (1ª barra) ou no flip
intradiário; saída por flip executa no open de t+1. 6 sinais × 2 ativos
= **12 configs**. PnL overnight é real (contínua não ajustada, mas
janelas de rolo excluídas por calendário ⇒ gap de rolo nunca entra num
trade).

### Arm C — overnight puro (close→open)
Estado no fechamento (última barra do pregão): E1 = lado EMA21x50 H1;
E2 = close vs EMA20 D1; E3 = lado MACD H1. Entra no close, sai:
X1 = open do pregão seguinte (1ª barra); X2 = close da 2ª barra M15
(09:15). 3 estados × 2 saídas × 2 ativos = **12 configs**.
Custo cheio 6 bps por RT aplicado (leilão de abertura tem slippage real
— anotar como risco além dos 6 bps).

Total: **56 configs**. A 5%, ~3 falsos positivos esperados por split.

## Custos

2/4/6 bps RT; **gate a 6 bps**. Arm B tem menos trades e holds longos —
custo pesa menos por construção; a barra não muda.

## Gates de promoção (por config; avaliados só ao fim)

1. PF_net6 ≥ 1.10 no DISCOVERY
2. n_trades: Arm A ≥ 200; Arms B/C ≥ 120 no DISCOVERY (holds longos ⇒
   n estruturalmente menor; declarado ANTES de rodar)
3. Expectancy net6 > 0 em DISCOVERY e CONFIRM
4. PF_net6 ≥ 1.05 no CONFIRM
5. PF_net6 ex-top2 ≥ 1.0 no DISCOVERY (B: ex-top3, holds multi-dia
   concentram PnL)
6. Nenhum ano do discovery com PF_net6 < 0.85
7. **Replicação estrutural**: o MESMO sinal (ou gate×trigger) com sinal
   de expectancy positivo no outro ativo (não precisa passar gates lá;
   precisa não inverter)

Config reprovado: reportar e descartar. Sem filtros novos pós-hoc.

## Fases

- **Fase 0**: varredura completa DISCOVERY+CONFIRM. Executor Sonnet,
  code review Fable. Saída `phase0/results.csv` + `summary.md`.
- **Fase 1** (só com sobrevivente): OOS 2025+ + bootstrap 1000×.
- Closeout obrigatório em qualquer desfecho.
