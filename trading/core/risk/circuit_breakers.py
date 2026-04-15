"""
Circuit breakers — evaluated in priority order before any engine runs.

Order per brief:
  1. KILL_STALE_DATA
  2. KILL_HEARTBEAT
  3. CIRCUIT_DD_DIARIO
  4. CIRCUIT_BANKROLL_MIN
  5. ONE_POSITION_PER_ENGINE
  6. NO_LIVE_ORDER          (enforced in fill_model.py)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from ..config.settings import (
    ENGINE_CONFIGS,
    STALE_DATA_THRESHOLD_MINUTES,
    HEARTBEAT_STALE_MINUTES,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class BreakerResult:
    triggered: bool
    breaker_name: str
    reason: str
    affected_engine: Optional[str] = None
    affected_symbol: Optional[str] = None
    observed_duration_seconds: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stale-data check (global — blocks ALL engines)
# ---------------------------------------------------------------------------

def check_stale_data(
    last_candle_times: Dict[str, Optional[datetime]],
) -> BreakerResult:
    """
    If ANY symbol required by an active engine has not received a new 1m
    candle in STALE_DATA_THRESHOLD_MINUTES minutes, block all engines.
    """
    now = datetime.now(timezone.utc)
    threshold = timedelta(minutes=STALE_DATA_THRESHOLD_MINUTES)

    required_symbols = {cfg.symbol for cfg in ENGINE_CONFIGS.values()}

    for symbol in required_symbols:
        last = last_candle_times.get(symbol)
        if last is None:
            return BreakerResult(
                triggered=True,
                breaker_name="KILL_STALE_DATA",
                reason=f"No candle data found for {symbol}",
                affected_symbol=symbol,
                details={
                    "symbol": symbol,
                    "threshold_minutes": STALE_DATA_THRESHOLD_MINUTES,
                    "last_candle_at": None,
                },
            )
        age = now - last
        if age > threshold:
            age_seconds = age.total_seconds()
            minutes = age_seconds / 60
            return BreakerResult(
                triggered=True,
                breaker_name="KILL_STALE_DATA",
                reason=(
                    f"el_candles_1m for {symbol} not updated in "
                    f"{minutes:.1f}min (threshold={STALE_DATA_THRESHOLD_MINUTES}min)"
                ),
                affected_symbol=symbol,
                observed_duration_seconds=age_seconds,
                details={
                    "symbol": symbol,
                    "last_candle_at": last.isoformat(),
                    "age_seconds": age_seconds,
                    "threshold_minutes": STALE_DATA_THRESHOLD_MINUTES,
                },
            )
    return BreakerResult(
        triggered=False,
        breaker_name="KILL_STALE_DATA",
        reason="OK",
        details={"threshold_minutes": STALE_DATA_THRESHOLD_MINUTES},
    )


# ---------------------------------------------------------------------------
# Heartbeat staleness (global — triggers alert)
# ---------------------------------------------------------------------------

def check_heartbeat_stale(last_loop_at: Optional[datetime]) -> BreakerResult:
    """
    If the worker loop has not completed a cycle in HEARTBEAT_STALE_MINUTES,
    return a triggered breaker (caller should send critical alert).
    """
    if last_loop_at is None:
        return BreakerResult(
            triggered=True,
            breaker_name="KILL_HEARTBEAT",
            reason="No heartbeat recorded yet",
            details={"threshold_minutes": HEARTBEAT_STALE_MINUTES},
        )
    now = datetime.now(timezone.utc)
    age = now - last_loop_at
    if age > timedelta(minutes=HEARTBEAT_STALE_MINUTES):
        age_seconds = age.total_seconds()
        minutes = age_seconds / 60
        return BreakerResult(
            triggered=True,
            breaker_name="KILL_HEARTBEAT",
            reason=f"Loop has been silent for {minutes:.1f}min",
            observed_duration_seconds=age_seconds,
            details={
                "last_loop_completed_at": last_loop_at.isoformat(),
                "age_seconds": age_seconds,
                "threshold_minutes": HEARTBEAT_STALE_MINUTES,
            },
        )
    return BreakerResult(
        triggered=False,
        breaker_name="KILL_HEARTBEAT",
        reason="OK",
        details={
            "last_loop_completed_at": last_loop_at.isoformat(),
            "threshold_minutes": HEARTBEAT_STALE_MINUTES,
        },
    )


# ---------------------------------------------------------------------------
# Per-engine breakers
# ---------------------------------------------------------------------------

def check_daily_drawdown(
    engine_id: str,
    daily_starting_equity: float,
    current_bankroll: float,
) -> BreakerResult:
    """
    If today's loss exceeds max_daily_dd_pct, pause the engine until midnight.
    """
    cfg = ENGINE_CONFIGS[engine_id]
    dd = (daily_starting_equity - current_bankroll) / daily_starting_equity
    if dd >= cfg.max_daily_dd_pct:
        return BreakerResult(
            triggered=True,
            breaker_name="CIRCUIT_DD_DIARIO",
            reason=(
                f"Daily drawdown {dd*100:.2f}% >= limit {cfg.max_daily_dd_pct*100:.1f}% "
                f"for engine {engine_id}"
            ),
            affected_engine=engine_id,
            details={
                "engine_id": engine_id,
                "daily_starting_equity": daily_starting_equity,
                "current_bankroll": current_bankroll,
                "dd_pct": dd,
                "dd_limit_pct": cfg.max_daily_dd_pct,
            },
        )
    return BreakerResult(
        triggered=False,
        breaker_name="CIRCUIT_DD_DIARIO",
        reason="OK",
        affected_engine=engine_id,
        details={"engine_id": engine_id, "dd_limit_pct": cfg.max_daily_dd_pct},
    )


def check_bankroll_minimum(
    engine_id: str,
    current_bankroll: float,
) -> BreakerResult:
    """
    If bankroll falls below the minimum, pause the engine indefinitely.
    """
    cfg = ENGINE_CONFIGS[engine_id]
    if current_bankroll < cfg.min_bankroll_usd:
        return BreakerResult(
            triggered=True,
            breaker_name="CIRCUIT_BANKROLL_MIN",
            reason=(
                f"Bankroll ${current_bankroll:.2f} < minimum "
                f"${cfg.min_bankroll_usd:.2f} for engine {engine_id}"
            ),
            affected_engine=engine_id,
            details={
                "engine_id": engine_id,
                "current_bankroll": current_bankroll,
                "min_bankroll_usd": cfg.min_bankroll_usd,
            },
        )
    return BreakerResult(
        triggered=False,
        breaker_name="CIRCUIT_BANKROLL_MIN",
        reason="OK",
        affected_engine=engine_id,
        details={"engine_id": engine_id, "min_bankroll_usd": cfg.min_bankroll_usd},
    )


def check_one_position(engine_id: str, has_open: bool) -> BreakerResult:
    """Only one open position allowed per engine."""
    if has_open:
        return BreakerResult(
            triggered=True,
            breaker_name="ONE_POSITION_PER_ENGINE",
            reason=f"Engine {engine_id} already has an open position",
            affected_engine=engine_id,
            details={"engine_id": engine_id, "has_open_position": True},
        )
    return BreakerResult(
        triggered=False,
        breaker_name="ONE_POSITION_PER_ENGINE",
        reason="OK",
        affected_engine=engine_id,
        details={"engine_id": engine_id, "has_open_position": False},
    )
