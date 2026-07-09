"""Break-even da rota: quanto de spread bruto é preciso só para empatar."""

from __future__ import annotations

from .models import FeeSchedule, RouteBreakEven


def compute_break_even(
    fee_buy: FeeSchedule,
    fee_sell: FeeSchedule,
    slippage_est_bps: float,
    latency_haircut_bps: float,
    rebalance_amortized_bps: float,
    fee_uncertainty_bps: float = 0.0,
) -> RouteBreakEven:
    """break_even_bps = taker buy + taker sell + slippage + latency + rebalance amortizado.

    Sempre assume taker nos dois lados (paper pessimista).
    """
    return RouteBreakEven(
        buy_exchange=fee_buy.exchange,
        sell_exchange=fee_sell.exchange,
        taker_buy_bps=fee_buy.taker_bps,
        taker_sell_bps=fee_sell.taker_bps,
        slippage_est_bps=slippage_est_bps,
        latency_haircut_bps=latency_haircut_bps,
        rebalance_amortized_bps=rebalance_amortized_bps + fee_uncertainty_bps,
    )


def break_even_from_config(fee_buy: FeeSchedule, fee_sell: FeeSchedule, cfg: dict) -> RouteBreakEven:
    be = cfg["break_even"]
    return compute_break_even(
        fee_buy,
        fee_sell,
        slippage_est_bps=float(be["slippage_est_bps"]),
        latency_haircut_bps=float(be["latency_haircut_bps"]),
        rebalance_amortized_bps=float(be["rebalance_amortized_bps"]),
        fee_uncertainty_bps=float(be.get("fee_uncertainty_bps", 0.0)),
    )
