"""R012 SOL Extreme Burst Reversal paper trader — configuration."""
from __future__ import annotations

import os
from pathlib import Path

# paths
DATA_DIR = Path(os.environ.get("BURST_DATA_DIR", "/data"))
LOG_DIR  = Path(os.environ.get("BURST_LOG_DIR",  "/logs"))

# database (jarvis postgres — candle_collector el_candles_1m)
DB_HOST = os.environ.get("BURST_DB_HOST", "172.18.0.2")
DB_PORT = int(os.environ.get("BURST_DB_PORT", "5432"))
DB_USER = os.environ.get("BURST_DB_USER", "jarvis")
DB_PASS = os.environ.get("BURST_DB_PASS", "CHANGE_ME")
DB_NAME = os.environ.get("BURST_DB_NAME", "jarvis")

# strategy
SYMBOL         = os.environ.get("BURST_SYMBOL", "SOLUSDT")
BAR_MINUTES    = int(os.environ.get("BURST_BAR_MINUTES", "5"))
SIGNAL_THR     = float(os.environ.get("BURST_SIGNAL_THR", "0.020"))   # |ret| > 2%
TP_FRAC        = float(os.environ.get("BURST_TP_FRAC",    "0.005"))   # +0.5% from entry
SL_FRAC        = float(os.environ.get("BURST_SL_FRAC",    "0.0025"))  # 0.25% from entry
TIMEOUT_BARS   = int(os.environ.get("BURST_TIMEOUT_BARS", "6"))       # 6 × 5min = 30min
TIMEOUT_MINUTES = TIMEOUT_BARS * BAR_MINUTES

# lookback (1m candles to fetch each tick — must cover open trade + signal bar context)
LOOKBACK_1M    = int(os.environ.get("BURST_LOOKBACK_1M", "240"))      # ~4h

# position sizing — Phase 0: $1000 nominal, 1x (matches R021 conventions)
NOTIONAL       = float(os.environ.get("BURST_NOTIONAL", "1000.0"))

# costs: 13 bps round-trip
FEE_RT_BPS     = float(os.environ.get("BURST_FEE_RT_BPS", "13.0"))
FEE_RT         = FEE_RT_BPS / 10_000.0

# Phase 0 gate (from R012 manifest)
GATE_MIN_N        = int(os.environ.get("BURST_GATE_MIN_N",   "50"))
GATE_WR           = float(os.environ.get("BURST_GATE_WR",    "0.42"))
CONSEC_LOSS_ALERT = int(os.environ.get("BURST_CONSEC_LOSS_ALERT", "4"))

# HTTP health port
HEALTH_PORT = int(os.environ.get("BURST_HEALTH_PORT", "8101"))
