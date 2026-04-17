# m1_eth — Autópsia

**Data:** 2026-04-17
**Decisão:** Arquivado (role: ARCHIVED)
**Justificativa:** Expectancy estruturalmente negativa confirmada por dois backtests independentes.

## Backtest v1 (2026-04-17, resample inicial)

- N trades: 142
- Período: 2023-01-01 → 2026-03-19
- WR: 28.87%
- Break-even WR (c/ custo): 52.48%
- Gap: -23.61 p.p.
- Net PnL: -$97.74
- Cost/gross: 415%

## Backtest v2 (2026-04-17, resample produção-fiel, ATR corrigido)

- N trades: 502
- WR: 30.88%
- Break-even WR (c/ custo): 49.19%
- Gap: -18.32 p.p.
- Net PnL: -$287.78
- Cost/gross: 409%

## Diagnóstico

1. **SL estrutural em compressão estreita** produz distâncias de 0.56% média, ~2× ATR — dentro do ruído normal do ativo.
2. **Custo 32 bps round-trip consome 4x o gross PnL.** Setup gera gross positivo mínimo, mas é aniquilado pelos custos.
3. **8.8% dos trades stopam em ≤2 barras** — sinal de que SL cai dentro de volatilidade normal sem movimento direcional contra.
4. **44.6% timeout** — breakout frequentemente não é breakout.
5. **WR empírico 30.9% significativamente abaixo de coinflip (50%)** — o filtro de compressão seleciona *contra* o setup.

## Lições

- Setups em TF ≤ 15m com SL < 1.5× ATR não são viáveis com custos reais.
- Backtest inicial com ATR restritivo subestima N (142 vs 502 do v2), mas o diagnóstico qualitativo foi preservado.
- LLMs tendem a produzir setups que parecem rigorosos em microestrutura mas ignoram custo real de execução.

## Referências

- `~/m_engines_audit/results/m1_eth/report.json`
- `~/m_engines_audit/results/m1_eth/trades.csv`
- Commit de arquivamento: 8593dec
