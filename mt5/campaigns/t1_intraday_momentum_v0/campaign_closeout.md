# t1_intraday_momentum_v0 — Closeout (2026-07-21)

**Status: `premise_refuted` na Fase 0.** Nenhum backtest foi rodado (correto:
a premissa morreu antes de virar mecânica).

## O que foi testado

Tese T1 (Gao/Han/Li/Zhou JFE 2018 + replicações): retorno da primeira meia
hora prevê o da última meia hora. 3 variantes (A: intraday puro; B: c/
overnight, Gao original; C: overnight puro, Liu & Tse) × 2 mercados
(WIN, WDO), contínuas não ajustadas M15, IS 2021-07→2024-12 (859 dias
válidos), OOS 2025+ **intocado**.

## Resultado (após correção do bug de fallback — ver Code review)

| Mercado/Var | slope t_NW | hit rate (IC95) | cond. mean bps | Status |
|---|---|---|---|---|
| WIN A | 1.63 | 51.1% [48.0, 54.5] | 0.70 | FAIL G1,G2,G3,G5 |
| WIN B | 0.47 | 50.6% [47.5, 53.9] | 0.43 | FAIL |
| WIN C | −0.34 | 46.3% [43.2, 49.5] | −0.56 | FAIL |
| WDO A | 0.45 | 48.7% [45.3, 51.8] | 0.39 | FAIL |
| WDO B | 0.80 | 50.4% [47.3, 53.6] | 0.73 | FAIL |
| WDO C | 0.77 | 49.9% [46.7, 53.2] | 0.70 | FAIL |

Gates pré-registrados: G1 t_NW≥2, G2 HR≥52% c/ IC>50%, G3 |média cond|≥4bps,
G4 sinal consistente ≥3/4 anos, G5 robusto a outliers. **0/6 passou.**

## Leitura honesta

- A magnitude do efeito (0.4-0.8 bps por trade) é ~5-10× menor que o
  necessário pra cobrir custo 2-6 bps RT. Não é "quase": é ordem de grandeza.
- Único sinal estatístico residual: WIN variante A com outliers removidos
  (slope t_NW=2.96) — direção certa, magnitude economicamente morta. Mesmo
  padrão das campanhas BTC (directional_macro_window_v0): sinal valida,
  magnitude não paga o custo.
- A anomalia intraday mais replicada da literatura (SPY, FTSE, EuroStoxx,
  China, VIX, crude, RUB) **não se manifesta em WIN/WDO 2021-2024** em
  magnitude tradeável. Possíveis razões: last half-hour da B3 já é dominado
  por leilão/rolagem, e o mecanismo (rebalanceamento institucional de ETFs)
  é muito mais fraco no Brasil.

## Code review da fase (regra do projeto)

Bug encontrado e corrigido: fallback de `ret_last` usava closes de barras
adjacentes (janela de 15min em vez de 30min) nos ~47% dos dias em que a
sessão fechava 17:45 (B3 seguia o DST dos EUA no horário de fechamento até
2023; unificou 18:15 em 2024 — é calendário real, não artefato de dados).
Corrigido para close(T)/close(T−30min); re-rodado; conclusão inalterada
(ficou até mais negativa). Dias de Quarta-feira de Cinzas (3) descartados.

## Não revisitar

Sem variantes post-hoc (pré-registro). Só reabrir intraday momentum B3 com
dado novo (fluxo/OFI, leilão de fechamento, ou janela última-meia-hora
definida sobre o leilão real).

## Próximo

T2 do thesis_registry: WDO × janelas PTAX (Krohn JoF 2024).
