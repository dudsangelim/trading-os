import pytest

from trading.local_arb.models import OrderBookLevel, OrderBookSnapshot

NOW = 1_750_000_000.0


def make_book(
    exchange="novadax",
    bid_start=5.560,
    ask_start=5.565,
    n_levels=5,
    size=100.0,
    ts=NOW - 1.0,
    latency_ms=100.0,
):
    return OrderBookSnapshot(
        exchange=exchange,
        symbol="USDT/BRL",
        ts=ts,
        bids=[OrderBookLevel(bid_start - i * 0.002, size) for i in range(n_levels)],
        asks=[OrderBookLevel(ask_start + i * 0.002, size) for i in range(n_levels)],
        latency_ms=latency_ms,
    )


@pytest.fixture
def now_ts():
    return NOW
