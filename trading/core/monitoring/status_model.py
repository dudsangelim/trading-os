"""
System status model — typed contract for the runtime state exposed by Trading Core.

This module is the authoritative schema for:
  - GET /system/status  (stable operations contract for OpenClaw and monitoring)
  - GET /health         (summary subset)
  - Internal monitoring and alerting consumers

CONTRACT_VERSION must be bumped whenever the shape of SystemStatusSnapshot changes
in a way that is not backward-compatible for downstream consumers (e.g. OpenClaw).

Additive changes (new optional keys) do NOT require a version bump.
Removals or renames always do.

Usage:
  - build_system_status() in healthcheck.py returns data conforming to this model.
  - Downstream consumers (e.g. OpenClaw skill mj-trading) should parse the output
    using this file as the canonical reference.
  - Future typed callers can construct a SystemStatusSnapshot from the dict via
    SystemStatusSnapshot.from_dict() if strict type safety is required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Contract version
# ---------------------------------------------------------------------------

# Bump the minor version for additive changes; major version for breaking changes.
CONTRACT_VERSION: str = "C.1"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

@dataclass
class HeartbeatCheckStatus:
    """
    Result of checking whether the worker loop has been observed recently.

    status: "ok" | "blocked"
    """
    status: str
    reason: Optional[str]
    observed_duration_seconds: Optional[float]
    threshold_minutes: int


@dataclass
class WorkerStatusSnapshot:
    """
    Snapshot of the worker loop's observable runtime state.

    status values (canonical):
      alive         — loop running normally within expected interval
      processing    — loop started but not yet completed this tick
      slow          — loop completed but behind expected interval
      blocked_risk  — loop is running but all engines are risk-blocked
      degraded      — loop completed but some engines in error/degraded state
      stopped       — loop last completion exceeds heartbeat stale threshold
    """
    status: str
    detail: Optional[str]
    last_loop_started_at: Optional[str]       # ISO-8601 UTC
    last_loop_completed_at: Optional[str]
    last_loop_degraded_at: Optional[str]
    last_loop_observed_at: Optional[str]
    last_loop_completed_age_seconds: Optional[float]
    last_freshness_check_at: Optional[str]
    runtime_state: Dict[str, Any]
    heartbeat_check: HeartbeatCheckStatus


@dataclass
class InputSymbolStatus:
    """
    Observed candle input quality for a single required symbol.

    status: "ok" | "stale" | "absent"   (backward-compatible binary field)
    quality: "fresh" | "degraded" | "stale" | "absent"  (richer signal from candle_contract)
    """
    symbol: str
    last_candle_at: Optional[str]     # ISO-8601 UTC
    age_seconds: Optional[float]
    threshold_minutes: int
    coverage_in_window: int           # candles present in COVERAGE_WINDOW_MINUTES
    coverage_window_minutes: int
    quality: str                      # InputQuality.value
    status: str                       # "ok" | "stale" | "absent" — backward-compat field


@dataclass
class InputStatus:
    """
    Aggregate candle input status — summarises freshness and coverage for all
    required symbols.

    status: "ok" | "stale"  (matches what the global stale circuit breaker observes)
    """
    status: str
    breaker: Dict[str, Any]
    symbols: List[InputSymbolStatus]


@dataclass
class EngineStatusSnapshot:
    """
    Snapshot of a single engine's observable runtime state.

    heartbeat_status values (canonical):
      PROCESSING    — engine is currently handling a bar
      OK            — engine completed last bar normally
      BLOCKED_RISK  — engine is blocked by a risk circuit breaker
      DEGRADED      — engine encountered a non-fatal error
      STOPPED       — engine has not been observed for too long
      UNKNOWN       — no heartbeat row yet
    """
    engine_id: str
    symbol: str
    mode: str                         # "ACTIVE" | "SIGNAL_ONLY"
    timeframe_minutes: int
    heartbeat_status: str
    heartbeat_detail: Optional[str]
    last_run_at: Optional[str]        # ISO-8601 UTC
    last_bar_at: Optional[str]
    last_processed_at: Optional[str]
    updated_at: Optional[str]
    runtime_state: Dict[str, Any]
    input: Optional[Dict[str, Any]]   # InputSymbolStatus dict for this engine's symbol
    open_position: Optional[Dict[str, Any]]
    pending_setup: Optional[Dict[str, Any]]
    latest_risk_event: Optional[Dict[str, Any]]


@dataclass
class RiskSnapshot:
    """
    Aggregate risk state — active pauses and recent risk/operational events.

    status: "ok" | "blocked" | "degraded"
    """
    status: str
    global_pauses: List[Dict[str, Any]]
    recent_events: List[Dict[str, Any]]


@dataclass
class DerivativesSymbolSnapshot:
    """
    Optional derivatives feature status for one symbol.
    """
    symbol: str
    available: bool
    quality: str
    freshness: str
    last_observation_at: Optional[str]
    age_seconds: Optional[float]
    features: Dict[str, Any]
    available_features: List[str]
    missing_features: List[str]


@dataclass
class DerivativesSnapshot:
    """
    Optional derivatives adapter status (Phase F, non-blocking).
    """
    status: str                        # "ok" | "degraded" | "disconnected"
    mode: str                          # DISCONNECTED | ADVISORY_ONLY | DEGRADED | AVAILABLE
    adapter_enabled: bool
    fallback_active: bool
    fallback_reason: Optional[str]
    timestamp: str                     # ISO-8601 UTC
    flags: Dict[str, Any]
    contract: Dict[str, Any]
    symbols: List[DerivativesSymbolSnapshot]
    canonical_snapshot: List[Dict[str, Any]]
    summary: Dict[str, Any]


# ---------------------------------------------------------------------------
# Top-level snapshot
# ---------------------------------------------------------------------------

@dataclass
class SystemStatusSnapshot:
    """
    Canonical runtime status snapshot for Trading Core.

    This is the type contract for GET /system/status.

    Fields:
      status           — "ok" | "blocked" | "degraded" | "error"
      ts               — ISO-8601 UTC timestamp of snapshot generation
      contract_version — schema version; must be parsed by consumers
      postgres         — True if the DB is reachable
      worker           — worker loop state
      inputs           — candle input freshness and coverage
      risk             — active risk pauses and recent operational events
      derivatives      — optional derivatives adapter state (Phase F; non-blocking)
      engines          — per-engine state
      error            — present only on status="error"; internal error message
    """
    status: str
    ts: str                              # ISO-8601 UTC
    contract_version: str
    postgres: bool
    worker: WorkerStatusSnapshot
    inputs: InputStatus
    risk: RiskSnapshot
    derivatives: Optional[DerivativesSnapshot]
    engines: List[EngineStatusSnapshot]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Key sets for downstream consumers
# ---------------------------------------------------------------------------

# Top-level keys present in every response (stable contract)
SYSTEM_STATUS_TOP_LEVEL_KEYS: tuple = (
    "status", "ts", "contract_version", "postgres",
    "worker", "inputs", "risk", "derivatives", "engines",
)

# Status values for result["status"]
STATUS_OK       = "ok"
STATUS_BLOCKED  = "blocked"
STATUS_DEGRADED = "degraded"
STATUS_ERROR    = "error"

# Worker loop status values
WORKER_STATUS_ALIVE        = "alive"
WORKER_STATUS_PROCESSING   = "processing"
WORKER_STATUS_SLOW         = "slow"
WORKER_STATUS_BLOCKED_RISK = "blocked_risk"
WORKER_STATUS_DEGRADED     = "degraded"
WORKER_STATUS_STOPPED      = "stopped"

# Engine heartbeat status values
ENGINE_STATUS_PROCESSING   = "PROCESSING"
ENGINE_STATUS_OK           = "OK"
ENGINE_STATUS_BLOCKED_RISK = "BLOCKED_RISK"
ENGINE_STATUS_DEGRADED     = "DEGRADED"
ENGINE_STATUS_STOPPED      = "STOPPED"
ENGINE_STATUS_UNKNOWN      = "UNKNOWN"
