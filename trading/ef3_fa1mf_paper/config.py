"""EF3 F-A.1.M.f paper trader — configuration.

Strategy: 30-bar H4 breakout + SMA200 Daily + ATR14×2.0 trailing stop
"""
from __future__ import annotations

import os
from pathlib import Path

# paths
DATA_DIR = Path(os.environ.get("FA1_DATA_DIR", "/data"))
LOG_DIR  = Path(os.environ.get("FA1_LOG_DIR",  "/logs"))

# exchange
BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL       = os.environ.get("FA1_SYMBOL", "BTCUSDT")
STRATEGY_ID  = os.environ.get(
    "FA1_STRATEGY_ID",
    "ef3_fa1mf_30h4_sma200_atr14x2_trailing_v1",
)

# position sizing (1x, no leverage — 1 unit of equity per trade)
INITIAL_CAPITAL  = float(os.environ.get("FA1_CAPITAL",      "1000.0"))
FEE_RT_BPS       = float(os.environ.get("FA1_FEE_RT_BPS",   "12.0"))   # round-trip bps
SLIPPAGE_ONE_BPS = float(os.environ.get("FA1_SLIPPAGE_BPS",  "1.0"))   # one-side bps
SLIPPAGE_ONE_SIDE = SLIPPAGE_ONE_BPS / 10_000.0

# strategy parameters
N_BREAKOUT = int(os.environ.get("FA1_N_BREAKOUT", "30"))
ATR_WIN    = int(os.environ.get("FA1_ATR_WIN",    "14"))
ATR_MULT   = float(os.environ.get("FA1_ATR_MULT", "2.0"))
SMA_WIN    = int(os.environ.get("FA1_SMA_WIN",    "200"))

# bars to fetch
H4_LOOKBACK = int(os.environ.get("FA1_H4_LOOKBACK", "400"))
D1_LOOKBACK = int(os.environ.get("FA1_D1_LOOKBACK", "310"))

# health server
HEALTH_PORT = int(os.environ.get("FA1_HEALTH_PORT", "8102"))

# telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
TELEGRAM_TAG       = os.environ.get("TELEGRAM_TAG",       "EF3FA1").strip()
