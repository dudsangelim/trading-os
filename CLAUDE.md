# Trading OS — CLAUDE.md

## What this is

Eduardo's self-hosted trading OS ("Jarvis Capital") running on a VPS.
Two layers: **standalone paper traders** (Docker, no DB) + **core worker** (asyncio, PostgreSQL/Redis).

## Active strategies

| Container | Port | Strategy | Capital |
|---|---|---|---|
| `trading_ny_open_paper` | 8094 | NY Open 2C fade, BTCUSDT 5m | $100 / 1x |
| `trading_asian_dema_paper` | 8095 | R021-B Asian DEMA 1h (DEMA 13/34), SOLUSDT 1h | $1000 / 1x |
| `trading_dow_3legs_paper` | 8096 | DOW 3-legs skip_low, BTCUSDT perp | $50 / 1.0x |
| `trading_bw_jawcross_paper` | 8097 | BW Alligator order-flip long-only, BTCUSDT 6h | $1000 / 5x / 30% stake |
| `trading_rsi_reversion_paper` | 8093 | R021-A C1 RSI Reversion 5m (RSI14 os=10 ob=85), SOLUSDT 5m | $1000 / 1x |
| `trading_sol_burst_paper` | 8101 | R012 SOL Extreme Burst Reversal 5m (\|ret\|>2% fade), SOLUSDT 5m | $1000 / 1x |
| `trading_ny_open_mom_paper` | 8104 | NY Open Momentum Long Phase 0, BTCUSDT 5m | $1000 / 1x |
| `trading_worker` | — | m3_sol (SOLUSDT) + m3_eth_shadow (ETHUSDT SIGNAL_ONLY) | $200 each |

Health check: `GET /health` or `/healthz` on each container's port.

### ny_open_mom_paper specifics (Phase 0)
- Signal: upside break of 30m NY range where break candle closes ≥ 0.10% above the range high.
- Entry: LIMIT LONG at range high on retest (within 15 min of break).
- Exit: TP1 = entry + range/2 → partial fill + BE stop; TP2 = entry + range.
- Stop: entry × (1 − clamp(0.4 × range_pct, 0.3%, 0.8%) × 1.5) — wider than fade stop.
- Phase 0 gate: halts if WR < 40% after 50 trades; Telegram alert on halt.
- Status: `docker exec trading_ny_open_mom_paper python -m trading.ny_open_mom_paper.main --status`
- Replay: `docker exec trading_ny_open_mom_paper python -m trading.ny_open_mom_paper.main --replay-days 30`

## Standalone paper traders (`trading/ny_open_paper/`, `trading/dow_3legs_paper/`, `trading/bw_jawcross_paper/`, `trading/asian_dema_paper/`, `trading/rsi_reversion_paper/`, `trading/sol_burst_paper/`)

- No auth, no DB. REST feed from Binance public endpoints.
- State persisted to `/data/state.json` (volume-mounted).
- CSVs: `trades.csv`, `decisions.csv`, `equity_curve.csv`.
- Telegram notifications via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
- Each has a `--once` flag for smoke tests and `--replay-days N` for quick backtesting.

### dow_3legs_paper specifics
- Trigger windows: Sun/Mon/Tue/Wed/Thu 23:55 UTC (±10 min tolerance).
- ATR gate refreshes daily 00:30–01:00 UTC. `DOW_DISABLE_GATE=true` to bypass.
- State machine: `flat → long_mon → flat → long_wed → [flat | short_thu] → flat`.
- Stale-position guard: force-closes any position open >30h (missed-close recovery).

### ny_open_paper specifics
- Session: 13:30–20:00 UTC weekdays. Decision bar at 14:00 UTC.
- Frozen model in `assets/2C_v1_frozen.json` — do NOT retrain without full validation.

### bw_jawcross_paper specifics
- Symbol: BTCUSDT, timeframe: 6h (switched from SOL/1h on 2026-05-11 — see DECISIONS.md).
- Trigger: XX:02 UTC every hour; engine drops bars already processed (only 4 ticks/day actually fire signals).
- Entry: **order-flip long** — Alligator just turned bull-ordered (`lips > teeth > jaw` now, was NOT on previous closed bar). Long-only.
- Exit: first closed bar with `close < lips`. No hard SL.
- Indicators: canonical Alligator SMMA(13)+8 / SMMA(8)+5 / SMMA(5)+3.
- Position sizing: equity × 30% × 5x = 1.5× effective leverage per trade.
- Fee: 0.08% RT (Binance Futures VIP0 taker). Slippage: 2 bps/side adverse (entry+exit).
- Status: `docker exec trading_bw_jawcross_paper python3 -m trading.bw_jawcross_paper.main --status`
- Replay: `docker exec trading_bw_jawcross_paper python3 -m trading.bw_jawcross_paper.main --replay-days 30`
- Audit (current config, BTC 6h): `/home/agent/backtests/audit_bw_btc6h_engine_logic.py` — realistic-fill backtest yields +157% over 2021–2026 / 200 trades / WR 38% / max DD -49% / PF 1.38. Signal-comparison matrix in `audit_bw_signal_compare.py` confirms BTC 6h order-flip is the only profitable cell across {1h, 6h} × {order_flip, jaw_cross} × {close_lips, lo_touch_lips}.

### asian_dema_paper specifics (R021-B Phase 0)
- Trigger: polls every minute, processes new closed 1h bars.
- Entry: DEMA(13) crosses DEMA(34) + slope(DEMA_slow, 3h) confirms, during Asian hours 00:00–07:59 UTC only.
- Exit: reverse crossover, any session (positions can close during London/NY).
- Data: asyncpg → `el_candles_1m` (jarvis_postgres 172.18.0.2:5432) → resample 1h.
- Fee: 13 bps RT. Notional: $1000/trade at 1x.
- Phase 0 gate: halts if WR < 22% after 30 trades; alerts at 4 consecutive losses.
- Status: `docker exec trading_asian_dema_paper python3 -m trading.asian_dema_paper.main --status`
- Replay: `docker exec trading_asian_dema_paper python3 -m trading.asian_dema_paper.main --replay-days 30`

### sol_burst_paper specifics (R012 Phase 0)
- Trigger: polls every 60s, detects new closed 5m bars from 1m candles.
- Entry: |ret_5m| > 2% → fade direction. SHORT se ret>+2%, LONG se ret<-2%. Entry at open of first 1m candle in next 5m bar.
- Exit: TP=0.5% / SL=0.25% / Timeout=30min from entry. Checked tick-by-tick on every 1m candle high/low.
- One position at a time (cooldown == full timeout).
- Data: asyncpg → `el_candles_1m` (jarvis_postgres 172.18.0.2:5432). No 5m resample stored — aggregated on the fly per tick.
- Fee: 13 bps RT. Notional: $1000/trade at 1x. PnL tracked in bps (matches R012 backtest convention).
- Phase 0 gate: halts if WR<42% after 50 trades; alerts at 4 consecutive losses.
- Status: `docker exec trading_sol_burst_paper python -m trading.sol_burst_paper.main --status`
- Replay: `docker exec trading_sol_burst_paper python -m trading.sol_burst_paper.main --replay-days 30`
- Migrado em 2026-05-12 do processo nu em `/home/agent/backtests/edge_factory/sol_extreme_burst_reversal_5m/phase0/` (PID 2773418). Zero trades disparados nos 7 dias anteriores.

### rsi_reversion_paper specifics (R021-A C1 Phase 0)
- Trigger: polls every minute, processes new closed 5m bars (30s grace after bar close).
- Entry: RSI(14 Wilder) crosses below 10 → LONG; crosses above 85 → SHORT. At bar close.
- Exit: RSI crosses 50, any session.
- Blacklist: hours 13–15 UTC and Tuesdays (permanent from R020/R021 research).
- Data: asyncpg → `el_candles_1m` → resample 5m. DB same as asian_dema_paper.
- Fee: 13 bps RT. Notional: $1000/trade at 1x.
- Phase 0 gate: halts if WR < 30% after 30 trades; alerts at 4 consecutive losses.
- Status: `docker exec trading_rsi_reversion_paper python3 -m trading.rsi_reversion_paper.main --status`
- Replay: `docker exec trading_rsi_reversion_paper python3 -m trading.rsi_reversion_paper.main --replay-days 30`

## Core worker (`trading/core/`)

- Runs `trading_worker` container. Reads `el_candles_1m` from `jarvis_edgelab` DB.
- Active engines: `m3_sol`, `m3_eth_shadow` (SIGNAL_ONLY).
- cn1–cn5 removed 2026-04-28 (zero signals ever, missing derivative data permanently).
- m1_eth, m2_btc deleted — replaced by m3 family.

## Key rules

- Closes always execute regardless of ATR gate — gate only blocks new opens.
- `FEE_RT_PCT = 0.10` is the roundtrip fee constant; defined in `config.py`, imported by engine.
- Never mock the DB in core worker tests — use real asyncpg connections.
- Don't touch `assets/2C_v1_frozen.json` — it's the production-frozen NY Open model.

## Common commands

```bash
# Health check
curl localhost:8094/health
curl localhost:8093/healthz
curl localhost:8095/healthz
curl localhost:8096/health
curl localhost:8101/healthz
curl localhost:8104/healthz

# Smoke test (one tick)
docker exec trading_dow_3legs_paper python -m trading.dow_3legs_paper.main --once

# Replay last 14 days
docker exec trading_dow_3legs_paper python -m trading.dow_3legs_paper.main --replay-days 14

# Rebuild and restart a paper trader
docker compose build dow_3legs_paper && docker compose up -d dow_3legs_paper

# View live logs
docker logs -f trading_dow_3legs_paper
docker logs -f trading_ny_open_paper
```
