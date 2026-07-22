# b3_factory_v0 — Relatório final (2026-07-22)

Mandato: PF≥1.2, maxDD≤20%, Sharpe>1, ≥3%/mês. 2 rodadas de workflow,
11 workers, ~774k tokens de subagentes. OOS 2025+ dos futuros reais:
**jamais tocado por nenhum worker** (verificado nos logs de cada um).

## Veredito honesto contra as metas

| Meta | Estrutura 1 | Estrutura 2 | Estrutura 3 |
|---|---|---|---|
| PF ≥ 1.2 | ✅ 2.7 (mensal) | ✅ 1.7-2.7 | ✅ 1.48-1.77 |
| maxDD ≤ 20% | ✅ −5.9% (1×) / −17.6% (3×) | ✅ −6.5 a −9.7% | ✅ (PnL diário pequeno) |
| Sharpe > 1 | ✅ 1.29-1.37 | ❌ 0.66-0.77 por perna | n/a (intraday, não medido) |
| ≥ 3%/mês | ❌ 0.5% (1×) / ~1.6% (3×) | ❌ ~0.5%/mês | ❌ expectancy pequena |

**Nenhuma estrutura honesta atinge 3%/mês com maxDD≤20%.** O construtor
de portfólio varreu alavancagem 1-5× em 3 configurações: zero células
viáveis. O melhor Sharpe do período comum 2023-24 (2.97) vem de um regime
anormalmente calmo — o mesmo par de streams no histórico 2000-2024 tem
maxDD −29% a 1× (e −146% a 5×). Com os edges validados até aqui, o
honesto entregável é **~1 a 1.7%/mês a 2-3× com DD 12-18%**, em cima da
Selic da margem. Chegar a 3%/mês com DD≤20% exigiria Sharpe sustentado
≥2.5 — hoje só aparece em janelas curtas/calmas.

## As 3 estruturas (prontas para os próximos gates)

### E1 — Carteira TSM multi-ativos B3 (a estrela)
Sleeves {WDO, DI1, BGI, CCM, ICF, WIN}, estado=sinal(ret L), L∈{63,126}
ambos ok (sem pico de parâmetro), F=21, vol-target 10%/sleeve, equal-weight.
2021-07→2024-12: **+6.2-7.0% a.a. líquido, Sharpe 1.29-1.37, maxDD −5.9%,
PF mensal 2.7**; IC bootstrap do retorno [0.6%, 13.0%], 98.1% dos resamples
positivos. Motor real: DI1 (Sharpe solo 1.33) + commodities agro + WDO;
WIN/IND têm TSM negativo e só entram pela descorrelação.
Limitações: 3.5 anos, ~41 rebalances/sleeve, sem split de sub-período formal.
**Próximo gate: walk-forward + holdout 2025+ (uma vez) → paper.**

### E2 — WDO dual-engine (TSM + carry condicional)
Perna 1: TSM L=126/F=21 vol-target (real-check: +6.7% a.a., Sharpe 0.66,
maxDD −9.7%, n=35). Perna 2: short-USD quando diff CDI−FFR>6pp e vol<med
(consistente nas 3 janelas; real-check +5.7% a.a., Sharpe 0.77, maxDD −6.5%,
PF 2.7, n=20). Corr entre pernas 0.12 full / 0.36-0.45 recente.
Limitações: n pequeno nas duas pernas; vizinho >4pp do carry falha.
**Próximo gate: mais amostra (walk-forward proxy 26a por perna) + holdout.**

### E3 — Fade de nível WIN M5 (intraday, custo novo)
Limit no 1º toque de PDH/PDL, stop 25 bps, TP 10 bps, time-stop 60min,
custo 1.5 bps: **PF 1.77 (disc 2023, n=230) / 1.48 (confirm 2024, n=235)**
— único candidato com n>400. Limitações GRAVES: célula vencedora muda
entre M5 e M15 (fragilidade de grade) e o backtest assume fill no toque
(otimista por construção — adverse selection real reduz PF).
**Próximo gate: paper trading na demo MT5 via bridge (valida fill de
verdade), NUNCA direto pro real.**

### Stream complementar: BTC 2C v2 (já em produção, fora da B3)
PF 2.7, Sharpe ~6, DD −0.95% (2023-24, n=78). Componente de portfólio
descorrelacionado já validado em live.

## Refutados nesta fábrica (não revisitar sem tese nova)
TSM em WIN/IND standalone (72/72 células negativas; confirmado no
multi-ativos: Sharpe −1.0); sweep de liquidez SMC diário (WIN e WDO,
32 células); pullback RSI2+tendência (colapsa nos futuros reais);
carry incondicional; TOM/DOW.

## ADENDO — Holdout 2025-01→2026-07 (tiro único, 2026-07-22)

Código validado primeiro contra os números 2021-24 (4/4 dentro de ±15%).
Resultados (gates do HOLDOUT_PREREG.md, sem retuning):

| Estrutura | Holdout | Detalhe |
|---|---|---|
| **E1 carteira multi-ativos** | **FAIL — arquivada** | L126 −3.7% aa (Sharpe −0.67), L63 ~0. Causa: agro morreu em 2025+ (ICF −18%, BGI/CCM negativos). 3ª confirmação do padrão "trend daily morre no regime novo". |
| **E2a TSM WDO** | **PASS** | +3.3% aa, Sharpe 0.36, maxDD −8.5%, PF 1.32 |
| **E2b Carry cond. WDO** | **PASS** | +7.0% aa, Sharpe 0.77, maxDD −6.6%, PF 2.68 (n=12 — pequeno) |
| **E3 Fade WIN M5** | **PASS** | PF líq 1.51, n=350, WR 81.7%, exp +2.3 bps/trade; decaindo (2025: 1.73 → 2026: 1.22) |

Instrumentação final em paper (demo XP): task `B3_Paper_E2_WDO_TSMCarry`
(09:05, TSM WDO + carry netados) e `B3_Paper_E3_FadeWIN` (08:55-17:40,
limites em PDH/PDL medindo fill real). E1 removida da instrumentação.

## Artefatos
Grid completos por worker em b3_factory_v0/W*/ e R*/; séries diárias dos
streams; matriz de correlação; 14 séries novas coletadas (IND/DOL/DI1/
BGI/CCM/ICF/BIT × {ADJ, N}) em mt5_history/.
