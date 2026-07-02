"""
LiveBroker — mirrors the StrategyEngine's (shadow) position onto a REAL Binance
(sub)account when EXECUTION_MODE == "live".

It does NOT re-implement the strategy. The engine stays the single source of truth;
this broker only enforces one auditable invariant after every engine step:

    exchange position  ==  engine position (fixed notional)
    exchange has a native STOP_MARKET at the engine's current stop_price

`reconcile()` is called after each candle/tick with a snapshot of the engine state
and drives the exchange toward that invariant:
  * engine in position, exchange flat        -> market-open + place protective stop
  * engine moved the stop (e.g. TP1 -> BE)   -> cancel old stop, place new one
  * engine flat, exchange not                -> cancel all + market-close

Safety posture (REAL MONEY):
  * A protective native stop is ALWAYS placed with the entry; if the stop fails to
    place, the just-opened position is immediately closed (never sit unprotected).
  * Any exception inside reconcile triggers an emergency flatten.
  * On startup the exchange is flattened (the paper engine forgets intraday state
    across restarts, so a resurrected live position would be an orphan).
  * A monotonic `seq` guard makes reconciles apply in order — a late snapshot from
    one thread can never revert a newer action from another.
  * Sizing is a FIXED notional (config), not the drifting paper equity.

v1 scope: single-unit position; the stop follows the engine (incl. the move to
breakeven at TP1). It does NOT bank the 50% partial at TP1 (Binance ~$100 min
notional makes a micro half-close awkward) — the shadow track still records it, and
the sim-vs-real delta is the deliverable. Real risk stays <= shadow risk.
"""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timezone

from trading.ny_open_paper.config import DATA_DIR
from trading.ny_open_paper.executor_live import BinanceLiveExecutor
from trading.ny_open_paper.persistence import append_row

log = logging.getLogger("ny_open_paper.live_broker")


def _avg_fill(ex, order) -> float:
    try:
        o = ex.fetch_order(order.get("id"))
    except Exception:
        o = order
    avg = o.get("average")
    if avg:
        return float(avg)
    info = o.get("info", {}) or {}
    ap = info.get("avgPrice")
    if ap and float(ap) > 0:
        return float(ap)
    return float(o.get("price") or 0.0)


class LiveBroker:
    def __init__(self, account, notional_usd, symbol, leverage, notify=None):
        self.notional = float(notional_usd)
        self.notify = notify or (lambda *_a, **_k: None)
        self._lock = threading.RLock()

        # live position mirror
        self.direction = 0          # -1 short, +1 long, 0 flat
        self.size = 0.0
        self.current_stop = 0.0
        self._last_seq = -1

        # builds the ccxt client, enforces one-way mode + leverage, fail-fast on creds
        self.ex = BinanceLiveExecutor(symbol, leverage, account=account)
        log.warning("[LIVE] broker armed  account=%s  notional=$%.0f", account, self.notional)
        self.startup_flatten()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self.direction = 0
        self.size = 0.0
        self.current_stop = 0.0

    def _log(self, event: str, **fields) -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        row.update({k: fields[k] for k in fields})
        try:
            append_row("live_fills.csv", row)
        except Exception:
            log.warning("[LIVE] could not write live_fills.csv")

    def startup_flatten(self) -> None:
        with self._lock:
            try:
                pos = self.ex.position()
                oo = self.ex.open_orders()
                residual = (pos.side is not None) or oo.entry or oo.stops or oo.take_profits or oo.other
                if residual:
                    log.warning("[LIVE] startup: residual pos/orders found — flattening (%s)", pos)
                    self.ex.flatten()
                    self._log("startup_flatten", had_pos=str(pos.side), amount=pos.amount)
                    self.notify("🧹 <b>[LIVE] estado residual limpo no startup</b>")
            except Exception:
                log.error("[LIVE] startup flatten error:\n%s", traceback.format_exc())
            self._reset()

    # ------------------------------------------------------------------
    # reconcile (the only entry point the trader calls)
    # ------------------------------------------------------------------

    def reconcile(self, seq: int, in_position: bool, direction: int,
                  stop_price: float, ref_price: float) -> None:
        with self._lock:
            if seq <= self._last_seq:      # stale snapshot — never revert a newer action
                return
            self._last_seq = seq
            try:
                if not in_position:
                    if self.size > 0 or self.direction != 0:
                        self._close("engine-flat")
                    return
                if self.size == 0:
                    self._open(direction, ref_price, stop_price)
                elif self.current_stop and abs(stop_price - self.current_stop) > max(ref_price * 1e-5, 1e-9):
                    self._update_stop(stop_price)
            except Exception:
                log.error("[LIVE] reconcile error — emergency flatten:\n%s", traceback.format_exc())
                self._safe_flatten("reconcile-error")

    # ------------------------------------------------------------------
    # primitives (assume caller holds self._lock)
    # ------------------------------------------------------------------

    def _open(self, direction: int, ref_price: float, stop_price: float) -> None:
        side = "sell" if direction == -1 else "buy"
        close_side = "buy" if direction == -1 else "sell"
        size = self.ex.amount_for_notional(self.notional, ref_price)   # cap-checked
        order = self.ex.market_open(side, size, ref_price)
        fill = _avg_fill(self.ex, order) or ref_price
        # CRITICAL: protective stop must land, or we abort the position.
        try:
            self.ex.place_stop_market(close_side, stop_price, size)
        except Exception as exc:
            log.error("[LIVE] STOP FAILED after entry (%s) — aborting position", exc)
            try:
                self.ex.market_close(close_side, size)
            except Exception:
                self.ex.flatten()
            self._reset()
            self.notify("🚨 <b>[LIVE] stop falhou ao abrir — posição fechada</b>")
            self._log("open_aborted", direction=direction, size=size, err=str(exc))
            return
        self.direction = direction
        self.size = size
        self.current_stop = stop_price
        self._log("open", direction=direction, size=size, fill=fill,
                  stop=stop_price, ref=ref_price, notional=self.notional)
        self.notify(
            f"📌 <b>[LIVE] entrada {side.upper()}</b>\nsize={size} @~{fill:.2f}  stop={stop_price:.2f}"
        )

    def _update_stop(self, new_stop: float) -> None:
        # if the stop already fired, the exchange is flat — resync and let engine follow
        pos = self.ex.position()
        if pos.side is None or pos.amount <= 0:
            log.warning("[LIVE] update_stop: exchange already flat (stop likely fired) — resetting")
            self.ex.cancel_all()
            self._log("stop_fired_detected", prev_stop=self.current_stop)
            self._reset()
            return
        close_side = "buy" if self.direction == -1 else "sell"
        self.ex.cancel_all()                                   # drop the old stop
        self.ex.place_stop_market(close_side, new_stop, pos.amount)
        self.current_stop = new_stop
        self._log("stop_update", new_stop=new_stop, size=pos.amount)
        self.notify(f"🔧 <b>[LIVE] stop → {new_stop:.2f}</b> (BE/trail)")

    def _close(self, reason: str) -> None:
        self.ex.cancel_all()
        pos = self.ex.position()
        if pos.side is not None and pos.amount > 0:
            close_side = "sell" if pos.side == "long" else "buy"
            order = self.ex.market_close(close_side, pos.amount)
            fill = _avg_fill(self.ex, order)
        else:
            fill = 0.0
        self._log("close", reason=reason, fill=fill)
        self.notify(f"🏁 <b>[LIVE] saída ({reason})</b>  fill~{fill:.2f}")
        self._reset()

    def _safe_flatten(self, reason: str) -> None:
        try:
            self.ex.flatten()
        except Exception:
            log.critical("[LIVE] emergency flatten FAILED during %s:\n%s", reason, traceback.format_exc())
        self._log("emergency_flatten", reason=reason)
        self.notify(f"🚨 <b>[LIVE] flatten de emergência ({reason})</b>")
        self._reset()

    # ------------------------------------------------------------------
    # optional: exercise the full order path with real (tiny) orders
    # ------------------------------------------------------------------

    def selftest(self, stop_far_pct: float = 0.05) -> None:
        """Open a tiny long, move its stop, then close — validates the live glue
        with REAL orders. Uses the configured notional. Leaves the account flat."""
        with self._lock:
            price = self.ex.mark_price()
            stop0 = price * (1 - stop_far_pct)     # far below: won't trigger
            log.warning("[LIVE][selftest] open long ref=%.2f stop=%.2f", price, stop0)
            self._open(+1, price, stop0)
            if self.size == 0:
                log.error("[LIVE][selftest] open failed — aborting selftest")
                return
            self._update_stop(price * (1 - stop_far_pct / 2))
            self._close("selftest")
            log.warning("[LIVE][selftest] done — account flat=%s", self.ex.position())
