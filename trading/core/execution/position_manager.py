"""
Position manager — checks stop / target / timeout on every closed bar.

Priority order (per brief):  stop > target > timeout

PnL formula (with realistic costs):
  gross_pnl = (exit_price - entry_price) * direction_factor * stake / entry_price
  fees      = stake * fee_bps/10_000          (entry notional)
             + exit_notional * fee_bps/10_000  (exit notional)
  pnl_usd   = gross_pnl - fees

Exit slippage (market orders only — SL and TIMEOUT):
  LONG  closes by selling → exit_price = ref * (1 - slippage_bps/10_000)
  SHORT closes by buying  → exit_price = ref * (1 + slippage_bps/10_000)
  TP uses a limit order → no exit slippage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..engines.base import Direction, Position, PositionAction
from ..data.candle_reader import Candle
from .fill_model import exit_fill_price


@dataclass
class CloseResult:
    action: PositionAction
    exit_price: float
    pnl_usd: float
    pnl_pct: float
    close_reason: str
    closed_at: datetime


def _pnl(
    entry_price: float,
    exit_price: float,
    direction: Direction,
    stake_usd: float,
    fee_bps: int = 10,
) -> tuple[float, float]:
    direction_factor = 1.0 if direction == Direction.LONG else -1.0
    gross = (exit_price - entry_price) * direction_factor * stake_usd / entry_price
    # fees on both legs (entry notional = stake, exit notional = stake × exit/entry)
    exit_notional = stake_usd * (exit_price / entry_price)
    fees = (stake_usd + exit_notional) * fee_bps / 10_000.0
    pnl_usd = gross - fees
    pnl_pct = pnl_usd / stake_usd
    return round(pnl_usd, 4), round(pnl_pct, 6)


def check_position(
    position: Position,
    candle: Candle,
    timeout_bars: int,
    slippage_bps: int = 5,
    fee_bps: int = 10,
    breakeven_after_r: float = 0.5,
) -> Optional[CloseResult]:
    """
    Inspect ``candle`` (the bar that just closed) against ``position``.

    Returns a CloseResult if the position should be closed, else None.
    Priority: break-even adjustment → stop > target > timeout.

    slippage_bps:       used for market-order exits (SL, TIMEOUT). TP uses limit — no exit slippage.
    fee_bps:            Binance taker fee per side (default 10 = 0.10%).
    breakeven_after_r:  move stop to entry when unrealised profit reaches this multiple of R.
                        Set to 0.0 to disable. Mutates position.stop_price in-place so the
                        updated value is persisted by the caller.
    """
    now = datetime.now(timezone.utc)
    direction = position.direction
    engine_id = position.engine_id
    bars_held = position.bars_held  # already incremented by caller

    # ---- Break-even: advance stop to entry after N×R profit ---------------
    if breakeven_after_r > 0:
        R = abs(position.entry_price - position.stop_price)
        if R > 0:
            if direction == Direction.LONG and position.stop_price < position.entry_price:
                if candle.high >= position.entry_price + breakeven_after_r * R:
                    position.stop_price = position.entry_price
            elif direction == Direction.SHORT and position.stop_price > position.entry_price:
                if candle.low <= position.entry_price - breakeven_after_r * R:
                    position.stop_price = position.entry_price

    # ---- Stop loss (market order → exit slippage applies) ----------------
    if direction == Direction.LONG and candle.low <= position.stop_price:
        exit_price = exit_fill_price(position.stop_price, direction, engine_id)
        pnl_usd, pnl_pct = _pnl(position.entry_price, exit_price, direction, position.stake_usd, fee_bps)
        return CloseResult(
            action=PositionAction.CLOSE_SL,
            exit_price=exit_price,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            close_reason="CLOSED_SL",
            closed_at=now,
        )
    if direction == Direction.SHORT and candle.high >= position.stop_price:
        exit_price = exit_fill_price(position.stop_price, direction, engine_id)
        pnl_usd, pnl_pct = _pnl(position.entry_price, exit_price, direction, position.stake_usd, fee_bps)
        return CloseResult(
            action=PositionAction.CLOSE_SL,
            exit_price=exit_price,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            close_reason="CLOSED_SL",
            closed_at=now,
        )

    # ---- Take profit (limit order → no exit slippage) --------------------
    if direction == Direction.LONG and candle.high >= position.target_price:
        exit_price = position.target_price
        pnl_usd, pnl_pct = _pnl(position.entry_price, exit_price, direction, position.stake_usd, fee_bps)
        return CloseResult(
            action=PositionAction.CLOSE_TP,
            exit_price=exit_price,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            close_reason="CLOSED_TP",
            closed_at=now,
        )
    if direction == Direction.SHORT and candle.low <= position.target_price:
        exit_price = position.target_price
        pnl_usd, pnl_pct = _pnl(position.entry_price, exit_price, direction, position.stake_usd, fee_bps)
        return CloseResult(
            action=PositionAction.CLOSE_TP,
            exit_price=exit_price,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            close_reason="CLOSED_TP",
            closed_at=now,
        )

    # ---- Timeout (market order → exit slippage applies) ------------------
    if bars_held >= timeout_bars:
        exit_price = exit_fill_price(candle.close, direction, engine_id)
        pnl_usd, pnl_pct = _pnl(position.entry_price, exit_price, direction, position.stake_usd, fee_bps)
        return CloseResult(
            action=PositionAction.CLOSE_TIMEOUT,
            exit_price=exit_price,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            close_reason="CLOSED_TIMEOUT",
            closed_at=now,
        )

    return None
