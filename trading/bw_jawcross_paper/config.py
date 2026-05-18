"""Alligator BTC 6h paper trader — configuration."""
from __future__ import annotations

import os
from pathlib import Path

# paths
DATA_DIR = Path(os.environ.get("BW_DATA_DIR", "/data"))
LOG_DIR  = Path(os.environ.get("BW_LOG_DIR",  "/logs"))

# exchange
BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL       = os.environ.get("BW_SYMBOL", "BTCUSDT")
TIMEFRAME    = os.environ.get("BW_TIMEFRAME", "6h")
STRATEGY_ID  = os.environ.get(
    "BW_STRATEGY_ID",
    "alligator_btc6h_order_flip_lips_long_v1",
)

# position sizing
# notional per trade = equity × STAKE_PCT × LEVERAGE
# default: 30% margin at 5x = 1.5× effective leverage on equity
# e.g. $1000 equity → $300 margin → $1500 notional
# avg win at 1.5x ≈ +1.14% equity | avg loss ≈ -0.57% equity | max loss ≈ -3.1%
INITIAL_CAPITAL = float(os.environ.get("BW_CAPITAL",   "1000.0"))
LEVERAGE        = float(os.environ.get("BW_LEVERAGE",  "5.0"))
STAKE_PCT       = float(os.environ.get("BW_STAKE_PCT", "0.30"))   # fraction of equity as margin

# fee: 0.04% per side taker (Binance Futures VIP0) → 0.08% RT
FEE_RT       = float(os.environ.get("BW_FEE_RT",       "0.0008"))  # round-trip fraction (0.0008 = 0.08%)
# slippage: market order ~2 bps per side, applied adversely.
SLIPPAGE_PCT = float(os.environ.get("BW_SLIPPAGE_PCT", "0.0002"))  # per side (0.0002 = 2 bps)

# alligator parameters (canonical Bill Williams, never change)
SMMA_JAW    = 13; JAW_SHIFT   = 8
SMMA_TEETH  = 8;  TEETH_SHIFT = 5
SMMA_LIPS   = 5;  LIPS_SHIFT  = 3
ATR_WIN     = 14

# bars to fetch for indicator warm-up + trade replay
LOOKBACK_BARS = int(os.environ.get("BW_LOOKBACK_BARS", "500"))   # ~125 days of 6h data

# health server
HEALTH_PORT = int(os.environ.get("BW_HEALTH_PORT", "8097"))

# telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
TELEGRAM_TAG       = os.environ.get("TELEGRAM_TAG",       "BWJC").strip()
