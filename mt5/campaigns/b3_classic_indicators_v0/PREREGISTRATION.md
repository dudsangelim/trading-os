# b3_classic_indicators_v0 — Pré-registro (2026-07-22)

## Tese

Indicadores técnicos clássicos (médias móveis, MACD, Bollinger, RSI, VWAP)
e suas combinações, aplicados como **regras mecânicas de entrada/saída
intraday** em WIN e WDO, podem capturar estrutura que os testes
descritivos do atlas não capturaram — porque uma regra com stop/exit
dinâmico não é o mesmo objeto que um forward-return incondicional.

**Prior declarado: desfavorável.** A família OHLCV intraday WIN foi
encerrada em 2026-07-21 (atlas 135 testes + VWAP descritivo, 0 candidatos).
Esta campanha reabre a família por pedido explícito do Eduardo, no ângulo
específico "indicadores clássicos como mecânica", que não foi coberto.
WDO intraday nunca foi varrido. Se der zero, é o resultado esperado e
encerra o ângulo em ambos os ativos.

## Dados

- `WIN_cont_N_M5/M15.parquet`, `WDO_cont_N_M5/M15.parquet` (contínua não
  ajustada; intraday-only com saída forçada EOD ⇒ rolagem não contamina).
- Entradas bloqueadas em dia de rolagem ±1 (gap de rolo no indicador).
- **DISCOVERY**: 2021→2023 (M15) / início-M5→2023 (M5, ~dez-2022).
- **CONFIRM**: 2024.
- **OOS SAGRADO**: 2025+ — só toca se algum config passar discovery E
  confirm. Nenhuma iteração de parâmetro após olhar confirm.

## Mecânicas pré-registradas (grade fixa, sem iteração posterior)

Sinal calculado no fechamento da barra t, **entrada na abertura de t+1**.
Entradas permitidas 09:15–16:00 local; saída forçada na última barra do
dia. Sem overnight. 1 contrato, PnL em bps sobre preço de entrada.

- **F1 — Cruzamento de EMAs (momentum)**: fast∈{9,21} × slow∈{21,50,100}
  (fast<slow). Long no cross-up, short no cross-down; sai no cross oposto
  ou EOD.
- **F2 — MACD (12,26,9)**: cross da signal line; variante com filtro de
  zero-line (só long se MACD>0, só short se MACD<0).
- **F3 — Bollinger (20, 2.0)**: (a) mean-reversion — fecha fora da banda,
  entra contra, sai na média (mid) ou EOD; (b) breakout — fecha fora,
  entra a favor, sai no cross da mid ou EOD.
- **F4 — RSI**: (a) RSI14 70/30 fade, sai em RSI 50 ou EOD; (b) RSI2
  90/10 fade (Connors), sai na média de 5 ou EOD; (c) RSI14 cross de 50
  momentum, sai no cross oposto ou EOD.
- **F5 — VWAP de sessão**: (a) cross momentum — fecha do lado oposto ao
  anterior, entra a favor, sai no cross oposto ou EOD; (b) stretch fade —
  |close−vwap| ≥ k×ATR20 (k∈{1.0,1.5}), entra contra, sai no toque do
  VWAP ou EOD.
- **F6 — Combos (filtro × gatilho)**: ADX14>25 como gate pró-tendência
  em F1/F2; ADX14<20 como gate pró-range em F3a/F4a/F5b; EMA200 do
  próprio TF como filtro direcional em F1 (só opera a favor).

TFs: M5 e M15. Ativos: WIN e WDO. Total ≈ 2×2×~35 ≈ **~140 backtests**.
A 5%, ~7 falsos positivos esperados no discovery — por isso o confirm.

## Custos (premissa registrada)

RT modelado em 3 níveis: 2 / 4 / 6 bps. **Gate avaliado no nível 6 bps**
(conservador: emolumentos + 1 tick de slippage por perna nos dois ativos).
Sem modelagem de fila; mecânicas F3a/F5b (limit-like) anotadas como
otimistas em fill.

## Gates de promoção (TODOS, avaliados só depois da varredura completa)

1. PF_net@6bps ≥ 1.10 no DISCOVERY
2. n_trades ≥ 200 no DISCOVERY
3. Expectancy net@6bps > 0 no DISCOVERY e no CONFIRM
4. PF_net@6bps ≥ 1.05 no CONFIRM (replicação, sem sign-flip)
5. Sem dependência de 1-2 trades (top-2 trades removidos, PF ainda ≥ 1.0)
6. Estabilidade ano a ano no discovery (nenhum ano com PF < 0.85)

Config que falhar qualquer um: reportar e descartar. **Proibido** adicionar
filtro novo pra salvar config reprovado (= overfitting; regra do CLAUDE.md).

## Fases

- **Fase 0** (esta): varredura da grade completa em DISCOVERY + CONFIRM.
  Executor: agente Sonnet. Code review: Fable. Saída: `phase0/results.csv`
  + sumário.
- **Fase 1** (só se ≥1 sobrevivente): OOS 2025+ + bootstrap 1000×.
- Closeout obrigatório em qualquer desfecho.
