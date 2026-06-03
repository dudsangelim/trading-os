"""
Configuration for the NY Open Momentum Long paper trader (Phase 0).

Strategy: BTCUSDT 5m, upside break-retest of 30m NY open range.
Signal: break candle closes > OS_THR_PCT above the range high (strong momentum).
Entry: LIMIT LONG at range high (retest). Stop/TP derived from range size.
Phase 0 gate: halts if WR < PHASE0_WR_FLOOR after PHASE0_MIN_TRADES trades.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- exchange ---
BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = os.environ.get("NYOM_SYMBOL", "BTCUSDT")

# --- session ---
SESSION_OPEN_UTC: str = os.environ.get("NYOM_SESSION_OPEN", "13:30")
SESSION_CLOSE_UTC: str = os.environ.get("NYOM_SESSION_CLOSE", "20:00")
WINDOW_MIN: int = int(os.environ.get("NYOM_WINDOW_MIN", "30"))     # range window (minutes)

# --- signal filter ---
OS_THR_PCT: float = float(os.environ.get("NYOM_OS_THR_PCT", "0.10"))  # os_close >= this to accept

# --- stop / target sizing ---
STOP_ALPHA: float = float(os.environ.get("NYOM_STOP_ALPHA", "0.40"))
STOP_MIN_PCT: float = float(os.environ.get("NYOM_STOP_MIN_PCT", "0.003"))
STOP_MAX_PCT: float = float(os.environ.get("NYOM_STOP_MAX_PCT", "0.008"))
SL_MULT: float = float(os.environ.get("NYOM_SL_MULT", "1.5"))         # widens stop vs fade
TP_MULT: float = float(os.environ.get("NYOM_TP_MULT", "1.0"))         # TP2 = entry + range × TP_MULT
BREAK_BUFFER: float = float(os.environ.get("NYOM_BREAK_BUFFER", "0.0005"))
MAX_RETEST_MIN: int = int(os.environ.get("NYOM_MAX_RETEST_MIN", "15"))

# --- fees ---
FEE_MAKER: float = float(os.environ.get("NYOM_FEE_MAKER", "0.0002"))
FEE_TAKER: float = float(os.environ.get("NYOM_FEE_TAKER", "0.0005"))

# --- paper trade sizing ---
INITIAL_CAPITAL: float = float(os.environ.get("NYOM_CAPITAL", "1000.0"))
LEVERAGE: float = float(os.environ.get("NYOM_LEVERAGE", "1.0"))
STRATEGY_ID: str = os.environ.get("NYOM_STRATEGY_ID", "ny_open_mom_btc_long_v1")

# --- Phase 0 gate ---
PHASE0_MIN_TRADES: int = int(os.environ.get("NYOM_PHASE0_MIN_TRADES", "50"))
PHASE0_WR_FLOOR: float = float(os.environ.get("NYOM_PHASE0_WR_FLOOR", "0.40"))

# --- paths ---
DATA_DIR = Path(os.environ.get("NYOM_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("NYOM_LOG_DIR", "/logs"))

# --- polling ---
POLL_OFFSET_SEC: int = int(os.environ.get("NYOM_POLL_OFFSET_SEC", "10"))

# --- health server ---
HEALTH_PORT: int = int(os.environ.get("NYOM_HEALTH_PORT", "8104"))

# --- telegram ---
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_TAG: str = os.environ.get("TELEGRAM_TAG", "NYOM")
