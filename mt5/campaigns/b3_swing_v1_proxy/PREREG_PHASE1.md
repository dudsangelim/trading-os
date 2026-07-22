# b3_swing_v1 — Pré-registro Fase 1 (2026-07-22)

## Contexto

Fase 0 (proxy 2000-2024): TSM 126d→5d no WDO positivo em todas as
janelas e nas duas construções. Fase 1 = mecânica completa com custos e
walk-forward de avaliação. NÃO é sweep de parâmetros: L=126 vem travado
da Fase 0; as únicas variantes são as duas cadências já existentes no
projeto (F=5 da Fase 0; F=21 do E2a em paper) e dois sizings.

**Nota de contaminação declarada**: a variante F=21 vol-target já viu
2025+ no holdout do factory (PASS, +3.3% aa). O 2025+ está VIRGEM
apenas pra F=5. Qualquer leitura do holdout F=21 aqui é re-confirmação,
não descoberta.

## Dados

- `campaigns/b3_swing_v1_proxy/proxy_raw.parquet` (2000→2026; replicar
  construção da Fase 0 — ver `fetch_proxy_data.py`/`validate_proxy.py`
  e `phase0_closeout.md`).
- `B3 Futuros/mt5_history/WDO_cont_ADJprop_D1.parquet` (futuros reais,
  ~5a) como construção de consistência.
- Períodos: WF 2002→2024 (proxy). **Holdout único 2025-01→2026-07**,
  avaliado UMA vez, só se os gates de WF passarem, e por último.

## Mecânica (grade fechada: 8 configs)

Sinal no close de D: sign(close_D / close_{D−126} − 1). Execução no
close de D+1 (delay 1 pregão, conservador). Reavaliação a cada F
pregões. Sem stop intraperíodo (fiel à Fase 0).

- F ∈ {5, 21}
- Sizing ∈ {1x fixo; vol-target 10% aa com teto 3x, vol de 20 pregões}
- Construção ∈ {proxy, ADJprop real (2021+ apenas)}

## Custos

6 bps RT por mudança de posição + 6 bps por rolagem mensal enquanto
posicionado (WDO rola todo mês; manter posição = pagar o rolo).
Reportar também bruto e a 3 bps (sensibilidade), gate no 6.

## Walk-forward de avaliação (sem fitting)

Janelas de 2 anos, passo de 1 ano, 2002-2003 → 2023-2024 (22 janelas).
Por janela: net_ann, Sharpe, maxDD, n_rebal.

## Gates (avaliados antes de abrir o holdout)

1. ≥ 70% das janelas WF com net > 0 (por config candidata)
2. Sharpe líquido full-period (2002-2024, proxy) ≥ 0.8
3. maxDD full-period ≤ 15% (sizing 1x) / ≤ 20% (vol-target)
4. Net > 0 em cada terço do período (2002-09 / 2010-16 / 2017-24)
5. Consistência ADJprop 2021-2024: net > 0 e mesmo sinal de posições
   ≥ 80% dos dias vs proxy
6. Bootstrap 1000× dos retornos por bloco de rebalance: IC do net anual
   excluindo zero

Config que passar 1-6: abre holdout 2025-01→2026-07 (uma vez). Gate
final: net holdout > 0 e maxDD dentro do limite do sizing. Passou:
candidata a paper (decisão de promoção é do Eduardo via Manifesto).
Falhou WF: closeout e fim da linha proxy.

## Saídas

`phase1/`: `run_phase1.py`, `wf_windows.csv`, `results.csv`,
`holdout_result.csv` (se aberto), `summary.md`. Executor Sonnet,
code review Fable, closeout obrigatório.
