# win_stock_leadlag_v0 — Pré-registro Phase 0A (2026-07-22)

## Tese (ideia do Eduardo)

As ações de maior peso do Ibovespa (VALE3, PETR4, ITUB4) carregam
informação que antecipa o WIN em horizonte de minutos.

**Prior declarado: desfavorável.** Literatura clássica (Kawaller 1987;
Stoll & Whaley 1990; replicações em vários mercados): futuros de índice
LIDERAM o à vista em 5-40min; a direção ação→futuro é fraca/nula porque
a arbitragem é quase instantânea. Precedente interno: lead-lag BTC→ETH
refutado (features cross-asset = ruído). O canal com mecanismo plausível
é estreito: **choque idiossincrático** na ação (driver externo — minério,
Brent, notícia própria) ainda não refletido no WIN, que precisa ajustar
por aritmética de índice. Phase 0A mede se esse ajuste é lento o
suficiente pra existir em M1/M5.

## Dados (coletar nesta fase)

Via MT5 (terminal XP demo): VALE3, PETR4, ITUB4, M1 e M5, máximo
disponível (teto 100k barras ≈ 9m em M1, ~4a em M5). Salvar parquets em
`B3 Futuros/mt5_history/` no padrão existente. WIN = `WIN_cont_N` M1/M5
já coletados. Alinhamento por timestamp B3; barras de ação e WIN
sincronizadas por interseção (descartar minutos sem ambos).

**Splits**: DISCOVERY = tudo ≤2023 que existir (M5; M1 terá só ~2026 —
usar M1 apenas como robustez, sem gate próprio); CONFIRM = 2024;
**OOS 2025+ sagrado** (filtrado no load).
Se a coleta M5 começar depois de 2023-01, DISCOVERY = primeira metade do
período pré-2025 e CONFIRM = segunda metade pré-2025, documentando o
corte exato ANTES de computar qualquer estatística.

## Testes pré-registrados (descritivos; sem entry/exit)

- **T1 (sanity, não é gate)**: correlação contemporânea de retornos M5
  ação×WIN. Esperado 0.4-0.8. Se <0.2, suspeitar de dados ruins.
- **T2 (lead-lag linear)**: corr(ret_ação[t−k], ret_WIN[t]) para
  k=1..6 barras, M5, por ação e por ano. Comparar com o espelho
  corr(ret_WIN[t−k], ret_ação[t]) (a direção que a literatura prevê
  dominante — serve de régua).
- **T3 (choque idiossincrático — o teste central)**: evento = |z_ação|
  ≥ 2.5 na janela de 15min (z sobre vol rolling 20d do horário) E
  |z_WIN| < 0.5 na mesma janela. Medir forward WIN 5/15/30min na
  direção do choque. Reportar n, mediana, média, IC bootstrap 1000×,
  por ano e por ação.
- **T4 (divergência de cesta)**: spread = ret_30min da cesta ponderada
  (pesos fixos aproximados VALE 0.15, PETR4 0.12, ITUB4 0.08,
  renormalizados) − ret_30min WIN. Quintis do spread → forward WIN
  30/60min (convergência?).

## Gates Phase 0A → Phase 0B (mecânica só se TODOS passarem)

1. T2: alguma corr lead ação→WIN (k=1, M5) ≥ 0.05 no DISCOVERY **e**
   ≥ 0.03 no CONFIRM com mesmo sinal — OU —
   T3: forward WIN 15min pós-choque com |média| ≥ 6 bps, n ≥ 100 no
   DISCOVERY, IC bootstrap excluindo zero, mesmo sinal no CONFIRM — OU —
   T4: quintil extremo com forward ≥ 6 bps e monotonicidade nos
   quintis, replicando no CONFIRM.
2. O efeito que passar deve aparecer em ≥ 2 das 3 ações (ou na cesta)
   — 1 ação sozinha = ruído provável.
3. Régua T2-espelho: se WIN→ação for ≥ 3× mais forte que ação→WIN
   (esperado pela literatura), documentar como confirmação do prior e
   NÃO promover mesmo com gate 1 marginal.

Falhou: closeout `premise_refuted`, sem iteração de thresholds.

## Fases

- **0A** (esta): coleta + descritivo. Executor Sonnet, review Fable.
  Saída: `phase0a/` (parquets novos ficam em `mt5_history/`).
- **0B** (se passar): mecânica com custos 6 bps, gates padrão.
- Closeout obrigatório.

## Riscos anotados

- Terminal MT5 precisa estar aberto/conectável — se a coleta falhar,
  reportar e parar (não simular dados).
- Horário de pregão de ações ≠ WIN (10:00-16:55 vs 09:00-18:25 em
  regime atual; leilões de abertura/fechamento) — usar só interseção e
  documentar o recorte por subperíodo se o horário mudou no histórico.
- Survivorship dos pesos: pesos fixos aproximados de hoje aplicados ao
  passado — viés conhecido, aceitável em premise check, anotar.
