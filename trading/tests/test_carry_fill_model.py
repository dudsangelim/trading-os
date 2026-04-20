"""
Unit tests for carry_fill_model.py
"""
from __future__ import annotations

import pytest
from trading.core.execution.carry_fill_model import CarryFillModel, CarryPosition


@pytest.fixture
def model() -> CarryFillModel:
    return CarryFillModel()


@pytest.fixture
def open_position(model: CarryFillModel) -> CarryPosition:
    fill = model.simulate_open(spot_price=80000.0, perp_price=80100.0, stake_usd=300.0)
    return CarryPosition(
        trade_id=1,
        notional_usd=300.0,
        spot_entry_price=fill.spot_entry_price,
        perp_entry_price=fill.perp_entry_price,
        funding_accrued_usd=0.0,
        funding_events_count=0,
        entry_fees_usd=fill.entry_fees_usd,
    )


class TestSimulateOpen:
    def test_spot_slippage_unfavourable(self, model):
        fill = model.simulate_open(80000.0, 80100.0, 300.0, slippage_bps=5.0)
        assert fill.spot_entry_price > 80000.0

    def test_perp_slippage_unfavourable(self, model):
        fill = model.simulate_open(80000.0, 80100.0, 300.0, slippage_bps=5.0)
        assert fill.perp_entry_price < 80100.0

    def test_entry_fees_positive(self, model):
        fill = model.simulate_open(80000.0, 80100.0, 300.0, fee_bps=10.0)
        assert fill.entry_fees_usd > 0

    def test_entry_fees_two_legs(self, model):
        fill = model.simulate_open(80000.0, 80100.0, 300.0, fee_bps=10.0, slippage_bps=0.0)
        expected = 300.0 * (10.0 / 10_000.0) * 2
        assert abs(fill.entry_fees_usd - expected) < 0.0001

    def test_notional_preserved(self, model):
        fill = model.simulate_open(80000.0, 80100.0, 300.0)
        assert fill.notional_usd == 300.0


class TestSimulateFundingEvent:
    def test_positive_funding_received(self, model, open_position):
        ev = model.simulate_funding_event(open_position, funding_rate_annualized=0.15)
        assert ev.funding_usd > 0

    def test_negative_funding_paid(self, model, open_position):
        ev = model.simulate_funding_event(open_position, funding_rate_annualized=-0.05)
        assert ev.funding_usd < 0

    def test_funding_formula(self, model, open_position):
        rate = 0.15
        expected = 300.0 * (rate / (365.0 * 3.0))
        ev = model.simulate_funding_event(open_position, funding_rate_annualized=rate)
        assert abs(ev.funding_usd - expected) < 1e-6

    def test_events_count_increments(self, model, open_position):
        ev = model.simulate_funding_event(open_position, funding_rate_annualized=0.20)
        assert ev.new_events_count == 1

    def test_accrued_accumulates(self, model, open_position):
        ev1 = model.simulate_funding_event(open_position, funding_rate_annualized=0.20)
        open_position.funding_accrued_usd = ev1.new_total_accrued_usd
        open_position.funding_events_count = ev1.new_events_count
        ev2 = model.simulate_funding_event(open_position, funding_rate_annualized=0.20)
        assert ev2.new_total_accrued_usd > ev1.new_total_accrued_usd
        assert ev2.new_events_count == 2


class TestSimulateClose:
    def test_exit_fees_positive(self, model, open_position):
        close = model.simulate_close(open_position, 80000.0, 80100.0)
        assert close.exit_fees_usd > 0

    def test_spot_exit_below_spot(self, model, open_position):
        close = model.simulate_close(open_position, 80000.0, 80100.0, slippage_bps=5.0)
        assert close.spot_exit_price < 80000.0

    def test_perp_exit_above_perp(self, model, open_position):
        close = model.simulate_close(open_position, 80000.0, 80100.0, slippage_bps=5.0)
        assert close.perp_exit_price > 80100.0

    def test_pnl_after_funding_events(self, model, open_position):
        # Accrue 3 events of 15% annualized on $300 stake
        for _ in range(3):
            ev = model.simulate_funding_event(open_position, 0.15)
            open_position.funding_accrued_usd = ev.new_total_accrued_usd
            open_position.funding_events_count = ev.new_events_count
        close = model.simulate_close(open_position, 80000.0, 80100.0)
        # With 3 funding events at 15%, funding >> fees so PnL should be positive
        expected_funding = 300.0 * (0.15 / (365.0 * 3.0)) * 3
        assert open_position.funding_accrued_usd == pytest.approx(expected_funding, rel=1e-4)

    def test_pnl_pct_is_fraction_of_notional(self, model, open_position):
        close = model.simulate_close(open_position, 80000.0, 80100.0)
        expected_pct = close.pnl_usd / 300.0
        assert abs(close.pnl_pct - expected_pct) < 1e-5
