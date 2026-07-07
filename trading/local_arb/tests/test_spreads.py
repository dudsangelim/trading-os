import pytest

from trading.local_arb.spreads import depth_ok, gross_spread_bps, route_depth_ok

from .conftest import make_book


def test_gross_spread_positive():
    buy_book = make_book(exchange="novadax", bid_start=5.560, ask_start=5.565)
    sell_book = make_book(exchange="mercadobitcoin", bid_start=5.650, ask_start=5.655)
    spread = gross_spread_bps(buy_book, sell_book)
    expected = (5.650 - 5.565) / 5.565 * 10_000
    assert spread == pytest.approx(expected)
    assert spread > 0


def test_gross_spread_negative_when_no_arb():
    buy_book = make_book(bid_start=5.650, ask_start=5.655)
    sell_book = make_book(bid_start=5.560, ask_start=5.565)
    assert gross_spread_bps(buy_book, sell_book) < 0


def test_gross_spread_none_on_empty_book():
    buy_book = make_book()
    sell_book = make_book()
    sell_book.bids = []
    assert gross_spread_bps(buy_book, sell_book) is None


def test_depth_ok_by_size():
    # 5 níveis × 100 USDT ≈ 5.565*100*5 ≈ 2783 BRL de notional nos asks
    book = make_book(n_levels=5, size=100.0)
    assert depth_ok(book, "buy", 1000.0)
    assert not depth_ok(book, "buy", 5000.0)
    assert depth_ok(book, "sell", 1000.0)
    assert not depth_ok(book, "sell", 5000.0)


def test_depth_ok_invalid_side():
    with pytest.raises(ValueError):
        depth_ok(make_book(), "hold", 100.0)


def test_route_depth_ok_requires_both_sides():
    deep = make_book(size=1000.0)
    shallow = make_book(size=1.0)
    assert route_depth_ok(deep, deep, 2000.0)
    assert not route_depth_ok(deep, shallow, 2000.0)
