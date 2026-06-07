"""Configuration for EF3 R003 / F-A.1.M.f paper trader."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("FA1MF_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("FA1MF_LOG_DIR", "/logs"))

BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = os.environ.get("FA1MF_SYMBOL", "BTCUSDT")

INITIAL_CAPITAL = float(os.environ.get("FA1MF_CAPITAL", "1000.0"))
LEVERAGE = float(os.environ.get("FA1MF_LEVERAGE", "1.0"))
# round-trip cost in bps (taker fee + slippage = 12 bps per manifest §6.2)
FEE_RT_BPS = float(os.environ.get("FA1MF_FEE_RT_BPS", "12.0"))

# strategy params (manifest §4-§5)
N_ENTRY = int(os.environ.get("FA1MF_N_ENTRY", "30"))   # 30-bar H4 close breakout
ATR_LEN = int(os.environ.get("FA1MF_ATR_LEN", "14"))
ATR_MULT = float(os.environ.get("FA1MF_ATR_MULT", "2.0"))
SMA_LEN = int(os.environ.get("FA1MF_SMA_LEN", "200"))   # SMA200 Daily

HEALTH_PORT = int(os.environ.get("FA1MF_HEALTH_PORT", "8102"))

WORKER = "ef3_fa1mf"
STRATEGY_ID = "R003_FA1Mf"
