"""
Storage interface groups — explicit Protocol definitions for TradingRepository.

TradingRepository is a single class that implements all storage needs.
This module breaks it into named logical groups so that:

  1. Each component (worker, API, monitoring) can declare which storage
     concerns it actually depends on — making coupling explicit.
  2. Future sub-implementations or test doubles can target a smaller surface.
  3. The boundary between storage domains is visible in the codebase.

TradingRepository satisfies all protocols below by structural compatibility
(Python's typing.Protocol is duck-typed — no explicit registration needed).

Usage:
    from trading.core.storage.interfaces import IOperationalEvents

    def build_component(repo: IOperationalEvents) -> ...:
        ...

Note: these are async protocols (all methods are coroutines).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Operational events
# ---------------------------------------------------------------------------

@runtime_checkable
class IOperationalEvents(Protocol):
    """
    Append-only audit log of operational and risk events.

    Used by: worker (write), API (read), monitoring (read).
    Table: tr_risk_events
    """

    async def insert_risk_event(
        self,
        event_type: str,
        engine_id: Optional[str],
        description: Optional[str],
        payload: Optional[Dict[str, Any]],
    ) -> int: ...

    async def get_risk_events(
        self,
        limit: int,
    ) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------

@runtime_checkable
class IEngineRuntimeState(Protocol):
    """
    Latest-state persistence for each engine's operational snapshot.

    Used by: worker (write), monitoring/healthcheck (read), recovery (read).
    Table: tr_engine_runtime_state
    """

    async def upsert_engine_runtime_state(
        self,
        engine_id: str,
        status: str,
        detail: Optional[str],
        last_bar_seen: Optional[datetime],
        last_processed_at: Optional[datetime],
        runtime_state: Optional[Dict[str, Any]],
    ) -> None: ...

    async def get_engine_runtime_states(self) -> List[Dict[str, Any]]: ...


@runtime_checkable
class IWorkerRuntimeState(Protocol):
    """
    Latest-state persistence for the worker loop.

    Used by: worker (write), monitoring/healthcheck (read), recovery (read).
    Table: tr_worker_runtime_state
    """

    async def upsert_worker_runtime_state(
        self,
        worker_id: str,
        status: str,
        detail: Optional[str],
        *,
        last_loop_started_at: Optional[datetime],
        last_loop_completed_at: Optional[datetime],
        last_freshness_check_at: Optional[datetime],
        runtime_state: Optional[Dict[str, Any]],
    ) -> None: ...

    async def get_worker_runtime_state(
        self,
        worker_id: str,
    ) -> Optional[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Pending setups
# ---------------------------------------------------------------------------

@runtime_checkable
class IPendingSetups(Protocol):
    """
    One-slot persistence for in-progress multi-bar setups per engine.

    Used by: worker (write + clear), recovery (read + validate).
    Table: tr_pending_setups
    """

    async def upsert_pending_setup(
        self,
        engine_id: str,
        setup_type: str,
        state_data: Dict[str, Any],
        reference_bar_ts: Optional[datetime],
        expires_at: Optional[datetime],
        detail: Optional[str],
        status: str,
    ) -> None: ...

    async def clear_pending_setup(
        self,
        engine_id: str,
        detail: Optional[str],
        status: str,
    ) -> None: ...

    async def get_active_pending_setups(self) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Runtime pauses
# ---------------------------------------------------------------------------

@runtime_checkable
class IRuntimePauses(Protocol):
    """
    Persistent record of active operational pauses (risk blocks, stale data, etc.).

    Used by: worker (write + resume), monitoring (read), recovery (read).
    Table: tr_runtime_pauses
    """

    async def upsert_runtime_pause(
        self,
        pause_key: str,
        scope: str,
        pause_type: str,
        *,
        engine_id: Optional[str],
        reason: Optional[str],
        started_at: Optional[datetime],
        observed_at: Optional[datetime],
        payload: Optional[Dict[str, Any]],
        status: str,
    ) -> None: ...

    async def resume_runtime_pause(
        self,
        pause_key: str,
        *,
        reason: Optional[str],
        payload: Optional[Dict[str, Any]],
    ) -> None: ...

    async def get_active_runtime_pauses(self) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Engine heartbeat (legacy service-state table)
# ---------------------------------------------------------------------------

@runtime_checkable
class IEngineHeartbeat(Protocol):
    """
    Per-engine heartbeat — last-run-at and service status.

    The heartbeat table pre-dates the runtime_state tables.
    Recovery now primarily reads tr_engine_runtime_state.
    tr_engine_heartbeat is retained for last_run_at chronology.

    Used by: worker (write), monitoring/healthcheck (read).
    Table: tr_engine_heartbeat
    """

    async def upsert_heartbeat(
        self,
        engine_id: str,
        status: str,
        last_bar_at: Optional[datetime],
        detail: Optional[str],
    ) -> None: ...

    async def get_heartbeats(self) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Trades and positions
# ---------------------------------------------------------------------------

@runtime_checkable
class ITradeRepository(Protocol):
    """
    Paper trade lifecycle — open, close, query.

    Used by: worker (write), API (read), risk/monitoring (read).
    Table: tr_paper_trades
    """

    async def open_trade(
        self,
        engine_id: str,
        symbol: str,
        signal_id: Optional[int],
        direction: Any,
        entry_price: float,
        stop_price: float,
        target_price: float,
        stake_usd: float,
    ) -> int: ...

    async def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        close_reason: str,
        closed_at: datetime,
    ) -> None: ...

    async def get_open_positions(self) -> List[Dict[str, Any]]: ...

    async def get_trades(
        self,
        engine_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Equity
# ---------------------------------------------------------------------------

@runtime_checkable
class IEquityRepository(Protocol):
    """
    Daily equity snapshots per engine.

    Used by: worker (write), API (read), risk circuits (read).
    Table: tr_daily_equity
    """

    async def upsert_daily_equity(
        self,
        engine_id: str,
        date_utc: date,
        starting_equity: float,
        ending_equity: Optional[float],
        pnl_usd: Optional[float],
        trades_count: int,
    ) -> None: ...

    async def get_equity(
        self,
        engine_id: Optional[str],
    ) -> List[Dict[str, Any]]: ...

    async def get_today_pnl(self, engine_id: str) -> float: ...

    async def get_today_starting_equity(self, engine_id: str) -> Optional[float]: ...


# ---------------------------------------------------------------------------
# State transitions (append-only audit log)
# ---------------------------------------------------------------------------

@runtime_checkable
class IStateTransitions(Protocol):
    """
    Append-only audit trail for status transitions.

    Used by: worker (write), API (read).
    Table: tr_state_transitions
    """

    async def insert_state_transition(
        self,
        entity_type: str,
        entity_id: str,
        from_status: Optional[str],
        to_status: str,
        detail: Optional[str],
        payload: Optional[Dict[str, Any]],
    ) -> int: ...

    async def get_state_transitions(
        self,
        entity_id: Optional[str],
        entity_type: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Convenience composite — full repository surface
# ---------------------------------------------------------------------------

class IFullRepository(
    IOperationalEvents,
    IEngineRuntimeState,
    IWorkerRuntimeState,
    IPendingSetups,
    IRuntimePauses,
    IEngineHeartbeat,
    ITradeRepository,
    IEquityRepository,
    IStateTransitions,
    Protocol,
):
    """
    Composite protocol — the complete storage surface required by the worker.

    TradingRepository implements this interface.
    New components that need only a subset should depend on the narrower
    sub-protocols above rather than IFullRepository.
    """
    ...
