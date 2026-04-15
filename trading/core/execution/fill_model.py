"""
Fill model — simulates order execution without touching any exchange API.

Entry (market order):
  LONG  → entry = close * (1 + slippage_bps / 10_000)
  SHORT → entry = close * (1 - slippage_bps / 10_000)

Exit — market order (SL, TIMEOUT):
  LONG  → exit = ref_price * (1 - slippage_bps / 10_000)   ← sells below
  SHORT → exit = ref_price * (1 + slippage_bps / 10_000)   ← buys above

Exit — limit order (TP):
  No slippage — executes at the exact target price.

Fees (Binance taker, applied to both entry and exit notional):
  fee = notional * fee_bps / 10_000
  Round-trip cost ≈ 2 × fee_bps.

Calling any real exchange API raises NotImplementedError immediately.
"""
from __future__ import annotations

from ..config.settings import SLIPPAGE_BPS
from ..engines.base import Direction


def simulated_fill_price(
    close_price: float,
    direction: Direction,
    engine_id: str,
) -> float:
    """Entry fill — market order, unfavourable slippage."""
    bps = SLIPPAGE_BPS.get(engine_id, 5)
    factor = 1.0 + bps / 10_000.0 if direction == Direction.LONG else 1.0 - bps / 10_000.0
    return close_price * factor


def exit_fill_price(
    ref_price: float,
    direction: Direction,
    engine_id: str,
) -> float:
    """
    Exit fill for market orders (SL / TIMEOUT).
    LONG  closes by selling → gets price below ref.
    SHORT closes by buying  → pays price above ref.
    """
    bps = SLIPPAGE_BPS.get(engine_id, 5)
    factor = 1.0 - bps / 10_000.0 if direction == Direction.LONG else 1.0 + bps / 10_000.0
    return ref_price * factor


def submit_live_order(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Guard — calling this function is forbidden."""
    raise NotImplementedError(
        "NO_LIVE_ORDER: calling a real exchange API is not permitted "
        "in this system."
    )
