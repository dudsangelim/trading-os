"""
Base engine interface + shared data contracts.

Concrete engines (M1, M2, M3, M3-shadow) inherit from BaseEngine and
implement generate_signal() / validate_signal() / build_order_plan() /
manage_open_position().  prepare_context() may also be overridden.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalAction(str, Enum):
    ENTER = "ENTER"
    SKIP = "SKIP"
    SIGNAL_ONLY = "SIGNAL_ONLY"


class PositionAction(str, Enum):
    HOLD = "HOLD"
    CLOSE_SL = "CLOSE_SL"
    CLOSE_TP = "CLOSE_TP"
    CLOSE_TIMEOUT = "CLOSE_TIMEOUT"


# ---------------------------------------------------------------------------
# Context / Signal / Decision / Plan / Position
# ---------------------------------------------------------------------------

@dataclass
class EngineContext:
    """All market data needed by an engine for one bar."""
    engine_id: str
    symbol: str
    bar_timestamp: datetime         # open_time of the bar that just closed
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    bar_volume: float
    recent_closes: List[float]      # last N close prices, most recent last
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Signal:
    engine_id: str
    symbol: str
    bar_timestamp: datetime
    direction: Direction
    timeframe: str = "5m"
    signal_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalDecision:
    signal_id: Optional[int]        # FK to tr_signals (set after persist)
    action: SignalAction
    reason: str
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    stake_usd: Optional[float] = None


@dataclass
class OrderPlan:
    entry_price: float
    stop_price: float
    target_price: float
    stake_usd: float
    direction: Direction


@dataclass
class Position:
    """In-memory representation of an open tr_paper_trades row."""
    trade_id: int
    engine_id: str
    symbol: str
    signal_id: Optional[int]
    direction: Direction
    entry_price: float
    stop_price: float
    target_price: float
    stake_usd: float
    opened_at: datetime
    bars_held: int = 0


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseEngine(ABC):
    """
    Contract every trading engine must satisfy.

    Methods generate_signal, validate_signal, build_order_plan, and
    manage_open_position MUST be implemented by subclasses.
    prepare_context has a default implementation that extracts standard
    fields from a Candle; override if your engine needs more.
    """

    def __init__(self, engine_id: str, symbol: str) -> None:
        self.engine_id = engine_id
        self.symbol = symbol
        self._open_position: Optional[Position] = None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def rehydrate(self, position: Position) -> None:
        """Restore open position after worker restart."""
        self._open_position = position

    @property
    def has_open_position(self) -> bool:
        return self._open_position is not None

    @property
    def open_position(self) -> Optional[Position]:
        return self._open_position

    def set_open_position(self, pos: Optional[Position]) -> None:
        self._open_position = pos

    @staticmethod
    def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _deserialize_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        return datetime.fromisoformat(value)

    def export_runtime_state(self) -> Dict[str, Any]:
        """Return serialisable engine state needed for deterministic recovery."""
        return {}

    def import_runtime_state(self, state: Dict[str, Any]) -> None:
        """Restore serialised runtime state."""
        return None

    def export_pending_state(self) -> Optional[Dict[str, Any]]:
        """Return current pending setup if the engine keeps one."""
        return None

    def import_pending_state(self, setup_type: str, state: Dict[str, Any]) -> None:
        """Restore the current pending setup if compatible with this engine."""
        return None

    def clear_pending_state(self) -> None:
        """Clear any in-memory pending setup."""
        return None

    # ------------------------------------------------------------------
    # Interface methods
    # ------------------------------------------------------------------

    def prepare_context(
        self,
        symbol: str,
        now: datetime,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_volume: float,
        bar_timestamp: datetime,
        recent_closes: List[float],
        extra: Optional[Dict[str, Any]] = None,
    ) -> EngineContext:
        """Build an EngineContext from raw bar data."""
        return EngineContext(
            engine_id=self.engine_id,
            symbol=symbol,
            bar_timestamp=bar_timestamp,
            bar_open=bar_open,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            bar_volume=bar_volume,
            recent_closes=recent_closes,
            extra=extra or {},
        )

    def set_skip_reason(self, ctx: EngineContext, reason: str, **details: Any) -> None:
        """Attach a machine-readable skip reason to the current bar context."""
        payload: Dict[str, Any] = {"reason": reason}
        if details:
            payload.update(details)
        ctx.extra["skip_reason"] = payload

    def clear_skip_reason(self, ctx: EngineContext) -> None:
        ctx.extra.pop("skip_reason", None)

    async def initialize(self, reader: Any) -> None:
        """Called once at worker startup. Override to fit models etc."""
        pass

    async def enrich_context(self, ctx: EngineContext, reader: Any) -> EngineContext:
        """Called before generate_signal. Override to add extra data to ctx.extra."""
        return ctx

    @abstractmethod
    def generate_signal(self, ctx: EngineContext) -> Optional[Signal]:
        """
        Analyse context and return a Signal, or None if no trade opportunity.

        MUST NOT look at the current open candle — only completed bars.
        """

    @abstractmethod
    def validate_signal(
        self, signal: Signal, ctx: EngineContext
    ) -> SignalDecision:
        """
        Apply engine-level filters (not risk filters) and decide
        ENTER / SKIP / SIGNAL_ONLY.
        """

    @abstractmethod
    def build_order_plan(
        self, signal: Signal, bankroll: float
    ) -> OrderPlan:
        """
        Given a valid signal and current bankroll, compute entry / stop /
        target prices and position size (stake_usd).

        MUST NOT call any exchange API — raises NotImplementedError if attempted.
        """

    @abstractmethod
    def manage_open_position(
        self, position: Position, ctx: EngineContext
    ) -> PositionAction:
        """
        Inspect current bar and decide whether to HOLD, close via stop,
        close via target, or close via timeout.
        """
