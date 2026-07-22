# vrp_paper — short-vol semanal Deribit/Binance (PAPER)

Vende **1 straddle ATM BTC** toda sexta ~08:05 UTC (vencimento na sexta seguinte, 08:00),
no **best bid real** da Deribit (API pública, sem chave), com **hedge de delta diário**
via perp paper ao index price (custo 6bps/ajuste). Settle no **delivery price oficial**
(fallback: index, se não publicado em 3h). Marca a mercado toda hora com tickers reais.

A variante Binance usa o mesmo motor e sinal DVOL, mas lê opções BTC/USDT da API
pública Binance, contabiliza prêmio/taxas em USDT e arredonda o paper para o lote
executável de 0,01 BTC. O estado fica isolado em `data_binance/`.

- Capital: $1000 paper, contratos fracionários (real exige mín. 0,1 contrato ≈ US$13k+).
- Sizing: `contratos = capital/S × min(0,55/DVOL, 2)` (escala por vol).
- Custos: taker 0,0003 BTC/ct (cap 12,5% do prêmio), delivery 0,00015 BTC/ct, hedge 6bps.
- Validação (research/options_study, pós-fix de look-ahead, 2022-01→2026-05):
  **2,56%/mês · PF trade 1,80 · PF mensal 2,99 · Sharpe 1,73 · maxDD -17,9% @1x**;
  OOS 2025-26: 3,38%/mês; todos os anos positivos; sobrevive custo 2x.
- Papel no portfólio-alvo 60/30/10 (com btc_lead 8106 e taker_cap 8107):
  3,40%/mês · PFmo 5,79 · Sharpe 2,07 · maxDD -10,7% (2023→2026-05).

## Comandos
```bash
curl localhost:8108/healthz            # health
curl localhost:8108/status             # estado completo
docker exec trading_vrp_paper python -m trading.vrp_paper.main --status
docker logs trading_vrp_paper --tail=30

# Binance (porta/estado próprios)
curl localhost:8112/healthz
docker exec trading_vrp_binance_paper python -m trading.vrp_paper.main --status
docker logs trading_vrp_binance_paper --tail=30
```

Estado em `data/state.json`; `trades.csv` (open/settle) e `equity.csv` (MtM horário).
Telegram tag `VRP` (startup/open/settle/daily 08:05/DD>15%/4 losses).
Na variante Binance, a tag é `VRPBINANCE`.

## Diferenças da variante Binance

- Ative com `VRP_VENUE=binance`; default continua `deribit`.
- Contrato BTC tem unidade 1 e lote/step de 0,01; com US$2 mil o sizing varia
  aproximadamente entre 0,01 e 0,06 BTC conforme DVOL (0,04 no smoke de 18/07).
- Prêmio, mark e liquidação são em USDT, não BTC.
- Entrada: 0,03% do underlying, limitada a 10% do prêmio por perna.
- Exercício: 0,015% do settlement apenas na perna ITM.
- O DVOL e o slope de superfície continuam vindo da Deribit como sinal externo;
  a execução e toda a marcação da posição vêm da Binance.

## Slope conditioning (2026-07-05 — research/options_edge2)

`volsurface.py`: amostra diária (08:05) de ATM IV 7d/30d + DVOL via mark-IV;
history em `data/slope_history.csv` (seed = série da pesquisa até 2026-05-15).
A cada entrada de sexta loga em `data/vol_signal.csv` o multiplicador de sizing
0.5/1/1.5 por tercil expanding de −z90(slope) e o fallback z-free (iv7−dvol).
Backtest (2022→2026-05): 3,27%/mês, Sharpe 1,93, maxDD -18,4% vs baseline
2,56%/1,73/-17,9%; melhor em todos os anos; custo 2x → 2,03%/mês.
LOG-ONLY por default; `VRP_SLOPE_SIZING=1` aplica o mult no sizing real.
