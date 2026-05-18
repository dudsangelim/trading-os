# NY Open 2C Paper Trader

Fade the NY Open (13:30–20:00 UTC) first 30-minute range on BTCUSDT perp, filtered by a
logistic regression model trained offline. Current paper model is `2C_v3`
(`threshold=0.60`, `leverage_target=2x`), retrained after the candle-replay
breakeven fix.

**Status**: Phase 1 — paper trade standalone. No real orders. No registry entry.

---

## How to run

```bash
# from /home/agent/trading-os

# Build
docker compose build trading-ny-open-paper

# Start (background)
docker compose up -d trading-ny-open-paper

# Stop (isolated — does NOT affect trading_worker or other containers)
docker compose stop trading-ny-open-paper

# Logs
docker logs trading_ny_open_paper --tail=50 -f

# Health check
curl -s http://127.0.0.1:8094/healthz | python3 -m json.tool

# Replay last 30 days (smoke test)
docker exec trading_ny_open_paper \
  python -m trading.ny_open_paper.main --dry-run-replay 30
```

---

## File layout

```
trading/ny_open_paper/
├── main.py           — entry point: PaperTrader, live loop, replay mode, health server
├── engine.py         — FSM: IDLE → COLLECTING → DECIDING → WAITING_BREAK → IN_POSITION → CLOSED
├── model.py          — FrozenModel: loads JSON, exposes predict_proba()
├── features.py       — compute_context_live(): 7 contextual features via Binance REST
├── feed.py           — BinanceFeed: klines + funding rate (public endpoints only)
├── persistence.py    — CSV append + state.json helpers
├── config.py         — infrastructure constants (paths, ports, sizing)
├── requirements.txt  — numpy, pandas, requests
└── assets/
    ├── 2C_v3_frozen.json       — current frozen model (corrected BE labels)
    ├── 2C_v2_frozen.json       — previous frozen model
    ├── 2C_v1_frozen.json       — legacy frozen model
    ├── trades_v3_with_pwin.csv — v3 training oracle with predicted probabilities
    ├── freeze_2C_v3_model.py   — reproducible v3 freeze script
    ├── engine_2C_reference.py  — reference FSM used as porting source (do not modify)
    └── paper_trade_reference.py — reference paper trader (do not modify)
```

---

## Output files (mounted at /home/agent/trading-os/trading/ny_open_paper/)

| File | Description |
|------|-------------|
| `logs/run.log` | Structured text log with UTC timestamps |
| `data/trades.csv` | One row per closed trade: entry/exit/pnl/equity |
| `data/decisions.csv` | Every engine decision: armed/break_p/filled/retest_timeout |
| `data/orders.csv` | One row per armed order (outcome: filled/cancelled) |
| `data/session_log.csv` | One row per 5m candle: state + equity |
| `data/state.json` | Engine snapshot written every candle (resume after restart) |

---

## Interpreting the CSVs

### trades.csv
- `direction`: -1 = short (fade high), +1 = long (fade low)
- `pnl_pct_net`: return in %, net of fees (Binance VIP0: maker 0.02%, taker 0.05%)
- `exit_reason`: `stop` | `tp2` | `trail_stop_be` | `time` | `time_partial`

### decisions.csv
- `decision=armed` — setup valid, waiting for price to break the extreme
- `decision=break_p` — break detected; `details.p_win` + `threshold` + verdict (ACCEPT/REJECT)
- `decision=filled` — entered position after retest
- `decision=retest_timeout` — break accepted but no retest within 15 min; order cancelled

### Quick performance check
```python
import pandas as pd
df = pd.read_csv("data/trades.csv")
pnl = df["pnl_pct_net"]
wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
print(f"Trades: {len(df)}  WR: {(pnl>0).mean():.2%}")
print(f"PF: {wins.sum()/abs(losses.sum()):.2f}  Exp: {pnl.mean():+.4f}%")
print(f"Equity: ${df['equity_after'].iloc[-1]:.2f}")
```

---

## Configuration

Environment variables (set in docker-compose or .env):

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADING_BOT_TOKEN` | (empty) | Telegram bot token for trade notifications |
| `TRADING_ALLOWED_USERS` | (empty) | Comma-separated chat IDs (first one used) |
| `NY_OPEN_DATA_DIR` | `/data` | Path for CSVs inside container |
| `NY_OPEN_LOG_DIR` | `/logs` | Path for run.log inside container |
| `NY_OPEN_HEALTH_PORT` | `8092` | Port for /healthz endpoint |

Strategy parameters (window_min, break_buffer, stop settings, threshold, leverage
target, etc.) are read from `assets/2C_v3_frozen.json` at startup. Do not
hardcode them elsewhere.

## Current audit notes

- Corrected local walk-forward (`2022-01-03`–`2026-04-17`) for v3-style labels:
  `n=209`, WR `66.5%`, PF `1.75`, mean `+0.122%` at `threshold=0.60`.
- At 5x with 5 bps slippage, the same walk-forward has estimated MDD around
  `-15.5%`; v3 therefore lowers paper leverage target to `2x`.
- Keep the strategy paper-only until there is enough post-v3 live sample and
  measured fill slippage stays materially below the modeled edge.

---

## Phase 2 promotion checklist (do NOT execute until 30-day paper review)

After 30+ days of paper data, verify:

- [ ] `fill_rate = len(df[df.exit_reason != 'retest_timeout']) / n_armed >= 0.70`
- [ ] Post-v3 paper PF remains above 1.5 after measured slippage
- [ ] No critical bugs in decisions.csv (unexpected skip_no_ctx, etc.)
- [ ] Review slippage estimate: compare `entry_price` vs actual fill proxy / Binance candle open of next bar

Then:
1. Create `trading/core/engines/ny_open_2c.py` inheriting BaseEngine
2. Register in `registry.py`
3. Add to `ENGINE_CONFIGS` in `settings.py` (capital=$100, leverage from model, TF=5min)
4. Set `ENGINE_ROLES["ny_open_2c"] = SIGNAL_ONLY`
5. Check if `candle_reader.py` supports 5min TF (currently 15min only) — extend if needed
6. Rebuild trading-worker
