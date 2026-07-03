"""Taker-capitulation paper trader — configuration.

H3 of the liqflow phase-2 study (2026-07-03): when the 24h taker long/short
volume ratio is extremely depressed vs its 7-day norm (z < -2, crowd selling
into the tape), go LONG for exactly 4 hours. LONG-only, fixed config,
SIGNAL_ONLY (paper fills — no real orders). BTC/ETH/SOL, 1h bars,
data via Binance REST (no DB dependency).

Validated on 3.5y (2023-01→2026-06, pre-registered): +17.6bps gross/trade,
t=2.80, n=801, hit 58%; excess over drift positive in all 4 years, 3/3
symbols, monotonic dose-response. As a 1x strategy: CAGR 7-10%, maxDD -12%,
Sharpe 0.9-1.25; corr ≈ 0 with every stable5 ensemble sleeve.
See /home/agent/research/liqflow_study/REPORT.md (FASE 2).
"""
from __future__ import annotations

import os
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("TCP_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("TCP_LOG_DIR", "/logs"))

# ── instruments / timeframe ──────────────────────────────────────────────────
SYMBOLS = os.environ.get("TCP_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
TIMEFRAME = "1h"
TF_MS = 60 * 60 * 1000

# ── strategy params (frozen from the validated phase-2 cell) ─────────────────
Z_TH = float(os.environ.get("TCP_Z_TH", "-2.0"))       # entry: z(taker) < Z_TH
HOLD_BARS = int(os.environ.get("TCP_HOLD_BARS", "4"))  # fixed 4h hold
TK_ROLL = 24        # rolling mean of log(taker 1h), hours
TK_ZWIN = 24 * 7    # z-score window, hours (7d)
TK_ROLL_MINP = 12
TK_ZWIN_MINP = TK_ZWIN // 2

# ── capital / sizing ─────────────────────────────────────────────────────────
INITIAL_CAPITAL = float(os.environ.get("TCP_CAPITAL", "1000.0"))  # total
LEVERAGE = float(os.environ.get("TCP_LEVERAGE", "1.0"))
SLEEVE_CAPITAL = INITIAL_CAPITAL / len(SYMBOLS)

# ── costs ────────────────────────────────────────────────────────────────────
FEE_BPS = float(os.environ.get("TCP_FEE_BPS", "4.0"))     # taker per side
SLIP_BPS = float(os.environ.get("TCP_SLIP_BPS", "2.0"))   # slippage per side
FUNDING_ENABLED = os.environ.get("TCP_FUNDING", "true").lower() == "true"

# ── data window ──────────────────────────────────────────────────────────────
FETCH_BARS = int(os.environ.get("TCP_FETCH_BARS", "300"))       # 1h klines
TK_FETCH_HOURS = int(os.environ.get("TCP_TK_FETCH_HOURS", "240"))  # 10d of 5m taker
MIN_TK_HOURS = TK_ZWIN + TK_ROLL + 8   # full z warm-up (matches research values)

# ── risk alerts ──────────────────────────────────────────────────────────────
DD_ALERT_PCT = float(os.environ.get("TCP_DD_ALERT_PCT", "0.10"))
CONSEC_LOSS_ALERT = int(os.environ.get("TCP_CONSEC_LOSS_ALERT", "6"))

# ── health server ────────────────────────────────────────────────────────────
HEALTH_PORT = int(os.environ.get("TCP_HEALTH_PORT", "8107"))

# ── telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TAG = os.environ.get("TCP_TELEGRAM_TAG", "TAKERCAP").strip()

# ── exchange endpoints ───────────────────────────────────────────────────────
FAPI_BASE = os.environ.get("TCP_FAPI_BASE", "https://fapi.binance.com")
KLINES_URL = f"{FAPI_BASE}/fapi/v1/klines"
TAKER_URL = f"{FAPI_BASE}/futures/data/takerlongshortRatio"

# fallback instrument filters (used if exchangeInfo fetch fails); from fapi
TICK_FALLBACK = {"BTCUSDT": 0.10, "ETHUSDT": 0.01, "SOLUSDT": 0.0100}
STEP_FALLBACK = {"BTCUSDT": 0.001, "ETHUSDT": 0.001, "SOLUSDT": 1.0}
MIN_NOTIONAL_FALLBACK = {"BTCUSDT": 100.0, "ETHUSDT": 20.0, "SOLUSDT": 5.0}
