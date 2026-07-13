"""
Mock-exchange tests for LiveBroker v3 (protection placed AFTER the fill).

No testnet exists for this venue, so we validate the ORDER LIFECYCLE against a fake
executor that records calls and lets the test drive exchange position state.

The v3 rewrite exists because Binance REJECTS a reduceOnly/closePosition protective
order while the account is FLAT (error -4509 "TIF GTE can only be used with open
positions"). So the invariant these tests enforce is: **no reduceOnly/closePosition
order is ever placed while the fake exchange position is flat.** Protection (stop, TP1,
TP2) is armed only after a confirmed open position, via monitor()/reconcile().

Second invariant (Binance 2025-12-09 algo-endpoint migration): STOP_MARKET /
TAKE_PROFIT (conditional) orders live on a SEPARATE "algo" list. In ccxt the plain
cancel_order / cancel_all_orders / fetch_open_orders touch ONLY the regular
(LIMIT/MARKET) list — the algo list is reached only with params={'stop': True}. The
fake executor below models BOTH lists so we can guard the zombie-order regression: a
plain cancel must NOT remove an algo order (needs conditional=True), and teardown must
leave NO algo orders behind (the executor's cancel_all now clears both lists).

Run (plain, no pytest needed): python3 -m trading.ny_open_paper.tests.test_live_broker_v2
Run (pytest, if installed):    python3 -m pytest trading/ny_open_paper/tests/test_live_broker_v2.py -x -q
"""
from __future__ import annotations

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


# Order types that Binance routes to the SEPARATE "algo" endpoint (2025-12-09 migration).
ALGO_TYPES = {"STOP_MARKET", "STOP", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"}


class MockExecutor:
    """Records order calls AND tracks resting-order state across TWO lists (regular vs
    algo), the way Binance/ccxt behave since the 2025-12-09 algo-endpoint migration.

    Guards two invariants:
      * reduceOnly orders are never placed while the exchange is flat (Binance -4509);
      * a PLAIN cancel/cancel_all only touches regular orders — algo orders (STOP/TP)
        require the conditional (stop=True) path, so teardown must not leave zombies.
    """

    def __init__(self, *a, **k):
        self.calls = []
        self._pos = Pos()          # test-controlled exchange position
        self._order_seq = 0
        # id -> {"type", "algo", "side", "price", "amount"} for every RESTING order
        self._orders = {}
        # records ("reduceonly_while_flat", type) any time a reduceOnly order is placed
        # while _pos is flat — must stay empty for the whole suite.
        self.reduceonly_while_flat = []

    # -- reads --
    def position(self):
        return self._pos

    def open_orders(self):
        oo = OO()
        for oid, o in self._orders.items():
            t = o["type"].upper()
            od = {"id": oid, **o}
            if "STOP" in t:
                oo.stops.append(od)
            elif "TAKE_PROFIT" in t:
                oo.take_profits.append(od)
            elif t == "LIMIT":
                oo.entry = od
            else:
                oo.other.append(od)
        return oo

    def mark_price(self):
        return 100.0

    def amount_for_notional(self, notional, price):
        return round(notional / price, 4)

    def round_amount(self, amount):
        return round(amount, 4)

    def min_tradeable(self, amount, price):
        return amount > 0

    def _oid(self):
        self._order_seq += 1
        return f"ord{self._order_seq}"

    def _register(self, otype, side, price, amount):
        oid = self._oid()
        self._orders[oid] = {
            "type": otype, "algo": otype.upper() in ALGO_TYPES,
            "side": side, "price": price, "amount": amount,
        }
        return oid

    def _guard_reduce_only(self, kind):
        if self._pos.side is None or self._pos.amount <= 0:
            self.reduceonly_while_flat.append(kind)

    # -- test-facing helpers --
    def algo_orders(self):
        return [o for o in self._orders.values() if o["algo"]]

    def has_order(self, oid):
        return oid in self._orders

    # -- orders --
    def place_entry_limit(self, side, price, amount):
        self.calls.append(("entry_limit", side, round(price, 2), amount))
        return {"id": self._register("LIMIT", side, round(price, 2), amount)}

    def place_stop_close_position(self, close_side, stop_price):
        # The v3 order path must NEVER call this. Record if it does so a test can catch it.
        self.calls.append(("stop_close", close_side, round(stop_price, 2)))
        return {"id": self._register("STOP_MARKET", close_side, round(stop_price, 2), None)}

    def place_stop_market(self, close_side, stop_price, amount):
        self._guard_reduce_only("stop_market")
        self.calls.append(("stop_market", close_side, round(stop_price, 2), amount))
        return {"id": self._register("STOP_MARKET", close_side, round(stop_price, 2), amount)}

    def place_take_profit_limit(self, close_side, tp_price, amount):
        self._guard_reduce_only("take_profit")
        self.calls.append(("take_profit", close_side, round(tp_price, 2), amount))
        return {"id": self._register("TAKE_PROFIT", close_side, round(tp_price, 2), amount)}

    def market_open(self, side, amount, ref):
        self.calls.append(("market_open", side, amount))
        return {"id": self._oid()}

    def market_close(self, close_side, amount):
        # closes the position; does NOT touch resting orders (that's cancel's job)
        self.calls.append(("market_close", close_side, amount))
        self._pos = Pos()
        return {"id": self._oid(), "average": 100.0}

    def cancel(self, order_id, conditional=False):
        # Plain cancel (conditional=False) touches ONLY the regular list; a conditional
        # cancel (stop=True) touches ONLY the algo list. So an order is removed only when
        # its list matches the cancel's routing: order.algo == conditional.
        self.calls.append(("cancel", order_id, conditional))
        o = self._orders.get(order_id)
        if o is not None and o["algo"] == conditional:
            del self._orders[order_id]

    def cancel_all(self):
        # The executor's cancel_all() now fires BOTH lists (plain + stop=True), so it
        # clears regular AND algo orders — no zombies left behind.
        self.calls.append(("cancel_all",))
        self._orders.clear()

    def flatten(self):
        self.calls.append(("flatten",))
        self._orders.clear()
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


def _assert_never_reduceonly_while_flat(b):
    _assert(not b.ex.reduceonly_while_flat,
            f"reduceOnly order placed while flat: {b.ex.reduceonly_while_flat}")
    _assert("stop_close" not in b.ex.types(),
            "v3 must NOT use place_stop_close_position anywhere")


# extreme=100 short: entry sell-limit at 100. stop above at 100.6 (0.6% stop).
# tp1=mid 99.4, tp2=opposite 98.8. ref (broken-out) price ~100.05, below stop.
SHORT_PENDING = EngineSnapshot("pending", -1, 100.0, 100.6, 100.05, tp1=99.4, tp2=98.8)


def test_arm_places_only_limit_entry():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    t = b.ex.types()
    _assert(t == ["entry_limit"], f"arm must place ONLY a limit entry, got {t}")
    _assert(b.armed and not b.in_position, "state must be armed, not in_position")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_arm_places_only_limit_entry")


def test_on_fill_places_stop_then_tps():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex.calls.clear()
    # exchange fills the LIMIT entry
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)
    b.monitor()   # tick-loop path arms protection
    t = b.ex.types()
    _assert(t == ["stop_market", "take_profit", "take_profit"],
            f"on fill: stop FIRST then two TPs, got {t}")
    # stop is full size; TPs are 50/50 summing to the full size
    stop = next(c for c in b.ex.calls if c[0] == "stop_market")
    tps = [c for c in b.ex.calls if c[0] == "take_profit"]
    _assert(abs(stop[3] - 2.4) < 1e-9, f"stop must be full size, got {stop[3]}")
    _assert(abs(tps[0][3] - 1.2) < 1e-9, f"TP1 must be 50%, got {tps[0][3]}")
    _assert(abs(tps[1][3] - 1.2) < 1e-9, f"TP2 must be remaining 50%, got {tps[1][3]}")
    _assert(abs((tps[0][3] + tps[1][3]) - 2.4) < 1e-9, "TP1+TP2 must sum to filled size")
    _assert(abs(tps[0][2] - 99.4) < 1e-9, "TP1 at mid")
    _assert(abs(tps[1][2] - 98.8) < 1e-9, "TP2 at opposite")
    _assert(b.in_position and not b.armed, "must be in_position after fill")
    _assert(not b.tp1_done, "tp1 not done yet")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_on_fill_places_stop_then_tps")


def test_tp1_fill_moves_stop_to_be():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)
    b.monitor()                       # arm protection
    old_stop_id = b.stop_order_id     # the algo STOP_MARKET resting on the exchange
    tp2_id = b.tp2_order_id
    _assert(b.ex.has_order(old_stop_id), "original stop must be resting on the algo list")
    b.ex.calls.clear()
    # TP1 fills -> position halves
    b.ex._pos = Pos(side="short", amount=1.2, entry=100.0)
    b.monitor()
    t = b.ex.types()
    _assert("cancel" in t and "stop_market" in t,
            f"TP1 fill must cancel old stop + place BE stop, got {t}")
    _assert("cancel_all" not in t, "must NOT cancel_all (would drop the resting TP2)")
    # the old stop is an ALGO order -> must be cancelled via conditional=True, else zombie
    cancel_call = next(c for c in b.ex.calls if c[0] == "cancel")
    _assert(cancel_call == ("cancel", old_stop_id, True),
            f"BE move must cancel the OLD stop with conditional=True, got {cancel_call}")
    _assert(not b.ex.has_order(old_stop_id), "old algo stop must be gone (no zombie)")
    _assert(b.ex.has_order(b.stop_order_id) and b.stop_order_id != old_stop_id,
            "new BE stop must be resting")
    _assert(b.ex.has_order(tp2_id), "the resting TP2 must survive the BE move")
    be_stop = next(c for c in b.ex.calls if c[0] == "stop_market")
    _assert(abs(be_stop[2] - 100.0) < 1e-9, "BE stop must be at entry fill price 100.0")
    _assert(abs(be_stop[3] - 1.2) < 1e-9, "BE stop sized to the remaining 1.2")
    _assert(b.tp1_done, "tp1_done must be set (idempotent)")
    # idempotent: a second monitor with same state does nothing new
    b.ex.calls.clear()
    b.monitor()
    _assert(b.ex.types() == [], "BE move must be one-shot")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_tp1_fill_moves_stop_to_be")


def test_stop_or_tp2_exit_cleans_up():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)
    b.monitor()                       # arm protection
    b.ex.calls.clear()
    # stop / TP2 flattens the position on the exchange
    b.ex._pos = Pos()
    b.monitor()
    _assert("cancel_all" in b.ex.types(), "exit must cancel leftover orders")
    _assert(b.ex.algo_orders() == [], "no zombie stop/TP algo orders after exit cleanup")
    _assert(not b.armed and not b.in_position, "broker must reset to flat after exit")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_stop_or_tp2_exit_cleans_up")


def test_unfilled_engine_flat_cancels_entry():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex.calls.clear()
    # engine times out the retest -> flat, position never filled
    b.reconcile(2, EngineSnapshot("flat", 0, 0.0, 0.0, 100.4))
    _assert("cancel_all" in b.ex.types(), "timeout must cancel the resting entry")
    _assert("market_close" not in b.ex.types(), "no close needed — never filled")
    _assert(not b.armed and not b.in_position, "broker flat after cancel")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_unfilled_engine_flat_cancels_entry")


def test_in_position_engine_flat_teardown():
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)
    b.monitor()                       # arm protection
    b.ex.calls.clear()
    # engine exits but the exchange still shows a residual position -> teardown closes it
    b.reconcile(2, EngineSnapshot("flat", 0, 0.0, 0.0, 100.3))
    t = b.ex.types()
    _assert("cancel_all" in t and "market_close" in t, f"teardown must cancel + close, got {t}")
    _assert(b.ex.algo_orders() == [], "no zombie stop/TP algo orders after teardown")
    _assert(not b.armed and not b.in_position, "broker flat after teardown")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_in_position_engine_flat_teardown")


def test_plain_cancel_ignores_algo_order():
    """Models the Binance 2025-12-09 split: a PLAIN cancel does NOT touch an algo
    (STOP/TP) order — only a conditional (stop=True) cancel does. This is the fake-exec
    behavior the zombie-order tests rely on."""
    ex = MockExecutor()
    ex._pos = Pos(side="short", amount=1.0, entry=100.0)
    stop_id = ex.place_stop_market("buy", 100.6, 1.0)["id"]
    ex.cancel(stop_id)                       # plain -> wrong list, no-op on algo
    _assert(ex.has_order(stop_id), "plain cancel must NOT remove an algo order")
    ex.cancel(stop_id, conditional=True)     # algo list -> removed
    _assert(not ex.has_order(stop_id), "conditional cancel must remove the algo order")
    print("ok  test_plain_cancel_ignores_algo_order")


def test_teardown_leaves_no_algo_zombies():
    """Regression guard for the 2025-12-09 algo-endpoint migration: after a full
    teardown/flatten, NO conditional (STOP/TP) orders may remain on the exchange. Before
    the fix, cancel_all() only hit the regular list and left stop/TP zombies."""
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)
    b.monitor()                       # protection armed: 1 STOP + 2 TP algo orders
    _assert(len(b.ex.algo_orders()) == 3, f"3 algo orders must rest, got {b.ex.algo_orders()}")
    # teardown via engine-exit (residual position on the exchange)
    b.reconcile(2, EngineSnapshot("flat", 0, 0.0, 0.0, 100.3))
    _assert(b.ex.algo_orders() == [], "NO zombie STOP/TP algo orders may remain after teardown")
    oo = b.ex.open_orders()
    _assert(not oo.stops and not oo.take_profits and oo.entry is None,
            f"open_orders must be empty after teardown, got {oo.__dict__}")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_teardown_leaves_no_algo_zombies")


def test_round_trip_captured():
    """THE bug v2 fixed and v3 keeps: entry fills AND stops inside one candle. The LIMIT
    entry was resting on the exchange during the retest window; the venue executes fill +
    protection + stop in real time (via the tick-loop monitor). The engine returns flat
    next candle -> broker tears down and the account ends flat."""
    b = _broker()
    b.reconcile(1, SHORT_PENDING)
    _assert("entry_limit" in b.ex.types(), "entry must be RESTING before the round-trip")
    # fill happens, protection arms, then stop fires — all between candles
    b.ex._pos = Pos(side="short", amount=2.4, entry=100.0)
    b.monitor()                       # fill -> protection armed
    b.ex._pos = Pos()                 # stop fired -> flat
    b.monitor()                       # exit detected
    _assert(not b.armed and not b.in_position, "broker must end flat after round-trip")
    # engine also reports flat next candle
    b.ex.calls.clear()
    b.reconcile(2, EngineSnapshot("flat", 0, 0.0, 0.0, 100.3))
    _assert(not b.armed and not b.in_position, "stays flat")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_round_trip_captured")


def test_unsafe_stop_refused():
    b = _broker()
    # stop 99.9 is BELOW ref 100.05 for a short -> already breached, would stop out on fill
    b.reconcile(1, EngineSnapshot("pending", -1, 100.0, 99.9, 100.05, tp1=99.4, tp2=98.8))
    _assert(not b.armed, "must refuse to arm when stop already breached")
    _assert("entry_limit" not in b.ex.types(), "must not place an entry for a degenerate setup")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_unsafe_stop_refused")


def test_stale_seq_ignored():
    b = _broker()
    b.reconcile(5, SHORT_PENDING)
    n = len(b.ex.calls)
    b.reconcile(3, EngineSnapshot("flat", 0, 0.0, 0.0, 100.0))   # stale
    _assert(len(b.ex.calls) == n, "stale seq must be a no-op")
    _assert_never_reduceonly_while_flat(b)
    print("ok  test_stale_seq_ignored")


def test_monitor_never_throws():
    class Boom(MockExecutor):
        def position(self):
            raise RuntimeError("exchange down")
    b = _broker()
    b.ex = Boom()
    b.armed = True
    # monitor must swallow the error and emergency-flatten, not propagate
    b.monitor()
    _assert("flatten" in b.ex.types(), "monitor must emergency-flatten on unexpected error")
    print("ok  test_monitor_never_throws")


def _run_all():
    test_arm_places_only_limit_entry()
    test_on_fill_places_stop_then_tps()
    test_tp1_fill_moves_stop_to_be()
    test_stop_or_tp2_exit_cleans_up()
    test_unfilled_engine_flat_cancels_entry()
    test_in_position_engine_flat_teardown()
    test_plain_cancel_ignores_algo_order()
    test_teardown_leaves_no_algo_zombies()
    test_round_trip_captured()
    test_unsafe_stop_refused()
    test_stale_seq_ignored()
    test_monitor_never_throws()


if __name__ == "__main__":
    _run_all()
    print("\nALL PASS")
