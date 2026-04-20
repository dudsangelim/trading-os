"""
Engine Carry Neutral BTC — delta-neutral funding capture.

Strategy: open long spot + short perp when annualized funding exceeds threshold.
Accumulate funding accrual every 8h cycle. Close when funding falls below lower
threshold or max_hold_hours is reached.

This engine has an autonomous loop (carry_worker) and a custom evaluate_carry()
interface. The BaseEngine abstract methods are implemented as stubs since this
engine never goes through the standard generate_signal → validate_signal path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from .base import (
    BaseEngine,
    Direction,
    EngineContext,
    OrderPlan,
    Position,
    PositionAction,
    Signal,
    SignalAction,
    SignalDecision,
)

logger = logging.getLogger(__name__)

ENGINE_ID = "carry_neutral_btc"


class CarryAction(str, Enum):
    OPEN_CARRY = "OPEN_CARRY"
    CLOSE_CARRY = "CLOSE_CARRY"
    HOLD = "HOLD"
    SKIP = "SKIP"


@dataclass
class CarryContext:
    funding_rate_annualized: float
    funding_rate_history_24h: list  # list[float] of last 3 funding rates
    spot_price: float
    perp_price: float
    bankroll_usd: float
    open_position: Optional[Dict[str, Any]]  # dict from tr_paper_trades row


@dataclass
class CarryDecision:
    action: CarryAction
    reason: str
    funding_rate_annualized: float = 0.0


class CarryNeutralBtcEngine(BaseEngine):
    ENGINE_ID = ENGINE_ID

    def __init__(self) -> None:
        super().__init__(engine_id=self.ENGINE_ID, symbol="BTCUSDT")

    # ------------------------------------------------------------------
    # Custom carry interface
    # ------------------------------------------------------------------

    def evaluate_carry(
        self,
        ctx: CarryContext,
        threshold_upper: float = 0.15,
        threshold_lower: float = 0.05,
        max_hold_hours: int = 168,
    ) -> CarryDecision:
        rate = ctx.funding_rate_annualized

        if ctx.open_position is not None:
            opened_at = ctx.open_position.get("opened_at")
            hours_held = 0.0
            if opened_at is not None:
                now = datetime.now(timezone.utc)
                if hasattr(opened_at, "total_seconds"):
                    hours_held = 0.0
                else:
                    hours_held = (now - opened_at).total_seconds() / 3600.0

            if rate < threshold_lower:
                return CarryDecision(
                    action=CarryAction.CLOSE_CARRY,
                    reason="FUNDING_BELOW_LOWER_THRESHOLD",
                    funding_rate_annualized=rate,
                )
            if hours_held >= max_hold_hours:
                return CarryDecision(
                    action=CarryAction.CLOSE_CARRY,
                    reason="MAX_HOLD_HOURS_REACHED",
                    funding_rate_annualized=rate,
                )
            return CarryDecision(
                action=CarryAction.HOLD,
                reason="FUNDING_IN_RANGE",
                funding_rate_annualized=rate,
            )

        # No open position — check if we should open
        if rate < threshold_upper:
            return CarryDecision(
                action=CarryAction.SKIP,
                reason="FUNDING_BELOW_UPPER_THRESHOLD",
                funding_rate_annualized=rate,
            )

        # Stability check: at least 2 of last 3 readings must be above upper threshold
        history = ctx.funding_rate_history_24h or []
        above_threshold = sum(1 for r in history if r >= threshold_upper)
        if history and above_threshold < min(2, len(history)):
            return CarryDecision(
                action=CarryAction.SKIP,
                reason="FUNDING_UNSTABLE",
                funding_rate_annualized=rate,
            )

        if ctx.bankroll_usd <= 0:
            return CarryDecision(
                action=CarryAction.SKIP,
                reason="BANKROLL_UNAVAILABLE",
                funding_rate_annualized=rate,
            )

        return CarryDecision(
            action=CarryAction.OPEN_CARRY,
            reason="FUNDING_ABOVE_THRESHOLD",
            funding_rate_annualized=rate,
        )

    # ------------------------------------------------------------------
    # BaseEngine stubs — carry uses its own evaluate_carry() path
    # ------------------------------------------------------------------

    def generate_signal(self, ctx: EngineContext) -> Optional[Signal]:
        # Reserved for future ACTIVE role in standard worker loop
        return None

    def validate_signal(self, signal: Signal, ctx: EngineContext) -> SignalDecision:
        # Reserved for future ACTIVE role in standard worker loop
        return SignalDecision(signal_id=None, action=SignalAction.SKIP, reason="CARRY_ENGINE_USES_CUSTOM_PATH")

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        # Reserved for future ACTIVE role in standard worker loop
        raise NotImplementedError("CarryNeutralBtcEngine uses evaluate_carry(), not build_order_plan()")

    def manage_open_position(self, position: Position, ctx: EngineContext) -> PositionAction:
        # Reserved for future ACTIVE role in standard worker loop
        return PositionAction.HOLD
