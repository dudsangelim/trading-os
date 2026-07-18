# b3_ny_open_v0 — Code review da Fase A (obrigatório antes da Fase B consumir resultados)

**Revisor (papel de análise):** Fable. **Implementador (papel mecânico):** Sonnet.
**Data:** 2026-07-18. **Arquivo revisado:** `map/build_ny_map.py`.
**Veredito: APROVADO COM 1 CORREÇÃO MATERIAL (aplicada e re-rodada).**

## Checklist crítico (lições da campanha 1) — todos verificados

| Item | Status |
|---|---|
| Filtro IS `datetime_b3 < 2025-01-01` NO LOAD (OOS nunca lido) | ✅ `load_raw` linha do filtro; réplica M5 também dentro do IS |
| Janelas por TIMESTAMP com `>=`, nunca contagem de barras / `==` | ✅ `bar_at_or_after`, `anchor_mask` |
| Anchor por data via zoneinfo (America/New_York → America/Sao_Paulo) | ✅ 10:30 (EDT) / 11:30 (EST) conferidos manualmente |
| Sessão = primeira/última barra do dia, nunca relógio fixo | ✅ |
| Manhã = [1ª barra, anchor), barra-âncora excluída dos extremos da manhã | ✅ |
| Gap overnight nulificado em roll day (WIN: dia do vencto; WDO: véspera) | ✅ conservador: nulifica também o dia seguinte ao roll (aceito, documentado) |
| TR da 1ª barra sem referência ao close do dia anterior (não contamina com gap) | ✅ |
| Quartas de Cinzas excluídas | ✅ |
| Spearman manual (rank+Pearson) por scipy ausente | ✅ equivalência verificada |

Reconciliação de contagens: 860 sessões IS por ativo (2021=114, 2022=249, 2023=247,
2024=250); `gap_on` NaN = 44 no WIN (22 roll days × 2) e 83 no WDO (41×2 + borda
inicial) — batem exatamente com a regra. Zero dias sem barra-âncora no M15.

## Finding 1 (MATERIAL, corrigido): scan de quebra do A5 parava na primeira quebra

O loop original de `broke_high`/`broke_low` saía no primeiro evento de quebra: um dia em
que a máxima da manhã quebra primeiro e a mínima quebra depois registrava só `broke_high`.
Evidência no próprio output pré-fix: P_high + P_low ≈ P_any (eventos quase mutuamente
exclusivos por construção). Correção: `broke_*` = quebrou em qualquer momento pós-anchor;
`t_first_break` inalterado (primeira quebra de qualquer lado).

Impacto (WIN, geral): P(broke_high) 44,42% → **57,91%**; P(broke_low) 51,74% → **62,56%**;
false break high 27,7% → **35,9%**; low 30,7% → **37,0%**. WDO análogo. P(broke_any)
inalterado (95,58% / 92,21%), como esperado. ~26% das sessões quebram os DOIS extremos.

## Findings menores (sem correção necessária)

- Nulificação do gap dos dois lados do roll day é mais conservadora que a spec exigia
  (o gap do dia seguinte ao roll não atravessa troca de contrato). Perde ~22/41 gaps por
  ativo — irrelevante p/ mapa descritivo; manter por simetria com a contagem verificada.
- 5 anomalias de abertura tardia só no M5 (fonte secundária, usada apenas no perfil A1);
  M15 primário limpo.
- Desvio documentado: roll days derivados do calendário de pregões do M15 (cobertura
  completa) em vez do M5 truncado — julgado correto, não é desvio de lógica.
