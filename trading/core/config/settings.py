"""
Central configuration — single source of truth for the trading system.
All other modules import from here; nothing is hardcoded elsewhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Database / infrastructure
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://jarvis:jarvispass@postgres:5432/jarvis",
)
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://redis:6379/0")

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.environ.get("TRADING_BOT_TOKEN", "")
ALLOWED_USERS_RAW: str = os.environ.get("TRADING_ALLOWED_USERS", "")
ALLOWED_USERS: List[int] = [
    int(u.strip()) for u in ALLOWED_USERS_RAW.split(",") if u.strip().isdigit()
]

# ---------------------------------------------------------------------------
# Per-engine risk defaults
# ---------------------------------------------------------------------------
@dataclass
class EngineRiskConfig:
    engine_id: str
    symbol: str
    initial_capital_usd: float
    risk_per_trade_pct: float      # e.g. 0.03 = 3% of bankroll as base stake
    max_daily_dd_pct: float        # e.g. 0.05 = 5%
    min_bankroll_usd: float
    slippage_bps: int
    leverage: int = 1              # notional = bankroll * risk_pct * leverage
    fee_bps: int = 10              # Binance taker fee per side (10 bps = 0.10%)
    signal_only: bool = False      # True → never opens real position
    timeout_bars: int = 6          # bars held before forced close
    timeframe_minutes: int = 5     # TF this engine operates on


ENGINE_CONFIGS: Dict[str, EngineRiskConfig] = {
    # ── Classic engines ──────────────────────────────────────────────
    "m1_eth": EngineRiskConfig(
        engine_id="m1_eth",
        symbol="ETHUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=False,
        timeout_bars=8,
        timeframe_minutes=15,
    ),
    "m2_btc": EngineRiskConfig(
        engine_id="m2_btc",
        symbol="BTCUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=False,
        timeout_bars=6,
        timeframe_minutes=15,
    ),
    "m3_sol": EngineRiskConfig(
        engine_id="m3_sol",
        symbol="SOLUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=8,
        leverage=10,
        signal_only=False,
        timeout_bars=64,
        timeframe_minutes=15,
    ),
    "m3_eth_shadow": EngineRiskConfig(
        engine_id="m3_eth_shadow",
        symbol="ETHUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=True,
        timeout_bars=96,
        timeframe_minutes=15,
    ),
    # ── Crypto-native engines (paper trade) ──────────────────────────
    "cn1_oi_divergence": EngineRiskConfig(
        engine_id="cn1_oi_divergence",
        symbol="BTCUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=False,
        timeout_bars=16,
        timeframe_minutes=15,
    ),
    "cn2_taker_momentum": EngineRiskConfig(
        engine_id="cn2_taker_momentum",
        symbol="BTCUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=False,
        timeout_bars=8,
        timeframe_minutes=15,
    ),
    "cn3_funding_reversal": EngineRiskConfig(
        engine_id="cn3_funding_reversal",
        symbol="BTCUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=False,
        timeout_bars=32,
        timeframe_minutes=15,
    ),
    "cn4_liquidation_cascade": EngineRiskConfig(
        engine_id="cn4_liquidation_cascade",
        symbol="BTCUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=False,
        timeout_bars=16,
        timeframe_minutes=15,
    ),
    "cn5_squeeze_zone": EngineRiskConfig(
        engine_id="cn5_squeeze_zone",
        symbol="BTCUSDT",
        initial_capital_usd=1000.0,
        risk_per_trade_pct=0.03,
        max_daily_dd_pct=0.05,
        min_bankroll_usd=200.0,
        slippage_bps=5,
        leverage=10,
        signal_only=False,
        timeout_bars=12,
        timeframe_minutes=15,
    ),
}

SLIPPAGE_BPS: Dict[str, int] = {
    cfg.engine_id: cfg.slippage_bps for cfg in ENGINE_CONFIGS.values()
}

# ---------------------------------------------------------------------------
# Kill-switch / circuit-breaker thresholds
# ---------------------------------------------------------------------------
STALE_DATA_THRESHOLD_MINUTES: int = 10   # no new candle → block all engines
HEARTBEAT_STALE_MINUTES: int = 15        # loop silent → critical alert

# ---------------------------------------------------------------------------
# Loop timing
# ---------------------------------------------------------------------------
WORKER_LOOP_INTERVAL_SECONDS: int = 60

# ---------------------------------------------------------------------------
# Operator repair API
# ---------------------------------------------------------------------------
# Bearer token required to call POST /operator/* endpoints.
# Empty string = no auth (suitable for local/dev only).
# Set a non-empty value in production.
OPERATOR_TOKEN: str = os.environ.get("OPERATOR_TOKEN", "")

# ---------------------------------------------------------------------------
# Phase F — optional derivatives integration
# ---------------------------------------------------------------------------

def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DERIVATIVES_DATABASE_URL: str = os.environ.get("DERIVATIVES_DATABASE_URL", "")

# Master switches
DERIVATIVES_ENABLED: bool = _bool_env("DERIVATIVES_ENABLED", False)
DERIVATIVES_FEATURE_ADAPTER_ENABLED: bool = _bool_env("DERIVATIVES_FEATURE_ADAPTER_ENABLED", False)

# Engine usage switches
ENGINE_DERIVATIVES_ADVISORY_ENABLED: bool = _bool_env("ENGINE_DERIVATIVES_ADVISORY_ENABLED", False)
ENGINE_DERIVATIVES_HARD_FILTER_ENABLED: bool = _bool_env("ENGINE_DERIVATIVES_HARD_FILTER_ENABLED", False)
# Phase G — shadow filter: evaluates hard-filter verdict without blocking real trades
ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED: bool = _bool_env("ENGINE_DERIVATIVES_SHADOW_FILTER_ENABLED", False)

# Adapter quality/freshness thresholds
DERIVATIVES_FRESHNESS_THRESHOLD_SECONDS: int = int(
    os.environ.get("DERIVATIVES_FRESHNESS_THRESHOLD_SECONDS", "600")
)
DERIVATIVES_STALE_THRESHOLD_SECONDS: int = int(
    os.environ.get("DERIVATIVES_STALE_THRESHOLD_SECONDS", "1800")
)
DERIVATIVES_MIN_FEATURES_REQUIRED: int = int(
    os.environ.get("DERIVATIVES_MIN_FEATURES_REQUIRED", "3")
)
