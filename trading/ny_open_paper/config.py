"""
Configuration constants for the NY Open 2C paper trader.

Strategy parameters (window_min, break_buffer, etc.) come from the frozen model JSON,
not from here. This file holds infrastructure config only.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- paths ---
ASSETS_DIR = Path(__file__).parent / "assets"
MODEL_PATH = ASSETS_DIR / "2C_v3_frozen.json"
FALLBACK_MODEL_PATH = ASSETS_DIR / "2C_v2_frozen.json"
LEGACY_MODEL_PATH = ASSETS_DIR / "2C_v1_frozen.json"

DATA_DIR = Path(os.environ.get("NY_OPEN_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("NY_OPEN_LOG_DIR", "/logs"))

# --- exchange ---
BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

# --- paper trade sizing ---
INITIAL_CAPITAL = 100.0
# leverage is read from model execution_notes.leverage_target at startup; no constant here

# --- polling ---
POLL_OFFSET_SEC = 10   # seconds after 5m boundary to fetch the closed candle

# --- health server ---
HEALTH_PORT = int(os.environ.get("NY_OPEN_HEALTH_PORT", "8094"))

# --- telegram ---
TRADING_BOT_TOKEN = os.environ.get("TRADING_BOT_TOKEN", "")
TRADING_ALLOWED_USERS = os.environ.get("TRADING_ALLOWED_USERS", "")
