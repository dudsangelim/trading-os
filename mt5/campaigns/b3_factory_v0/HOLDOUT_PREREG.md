# Holdout 2025+ — Pré-registro (2026-07-22, ANTES de rodar)

Autorização do Eduardo: "destrave os 3". Este documento fixa os gates
ANTES de qualquer cálculo no período 2025-01-01→2026-07-17 (~385 pregões,
virgem até agora). É avaliação única; não haverá segunda chance nem ajuste
pós-resultado. Implementação deve PRIMEIRO reproduzir os números conhecidos
de 2021-24 (validação do código) e só então computar o holdout.

## E1 — Carteira TSM multi-ativos ({WDO,DI1,BGI,CCM,ICF,WIN}, F=21, vt10%)
- PASS: net ann > 0 nos DOIS L (63 e 126) E Sharpe ≥ 0.5 no melhor L
  E maxDD ≤ 12% a 1×.
- PARTIAL (paper em observação, sem promoção): net > 0 em ≥1 L mas
  Sharpe < 0.5 ou DD 12-20%.
- FAIL (arquiva, não instrumentaliza): net ≤ 0 nos dois L ou DD > 20%.

## E2a — TSM WDO L=126/F=21 vt: PASS se net > 0 e DD ≤ 15%.
## E2b — Carry condicional WDO (diff>6pp, vol<med252): PASS se net > 0 e
DD ≤ 10%. (Cada perna avaliada sozinha; perna que falha sai do bot.)

## E3 — Fade PDH/PDL WIN M5 S25/T10, custo 1.5 bps
- PASS: PF líquido ≥ 1.2 no holdout (2025-01→2026-07, n esperado ~380+).
- PARTIAL: 1.0 < PF < 1.2 → paper mesmo assim (o objetivo do paper é
  validar FILL, e PF>1 marginal ainda justifica medição).
- FAIL: PF ≤ 1.0 → não instrumentaliza.

Custos: 1.5 bps RT (E1/E2 + 1.2 bps/mês rolagem; BGI/CCM/ICF 3 bps).
Nenhum parâmetro pode ser alterado em função do resultado do holdout.
