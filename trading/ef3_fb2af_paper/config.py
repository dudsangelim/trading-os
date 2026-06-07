"""Configuration for EF3 R004 / F-B.2.A.f paper trader."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("FB2AF_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("FB2AF_LOG_DIR", "/logs"))

BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = os.environ.get("FB2AF_SYMBOL", "BTCUSDT")

INITIAL_CAPITAL = float(os.environ.get("FB2AF_CAPITAL", "1000.0"))
LEVERAGE = float(os.environ.get("FB2AF_LEVERAGE", "1.0"))
FEE_RT_BPS = float(os.environ.get("FB2AF_FEE_RT_BPS", "12.0"))

# strategy params (manifest §4-§5)
N_COMP = int(os.environ.get("FB2AF_N_COMP", "20"))        # 20-bar H4 lookback
COMP_THRESH = float(os.environ.get("FB2AF_COMP_THRESH", "0.12"))  # range/close < 12%
ATR_LEN = int(os.environ.get("FB2AF_ATR_LEN", "14"))
ATR_SL_MULT = float(os.environ.get("FB2AF_ATR_SL_MULT", "1.0"))   # SL = 1×ATR14
TP_RR = float(os.environ.get("FB2AF_TP_RR", "8.0"))      # TP = 8:1
SMA_LEN = int(os.environ.get("FB2AF_SMA_LEN", "200"))

HEALTH_PORT = int(os.environ.get("FB2AF_HEALTH_PORT", "8103"))

WORKER = "ef3_fb2af"
STRATEGY_ID = "R004_FB2Af"
