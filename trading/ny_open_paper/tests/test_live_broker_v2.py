"""
Mock-exchange tests for LiveBroker v2 (pre-placed resting orders).

No testnet exists for this venue, so we validate the ORDER LIFECYCLE against a fake
executor that records calls and lets the test drive exchange position state. The star
case is the intra-candle round-trip that v1 missed (2026-07-06): with v2 the entry +
protective stop must be RESTING on the exchange during the retest window, so the venue
fills/stops in real time.

Run: python3 -m trading.ny_open_paper.tests.test_live_broker_v2
"""
from __future__ import annotations

import trading.ny_open_paper.live_broker as lb
from trading.ny_open_paper.live_broker import EngineSnapshot, LiveBroker


class Pos:
    def __init__(self, side=None, amount=0.0, entry=0.0):
        self.side = side
        self.amount = amount
        self.entry_price = entry
        self.unrealized_pnl = 0.0


class OO:
    def __init__(self):
        self.entry = None
        self.stops = []
        self.take_profits = []
        self.other = []


class MockExecutor:
    """Records order calls; the test sets `self._pos` to simulate exchange fills."""

    def __init__(self, *a, **k):
        self.calls = []
        self._pos = Pos()          # test-controlled exchange position
        self._order_seq = 0

    # -- reads --
    def position(self):
        return self._pos

    def open_orders(self):
        return OO()

    def mark_price(self):
        return 100.0

    def amount_for_notional(self, notional, price):
        return round(notional / price, 4)

    def _oid(self):
        self._order_seq += 1
        return f"ord{self._order_seq}"

    # -- orders --
    def place_stop_close_position(self, close_side, stop_price):
        self.calls.append(("stop_close", close_side, round(stop_price, 2)))
        return {"id": self._oid()}

    def place_stop_entry(self, side, trigger_price, amount):
        self.calls.append(("stop_entry", side, round(trigger_price, 2), amount))
        return {"id": self._oid()}

    def place_stop_market(self, close_side, stop_price, amount):
        self.calls.append(("stop_market", close_side, round(stop_price, 2)))
        return {"id": self._oid()}

    def market_open(self, side, amount, ref):
        self.calls.append(("market_open", side, amount))
        return {"id": self._oid()}

    def market_close(self, close_side, amount):
        self.calls.append(("market_close", close_side, amount))
        self._pos = Pos()
        return {"id": self._oid(), "average": 100.0}

    def cancel_all(self):
        self.calls.append(("cancel_all",))

    def flatten(self):
        self.calls.append(("flatten",))
        self._pos = Pos()

    def fetch_order(self, oid):
        return {"average": 100.0}

    def types(self):
        return [c[0] for c in self.calls]


def _broker():
    """Build a LiveBroker with the real __init__ bypassed (no ccxt / no network)."""
    b = LiveBroker.__new__(LiveBroker)
    import threading
    b.notional = 240.0
    b.notify = lambda *a, **k: None
    b._lock = threading.RLock()
    b._reset()
    b._last_seq = -1
    b.ex = MockExecutor()
    return b


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


# extreme=100 short: entry sell-stop triggers on retest DOWN to 100; protective buy-stop
# above at 100.6 (0.6% stop). ref (broken-out) price ~100.05, safely below protective.
SHORT_PENDING = EngineSnapshot("pending", -1, 100.0, 100.6, 100.05)


def test_arm_places_both_resting_orders():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    t = b.ex.types()
    _assert("stop_close" in t and "stop_entry" in t, f"arm must place both orders, got {t}")
    # protective placed BEFORE entry (no naked window)
    _assert(t.index("stop_close") < t.index("stop_entry"), "protective must precede entry")
    _assert(b.armed and not b.in_position, "state must be armed, not in_position")
    print("ok  test_arm_places_both_resting_orders")


def test_unsafe_stop_refused():
    b = _broker()
    # protective stop 99.9 is BELOW ref 100.05 for a short -> would trigger immediately
    b.reconcile(1, EngineSnapshot("pending", -1, 100.0, 99.9, 100.05))
    _assert(not b.armed, "must refuse to arm when stop would trigger immediately")
    _assert("stop_close" not in b.ex.types() or ("cancel_all" in b.ex.types()),
            "must not leave a naked protective order")
    print("ok  test_unsafe_stop_refused")


def test_promote_on_fill():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)   # exchange filled the entry
    b.reconcile(2, EngineSnapshot("in_position", -1, 0.0, 100.6, 99.5))
    _assert(b.in_position and not b.armed, "must promote to in_position after fill")
    print("ok  test_promote_on_fill")


def test_be_stop_move():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)
    b.reconcile(2, EngineSnapshot("in_position", -1, 0.0, 100.6, 99.5))
    b.ex.calls.clear()
    # engine trails stop to breakeven (100.0)
    b.reconcile(3, EngineSnapshot("in_position", -1, 0.0, 100.0, 99.5))
    t = b.ex.types()
    _assert("cancel_all" in t and "stop_close" in t, f"BE move must cancel+replace, got {t}")
    _assert(abs(b.current_stop - 100.0) < 1e-9, "current_stop must update to BE")
    print("ok  test_be_stop_move")


def test_round_trip_captured():
    """THE bug: entry fills AND stops inside one candle. With v2 the orders were resting
    on the exchange during the retest window (armed on the prior candle), so the venue
    executes it for real. The engine returns flat next candle -> broker tears down and
    the account ends flat. Contrast v1, which never placed any order at all."""
    b = _broker()
    # candle N: break accepted -> WAITING_RETEST -> broker arms the resting pair
    b.reconcile(1, SHORT_PENDING)
    _assert("stop_entry" in b.ex.types(), "entry must be RESTING before the round-trip candle")
    # candle N+1: exchange filled + stopped in real time -> engine already flat (CLOSED)
    b.ex._pos = Pos()   # exchange flat again (stop fired)
    b.ex.calls.clear()
    b.reconcile(2, EngineSnapshot("flat", 0, 0.0, 0.0, 100.3))
    _assert("cancel_all" in b.ex.types(), "teardown must cancel any leftover orders")
    _assert(not b.armed and not b.in_position, "broker must end flat after round-trip")
    print("ok  test_round_trip_captured")


def test_retest_timeout_cancels():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex.calls.clear()
    # engine times out the retest -> flat, no position ever taken
    b.reconcile(2, EngineSnapshot("flat", 0, 0.0, 0.0, 100.4))
    _assert("cancel_all" in b.ex.types(), "timeout must cancel the resting entry")
    _assert("market_close" not in b.ex.types(), "no close needed — never filled")
    print("ok  test_retest_timeout_cancels")


def test_stale_seq_ignored():
    b = _broker()
    b.reconcile(5, SHORT_PENDING)
    n = len(b.ex.calls)
    b.reconcile(3, EngineSnapshot("flat", 0, 0.0, 0.0, 100.0))   # stale
    _assert(len(b.ex.calls) == n, "stale seq must be a no-op")
    print("ok  test_stale_seq_ignored")


if __name__ == "__main__":
    test_arm_places_both_resting_orders()
    test_unsafe_stop_refused()
    test_promote_on_fill()
    test_be_stop_move()
    test_round_trip_captured()
    test_retest_timeout_cancels()
    test_stale_seq_ignored()
    print("\nALL PASS")
