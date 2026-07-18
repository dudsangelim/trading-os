# Pesquisa B3 (WIN/WDO) — contexto para qualquer sessão nova

> Doc de onboarding auto-suficiente. Uma sessão Claude (cloud/mobile/desktop) que clone
> este repo deve conseguir continuar a pesquisa lendo APENAS este arquivo e os que ele aponta.
> Última atualização: 2026-07-18.

## Estado atual (1 linha)

Campanha 1 (`b3_or_continuation_v0`, opening range) **REFUTADA** em 2026-07-18; próxima
campanha sugerida: **interação sessão B3 × abertura NY (~10:30 local)**. OOS 2025+ **virgem**.

## Dados — `research/b3_win_wdo_data_audit/mt5_history/`

Parquets MT5 (XP demo), séries contínuas B3 **não ajustadas** (`WIN_cont_N_*`, `WDO_cont_N_*`):

| TF | Cobertura | Uso |
|---|---|---|
| M15/M30/H1/D1 | 2021-07-19 → 2026-07-17 (~5 anos completos) | principal |
| M5 | 2022-12 → 2026-07 (~3.6a, truncado no cap 100k do MT5) | intraday fino |
| M1 | 2025-10 → 2026-07 (~9m, truncado) | só validação de execução |

- Colunas: `datetime_b3` (**horário local B3, UTC-3 naive — NÃO é UTC**), `epoch`, OHLC,
  `tick_volume`, `real_volume`. Timestamp = ABERTURA da barra.
- `*_cont_ADJprop_*` = back-ajustadas: NUNCA usar pra preço executável/P&L.
- Contratos reais (WINQ26 etc.): só 2026+, fase de lançamento é esparsa — usar só pra
  estudo de rolagem.
- Ler `MT5_COLLECTION_REPORT.md` (mesma pasta) ANTES de qualquer análise: regras de
  sessão (abre 09:00; fechamento varia com DST dos EUA ~18:00/18:30 — usar última barra
  do dia, nunca relógio fixo), quartas de Cinzas (abre 13:00, excluir), rolagem
  (WIN: dia do vencto; WDO: véspera; sempre overnight, nunca intradiária), dias anômalos.

## Regras invioláveis da pesquisa

1. **OOS sagrado: `datetime_b3 >= 2025-01-01` — filtrar fora NO LOAD.** Nenhuma
   estatística, tuning ou olhada no OOS até uma mecânica passar gates no IS.
2. Custos POR EXECUÇÃO em ticks (0.5/1/2; referência = 1 tick). WIN tick=5 pts
   (R$0.20/pt); WDO tick=0.5 pt (R$10/pt). Entrada+saída = 2 execuções.
3. Sinal no close da barra t → fill no OPEN de t+1. Stop-first em barra ambígua.
   Flat na última barra da sessão. Nunca overnight. Máx 1 trade/dia/ativo.
4. Critério de abandono PRÉ-REGISTRADO antes de rodar backtests (ver exemplo em
   `research/b3_or_continuation_v0/DECISION_PHASE_B.md` seção 7). Gate não flexibiliza
   post-hoc. Replicação cross-asset (WIN↔WDO) como gate.
5. Parametrização única conservadora na fase de mecânicas; varredura só depois, ampla.
6. Honestidade brutal: negativos são reportados por inteiro; não salvar mecânica com filtros.

## Workflow das campanhas (pedido do Eduardo)

Fases: A mapa descritivo (mecânico) → B decisão/spec congelada (análise) → C backtests
(mecânico) → D veredito contra critério pré-registrado (análise). **Code review ao fim de
CADA fase** antes da seguinte consumir os resultados — na campanha 1 isso pegou um bug real
("TP fantasma") logo na primeira aplicação. Quando houver múltiplos modelos disponíveis:
Sonnet pra fases mecânicas, Fable pra análise/decisão/reviews.

## O que a campanha 1 estabeleceu (não retestar)

Ver `research/b3_or_continuation_v0/campaign_closeout.md`. Resumo:
- Corr abertura→resto-do-dia ~zero e instável (nem momentum nem reversal em barras).
- False break de OR 79-95% mas NÃO monetizável: fade tem edge bruto ~zero (C1_WIN
  PF líq = 1.00 exato). Refutado em 2 mercados (BTC 10bps, B3 2-6bps).
- Melhor config C3 ORB-CONT WIN M5 = PF 1.079 < gate 1.10, sem replicação WDO — ruído
  selecionado (melhor de 12). Não continuar esta família sem dado novo (book/fluxo B3).

## Próxima campanha (aberta, não iniciada)

**Sessão B3 × abertura NY (~10:30 local B3 quando NY abre 09:30 EDT; ~11:30 quando EST — 
cuidado: o offset muda com o DST americano).** Regime de vol distinto, não testado. Começar
pela fase A (mapa descritivo): comportamento de WIN/WDO na janela pré/pós abertura NYSE,
condicionado a gap overnight, direção da manhã B3, e vol. IS até 2024-12, como sempre.

## Limitações de ambiente (sessão cloud/mobile)

- Sem acesso à VPS (Tailscale) nem ao MT5 — dados novos só em sessão no notebook do Eduardo.
- Tudo necessário pra pesquisa de barras está NESTE repo.
