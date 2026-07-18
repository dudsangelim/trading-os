# b3_pair_divergence_v0 — Code review da Fase A

**Revisor (análise):** Fable. **Implementador (mecânico):** Sonnet. **Data:** 2026-07-18.
**Arquivo revisado:** `map/build_pair_map.py`.
**Veredito: APROVADO SEM CORREÇÕES.**

## Checklist crítico

| Item | Status |
|---|---|
| Filtro IS `< 2025-01-01` no load; Cinzas excluídas | ✅ |
| `bar(13:00)` por timestamp `>=`; sem `==`/contagem | ✅ |
| σ60 trailing EXCLUI t (`shift(1).rolling(60, min_periods=60)`), calendário próprio de cada ativo ANTES do join | ✅ sem look-ahead |
| t+1 = próxima linha do calendário ALINHADO; última linha descartada | ✅ |
| Combos com ret==0 excluídos e contados à parte; NaN separado | ✅ |
| Robustez Q3 com/sem pares cruzando roll day (qualquer ativo, t ou t+1) | ✅ conservador |
| Rolling corr do Q1 inclui t — documentado como estatística de estabilidade, não preditiva | ✅ aceito |
| Tudo intradiário; nenhum retorno atravessa overnight | ✅ |

## Reconciliação independente (reimplementação mínima, parquets crus)

| Número | Script | Recomputado | Match |
|---|---|---|---|
| n dias alinhados | 860 | 860 | exato |
| corr full-sample ret_day | −0.5597 | −0.5597 | exato |
| P(mesmo sinal) ret_day | 33.4% (n=854) | 33.37% (n=854) | exato |
| (−,−) → ret_day WDO t+1 | +0.0744% (n=130) | +0.0744% (n=130) | exato |

Extra do revisor: t-stat da melhor célula ((−,−)→WDO t+1) = **0.97** (std 0.88%, n=130).

## Notas menores (sem correção)

- 0 anomalias em 860×2 sessões (bar 13:00 sempre presente; sessões sempre abrem 09:00
  no M15) — consistente com as campanhas 2-3.
- Q4 células "forte" têm n=4 e n=1 — sem poder estatístico; o script reporta como está,
  correto não esconder.
