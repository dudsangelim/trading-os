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
    "postgresql://jarvis:CHANGE_ME@postgres:5432/jarvis",
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
    # m3_sol APOSENTADO em 2026-06-14 (audit): negativo (-$5,43 / 5 trades em 3 meses),
    # frequência baixíssima. Removido do registry e do banco.
    # m3_eth_shadow APOSENTADO em 2026-07-05 por ordem do operador — era shadow/paper,
    # sem dinheiro real; removido do registry, do ENGINE_CONFIGS e do banco.
    # Sem engines clássicos ativos: worker roda o loop sem engine no registry principal.
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

# ---------------------------------------------------------------------------
# Liquidity zone providers (Phases 1–2)
# ---------------------------------------------------------------------------
# Phase 1: Equal Levels provider — enabled by default (pure OHLCV, no new deps)
LIQUIDITY_PROVIDER_EQUAL_LEVELS_ENABLED: bool = _bool_env("LIQUIDITY_PROVIDER_EQUAL_LEVELS_ENABLED", True)

# Phase 2 (V1 legacy, unused): Liquidation estimator — off by default
LIQUIDITY_PROVIDER_LIQUIDATION_ESTIMATOR_ENABLED: bool = _bool_env(
    "LIQUIDITY_PROVIDER_LIQUIDATION_ESTIMATOR_ENABLED", False
)
LIQUIDATION_ESTIMATOR_LOOKBACK_HOURS: int = int(os.environ.get("LIQUIDATION_ESTIMATOR_LOOKBACK_HOURS", "24"))
LIQUIDATION_ESTIMATOR_WINDOW_MINUTES: int = int(os.environ.get("LIQUIDATION_ESTIMATOR_WINDOW_MINUTES", "60"))
LIQUIDATION_ESTIMATOR_MIN_INTENSITY: float = float(os.environ.get("LIQUIDATION_ESTIMATOR_MIN_INTENSITY", "0.1"))

# Phase 2 (V2): Liquidation heatmap provider — reads pre-computed table from derivatives-collector
LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED: bool = _bool_env(
    "LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED", False
)
LIQUIDATION_HEATMAP_MIN_INTENSITY: float = float(
    os.environ.get("LIQUIDATION_HEATMAP_MIN_INTENSITY", "0.3")
)
LIQUIDATION_HEATMAP_MAX_ZONES_PER_SIDE: int = int(
    os.environ.get("LIQUIDATION_HEATMAP_MAX_ZONES_PER_SIDE", "5")
)
LIQUIDATION_HEATMAP_MAX_AGE_MINUTES: int = int(
    os.environ.get("LIQUIDATION_HEATMAP_MAX_AGE_MINUTES", "10")
)

# ---------------------------------------------------------------------------
# Liquidity Zones — Fase 4: Aggregator + Overlay Variants
# ---------------------------------------------------------------------------
LIQUIDITY_AGGREGATOR_ENABLED: bool = _bool_env("LIQUIDITY_AGGREGATOR_ENABLED", False)

LIQUIDITY_PROVIDER_SWING_ENABLED: bool = _bool_env("LIQUIDITY_PROVIDER_SWING_ENABLED", True)
LIQUIDITY_PROVIDER_FVG_ENABLED: bool = _bool_env("LIQUIDITY_PROVIDER_FVG_ENABLED", True)
LIQUIDITY_PROVIDER_PRIOR_LEVELS_ENABLED: bool = _bool_env("LIQUIDITY_PROVIDER_PRIOR_LEVELS_ENABLED", True)

OVERLAY_GHOST_VARIANTS_ENABLED: bool = _bool_env("OVERLAY_GHOST_VARIANTS_ENABLED", False)
OVERLAY_VARIANTS_MIN_INTENSITY: float = float(
    os.environ.get("OVERLAY_VARIANTS_MIN_INTENSITY", "0.3")
)

# ---------------------------------------------------------------------------
# Carry Neutral engine
# ---------------------------------------------------------------------------
CARRY_NEUTRAL_ENABLED: bool = _bool_env("CARRY_NEUTRAL_ENABLED", False)
CARRY_NEUTRAL_THRESHOLD_UPPER_ANNUALIZED: float = float(
    os.environ.get("CARRY_NEUTRAL_THRESHOLD_UPPER_ANNUALIZED", "0.15")
)
CARRY_NEUTRAL_THRESHOLD_LOWER_ANNUALIZED: float = float(
    os.environ.get("CARRY_NEUTRAL_THRESHOLD_LOWER_ANNUALIZED", "0.05")
)
CARRY_NEUTRAL_MAX_HOLD_HOURS: int = int(
    os.environ.get("CARRY_NEUTRAL_MAX_HOLD_HOURS", "168")
)
CARRY_NEUTRAL_VARIANT: str = os.environ.get("CARRY_NEUTRAL_VARIANT", "sell_accrual")
CARRY_NEUTRAL_STAKE_USD: float = float(
    os.environ.get("CARRY_NEUTRAL_STAKE_USD", "300.0")
)
CARRY_WORKER_LOOP_INTERVAL_SECONDS: int = int(
    os.environ.get("CARRY_WORKER_LOOP_INTERVAL_SECONDS", "300")
)

# ---------------------------------------------------------------------------
# Engine role configuration — explicit declaration of role for each engine
# ---------------------------------------------------------------------------
# ACTIVE: engine executes validate_signal → ENTER and opens paper trades
# SIGNAL_ONLY: engine always emits SIGNAL_ONLY, never opens trades
# EXPERIMENTAL: engine is disabled, registered but not called by worker
# ARCHIVED: engine confirmed structurally unprofitable, registered but never called; see research_log/
ENGINE_ROLES: Dict[str, str] = {
    # m3_sol APOSENTADO 2026-06-14; m3_eth_shadow APOSENTADO 2026-07-05 (ordem do operador).
    # Ambos removidos do registry, ENGINE_CONFIGS, ENGINE_ROLES e do banco.
    "carry_neutral_btc": "ACTIVE",
}
