"""R021-A C1 RSI Reversion paper trader — configuration."""
from __future__ import annotations

import os
from pathlib import Path

# paths
DATA_DIR = Path(os.environ.get("RSI_DATA_DIR", "/data"))
LOG_DIR  = Path(os.environ.get("RSI_LOG_DIR",  "/logs"))

# database (jarvis postgres — candle_collector el_candles_1m)
DB_HOST = os.environ.get("RSI_DB_HOST", "172.18.0.2")
DB_PORT = int(os.environ.get("RSI_DB_PORT", "5432"))
DB_USER = os.environ.get("RSI_DB_USER", "jarvis")
DB_PASS = os.environ.get("RSI_DB_PASS", "CHANGE_ME")
DB_NAME = os.environ.get("RSI_DB_NAME", "jarvis")

# strategy
SYMBOL        = os.environ.get("RSI_SYMBOL", "SOLUSDT")
TIMEFRAME     = "5m"
RSI_PERIOD    = int(os.environ.get("RSI_PERIOD", "14"))
OVERSOLD      = float(os.environ.get("RSI_OVERSOLD", "10"))   # LONG entry
OVERBOUGHT    = float(os.environ.get("RSI_OVERBOUGHT", "85")) # SHORT entry
EXIT_LEVEL    = float(os.environ.get("RSI_EXIT_LEVEL", "50")) # RSI crosses 50 → exit
LOOKBACK_1M   = int(os.environ.get("RSI_LOOKBACK_1M", "1500"))  # ~5 days 1m bars

# blacklist (permanent from R020/R021 research)
# hours 13-16h UTC (NY+London overlap) and Tuesdays
BLACKLIST_HOURS    = set(range(13, 16))  # {13, 14, 15}
BLACKLIST_WEEKDAYS = {1}                  # Tuesday

# position sizing — Phase 0: $1000 nominal, no leverage
NOTIONAL  = float(os.environ.get("RSI_NOTIONAL", "1000.0"))

# costs: 13 bps round-trip
FEE_RT    = float(os.environ.get("RSI_FEE_RT", "0.0013"))

# Phase 0 gate thresholds
PHASE0_MIN_TRADES   = int(os.environ.get("RSI_PHASE0_MIN_TRADES", "30"))
PHASE0_MIN_WR       = float(os.environ.get("RSI_PHASE0_MIN_WR",   "0.30"))
CONSEC_LOSS_ALERT   = int(os.environ.get("RSI_CONSEC_LOSS_ALERT", "4"))

# HTTP health port
HEALTH_PORT = int(os.environ.get("RSI_HEALTH_PORT", "8093"))
