# m2_btc — Autópsia

**Data:** 2026-04-17
**Decisão:** Arquivado (role: ARCHIVED)
**Justificativa:** Expectancy estruturalmente negativa confirmada com N=3.395 trades.

## Backtest (2026-04-17)

- N trades: 3.395
- Período: 2023-01-01 → 2025-07-31 (parquet BTC trunca em julho/2025)
- WR: 24.5%
- Break-even WR (c/ custo): 61.1%
- Gap: -36.6 p.p.
- Net PnL: -$961.62
- Cost/gross: 1296%

## Caveat do backtest

Rodou **sem gate direcional** — upper bound de frequência de sinais. Com EMA filter teria menos trades, potencialmente WR melhor. Mas o gap de -36.6 p.p. é tão grande que é matematicamente implausível que filtro recupere expectancy positiva sem mudar o design.

## Diagnóstico

1. **26.8% dos trades batem SL em ≤2 barras** (911 trades) — maior taxa entre todas as engines M auditadas.
2. **SL mean 0.57%** — mesmo padrão do m1_eth, SL dentro do ruído ATR de BTC 15m.
3. **Cost/gross 1296%** — o setup perdeu ~13x o gross em custos. Gross positivo existe, mas é ínfimo vs fees+slippage.
4. **36.0% timeout** — padrão de falso breakout confirmado.

## Lições (adicionais ao m1_eth)

- A falha do m1_eth não era idiossincrática — é da família "compression breakout micro".
- Em ativo de volatilidade superior (BTC > ETH em termos absolutos), o problema se amplifica: mais trades, mais custos, expectancy ainda mais negativa.
- **Amostra de 3.395 trades elimina qualquer dúvida de significância estatística.**

## Referências

- `~/m_engines_audit/results/m2_btc/report.json`
- `~/m_engines_audit/results/m2_btc/trades.csv`
- Commit de arquivamento: 8593dec
