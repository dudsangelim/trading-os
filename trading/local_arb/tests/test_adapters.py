"""Adapters: sintético determinístico, REST genérico com http_get injetado, falhas controladas."""

import pytest

from trading.local_arb.adapters import (
    SYNTHETIC_TS,
    PublicRestAdapter,
    SyntheticAdapter,
    build_adapters,
)
from trading.local_arb.config import DEFAULT_CONFIG_PATH, load_config

NOW = 1_750_000_000.0


@pytest.fixture
def cfg():
    return load_config(DEFAULT_CONFIG_PATH)


class TestSyntheticAdapter:
    def test_returns_ok_book(self):
        ad = SyntheticAdapter("novadax", "USDT/BRL", bid=5.560, ask=5.565)
        res = ad.fetch_order_book(now_ts=NOW)
        assert res.ok and res.error is None
        assert res.book.best_bid == pytest.approx(5.560)
        assert res.book.best_ask == pytest.approx(5.565)
        assert res.book.best_bid < res.book.best_ask
        assert len(res.book.bids) == 6 and len(res.book.asks) == 6

    def test_deterministic(self):
        ad = SyntheticAdapter("x", "USDT/BRL", bid=5.6, ask=5.61)
        a = ad.fetch_order_book(now_ts=NOW)
        b = ad.fetch_order_book(now_ts=NOW)
        assert a.book.ts == b.book.ts == NOW - 1.0
        assert [lvl.price for lvl in a.book.bids] == [lvl.price for lvl in b.book.bids]

    def test_default_ts_is_fixed(self):
        res = SyntheticAdapter("x", "USDT/BRL", 5.6, 5.61).fetch_order_book()
        assert res.book.ts == SYNTHETIC_TS - 1.0


class TestPublicRestAdapter:
    API = {"url": "https://example.test/depth", "data_path": ["data"],
           "level_format": "pair", "timeout_s": 1}

    def test_parses_pair_payload(self):
        payload = {"data": {"bids": [["5.60", "100"], ["5.59", "50"]],
                            "asks": [["5.62", "80"], ["5.63", "40"]]}}
        ad = PublicRestAdapter("novadax", "USDT/BRL", self.API,
                               http_get=lambda url, t: payload)
        res = ad.fetch_order_book(now_ts=NOW)
        assert res.ok
        assert res.book.best_bid == pytest.approx(5.60)
        assert res.book.best_ask == pytest.approx(5.62)
        assert res.book.ts == NOW

    def test_parses_obj_payload_and_sorts(self):
        payload = {"bids": [{"price": 5.59, "amount": 50}, {"price": 5.60, "amount": 100}],
                   "asks": [{"price": 5.63, "amount": 40}, {"price": 5.62, "amount": 80}]}
        api = {"url": "https://example.test/ob", "level_format": "obj",
               "price_key": "price", "size_key": "amount"}
        res = PublicRestAdapter("bitypreco", "USDT/BRL", api,
                                http_get=lambda url, t: payload).fetch_order_book(now_ts=NOW)
        assert res.ok
        assert res.book.best_bid == pytest.approx(5.60)   # bids desc
        assert res.book.best_ask == pytest.approx(5.62)   # asks asc

    def test_network_failure_is_controlled(self):
        def boom(url, t):
            raise TimeoutError("connect timeout")

        res = PublicRestAdapter("novadax", "USDT/BRL", self.API,
                                http_get=boom).fetch_order_book(now_ts=NOW)
        assert not res.ok and res.book is None
        assert "fetch_error" in res.flags
        assert "TimeoutError" in res.error

    def test_bad_payload_is_controlled(self):
        res = PublicRestAdapter("novadax", "USDT/BRL", self.API,
                                http_get=lambda url, t: {"oops": True}).fetch_order_book(now_ts=NOW)
        assert not res.ok and "parse_error" in res.flags

    def test_empty_book_is_controlled(self):
        payload = {"data": {"bids": [], "asks": []}}
        res = PublicRestAdapter("novadax", "USDT/BRL", self.API,
                                http_get=lambda url, t: payload).fetch_order_book(now_ts=NOW)
        assert not res.ok and "empty_book" in res.flags

    def test_missing_endpoint_is_controlled(self):
        res = PublicRestAdapter("x", "USDT/BRL", None).fetch_order_book(now_ts=NOW)
        assert not res.ok and "no_endpoint" in res.flags


class TestBuildAdapters:
    def test_synthetic_covers_enabled_exchanges(self, cfg):
        adapters = build_adapters(cfg, synthetic=True)
        enabled = [k for k, v in cfg["exchanges"].items() if v.get("enabled")]
        assert sorted(a.exchange for a in adapters) == sorted(enabled)
        assert all(isinstance(a, SyntheticAdapter) for a in adapters)
        # cenário padrão tem arb: novadax barata, mercadobitcoin cara
        books = {a.exchange: a.fetch_order_book(now_ts=NOW).book for a in adapters}
        assert books["mercadobitcoin"].best_bid > books["novadax"].best_ask

    def test_rest_mode_builds_rest_adapters(self, cfg):
        adapters = build_adapters(cfg, synthetic=False, http_get=lambda url, t: {})
        assert all(isinstance(a, PublicRestAdapter) for a in adapters)

    def test_disabled_exchange_is_skipped(self, cfg):
        cfg["exchanges"]["bitypreco"]["enabled"] = False
        adapters = build_adapters(cfg, synthetic=True)
        assert "bitypreco" not in [a.exchange for a in adapters]
