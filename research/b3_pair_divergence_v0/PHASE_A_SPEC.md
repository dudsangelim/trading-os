# b3_pair_divergence_v0 — Fase A: mapa descritivo (SPEC congelada)

**Campanha 4:** par WIN×WDO — quebras da relação inversa. **Data:** 2026-07-18.
**Autor (análise):** Fable. **Fase A é MECÂNICA:** só estatísticas descritivas. Zero
backtest, zero trade, zero custo, zero PF.

## Hipótese-mãe (a especificar na Fase B, NÃO aqui)

A relação normal índice×dólar é inversa (corr intradiária ≈ −0,55, estável 4/4 anos —
campanha 2, A6): risk-on = WIN↑ + WDO↓. Dias/manhãs de MESMO sinal são quebras da
relação (fluxo anômalo: ex. rally com câmbio fraco, ou queda com real forte). Predições
mapeáveis: (i) mesmo-sinal é minoria estável; (ii) após uma quebra, a relação tende a se
recompor — na tarde do mesmo dia ou no pregão seguinte — via reversão de UM dos ativos;
(iii) o efeito cresce com a magnitude da quebra (dose-resposta). O mapa mede tudo isso
SEM montar estratégia.

## Dados e janelas

- `{WIN,WDO}_cont_N_M15.parquet`; IS `datetime_b3 < 2025-01-01` no load; quartas de
  Cinzas excluídas; sessão = primeira/última barra; roll days flagados (lógica padrão).
- `bar(T)` = 1ª barra com `time >= T` e `< T+15min` (convenção das campanhas 2-3).
- `ret_am` = open(bar(13:00)) / open(1ª barra) − 1;
  `ret_pm` = close(última barra) / open(bar(13:00)) − 1;
  `ret_day` = close(última) / open(1ª) − 1. Tudo intradiário (imune a roll).
- Alinhar WIN e WDO por data (inner join); dias sem bar(13:00) em qualquer ativo saem
  das condicionais (log).
- **Combo de sinal** de uma janela: (sign WIN, sign WDO) ∈ {(+,+), (+,−), (−,+), (−,−)};
  retorno exatamente 0 = excluído da célula (contar à parte).
- **Componente de divergência**: `z_a(t)` = ret_day do ativo a padronizado pela σ
  TRAILING de 60 pregões do próprio ativo **excluindo t** (janela t−60..t−1; primeiros
  60 pregões ficam NaN). `div(t) = z_win(t) + z_wdo(t)` — sob relação inversa, a soma
  captura o movimento comum/anômalo. Sem look-ahead: σ nunca usa t nem futuro.

## Estatísticas do mapa (por ano e agregado; n SEMPRE reportado)

- **Q1 Base rates e estabilidade:** corr rolling 60d de ret_day e de ret_pm WIN×WDO
  (média/min/max por ano); P(mesmo sinal) em ret_am e ret_day, por ano; matriz 2×2 de
  combos (freq de cada célula).
- **Q2 Quebra na manhã → tarde do MESMO dia:** para cada combo de ret_am: média/mediana
  de ret_pm de CADA ativo + P(tarde recompõe relação inversa) (= sinais de ret_pm
  opostos). Foco: (+,+) e (−,−) vs células normais. Por ano.
- **Q3 Quebra no dia t → pregão t+1 (intraday only):** para cada combo de ret_day(t):
  média de ret_day(t+1) e ret_am(t+1) de cada ativo. Autocorr lag-1 de div; média de
  div(t+1) por quintil de div(t) (dose-resposta). t+1 = pregão seguinte no calendário
  alinhado; pares atravessando roll de qualquer ativo: manter (intraday é imune) mas
  computar também a versão excluindo-os (robustez).
- **Q4 Dose-resposta na magnitude:** subdividir (+,+) e (−,−) de ret_day(t) em
  "forte" (|z_win| e |z_wdo| ambos > 1) vs "fraco" (resto): média de ret_day(t+1) por
  ativo e de div(t+1). n vai ser pequeno na célula forte — reportar, não esconder.
- **Q5 Qual ativo ajusta:** nas células (+,+) e (−,−) de dia t: comparar |média de
  ret_day(t+1)| WIN vs WDO e o SINAL (qual reverte na direção que recompõe a relação).
  Idem AM→PM (Q2). Sem juízo — juízo é da Fase B.
- **Q6 Qualidade:** n dias alinhados, dias excluídos (sem 13:00 / ret 0), roll days,
  NaN de σ60 no warm-up, anomalias padrão.

## Saídas (em `research/b3_pair_divergence_v0/map/`)

`build_pair_map.py` (auto-suficiente, paths relativos), `sessions_pair.csv` (linha por
data alinhada: rets/z/div/combos/flags dos dois ativos), `pair_map_summary.json`
(Q1–Q6), `SUMMARY.txt` legível.

## Proibições

As mesmas das campanhas 1-3: nada ≥2025-01-01; janelas por timestamp com `>=`; scans em
janela inteira; última barra como fechamento; nenhum parâmetro escolhido olhando
resultado; nenhuma simulação de trade. σ trailing NUNCA inclui o dia corrente (anti
look-ahead dentro do próprio mapa).
