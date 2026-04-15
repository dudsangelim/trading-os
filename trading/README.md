# Trading System — Jarvis Capital

Paper trading engine built on top of the Jarvis Capital infrastructure.
Execution bars are derived from `el_candles_1m` (owned by `jarvis_edgelab`).
HMM warm-up and regime context can be loaded from persisted `tr_candles_4h`
backfilled from Binance public API.

## Architecture

```
trading/
  core/
    config/       — central config (single source of truth)
    data/         — CandleReader (read-only el_candles_1m + persisted tr_candles_4h)
    engines/      — BaseEngine interface + implemented M1/M2/M3/M3-shadow engines
    execution/    — fill model (slippage) + position manager (SL/TP/timeout)
    risk/         — circuit breakers + position sizing
    storage/      — asyncpg repository for all tr_* tables
    monitoring/   — structured JSON logger + Telegram alerts + healthcheck
  apps/
    worker/       — asyncio main loop (runs every minute)
    api/          — FastAPI read-only API (7 endpoints)
  tests/          — interface + storage/position tests (asyncpg, real DB)
  Dockerfile.worker
  Dockerfile.api
  requirements.txt
  bootstrap.sh
  smoke_test.sh
```

## Database Tables (prefix `tr_`)

| Table | Purpose |
|-------|---------|
| `tr_signals` | Generated trading signals (dedup key: engine_id + bar_ts + direction) |
| `tr_signal_decisions` | ENTER / SKIP / SIGNAL_ONLY decision per signal |
| `tr_paper_trades` | Paper positions (OPEN → CLOSED_SL/TP/TIMEOUT) |
| `tr_daily_equity` | Daily bankroll tracking per engine |
| `tr_engine_heartbeat` | Worker liveness per engine |
| `tr_risk_events` | Circuit breaker firings and errors |

## Engines

| Engine | Symbol | Mode | Capital |
|--------|--------|------|---------|
| m1_eth | ETHUSDT | ACTIVE | $200 |
| m2_btc | BTCUSDT | ACTIVE | $200 |
| m3_sol | SOLUSDT | ACTIVE | $200 |
| m3_eth_shadow | ETHUSDT | SIGNAL_ONLY | $200 (simulated) |

Current engine status:
- `m1_eth`: implemented, active, HMM-filtered ETH breakout engine
- `m2_btc`: implemented, active, HMM-filtered BTC sweep/reclaim engine
- `m3_sol`: implemented, active, multi-TF SOL retest engine
- `m3_eth_shadow`: implemented, signal-only ETH shadow engine

The worker now emits structured `no_signal` telemetry with machine-readable
`reason` fields so filter/rule skips can be audited without guessing.

## Circuit Breakers (priority order)

1. `KILL_STALE_DATA` — no new 1m candle for >10min → block all engines
2. `KILL_HEARTBEAT` — loop silent >15min → critical Telegram alert
3. `CIRCUIT_DD_DIARIO` — daily PnL < -DD_MAX → pause engine until midnight UTC
4. `CIRCUIT_BANKROLL_MIN` — bankroll < minimum → pause engine indefinitely
5. `ONE_POSITION_PER_ENGINE` — engine already has OPEN position
6. `NO_LIVE_ORDER` — any real exchange API call raises `NotImplementedError`

## Warm-Up

M1 and M2 require 4h HMM warm-up. The current minimum is `100` completed 4h
bars, but operationally the worker now trains from up to `500` persisted bars
when available.

Bootstrap prints the persisted 4h inventory per symbol and marks it `OK` or
`INSUFFICIENT`.

## API Endpoints (read-only, port 8090)

```
GET /health
GET /status
  GET /positions
  GET /trades?engine_id=&limit=
  GET /signals?engine_id=&limit=
  GET /equity?engine_id=
  GET /risk-events?limit=
```

## Running

```bash
# Build and start
docker-compose up -d trading_worker trading_api

# Bootstrap (migrations + bankroll init)
docker exec trading_worker bash /app/trading/bootstrap.sh

# Backfill persisted 4h history for HMM warm-up
docker exec trading_worker python3 /app/trading/scripts/backfill_4h.py \
  --days 60 --symbols BTCUSDT ETHUSDT SOLUSDT

# Smoke test
bash trading/smoke_test.sh

# Logs
docker logs -f trading_worker

# Tests (from host, requires postgres accessible on localhost:5432)
cd /home/agent/agents/stack
TEST_DATABASE_URL=postgresql://jarvis:jarvispass@localhost:5432/jarvis \
  pytest trading/tests/ -v
```

## Temporal Rule

**FORBIDDEN:** using data from the currently open candle.
A signal for bar X is only generated after bar X is completely closed.
The worker checks which TF bars have closed since the last loop iteration.

## Telemetry

Structured worker logs include:
- `signal_generated`, `signal_decision`, `trade_opened`, `trade_closed`
- `no_signal` with explicit skip reasons such as `REGIME_MISMATCH`,
  `OUTSIDE_ENTRY_WINDOW`, `NO_PENDING_RETEST`, `NO_SWEEP_SETUP`,
  `SWEEP_DETECTED_WAITING_CONFIRMATION`

This is intended to distinguish genuine inactivity from hidden filter blocks.
The Mary/Openclaw briefing is the consumer responsible for summarising these
logs into operator-facing monitoring.

## Planned Parallel Overlay

The base trading runtime is the canonical execution path.
A separate parallel liquidity overlay is planned for comparative analysis only:

- it will observe `tr_signals` and `tr_paper_trades`
- it will persist overlay decisions and ghost trades separately
- it will not block, rewrite, or override base engine decisions

Design reference:
- `trading/docs/liquidity_overlay_architecture.md`

## Implementing an Engine

1. Edit `trading/core/engines/m2_btc.py` (or whichever engine).
2. Override `generate_signal(ctx)` — return a `Signal` or `None`.
3. Override `validate_signal(signal, ctx)` — return a `SignalDecision`.
4. Override `build_order_plan(signal, bankroll)` — return an `OrderPlan`.
5. Override `manage_open_position(position, ctx)` — return a `PositionAction`.
6. Never call any exchange API — it will raise `NotImplementedError`.
