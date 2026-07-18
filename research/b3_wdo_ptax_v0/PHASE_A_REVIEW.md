# b3_wdo_ptax_v0 — Code review da Fase A

**Revisor (análise):** Fable. **Implementador (mecânico):** Sonnet. **Data:** 2026-07-18.
**Arquivo revisado:** `map/build_ptax_map.py`.
**Veredito: APROVADO SEM CORREÇÕES** — primeira campanha em que o review não achou bug
material (as lições das campanhas 1-2 foram incorporadas no prompt mecânico e na spec).

## Checklist crítico

| Item | Status |
|---|---|
| Filtro IS `datetime_b3 < 2025-01-01` no load; réplica M5 dentro do IS | ✅ |
| `bar(T)` = 1ª barra com `time >= T` e `< T+15min`; sem `==`/contagem | ✅ |
| Scans varrem a janela inteira (lição do A5 da campanha 2) | ✅ n/a-safe |
| Definições de ret idênticas à spec (into/during/after, am_prefix, pos_fix) | ✅ |
| TR da 1ª barra = high−low (sem contaminação de gap) | ✅ |
| fixing_eom/eom_offset do calendário de pregões M15 do próprio instrumento | ✅ |
| Nenhuma estatística atravessa overnight de roll (tudo intradiário) | ✅ |
| Quartas de Cinzas excluídas; sessão = primeira/última barra | ✅ |

## Reconciliação independente (reimplementação mínima, dados crus)

| Número | Script | Recomputado | Match |
|---|---|---|---|
| P4 corr fixing WDO | 0.2305 (n=42) | 0.2305 (n=42) | exato |
| P4 corr normal WDO | 0.0683 (n=818) | 0.0683 (n=818) | exato |
| P2 ratio_tr 10:00 WDO | 1.069 (n=860) | 1.069 (n=860) | exato |

## Verificações de consistência

- n sessões 860/860; n fixing 42/42; janelas ausentes = 0 em todas as 4 × 2 ativos (M15).
- Overlap fixing×roll: WDO 41/42 (o 1 faltante é 2024-12-30, borda final do IS — o
  algoritmo de roll precisa observar o mês seguinte; estrutural, não é bug); WIN 0/42
  (correto: WIN vence em meados de meses pares).
- 5 anomalias, todas M5 (aberturas tardias já conhecidas da campanha 2); M15 limpo.

## Notas menores (sem correção)

- Réplica M5 do P2 usa só a 1ª barra 5m da janela de 10min (a 2ª barra 5m fica de fora
  do numerador). Aceitável como réplica de formato; o P1 M5 mostra o perfil completo.
- `n_pos`/`n_neg` dos condicionais contam linhas antes de dropar NaN do alvo — sem efeito
  prático aqui (0 janelas ausentes no M15).
- Coluna extra `absret_viz_media_w` no CSV além da lista da spec — necessária p/ o
  denominador do P2; documentada no docstring. OK.
