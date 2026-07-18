# Pesquisa B3 (WIN/WDO) — contexto para qualquer sessão nova

> Doc de onboarding auto-suficiente. Uma sessão Claude (cloud/mobile/desktop) que clone
> este repo deve conseguir continuar a pesquisa lendo APENAS este arquivo e os que ele aponta.
> Última atualização: 2026-07-18.

## Estado atual (1 linha)

Campanhas 1 (`b3_or_continuation_v0`, opening range) **REFUTADA**; 2 (`b3_ny_open_v0`,
sessão B3 × abertura NY) e 3 (`b3_wdo_ptax_v0`, janelas PTAX/fixing EOM) **ENCERRADAS NA
FASE B** (`premise_not_supported`, 0 backtests gastos) — tudo em 2026-07-18. Famílias
intraday-direcionais em OHLCV estão se esgotando; candidatas restantes abaixo. USDT/BRL ×
WDO adiada até haver histórico do Basis Observer (VPS). OOS 2025+ **virgem**.

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

## O que a campanha 2 estabeleceu (não retestar)

Ver `research/b3_ny_open_v0/` (spec, mapa, review, decisão). Resumo:
- O bump de vol da abertura NY é REAL e segue o relógio de NY (desloca com o DST; eco
  também dos indicadores US 08:30 ET) — mas NÃO carrega direção monetizável: corr
  manhã→pós ≤0,05, gap→pós sem dose-resposta, impulso NY-30min→resto ≈ 0.
- Range da manhã quebra pós-anchor em 92-96% dos dias (~26% quebram os 2 lados).
- corr(WIN,WDO) pós-anchor = −0,55 estável todos os anos — estrutura mais forte do
  mapa; avaliar futuras estratégias B3 também como PAR.
- Rompimento/fade do range da manhã = re-tunagem da família refutada da campanha 1;
  proibido sem dado novo (Manifesto §5).

## O que a campanha 3 estabeleceu (não retestar)

Ver `research/b3_wdo_ptax_v0/`. Resumo: janelas do PTAX NÃO são localmente quentes de
forma específica no WDO (aquecimento de hora cheia é market-wide — WIN 10:00 razão 1.21
estável 4/4 anos, maior que qualquer janela do WDO); arco drift→unwind do dia de fixing
tem o SINAL OPOSTO ao previsto (corr +0.23, n=42); nenhum contraste WDO≫WIN. Reabre só
com dado de fluxo cambial ou tick data (Manifesto §5). Observação registrada (não é
hipótese): leve drift positivo intradiário do WDO em D-3/D-1/D0 do fim de mês (~10-20bps,
t≈1.7, possível seleção entre células do P5).

## Candidatas restantes (avaliadas em 2026-07-18, nenhuma aberta)

1. **Par WIN×WDO divergência** — explorar a corr −0,55 estável (campanha 2 A6): mapear
   dias de mesmo-sinal (quebra da relação) e o que segue. Sinal cross-asset, não refutado.
2. **Overnight vs intradiário** — maior área não testada (campanhas foram 100% intraday).
   EXIGE Eduardo relaxar a regra "nunca overnight" + contabilidade de rolagem no P&L.
3. **Última hora (fluxo MOC)** — dia→última hora; fronteira do refutado, spec precisaria
   blindagem extra contra o closeout da campanha 1.
4. **Dias de macro US (FOMC/CPI/payroll)** — eco do 08:30 ET é visível (campanha 2 A1);
   exige montar calendário de eventos externo.
5. **WDO/câmbio × prêmio USDT/BRL (cross-market)** — adiada: aguarda semanas de histórico
   do Basis Observer (VPS) versionadas no repo.

## Limitações de ambiente (sessão cloud/mobile)

- Sem acesso à VPS (Tailscale) nem ao MT5 — dados novos só em sessão no notebook do Eduardo.
- Tudo necessário pra pesquisa de barras está NESTE repo.
