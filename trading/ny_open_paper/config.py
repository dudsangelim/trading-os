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
# Market data ALWAYS comes from production (public endpoints, no auth). Only order
# execution is routed to the testnet (see executor.py / set_sandbox_mode).
BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

# --- execution mode ---
# "shadow" : fills are simulated by the engine only (default — current behaviour).
# "live"   : the engine still simulates (shadow track), AND a LiveBroker mirrors the
#            engine's position + protective stop onto a REAL Binance (sub)account.
#            Requires BINANCE_<LIVE_ACCOUNT>_API_KEY/SECRET in .env. See live_broker.py.
EXECUTION_MODE = os.environ.get("NY_OPEN_EXECUTION_MODE", "shadow").strip().lower()

# --- live execution (only used when EXECUTION_MODE == "live") ---
# Which Binance (sub)account label to trade -> BINANCE_<ACCOUNT>_* env vars.
LIVE_ACCOUNT = os.environ.get("NY_OPEN_LIVE_ACCOUNT", "NY_OPEN").strip().upper()
# Fixed real notional per trade (USD), independent of the drifting paper equity.
# Kept >= $200 so it clears Binance's ~$100 min notional with headroom, and under
# the executor's hard cap. Real risk per trade ≈ notional × stop_pct.
LIVE_NOTIONAL_USD = float(os.environ.get("NY_OPEN_LIVE_NOTIONAL_USD", "240"))
# Leverage on the live account. Does NOT change strategy risk (that's notional × stop);
# it only sets how much margin is locked + the liquidation distance. 10x => margin ~10%
# of a $240 notional locks ~$24, freeing the rest, with liquidation ~10% away (far
# beyond the ~0.5-0.8% stop).
LIVE_LEVERAGE = float(os.environ.get("NY_OPEN_LIVE_LEVERAGE", "10"))

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
