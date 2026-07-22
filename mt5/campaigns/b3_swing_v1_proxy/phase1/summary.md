# b3_swing_v1_proxy -- Fase 1: resultado (executor Sonnet)

Pre-registro: `PREREG_PHASE1.md`. Mecanica TSM WDO, L=126 travado, F in {5,21}, sizing in {1x fixo, vol-target 10%aa cap 3x}, construcao in {proxy, ADJprop real}.

## Sanity check vs ancora da Fase 0

- Ancora (`phase0_closeout.md`): CONFIRM 2019-2024, WDO TSM L=126 F=5, spread up-down = **+3.55 bps/dia** (replicacao independente Fable). `build_proxy_map.py` (execucao original) deu +3.551 bps/dia -- ambos concordam.
- Minha reproducao (mesma construcao exata do `independent_check.py`: grid nao-sobreposta, preco reconstruido localmente dentro do split CONFIRM, sem delay de execucao, sem custos): n_grid=273 spread=**+3.551 bps/dia**
- Divergencia relativa: **0.0%** (limite 10%) -> PASS, prossegui com o walk-forward

## Gates por config (proxy, 2002-2024, custo=6bps)

| config | F | sizing | G1 WF%pos | G2 Sharpe | G3 maxDD | G4 tercos | G5 consist. ADJprop | G6 bootstrap | TODOS OS GATES |
|---|---|---|---|---|---|---|---|---|---|
| proxy_F5_fixed_1x | 5 | fixed_1x | PASS (77%) | FAIL (0.65) | FAIL (-33.6%) | PASS | PASS (81% sinal) | PASS | **FAIL** |
| proxy_F5_voltarget_10pct_cap3x | 5 | voltarget_10pct_cap3x | PASS (82%) | FAIL (0.77) | FAIL (-23.9%) | PASS | PASS (81% sinal) | PASS | **FAIL** |
| proxy_F21_fixed_1x | 21 | fixed_1x | PASS (86%) | FAIL (0.69) | FAIL (-43.1%) | PASS | FAIL (77% sinal) | PASS | **FAIL** |
| proxy_F21_voltarget_10pct_cap3x | 21 | voltarget_10pct_cap3x | PASS (91%) | FAIL (0.80) | FAIL (-31.0%) | PASS | FAIL (77% sinal) | PASS | **FAIL** |

## Full-period (2002-2024, proxy) nos 3 niveis de custo

| config | net_ann 0bps | net_ann 3bps | net_ann 6bps | sharpe 0bps | sharpe 3bps | sharpe 6bps | maxdd 6bps |
|---|---|---|---|---|---|---|---|
| proxy_F5_fixed_1x | +10.35% | +9.78% | +9.21% | 0.72 | 0.68 | 0.65 | -33.6% |
| proxy_F5_voltarget_10pct_cap3x | +10.04% | +9.34% | +8.65% | 0.88 | 0.83 | 0.77 | -23.9% |
| proxy_F21_fixed_1x | +10.90% | +10.41% | +9.93% | 0.75 | 0.72 | 0.69 | -43.1% |
| proxy_F21_voltarget_10pct_cap3x | +10.56% | +10.02% | +9.48% | 0.88 | 0.84 | 0.80 | -31.0% |

## Construcao ADJprop (2021-2024, referencia -- sem walk-forward, regra 5)

| config | net_ann 6bps | sharpe 6bps | maxdd 6bps | n_rebal |
|---|---|---|---|---|
| adjprop_F5_fixed_1x | +1.09% | 0.15 | -26.3% | 146 |
| adjprop_F5_voltarget_10pct_cap3x | +1.91% | 0.24 | -16.4% | 146 |
| adjprop_F21_fixed_1x | +3.38% | 0.32 | -15.0% | 34 |
| adjprop_F21_voltarget_10pct_cap3x | +5.95% | 0.63 | -10.5% | 34 |

## Walk-forward resumido (22 janelas de 2 anos, 2002-2003 -> 2023-2024)

| config | % janelas net>0 | n janelas |
|---|---|---|
| proxy_F5_fixed_1x | 77.3% | 22 |
| proxy_F5_voltarget_10pct_cap3x | 81.8% | 22 |
| proxy_F21_fixed_1x | 86.4% | 22 |
| proxy_F21_voltarget_10pct_cap3x | 90.9% | 22 |

## Holdout (2025-01-01 -> 2026-07-17)

**Holdout NAO ABERTO.** Nenhuma config proxy passou os 6 gates do walk-forward -- por regra do pre-registro, o holdout permanece intocado.

## Ressalvas

- Decisoes metodologicas nao especificadas literalmente no pre-registro (custo RT-equivalente para sizing continuo, escala da rolagem por tamanho de posicao, timing exato do custo de transacao) estao documentadas no topo de `run_phase1.py` (blocos D1-D10). Nenhuma foi ajustada apos ver os resultados -- foram fixadas antes de rodar o walk-forward.
- Gates avaliados apenas no nivel de custo 6 bps, conforme regra 3 do pre-registro ("gate no 6"); 0 e 3 bps sao so sensibilidade.
- ADJprop-construction (2021-2024) nao passa pelo walk-forward de 22 janelas nem pelos gates 1-4/6 -- serve so de referencia e para o gate 5, conforme regra 5 do pre-registro.
- Veredito de promocao NAO e deste script. Revisao (Fable) e decisao de governanca (Eduardo, via Manifesto) sao os proximos passos.

## Log completo

```
==============================================================================
b3_swing_v1_proxy -- Fase 1: mecanica TSM WDO L=126, custos, walk-forward
==============================================================================

proxy_work: n=6190 range=2000-01-04 -> 2024-12-30
proxy_holdout (EMBARGADO): n=385 range=2025-01-02 -> 2026-07-17
adj_work: n=862 range=2021-07-20 -> 2024-12-30
adj_holdout (EMBARGADO): n=385 range=2025-01-02 -> 2026-07-17

==============================================================================
SANITY CHECK vs ancora da Fase 0
==============================================================================
Fase 0 (phase0_closeout.md, replicacao independente): CONFIRM 2019-2024 L=126 F=5 spread up-dn = +3.55 bps/dia
Fase 0 (build_proxy_map.py execucao original): CONFIRM spread = +3.551 bps/dia (n_grid=273)
Minha reproducao (mesma construcao: grid nao-sobreposta, preco local ao split, sem delay de execucao, sem custos): n_grid=273 n_up=126 n_dn=147 spread = +3.551 bps/dia
Divergencia relativa vs ancora (3.55): 0.0%
SANITY PASS (limite 10% de divergencia relativa)

==============================================================================
FULL-PERIOD + WALK-FORWARD + GATES (por config)
==============================================================================

--- proxy_F5_fixed_1x ---
  cost=0bps full-period 2002-2024: net_ann=+10.35% sharpe=0.72 maxdd=-31.08% n_rebal=1138
  cost=3bps full-period 2002-2024: net_ann=+9.78% sharpe=0.68 maxdd=-32.35% n_rebal=1138
  cost=6bps full-period 2002-2024: net_ann=+9.21% sharpe=0.65 maxdd=-33.59% n_rebal=1138
  GATE1 (% janelas WF net>0 >=70%): 77.3% (17/22) -> True
  GATE2 (Sharpe full-period >=0.8): 0.65 -> False
  GATE3 (maxDD full-period >= -15%): -33.59% -> False
    terco 2002-2009: net_ann=+19.69% (n_rebal=396)
    terco 2010-2016: net_ann=+5.46% (n_rebal=346)
    terco 2017-2024: net_ann=+2.72% (n_rebal=396)
  GATE4 (net>0 em cada terco): -> True
  GATE6 (bootstrap IC95 net anual exclui 0): point=+10.73% IC95=[+3.40%,+18.30%] n_blocks=1138 -> True

--- proxy_F5_voltarget_10pct_cap3x ---
  cost=0bps full-period 2002-2024: net_ann=+10.04% sharpe=0.88 maxdd=-21.87% n_rebal=1138
  cost=3bps full-period 2002-2024: net_ann=+9.34% sharpe=0.83 maxdd=-22.88% n_rebal=1138
  cost=6bps full-period 2002-2024: net_ann=+8.65% sharpe=0.77 maxdd=-23.89% n_rebal=1138
  GATE1 (% janelas WF net>0 >=70%): 81.8% (18/22) -> True
  GATE2 (Sharpe full-period >=0.8): 0.77 -> False
  GATE3 (maxDD full-period >= -20%): -23.89% -> False
    terco 2002-2009: net_ann=+15.97% (n_rebal=396)
    terco 2010-2016: net_ann=+7.16% (n_rebal=346)
    terco 2017-2024: net_ann=+3.03% (n_rebal=396)
  GATE4 (net>0 em cada terco): -> True
  GATE6 (bootstrap IC95 net anual exclui 0): point=+9.51% IC95=[+3.96%,+15.12%] n_blocks=1138 -> True

--- proxy_F21_fixed_1x ---
  cost=0bps full-period 2002-2024: net_ann=+10.90% sharpe=0.75 maxdd=-41.40% n_rebal=270
  cost=3bps full-period 2002-2024: net_ann=+10.41% sharpe=0.72 maxdd=-42.24% n_rebal=270
  cost=6bps full-period 2002-2024: net_ann=+9.93% sharpe=0.69 maxdd=-43.06% n_rebal=270
  GATE1 (% janelas WF net>0 >=70%): 86.4% (19/22) -> True
  GATE2 (Sharpe full-period >=0.8): 0.69 -> False
  GATE3 (maxDD full-period >= -15%): -43.06% -> False
    terco 2002-2009: net_ann=+18.99% (n_rebal=94)
    terco 2010-2016: net_ann=+7.67% (n_rebal=83)
    terco 2017-2024: net_ann=+3.42% (n_rebal=93)
  GATE4 (net>0 em cada terco): -> True
  GATE6 (bootstrap IC95 net anual exclui 0): point=+11.46% IC95=[+3.99%,+19.30%] n_blocks=270 -> True

--- proxy_F21_voltarget_10pct_cap3x ---
  cost=0bps full-period 2002-2024: net_ann=+10.56% sharpe=0.88 maxdd=-29.48% n_rebal=270
  cost=3bps full-period 2002-2024: net_ann=+10.02% sharpe=0.84 maxdd=-30.24% n_rebal=270
  cost=6bps full-period 2002-2024: net_ann=+9.48% sharpe=0.80 maxdd=-30.99% n_rebal=270
  GATE1 (% janelas WF net>0 >=70%): 90.9% (20/22) -> True
  GATE2 (Sharpe full-period >=0.8): 0.80 -> False
  GATE3 (maxDD full-period >= -20%): -30.99% -> False
    terco 2002-2009: net_ann=+14.48% (n_rebal=94)
    terco 2010-2016: net_ann=+9.38% (n_rebal=83)
    terco 2017-2024: net_ann=+4.78% (n_rebal=93)
  GATE4 (net>0 em cada terco): -> True
  GATE6 (bootstrap IC95 net anual exclui 0): point=+10.60% IC95=[+4.60%,+17.47%] n_blocks=270 -> True

==============================================================================
GATE 5 -- consistencia proxy vs ADJprop (overlap 2021-2024)
==============================================================================
  proxy_F5_fixed_1x: overlap n_dias=862 sign_match=81.3% net_ann_ADJprop_overlap=+1.09% -> GATE5=True
  proxy_F5_voltarget_10pct_cap3x: overlap n_dias=862 sign_match=81.3% net_ann_ADJprop_overlap=+1.91% -> GATE5=True
  proxy_F21_fixed_1x: overlap n_dias=862 sign_match=76.8% net_ann_ADJprop_overlap=+3.38% -> GATE5=False
  proxy_F21_voltarget_10pct_cap3x: overlap n_dias=862 sign_match=76.8% net_ann_ADJprop_overlap=+5.95% -> GATE5=False

==============================================================================
Construcao ADJprop (2021-2024) -- referencia full-period, SEM walk-forward (regra 5)
==============================================================================
  adjprop_F5_fixed_1x: net_ann(6bps)=+1.09% sharpe=0.15 maxdd=-26.25% n_rebal=146 (2021-2024, ref. p/ gate5)
  adjprop_F5_voltarget_10pct_cap3x: net_ann(6bps)=+1.91% sharpe=0.24 maxdd=-16.36% n_rebal=146 (2021-2024, ref. p/ gate5)
  adjprop_F21_fixed_1x: net_ann(6bps)=+3.38% sharpe=0.32 maxdd=-15.04% n_rebal=34 (2021-2024, ref. p/ gate5)
  adjprop_F21_voltarget_10pct_cap3x: net_ann(6bps)=+5.95% sharpe=0.63 maxdd=-10.52% n_rebal=34 (2021-2024, ref. p/ gate5)

salvo: C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy\phase1\results.csv (8 linhas)
salvo: C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy\phase1\wf_windows.csv (88 linhas)

==============================================================================
HOLDOUT (2025-01-01 -> 2026-07-17)
==============================================================================
Nenhuma config proxy passou os 6 gates -> HOLDOUT NAO ABERTO (permanece intocado).
```