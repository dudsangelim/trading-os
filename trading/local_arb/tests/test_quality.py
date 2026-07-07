from trading.local_arb.quality import quality_score

from .conftest import NOW, make_book


def test_fresh_deep_book_scores_100():
    score, flags = quality_score(make_book(n_levels=6), NOW, max_age_s=10.0)
    assert score == 100
    assert flags == []


def test_empty_book_scores_zero():
    book = make_book()
    book.asks = []
    score, flags = quality_score(book, NOW)
    assert score == 0
    assert flags == ["empty_book"]


def test_stale_book_penalized():
    book = make_book(ts=NOW - 60.0)
    score, flags = quality_score(book, NOW, max_age_s=10.0)
    assert "stale" in flags
    assert score == 60


def test_thin_book_penalized():
    score, flags = quality_score(make_book(n_levels=2), NOW)
    assert "thin_book" in flags
    assert score == 80


def test_crossed_book_penalized():
    book = make_book(bid_start=5.700, ask_start=5.565)
    score, flags = quality_score(book, NOW)
    assert "crossed" in flags


def test_high_latency_penalized_and_clamped_at_zero():
    book = make_book(n_levels=2, ts=NOW - 60.0, latency_ms=5000.0)
    book.bids[0] = type(book.bids[0])(book.asks[0].price + 0.01, 100.0)  # também crossed
    score, flags = quality_score(book, NOW, max_age_s=10.0)
    assert {"stale", "thin_book", "crossed", "high_latency"} == set(flags)
    assert score == 0  # 100-40-20-30-10 = 0, clampado em [0,100]
