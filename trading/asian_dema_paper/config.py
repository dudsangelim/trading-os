"""R021-B Asian DEMA paper trader — configuration."""
from __future__ import annotations

import os
from pathlib import Path

# paths
DATA_DIR = Path(os.environ.get("ADEMA_DATA_DIR", "/data"))
LOG_DIR  = Path(os.environ.get("ADEMA_LOG_DIR",  "/logs"))

# database (jarvis postgres — candle_collector)
DB_HOST = os.environ.get("ADEMA_DB_HOST", "172.18.0.2")
DB_PORT = int(os.environ.get("ADEMA_DB_PORT", "5432"))
DB_USER = os.environ.get("ADEMA_DB_USER", "jarvis")
DB_PASS = os.environ.get("ADEMA_DB_PASS", "jarvis")
DB_NAME = os.environ.get("ADEMA_DB_NAME", "jarvis")

# strategy
SYMBOL      = os.environ.get("ADEMA_SYMBOL", "SOLUSDT")
TIMEFRAME   = "1h"
DEMA_FAST   = int(os.environ.get("ADEMA_FAST",  "13"))
DEMA_SLOW   = int(os.environ.get("ADEMA_SLOW",  "34"))
SLOPE_BARS  = int(os.environ.get("ADEMA_SLOPE_BARS", "3"))   # bars back for slope
LOOKBACK_1M = int(os.environ.get("ADEMA_LOOKBACK_1M", "200"))  # 1m candles to fetch

# Asian session hours (UTC, inclusive)
ASIAN_HOURS = set(range(0, 8))  # 00:00–07:59 UTC

# position sizing — Phase 0: $1000 nominal, no leverage
NOTIONAL    = float(os.environ.get("ADEMA_NOTIONAL", "1000.0"))
INITIAL_PNL = 0.0  # pnl tracker starts at 0

# costs: 13 bps round-trip
FEE_RT      = float(os.environ.get("ADEMA_FEE_RT", "0.0013"))  # 13 bps RT

# Phase 0 gate thresholds
PHASE0_MIN_TRADES   = int(os.environ.get("ADEMA_PHASE0_MIN_TRADES", "30"))
PHASE0_MIN_WR       = float(os.environ.get("ADEMA_PHASE0_MIN_WR",   "0.22"))
CONSEC_LOSS_ALERT   = int(os.environ.get("ADEMA_CONSEC_LOSS_ALERT", "4"))

# health server
HEALTH_PORT = int(os.environ.get("ADEMA_HEALTH_PORT", "8095"))

# telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
TELEGRAM_TAG       = os.environ.get("ADEMA_TELEGRAM_TAG", "R021B").strip()
