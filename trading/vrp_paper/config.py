"""VRP paper trader — configuration.

Weekly short ATM straddle on Deribit BTC options, delta-hedged daily via a
paper perp at the Deribit index price. PAPER ONLY — public API, no keys.

Cycle: every Friday ~08:05 UTC sells the ATM straddle expiring next Friday at
the REAL best bid; every day ~08:05 UTC re-hedges net delta; at expiry settles
on Deribit's official delivery price. Sizing is vol-scaled by DVOL.

Validated (research/options_study, pós-fix look-ahead, 2022-01→2026-05):
2.56%/mês, PF trade 1.80, PF mensal 2.99, Sharpe 1.73, maxDD -17.9% @1x.
OOS 2025-26: 3.38%/mês. Todos os anos positivos. Custos 2x → 1.9%/mês.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("VRP_DATA_DIR", "/data"))
LOG_DIR = Path(os.environ.get("VRP_LOG_DIR", "/logs"))

# ── capital / sizing ─────────────────────────────────────────────────────────
INITIAL_CAPITAL = float(os.environ.get("VRP_CAPITAL", "1000.0"))
SIZE_MULT = float(os.environ.get("VRP_SIZE_MULT", "1.0"))
DVOL_REF = float(os.environ.get("VRP_DVOL_REF", "0.55"))   # sizing reference vol
VOL_SCALE_CAP = float(os.environ.get("VRP_VOL_SCALE_CAP", "2.0"))

# ── structure ────────────────────────────────────────────────────────────────
ENTRY_DOW = 4              # Friday
DTE_LO, DTE_HI = 6.0, 8.0  # next-Friday weekly
MAX_STRIKE_DIST = 0.03     # ATM: strike within 3% of index

# ── costs (Deribit schedule + hedge) ─────────────────────────────────────────
FEE_TRADE_BTC = 0.0003     # per contract, capped at 12.5% of premium
FEE_TRADE_CAP = 0.125
FEE_SETTLE_BTC = 0.00015   # delivery fee per contract
HEDGE_COST = float(os.environ.get("VRP_HEDGE_COST", "0.0006"))  # 6bps per adjustment
# entry uses the real best bid — no extra haircut (the research haircut modeled
# exactly this: crossing from last trade to the bid).

# ── vol-surface conditioning (research/options_edge2, 2026-07-05) ───────────
# Tercile sizing on signed = -z90(slope), slope = ATM IV30 - IV7. LOG-ONLY by
# default: multiplier recorded per entry, sizing unchanged (linear => the
# conditioned track is mult × weekly return). Set VRP_SLOPE_SIZING=1 to apply.
SLOPE_SIZING = os.environ.get("VRP_SLOPE_SIZING", "0").strip() in ("1", "true")
SLOPE_STEPS = (0.5, 1.0, 1.5)
SLOPE_TER_MIN = 180        # min obs before tercile thresholds are trusted

# ── risk alerts ──────────────────────────────────────────────────────────────
DD_ALERT_PCT = float(os.environ.get("VRP_DD_ALERT_PCT", "0.15"))
CONSEC_LOSS_ALERT = int(os.environ.get("VRP_CONSEC_LOSS_ALERT", "4"))

# ── schedule ─────────────────────────────────────────────────────────────────
ACTION_HOUR_UTC = 8        # entries/hedges/settles happen on the 08:05 cycle
CYCLE_MINUTE = 5           # cycles run at :05 every hour (marks hourly)

# ── health / telegram ────────────────────────────────────────────────────────
HEALTH_PORT = int(os.environ.get("VRP_HEALTH_PORT", "8108"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TAG = os.environ.get("VRP_TELEGRAM_TAG", "VRP").strip()

DERIBIT_BASE = os.environ.get("VRP_DERIBIT_BASE", "https://www.deribit.com/api/v2")
