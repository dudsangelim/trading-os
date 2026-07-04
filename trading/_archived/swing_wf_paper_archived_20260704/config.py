"""Swing Walk-Forward paper trader — configuration.

Trades the validated 4h walk-forward portfolio (BTC/ETH/SOL) as paper money.
Each asset is an independent equal-weight sleeve. Every REOPT_BARS closed 4h
bars the strategy/params are re-selected on the trailing training window (the
walk-forward made live). Data comes from Binance REST (no DB dependency).
"""
from __future__ import annotations

import os
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("SWF_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("SWF_LOG_DIR", "/logs"))

# ── universe / timeframe ─────────────────────────────────────────────────────
SYMBOLS = [s.strip() for s in os.environ.get("SWF_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")]
TIMEFRAME = "4h"
TF_MS = 4 * 60 * 60 * 1000

# ── capital / sizing ─────────────────────────────────────────────────────────
INITIAL_CAPITAL = float(os.environ.get("SWF_CAPITAL", "1000.0"))   # total portfolio
LEVERAGE = float(os.environ.get("SWF_LEVERAGE", "1.0"))            # 1x = validated, unleveraged
# each symbol gets an equal sleeve of the initial capital
SLEEVE_CAPITAL = INITIAL_CAPITAL / len(SYMBOLS)

# ── costs ────────────────────────────────────────────────────────────────────
FEE_BPS = float(os.environ.get("SWF_FEE_BPS", "4.0"))     # taker per side
SLIP_BPS = float(os.environ.get("SWF_SLIP_BPS", "2.0"))   # slippage per side
FUNDING_ENABLED = os.environ.get("SWF_FUNDING", "true").lower() == "true"

# ── execution ────────────────────────────────────────────────────────────────
# MARKET: fill at the open of the next 4h bar (matches the backtest), tick-rounded
#         with adverse slippage. LIMIT: post at the bar close rounded to tick;
#         fills only if the following bar trades through it.
ORDER_TYPE = os.environ.get("SWF_ORDER_TYPE", "MARKET").upper()

# ── walk-forward selection ───────────────────────────────────────────────────
TRAIN_BARS = int(os.environ.get("SWF_TRAIN_BARS", "2160"))  # ~360d of 4h
REOPT_BARS = int(os.environ.get("SWF_REOPT_BARS", "540"))   # re-select every ~90d
MIN_TRAIN_TRADES = int(os.environ.get("SWF_MIN_TRAIN_TRADES", "10"))
MIN_TRAIN_PF = float(os.environ.get("SWF_MIN_TRAIN_PF", "1.05"))
# warm-up bars needed before TRAIN_BARS so indicators (e.g. ema200) are valid
WARMUP_BARS = int(os.environ.get("SWF_WARMUP_BARS", "250"))

# ── risk alerts ──────────────────────────────────────────────────────────────
DD_ALERT_PCT = float(os.environ.get("SWF_DD_ALERT_PCT", "0.20"))     # alert if portfolio DD breaches
CONSEC_LOSS_ALERT = int(os.environ.get("SWF_CONSEC_LOSS_ALERT", "5"))

# ── health server ────────────────────────────────────────────────────────────
HEALTH_PORT = int(os.environ.get("SWF_HEALTH_PORT", "8105"))

# ── telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TAG = os.environ.get("SWF_TELEGRAM_TAG", "SWF4H").strip()

# ── exchange endpoints ───────────────────────────────────────────────────────
KLINES_URL = os.environ.get("SWF_KLINES_URL", "https://api.binance.com/api/v3/klines")
FAPI_BASE = os.environ.get("SWF_FAPI_BASE", "https://fapi.binance.com")

# fallback instrument filters (used if exchangeInfo fetch fails); from fapi
TICK_FALLBACK = {"BTCUSDT": 0.10, "ETHUSDT": 0.01, "SOLUSDT": 0.01}
STEP_FALLBACK = {"BTCUSDT": 0.001, "ETHUSDT": 0.001, "SOLUSDT": 0.01}
MIN_NOTIONAL_FALLBACK = {"BTCUSDT": 50.0, "ETHUSDT": 20.0, "SOLUSDT": 5.0}
