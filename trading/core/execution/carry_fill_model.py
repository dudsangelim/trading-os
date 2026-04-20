"""
Carry fill model — simulates execution for delta-neutral carry positions.

A carry position has two legs:
  - Long spot: buys BTC at spot_price * (1 + slippage)
  - Short perp: sells BTC perp at perp_price * (1 - slippage)

PnL sources:
  1. Funding accrual (primary)
  2. Basis change on close (perp_entry - perp_exit ± slippage)
  3. Fees: fee_bps per side, per leg (4 fills total: open spot, open perp, close spot, close perp)

Delta-neutral assumption: spot and perp move together so price changes cancel.
Net P&L = funding_accrued - entry_fees - exit_fees + basis_change
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CarryPosition:
    trade_id: int
    notional_usd: float           # stake_usd (= spot and perp leg size)
    spot_entry_price: float
    perp_entry_price: float
    funding_accrued_usd: float = 0.0
    funding_events_count: int = 0
    entry_fees_usd: float = 0.0
    variant: str = "sell_accrual"  # 'sell_accrual' | 'hold_accrual'


@dataclass
class CarryOpenFill:
    spot_entry_price: float
    perp_entry_price: float
    notional_usd: float
    entry_fees_usd: float


@dataclass
class CarryFundingFill:
    funding_usd: float            # positive = received, negative = paid
    new_total_accrued_usd: float
    new_events_count: int
    funding_rate_per_event: float


@dataclass
class CarryCloseFill:
    spot_exit_price: float
    perp_exit_price: float
    exit_fees_usd: float
    basis_change_usd: float       # perp basis captured on close
    pnl_usd: float                # total: funding - fees + basis
    pnl_pct: float                # as fraction of notional


class CarryFillModel:
    def simulate_open(
        self,
        spot_price: float,
        perp_price: float,
        stake_usd: float,
        slippage_bps: float = 5.0,
        fee_bps: float = 10.0,
    ) -> CarryOpenFill:
        slip = slippage_bps / 10_000.0
        fee = fee_bps / 10_000.0

        # Long spot: buy above spot (unfavourable slippage)
        spot_fill = spot_price * (1.0 + slip)
        # Short perp: sell below perp (unfavourable slippage)
        perp_fill = perp_price * (1.0 - slip)

        # Fees on both legs at open
        entry_fees = stake_usd * fee * 2.0  # spot leg + perp leg

        return CarryOpenFill(
            spot_entry_price=spot_fill,
            perp_entry_price=perp_fill,
            notional_usd=stake_usd,
            entry_fees_usd=entry_fees,
        )

    def simulate_funding_event(
        self,
        position: CarryPosition,
        funding_rate_annualized: float,
        variant: str = "sell_accrual",
    ) -> CarryFundingFill:
        # Funding rate is annualized; each event covers 8h = 1/3 day = 1/1095 year
        funding_per_event_pct = funding_rate_annualized / (365.0 * 3.0)
        # Short perp receives funding when rate is positive
        funding_usd = position.notional_usd * funding_per_event_pct

        new_accrued = position.funding_accrued_usd + funding_usd
        new_count = position.funding_events_count + 1

        return CarryFundingFill(
            funding_usd=funding_usd,
            new_total_accrued_usd=new_accrued,
            new_events_count=new_count,
            funding_rate_per_event=funding_per_event_pct,
        )

    def simulate_close(
        self,
        position: CarryPosition,
        current_spot: float,
        current_perp: float,
        slippage_bps: float = 5.0,
        fee_bps: float = 10.0,
    ) -> CarryCloseFill:
        slip = slippage_bps / 10_000.0
        fee = fee_bps / 10_000.0

        # Close spot (sell): receive below current spot
        spot_exit = current_spot * (1.0 - slip)
        # Close perp short (buy back): pay above current perp
        perp_exit = current_perp * (1.0 + slip)

        exit_fees = position.notional_usd * fee * 2.0

        # Basis change: original perp_entry - perp_exit (positive if perp fell)
        basis_change = position.notional_usd * (
            (position.perp_entry_price - perp_exit) / position.perp_entry_price
        )

        pnl = (
            position.funding_accrued_usd
            - position.entry_fees_usd
            - exit_fees
            + basis_change
        )
        pnl_pct = pnl / position.notional_usd if position.notional_usd > 0 else 0.0

        return CarryCloseFill(
            spot_exit_price=spot_exit,
            perp_exit_price=perp_exit,
            exit_fees_usd=exit_fees,
            basis_change_usd=basis_change,
            pnl_usd=round(pnl, 4),
            pnl_pct=round(pnl_pct, 8),
        )
