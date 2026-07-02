# btc_lead_paper — BTC→ETH lead-lag paper trader (SIGNAL_ONLY)

Paper trader da vencedora da rodada 2 do estudo single-asset 2023+ (engine
corrigido, 02/07/2026): **ETHUSDT 4h operado na direção do trend+momentum do
BTCUSDT** (`btc_lead(roc_n=40, ema_n=100)`, long+short, saída só por sinal).

Regras (sinal no close do candle 4h do BTC, execução no open do candle seguinte do ETH):
- **Long** quando `ROC(40) do BTC > 0` **e** `BTC > EMA(100) do BTC`; sai quando qualquer condição inverte.
- **Short** espelhado. Capital cheio, 1x, sem stops (saída por sinal venceu no estudo).

Números validados (2023-01→2026-07, líquido 6bps/lado + funding real, MC p=0.013):
PF 1.53 · 4,93%/mês · Sharpe 1.23 · maxDD -33% · 424 trades · todos os anos positivos.
Walk-forward só-família: 5,94%/mês 100% OOS. Ver `/home/agent/research/single_asset_2023/REPORT.md`.

## Arquitetura
Clone estrutural do `swing_wf_paper` (mesma máquina de estados de execução,
provada idêntica ao backtest de pesquisa para configs signal-exit em
`swing_wf_paper/verify_fidelity.py`), simplificado: 1 símbolo, config fixa,
sem re-otimização. Dados via Binance REST (klines spot p/ preço, fapi p/
funding e filtros de instrumento). Sem DB.

- Fill: sinal no candle fechado → open real do candle em progresso, tick-rounded, slippage adversa.
- Custos: 4+2 bps/lado + funding real somado na janela do holding (long paga funding positivo).
- Estado em `/data/state.json`; `trades.csv` + `equity.csv`.
- Telegram (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`): startup/open/close/daily 08:00 UTC/DD>20%/5 losses/erros. Tag `BTCLEAD`.

## Comandos
```bash
curl localhost:8106/healthz                 # health
curl localhost:8106/status                  # estado completo + sinal atual
docker exec trading_btc_lead_paper python -m trading.btc_lead_paper.main --status
# smoke (só com o container parado — conflito de porta):
docker compose run --rm btc-lead-paper python -m trading.btc_lead_paper.main --once
```
