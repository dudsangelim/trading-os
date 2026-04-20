"""
Smoke test for carry_neutral_btc engine.
Validates: fill model math, engine decisions, PnL accounting.

Run: python -m trading.scripts.smoke_carry
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from trading.core.engines.carry_neutral_btc import (
    CarryAction,
    CarryContext,
    CarryNeutralBtcEngine,
)
from trading.core.execution.carry_fill_model import CarryFillModel, CarryPosition


def _assert(condition: bool, msg: str) -> None:
    if not condition:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  OK: {msg}")


def smoke_fill_model() -> None:
    print("\n[carry_fill_model]")
    model = CarryFillModel()

    fill = model.simulate_open(spot_price=80000.0, perp_price=80100.0, stake_usd=300.0)
    _assert(fill.spot_entry_price > 80000.0, "spot entry has positive slippage")
    _assert(fill.perp_entry_price < 80100.0, "perp entry has negative slippage")
    _assert(fill.entry_fees_usd > 0, "entry fees are positive")
    _assert(abs(fill.entry_fees_usd - 0.6) < 0.01, f"entry fees ~$0.60 (got {fill.entry_fees_usd:.4f})")

    pos = CarryPosition(
        trade_id=1,
        notional_usd=300.0,
        spot_entry_price=fill.spot_entry_price,
        perp_entry_price=fill.perp_entry_price,
        funding_accrued_usd=0.0,
        funding_events_count=0,
        entry_fees_usd=fill.entry_fees_usd,
    )

    # At 15% annualized funding: per event = 15% / (365*3) ≈ 0.01370%
    funding_fill = model.simulate_funding_event(pos, funding_rate_annualized=0.15)
    expected_per_event = 0.15 / (365.0 * 3.0)
    expected_usd = 300.0 * expected_per_event
    _assert(
        abs(funding_fill.funding_usd - expected_usd) < 0.0001,
        f"funding per event ~${expected_usd:.4f} (got {funding_fill.funding_usd:.4f})"
    )
    _assert(funding_fill.new_events_count == 1, "events count incremented")

    # Update position after funding
    pos.funding_accrued_usd = funding_fill.new_total_accrued_usd
    pos.funding_events_count = funding_fill.new_events_count

    close_fill = model.simulate_close(pos, current_spot=80000.0, current_perp=80100.0)
    _assert(close_fill.exit_fees_usd > 0, "exit fees are positive")
    _assert(isinstance(close_fill.pnl_usd, float), "pnl_usd is float")
    print(f"  INFO: 1-event PnL = ${close_fill.pnl_usd:.4f} (fees=${fill.entry_fees_usd:.4f}+{close_fill.exit_fees_usd:.4f}, funding=${pos.funding_accrued_usd:.4f})")


def smoke_engine_decisions() -> None:
    print("\n[carry_neutral_btc engine decisions]")
    engine = CarryNeutralBtcEngine()

    # Case: funding below threshold → SKIP
    ctx_low = CarryContext(
        funding_rate_annualized=0.10,
        funding_rate_history_24h=[0.10, 0.09, 0.11],
        spot_price=80000.0,
        perp_price=80100.0,
        bankroll_usd=300.0,
        open_position=None,
    )
    d = engine.evaluate_carry(ctx_low, threshold_upper=0.15, threshold_lower=0.05)
    _assert(d.action == CarryAction.SKIP, f"SKIP when rate<threshold (got {d.action})")

    # Case: funding above threshold, stable → OPEN_CARRY
    ctx_high = CarryContext(
        funding_rate_annualized=0.20,
        funding_rate_history_24h=[0.18, 0.19, 0.20],
        spot_price=80000.0,
        perp_price=80100.0,
        bankroll_usd=300.0,
        open_position=None,
    )
    d = engine.evaluate_carry(ctx_high, threshold_upper=0.15, threshold_lower=0.05)
    _assert(d.action == CarryAction.OPEN_CARRY, f"OPEN_CARRY when rate>threshold, stable (got {d.action})")

    # Case: funding above threshold but unstable history → SKIP
    ctx_unstable = CarryContext(
        funding_rate_annualized=0.20,
        funding_rate_history_24h=[0.05, 0.03, 0.20],
        spot_price=80000.0,
        perp_price=80100.0,
        bankroll_usd=300.0,
        open_position=None,
    )
    d = engine.evaluate_carry(ctx_unstable, threshold_upper=0.15, threshold_lower=0.05)
    _assert(d.action == CarryAction.SKIP, f"SKIP when rate unstable (got {d.action})")

    # Case: position open, funding above lower → HOLD
    from datetime import datetime, timezone, timedelta
    ctx_hold = CarryContext(
        funding_rate_annualized=0.10,
        funding_rate_history_24h=[0.10, 0.10, 0.10],
        spot_price=80000.0,
        perp_price=80100.0,
        bankroll_usd=300.0,
        open_position={"id": 1, "opened_at": datetime.now(timezone.utc) - timedelta(hours=10), "funding_events_count": 1},
    )
    d = engine.evaluate_carry(ctx_hold, threshold_upper=0.15, threshold_lower=0.05)
    _assert(d.action == CarryAction.HOLD, f"HOLD when position open, rate>lower (got {d.action})")

    # Case: position open, funding below lower → CLOSE_CARRY
    ctx_close = CarryContext(
        funding_rate_annualized=0.03,
        funding_rate_history_24h=[0.03, 0.03, 0.03],
        spot_price=80000.0,
        perp_price=80100.0,
        bankroll_usd=300.0,
        open_position={"id": 1, "opened_at": datetime.now(timezone.utc) - timedelta(hours=10), "funding_events_count": 1},
    )
    d = engine.evaluate_carry(ctx_close, threshold_upper=0.15, threshold_lower=0.05)
    _assert(d.action == CarryAction.CLOSE_CARRY, f"CLOSE_CARRY when rate<lower (got {d.action})")

    # Case: position open but max_hold exceeded → CLOSE_CARRY
    ctx_timeout = CarryContext(
        funding_rate_annualized=0.20,
        funding_rate_history_24h=[0.20, 0.20, 0.20],
        spot_price=80000.0,
        perp_price=80100.0,
        bankroll_usd=300.0,
        open_position={"id": 1, "opened_at": datetime.now(timezone.utc) - timedelta(hours=200), "funding_events_count": 25},
    )
    d = engine.evaluate_carry(ctx_timeout, threshold_upper=0.15, threshold_lower=0.05, max_hold_hours=168)
    _assert(d.action == CarryAction.CLOSE_CARRY, f"CLOSE_CARRY on timeout (got {d.action})")


def smoke_pnl_lifecycle() -> None:
    print("\n[carry PnL lifecycle — 3 funding events]")
    model = CarryFillModel()
    engine = CarryNeutralBtcEngine()

    fill = model.simulate_open(spot_price=80000.0, perp_price=80050.0, stake_usd=300.0)
    pos = CarryPosition(
        trade_id=1,
        notional_usd=300.0,
        spot_entry_price=fill.spot_entry_price,
        perp_entry_price=fill.perp_entry_price,
        funding_accrued_usd=-fill.entry_fees_usd,  # start accounting for entry cost
        funding_events_count=0,
        entry_fees_usd=fill.entry_fees_usd,
    )
    pos.funding_accrued_usd = 0.0  # entry fees tracked separately in entry_fees_usd

    total_funding = 0.0
    for i in range(3):
        ev = model.simulate_funding_event(pos, funding_rate_annualized=0.20)
        pos.funding_accrued_usd = ev.new_total_accrued_usd
        pos.funding_events_count = ev.new_events_count
        total_funding += ev.funding_usd

    close = model.simulate_close(pos, current_spot=80000.0, current_perp=80050.0)
    _assert(total_funding > 0, f"total funding positive: ${total_funding:.4f}")
    _assert(isinstance(close.pnl_usd, float), "pnl_usd is float")
    print(f"  INFO: 3-event lifecycle | funding=${total_funding:.4f} | pnl=${close.pnl_usd:.4f}")


if __name__ == "__main__":
    print("=== carry_neutral smoke test ===")
    smoke_fill_model()
    smoke_engine_decisions()
    smoke_pnl_lifecycle()
    print("\nAll smoke tests PASSED")
