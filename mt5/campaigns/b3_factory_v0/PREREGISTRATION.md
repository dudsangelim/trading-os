# b3_factory_v0 — Fábrica de estratégias (2026-07-22)

Mandato do Eduardo: esgotar possibilidades WIN/WDO (+ outros dados
disponíveis) atrás de estruturas com PF≥1.2, maxDD≤20%, Sharpe>1,
≥3%/mês. Entregar 3 estruturas prontas. Orquestração Fable + workers
Sonnet/Opus low-medium.

## Regras invioláveis (herdam Manifesto + lições da sessão)

1. **OOS 2025+ dos futuros reais B3: NENHUM worker carrega.** Proxies e
   futuros só até 2024-12-31. BTC: OOS conforme campanhas anteriores.
2. Custos B3 CORRIGIDOS: 1.5 bps RT base (sensibilidade 1 e 2), rolagem
   1.2 bps/mês em posição. BTC: 10 bps RT como sempre.
3. Toda família tem grid PRÉ-FIXADO neste doc (via prompts do workflow,
   que é o artefato de pré-registro). TODAS as células reportadas.
4. Split padrão proxies: DISCOVERY 2000-2018 / CONFIRM 2019-2024.
   Futuros reais 2021-2024 = real-check. Split intraday: discovery
   2023 (M5) ou 2021-2023 (M15) / confirm 2024.
5. Resultado positivo só entra na síntese após replicação/review do Fable.
6. Metas do Eduardo avaliadas na SÍNTESE de portfólio (streams
   descorrelacionados + alavancagem), não por worker isolado. Relatório
   final diz claramente o que atinge e o que não atinge as metas.

## Workers (grid resumido; detalhe integral nos prompts do workflow)

- **W0 Fase1 TSM WDO**: L∈{63,126}×F∈{5,21}, com e sem vol-targeting
  (alvo 10%, vol 20d, cap 3x). Ano a ano. Base da síntese.
- **W1 TSM combo**: idem em WIN proxy + combo 50/50 WIN+WDO; alavancagem
  {1,2,3,4}× na síntese de metas.
- **W2 Carry condicional WDO**: short-USD só quando diff(CDI−FFR)
  ∈ {>4%,>6%,>8%} × vol20d ∈ {<p50,<p75,todas}. 9 células, 26 anos.
- **W3 Fade de nível WIN intraday** (custo novo 1.5 bps): limit no 1º
  toque de PDH/PDL, stop {15,25} bps × TP {10,20} bps × time-stop 60min.
- **W4 Pullback multi-timeframe** (Connors RSI2 + tendência semanal):
  WIN e WDO, proxies 26a + real-check.
- **W5 Sweep de liquidez diário (SMC)**: varre mínima/máxima de ontem
  ≥{5,10} bps e fecha de volta no range → reversão 1-3d. 26a proxy + real.
- **W6 Recon de símbolos MT5** (dados novos p/ ampliar portfólio).
- **W7 Stream BTC 2C v2** (motor legacy): extrair retornos mensais do
  backtest local p/ matriz de correlação do portfólio.

## Síntese (Fable)

Matriz de correlação dos streams sobreviventes → portfólio vol-target →
alavancagem necessária p/ 3%/mês → PF/Sharpe/DD/mensal honestos →
3 estruturas finais documentadas.
