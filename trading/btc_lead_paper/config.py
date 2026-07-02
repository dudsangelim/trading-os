"""BTC-lead paper trader — configuration.

Trades ETHUSDT 4h in the direction of BTC's trend+momentum (lead-lag), the
round-2 winner of the single-asset 2023+ study (corrected engine):
long when ROC(40) of BTC > 0 AND BTC > EMA(100) of BTC; short mirrored;
exit on signal loss / flip. Fixed config, no re-optimization, SIGNAL_ONLY
(paper fills — no real orders). Data via Binance REST (no DB dependency).

Validated (2023-01→2026-07, net 6bps/side + real funding): PF 1.53,
4.93%/mo, Sharpe 1.23, maxDD -33%, 424 trades, every year positive,
MC random-entry p=0.013. See /home/agent/research/single_asset_2023/REPORT.md.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("BLP_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("BLP_LOG_DIR", "/logs"))

# ── instrument / timeframe ───────────────────────────────────────────────────
TRADE_SYMBOL = os.environ.get("BLP_TRADE_SYMBOL", "ETHUSDT")   # what we trade
SIGNAL_SYMBOL = os.environ.get("BLP_SIGNAL_SYMBOL", "BTCUSDT") # what signals come from
TIMEFRAME = "4h"
TF_MS = 4 * 60 * 60 * 1000

# ── strategy params (frozen from the validated sweep config) ─────────────────
ROC_N = int(os.environ.get("BLP_ROC_N", "40"))
EMA_N = int(os.environ.get("BLP_EMA_N", "100"))
ALLOW_SHORT = os.environ.get("BLP_ALLOW_SHORT", "true").lower() == "true"

# ── capital / sizing ─────────────────────────────────────────────────────────
INITIAL_CAPITAL = float(os.environ.get("BLP_CAPITAL", "1000.0"))
LEVERAGE = float(os.environ.get("BLP_LEVERAGE", "1.0"))

# ── costs ────────────────────────────────────────────────────────────────────
FEE_BPS = float(os.environ.get("BLP_FEE_BPS", "4.0"))     # taker per side
SLIP_BPS = float(os.environ.get("BLP_SLIP_BPS", "2.0"))   # slippage per side
FUNDING_ENABLED = os.environ.get("BLP_FUNDING", "true").lower() == "true"

# ── data window ──────────────────────────────────────────────────────────────
# EMA(100) + ROC(40) need warm-up; fetch enough closed bars for stable values.
FETCH_BARS = int(os.environ.get("BLP_FETCH_BARS", "400"))
MIN_BARS = EMA_N + ROC_N + 60

# ── risk alerts ──────────────────────────────────────────────────────────────
DD_ALERT_PCT = float(os.environ.get("BLP_DD_ALERT_PCT", "0.20"))
CONSEC_LOSS_ALERT = int(os.environ.get("BLP_CONSEC_LOSS_ALERT", "5"))

# ── health server ────────────────────────────────────────────────────────────
HEALTH_PORT = int(os.environ.get("BLP_HEALTH_PORT", "8106"))

# ── telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TAG = os.environ.get("BLP_TELEGRAM_TAG", "BTCLEAD").strip()

# ── exchange endpoints ───────────────────────────────────────────────────────
KLINES_URL = os.environ.get("BLP_KLINES_URL", "https://api.binance.com/api/v3/klines")
FAPI_BASE = os.environ.get("BLP_FAPI_BASE", "https://fapi.binance.com")

# fallback instrument filters (used if exchangeInfo fetch fails); from fapi
TICK_FALLBACK = {"ETHUSDT": 0.01, "BTCUSDT": 0.10}
STEP_FALLBACK = {"ETHUSDT": 0.001, "BTCUSDT": 0.001}
MIN_NOTIONAL_FALLBACK = {"ETHUSDT": 20.0, "BTCUSDT": 50.0}
