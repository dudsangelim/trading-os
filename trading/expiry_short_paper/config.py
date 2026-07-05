"""Expiry-short paper trader — configuration.

SHORT BTCUSDT + ETHUSDT perp (paper) into the Deribit weekly options expiry:
open Thursday 08:00 UTC, close Friday 08:00 UTC (settle da Deribit). Validated
in research/options_edge3 (2026-07-05): pre-expiry 24h drift -38/-45bps vs
+17/+22 baseline, t=-2.7/-2.1, BTC 6/6 anos; net 1.64%/mês PFmo 1.90
maxDD -18.3% (BTC+ETH 50/50, 12bps RT + funding, 2023→2026-06); placebo:
shorting any other 24h window LOSES. SOL excluded (failed the gate, t=-0.96).
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("EXPSHORT_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("EXPSHORT_LOG_DIR", "/logs"))

INITIAL_CAPITAL = float(os.environ.get("EXPSHORT_CAPITAL", "1000.0"))
SYMBOLS = ["BTCUSDT", "ETHUSDT"]          # 50/50
LEVERAGE = float(os.environ.get("EXPSHORT_LEVERAGE", "1.0"))

OPEN_DOW, OPEN_HOUR = 3, 8                # Thursday 08 UTC
CLOSE_DOW, CLOSE_HOUR = 4, 8              # Friday 08 UTC
CYCLE_MINUTE = 10                         # hourly cycle at :10

FEE_SIDE = 0.0004                         # taker 4bps
SLIP_SIDE = 0.0002                        # 2bps
DD_ALERT_PCT = float(os.environ.get("EXPSHORT_DD_ALERT_PCT", "0.15"))
CONSEC_LOSS_ALERT = int(os.environ.get("EXPSHORT_CONSEC_LOSS_ALERT", "5"))

HEALTH_PORT = int(os.environ.get("EXPSHORT_HEALTH_PORT", "8109"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TAG = os.environ.get("EXPSHORT_TELEGRAM_TAG", "EXPSHORT").strip()

BINANCE_FAPI = os.environ.get("EXPSHORT_FAPI", "https://fapi.binance.com")
