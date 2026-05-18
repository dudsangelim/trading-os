"""Configuration constants for the DOW 3-legs skip_low paper trader."""
from __future__ import annotations

import os
from pathlib import Path

# paths
DATA_DIR = Path(os.environ.get("DOW_OUTDIR", "/data"))
LOG_DIR = Path(os.environ.get("DOW_LOG_DIR", "/logs"))

# exchange
BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

# sizing (firm decision: $50 / 1.5x until 30-day review)
INITIAL_CAPITAL = float(os.environ.get("DOW_CAPITAL", "50.0"))
LEVERAGE = float(os.environ.get("DOW_LEVERAGE", "1.5"))
FEE_RT_PCT = 0.10  # roundtrip, conservative (Binance VIP0 taker)

# ATR gate parameters (validated in backtest)
ATR_LEN = 14
REGIME_WINDOW = 60
Q_LOW = 0.20
DISABLE_GATE = os.environ.get("DOW_DISABLE_GATE", "false").lower() == "true"

# health server
HEALTH_PORT = int(os.environ.get("DOW_HEALTH_PORT", "8096"))

# telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TAG = os.environ.get("TELEGRAM_TAG", "DOW3L").strip()
