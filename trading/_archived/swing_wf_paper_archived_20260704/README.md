# swing_wf_paper — Swing Walk-Forward Paper Trader

Paper-trades the **validated 4h walk-forward portfolio** (BTC/ETH/SOL) researched in
`/home/agent/research/swing_study/`. $1000 total, split into three equal $333.33 sleeves,
unleveraged (1x). No real orders — virtual money.

## How it works
- **Universe / TF:** BTCUSDT, ETHUSDT, SOLUSDT on 4h closed candles (Binance REST, no DB).
- **Walk-forward selection:** every `REOPT_BARS` (540 ≈ 90d) closed bars, each sleeve re-optimizes
  over the trailing `TRAIN_BARS` (2160 ≈ 360d) window, picking the config with the best train
  Sharpe (subject to ≥10 trades, PF≥1.05). Same logic as the research walk-forward.
- **Config space:** signal-exit strategies only (ema_cross, supertrend, macd_rsi, donchian,
  keltner, roc_mom, bb/rsi only when signal-exit). Stop-based exits are excluded because the
  live state machine can only reproduce `run_backtest` **exactly** for signal exits
  (proven in `verify_fidelity.py`: 100% side-match, corr 1.0000).
- **Execution (faithful to the backtest):** signal on the just-closed 4h bar → fill at the
  **open of the next bar** (the in-progress bar's real open), tick-rounded with adverse slippage.
  Stop/target exits (if ever enabled) fill at the stop price honouring gap opens.
- **Costs:** 4 bps fee + 2 bps slippage per side, plus **real Binance funding** charged per 8h
  over each holding window (long pays positive funding, short receives).

## Monitoring (OpenClaw / Telegram)
Control notifications via `TRADING_BOT_TOKEN`: startup, re-optimization picks, every open/close,
daily status (08:00 UTC), drawdown alert (>20%), consecutive-loss alert, errors. The
`trading_watcher.py` cron (*/10) also pings `/healthz` and alerts if the container goes down.

## Run
```bash
# build + start
cd /home/agent/trading-os
docker compose build trading-swing-wf-paper && docker compose up -d trading-swing-wf-paper

# health / state
curl localhost:8105/healthz
docker exec trading_swing_wf_paper python -m trading.swing_wf_paper.main --status

# one cycle (smoke test) / force re-optimization
docker exec trading_swing_wf_paper python -m trading.swing_wf_paper.main --once
docker exec trading_swing_wf_paper python -m trading.swing_wf_paper.main --once --reopt

# fidelity check (live engine vs research backtest)
PYTHONPATH=/home/agent/trading-os python3 trading/swing_wf_paper/verify_fidelity.py
```

## Files
`config.py` env-driven config · `indicators.py` / `strategies.py` / `backtest.py` vendored from
research · `exchange.py` Binance REST + tick/lot rounding · `engine.py` WF selection +
live state machine · `notifier.py` Telegram · `main.py` loop/health/persistence ·
`verify_fidelity.py` parity test. State in `data/state.json`; `data/trades.csv`, `data/equity.csv`.

## Expected performance (signal-exit space, walk-forward OOS, true MtM)
base 2.74%/mo · PF 2.18 · Sharpe 1.05 · maxDD -27%. Worst case (2× cost + funding):
2.36%/mo · PF 1.90. Past simulation; not a guarantee.
