from trading.local_arb.break_even import break_even_from_config, compute_break_even
from trading.local_arb.models import FeeSchedule


def test_break_even_is_sum_of_components():
    fee_buy = FeeSchedule(exchange="novadax", taker_bps=50.0, maker_bps=25.0)
    fee_sell = FeeSchedule(exchange="mercadobitcoin", taker_bps=70.0, maker_bps=30.0)
    be = compute_break_even(
        fee_buy,
        fee_sell,
        slippage_est_bps=5.0,
        latency_haircut_bps=3.0,
        rebalance_amortized_bps=8.0,
    )
    assert be.total_bps == 50.0 + 70.0 + 5.0 + 3.0 + 8.0
    assert be.buy_exchange == "novadax"
    assert be.sell_exchange == "mercadobitcoin"


def test_break_even_uses_taker_not_maker():
    fee = FeeSchedule(exchange="x", taker_bps=100.0, maker_bps=0.0)
    be = compute_break_even(fee, fee, 0.0, 0.0, 0.0)
    assert be.total_bps == 200.0


def test_break_even_from_config():
    cfg = {"break_even": {"slippage_est_bps": 1, "latency_haircut_bps": 2, "rebalance_amortized_bps": 3}}
    fee = FeeSchedule(exchange="x", taker_bps=10.0, maker_bps=5.0)
    be = break_even_from_config(fee, fee, cfg)
    assert be.total_bps == 10 + 10 + 1 + 2 + 3


def test_break_even_adds_fee_uncertainty_buffer():
    cfg = {"break_even": {
        "slippage_est_bps": 1,
        "latency_haircut_bps": 2,
        "rebalance_amortized_bps": 3,
        "fee_uncertainty_bps": 10,
    }}
    fee = FeeSchedule(exchange="x", taker_bps=10.0, maker_bps=5.0)
    be = break_even_from_config(fee, fee, cfg)
    assert be.total_bps == 10 + 10 + 1 + 2 + 3 + 10
