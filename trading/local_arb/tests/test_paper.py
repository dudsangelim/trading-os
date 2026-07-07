import pytest

from trading.local_arb.models import OrderBookLevel
from trading.local_arb.paper import fill_buy, fill_sell, simulate_arb

from .conftest import make_book


def test_fill_buy_multilevel_walks_worse_prices():
    asks = [OrderBookLevel(10.0, 10.0), OrderBookLevel(11.0, 10.0), OrderBookLevel(12.0, 10.0)]
    # 100 esgota o nível 1 (10*10); mais 55 vem do nível 2 a 11 → vwap > 10
    base, spent, ratio = fill_buy(asks, 155.0)
    assert ratio == pytest.approx(1.0)
    assert spent == pytest.approx(155.0)
    assert base == pytest.approx(10.0 + 55.0 / 11.0)
    vwap = spent / base
    assert 10.0 < vwap < 11.0


def test_fill_buy_respects_level_participation():
    asks = [OrderBookLevel(10.0, 10.0), OrderBookLevel(11.0, 10.0)]
    # com 50% de participação, só 50 de notional por nível 1
    base, spent, ratio = fill_buy(asks, 100.0, level_participation=0.5)
    assert spent == pytest.approx(100.0)
    assert base == pytest.approx(5.0 + 50.0 / 11.0)


def test_fill_buy_partial_when_book_too_shallow():
    asks = [OrderBookLevel(10.0, 5.0)]  # só 50 de notional
    base, spent, ratio = fill_buy(asks, 100.0)
    assert spent == pytest.approx(50.0)
    assert ratio == pytest.approx(0.5)


def test_fill_sell_multilevel():
    bids = [OrderBookLevel(12.0, 5.0), OrderBookLevel(11.0, 5.0)]
    sold, received, ratio = fill_sell(bids, 8.0)
    assert sold == pytest.approx(8.0)
    assert received == pytest.approx(5 * 12.0 + 3 * 11.0)
    assert ratio == pytest.approx(1.0)


def test_simulate_arb_filled_with_fees_and_decay():
    buy_book = make_book(exchange="novadax", bid_start=5.560, ask_start=5.565, size=500.0)
    sell_book = make_book(exchange="mercadobitcoin", bid_start=5.650, ask_start=5.655, size=500.0)
    expected = 30.0
    trade = simulate_arb(
        buy_book, sell_book,
        size_quote=1000.0,
        fee_buy_bps=50.0, fee_sell_bps=70.0,
        expected_net_bps=expected,
        min_fill_ratio=0.95,
        level_participation=0.5,
    )
    assert trade.status == "filled"
    assert trade.fill_ratio >= 0.95
    # gross ≈ 152.7 bps - 120 bps de fees ≈ ~30 bps líquido (menos slippage de nível)
    assert trade.realized_net_bps > 0
    assert trade.decay_bps == pytest.approx(expected - trade.realized_net_bps)
    # taker fee sempre cobrada: realized < spread bruto puro
    gross_pure = (5.650 - 5.565) / 5.565 * 10_000
    assert trade.realized_net_bps < gross_pure - 100  # fees somam 120 bps


def test_simulate_arb_rejects_below_min_fill():
    buy_book = make_book(size=500.0)
    sell_book = make_book(exchange="mercadobitcoin", bid_start=5.650, ask_start=5.655, size=500.0)
    # bids do sell_book rasos: só 1 nível de 10 USDT — não absorve a compra
    sell_book.bids = [OrderBookLevel(5.650, 10.0)]
    trade = simulate_arb(
        buy_book, sell_book,
        size_quote=1000.0,
        fee_buy_bps=50.0, fee_sell_bps=70.0,
        expected_net_bps=30.0,
        min_fill_ratio=0.95,
    )
    assert trade.status == "rejected_min_fill"
    assert trade.fill_ratio < 0.95
    assert trade.realized_net_bps == 0.0
    assert trade.base_filled == 0.0


def test_simulate_arb_decay_positive_when_multilevel_slippage():
    # book raso força consumir níveis piores → realized < expected → decay > 0
    buy_book = make_book(exchange="novadax", ask_start=5.565, size=60.0)
    sell_book = make_book(exchange="mercadobitcoin", bid_start=5.650, size=60.0)
    gross_top = (5.650 - 5.565) / 5.565 * 10_000
    expected = gross_top - 120.0  # como se enchesse tudo no topo do book
    trade = simulate_arb(
        buy_book, sell_book,
        size_quote=1000.0,
        fee_buy_bps=50.0, fee_sell_bps=70.0,
        expected_net_bps=expected,
        min_fill_ratio=0.95,
    )
    assert trade.status == "filled"
    assert trade.decay_bps > 0
