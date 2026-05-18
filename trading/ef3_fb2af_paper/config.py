"""EF3 F-B.2.A.f paper trader — configuration.

Strategy: 20-bar H4 breakout + range compression <12% + SMA200 Daily + SL 1×ATR14 + TP 8:1 fixed
"""
from __future__ import annotations

import os
from pathlib import Path

# paths
DATA_DIR = Path(os.environ.get("FB2_DATA_DIR", "/data"))
LOG_DIR  = Path(os.environ.get("FB2_LOG_DIR",  "/logs"))

# exchange
BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL       = os.environ.get("FB2_SYMBOL", "BTCUSDT")
STRATEGY_ID  = os.environ.get(
    "FB2_STRATEGY_ID",
    "ef3_fb2af_20h4_compress12_sma200_sl1_tp8_v1",
)

# position sizing (1x, no leverage)
INITIAL_CAPITAL  = float(os.environ.get("FB2_CAPITAL",      "1000.0"))
FEE_RT_BPS       = float(os.environ.get("FB2_FEE_RT_BPS",   "12.0"))
SLIPPAGE_ONE_BPS = float(os.environ.get("FB2_SLIPPAGE_BPS",  "1.0"))
SLIPPAGE_ONE_SIDE = SLIPPAGE_ONE_BPS / 10_000.0

# strategy parameters
N_BREAKOUT   = int(os.environ.get("FB2_N_BREAKOUT",   "20"))
COMP_THRESH  = float(os.environ.get("FB2_COMP_THRESH", "0.12"))
ATR_WIN      = int(os.environ.get("FB2_ATR_WIN",       "14"))
ATR_SL_MULT  = float(os.environ.get("FB2_ATR_SL_MULT", "1.0"))
TP_RR        = float(os.environ.get("FB2_TP_RR",       "8.0"))
SMA_WIN      = int(os.environ.get("FB2_SMA_WIN",       "200"))

# bars to fetch
H4_LOOKBACK = int(os.environ.get("FB2_H4_LOOKBACK", "400"))
D1_LOOKBACK = int(os.environ.get("FB2_D1_LOOKBACK", "310"))

# health server
HEALTH_PORT = int(os.environ.get("FB2_HEALTH_PORT", "8103"))

# telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
TELEGRAM_TAG       = os.environ.get("TELEGRAM_TAG",       "EF3FB2A").strip()
