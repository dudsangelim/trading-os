"""
LiveBroker — mirrors the StrategyEngine's (shadow) intent onto a REAL Binance
(sub)account when EXECUTION_MODE == "live".

It does NOT re-implement the strategy. The engine stays the single source of truth
for *intent*; the exchange is the source of truth for *fills*. This broker keeps the
real account consistent with the engine's intent after every candle step.

### Why v2 (pre-placed resting orders)
v1 mirrored the engine POSITION *after the fact*: it market-opened only once the
engine had already confirmed a fill at candle close, and reconciled net state at the
end of each candle. That has a fatal blind spot — a trade that FILLS and STOPS inside
the same 5-minute candle round-trips back to flat before reconcile runs, so the real
account never sees it (this is exactly what happened 2026-07-06: filled+exit at the
same candle, zero real orders). v1 also entered at MARKET (candle-close price), not at
the strategy's actual retest level.

v2 pre-places the real orders while the engine is still WAITING_RETEST — i.e. the
break has been accepted and the engine is waiting for price to retest the extreme.
The engine GUARANTEES at least one candle in this state before any fill (same-bar
fill was removed 2026-07-05 as look-ahead), so reconcile always gets a window to arm:

  * entry      = a resting STOP_MARKET *entry* triggered at the extreme (the retest
                 level). SELL-stop for a short fade, BUY-stop for a long fade. It fires
                 in real time when price retests — captured by the exchange, not by our
                 5-minute poll. Fills at market (taker); the maker→taker slippage delta
                 is precisely what this micro-live probe exists to measure.
  * protection = a STOP_MARKET with closePosition=true at the engine's stop_price,
                 armed ALONGSIDE the entry (rests without needing a position), so there
                 is never an unprotected window between fill and stop.

reconcile() (called each candle) then just manages the lifecycle:
  * engine WAITING_RETEST, nothing armed on the exchange -> arm the pair
  * engine IN_POSITION / entry-stop filled                -> mark filled; keep stop; on
                                                             TP1 the engine moves stop to
                                                             BE -> we move the exchange stop
  * engine flat/timeout/exit -> cancel the pair + flatten any residual position

Safety posture (REAL MONEY):
  * The protective stop is armed together with the entry — never a naked position.
  * If the protective stop would trigger IMMEDIATELY given the current price (would
    happen only if stop_pct <= break_buffer, i.e. a degenerate setup), we REFUSE to arm
    live for that trade and stay flat — better to skip than sit unprotected.
  * Any exception inside reconcile triggers an emergency flatten.
  * Startup flattens the exchange (a restart forgets intraday state; a resurrected live
    position/order would be an orphan).
  * A monotonic `seq` guard makes reconciles apply in order.
  * Sizing is a FIXED notional (config), not the drifting paper equity.

v2 scope: single-unit position; the stop follows the engine (incl. move to breakeven at
TP1). It does NOT bank the 50% partial at TP1 (Binance ~$100 min notional makes a micro
half-close awkward) — the shadow track still records it; the sim-vs-real delta is the
deliverable. Real risk stays <= shadow risk.
"""
from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone

from trading.ny_open_paper.executor_live import BinanceLiveExecutor
from trading.ny_open_paper.persistence import append_row

log = logging.getLogger("ny_open_paper.live_broker")


@dataclass
class EngineSnapshot:
    """Immutable view of the engine's intent, captured atomically under the trader lock."""
    phase: str            # "pending" (WAITING_RETEST) | "in_position" | "flat"
    direction: int        # -1 short, +1 long, 0 flat
    limit_price: float    # the extreme / retest trigger (pending only)
    stop_price: float     # protective stop
    ref_price: float      # last close or tick price, for immediate-trigger guards


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

        # live mirror state
        self.armed = False          # entry+protective resting on the exchange, not yet filled
        self.in_position = False    # entry-stop has filled; protective stop resting
        self.direction = 0          # -1 short, +1 long, 0 flat
        self.current_stop = 0.0
        self.entry_order_id = None
        self.stop_order_id = None
        self._last_seq = -1

        # builds the ccxt client, enforces one-way mode + leverage, fail-fast on creds
        self.ex = BinanceLiveExecutor(symbol, leverage, account=account)
        log.warning("[LIVE] broker armed  account=%s  notional=$%.0f", account, self.notional)
        self.startup_flatten()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self.armed = False
        self.in_position = False
        self.direction = 0
        self.current_stop = 0.0
        self.entry_order_id = None
        self.stop_order_id = None

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

    def reconcile(self, seq: int, snap: EngineSnapshot) -> None:
        with self._lock:
            if seq <= self._last_seq:      # stale snapshot — never revert a newer action
                return
            self._last_seq = seq
            try:
                if snap.phase == "pending":
                    if self.in_position:
                        # engine re-armed while we still show a live position — shouldn't
                        # happen (engine can't re-arm same session); treat defensively.
                        self._detect_and_handle_exit()
                    elif not self.armed:
                        self._arm_pending(snap)
                    else:
                        self._promote_if_filled(snap)

                elif snap.phase == "in_position":
                    if not self.armed and not self.in_position:
                        # engine filled but we never armed (e.g. restart mid-retest).
                        # We deliberately do NOT chase — a retro market entry would be a
                        # different trade at a different price. Log the divergence.
                        self._log("divergence_unarmed_fill", direction=snap.direction,
                                  stop=snap.stop_price)
                        log.warning("[LIVE] engine IN_POSITION but broker unarmed — skipping (no retro-entry)")
                        return
                    self._promote_if_filled(snap)
                    if self.in_position:
                        self._sync_stop(snap.stop_price, snap.ref_price)

                elif snap.phase == "flat":
                    if self.in_position:
                        # engine exited — the exchange stop may already have fired, or not
                        self._teardown("engine-exit")
                    elif self.armed:
                        # break rejected / retest timeout — cancel the resting pair
                        self._teardown("engine-cancel")
            except Exception:
                log.error("[LIVE] reconcile error — emergency flatten:\n%s", traceback.format_exc())
                self._safe_flatten("reconcile-error")

    # ------------------------------------------------------------------
    # primitives (assume caller holds self._lock)
    # ------------------------------------------------------------------

    def _arm_pending(self, snap: EngineSnapshot) -> None:
        direction = snap.direction
        entry_side = "sell" if direction == -1 else "buy"
        close_side = "buy" if direction == -1 else "sell"

        # immediate-trigger guard for the PROTECTIVE stop: for a short the stop is a BUY
        # stop above (fires when price >= stop_price); for a long it is a SELL stop below
        # (fires when price <= stop_price). If the current price already sits on the wrong
        # side, the protective stop would fire on placement and leave the entry naked —
        # refuse to arm live for this (degenerate) trade.
        unsafe = (
            (direction == -1 and snap.ref_price >= snap.stop_price) or
            (direction == 1 and snap.ref_price <= snap.stop_price)
        )
        if unsafe:
            log.warning("[LIVE] refusing to arm: protective stop %.2f would trigger immediately "
                        "at ref %.2f (dir=%d) — staying flat", snap.stop_price, snap.ref_price, direction)
            self._log("arm_refused_unsafe_stop", stop=snap.stop_price, ref=snap.ref_price, direction=direction)
            self.notify("⚠️ <b>[LIVE] setup degenerado — não armei (stop imediato)</b>")
            return

        size = self.ex.amount_for_notional(self.notional, snap.limit_price)   # cap-checked

        # 1) resting protective stop FIRST (closePosition, no naked-window even if the
        #    entry fills a millisecond later)
        stop_order = self.ex.place_stop_close_position(close_side, snap.stop_price)
        # 2) resting entry stop at the extreme (the retest level)
        try:
            entry_order = self.ex.place_stop_entry(entry_side, snap.limit_price, size)
        except Exception as exc:
            log.error("[LIVE] entry-stop placement FAILED (%s) — cancelling protective, staying flat", exc)
            self.ex.cancel_all()
            self._reset()
            self._log("arm_failed", err=str(exc))
            self.notify("🚨 <b>[LIVE] falha ao armar entrada — cancelei tudo, flat</b>")
            return

        self.armed = True
        self.in_position = False
        self.direction = direction
        self.current_stop = snap.stop_price
        self.entry_order_id = entry_order.get("id")
        self.stop_order_id = stop_order.get("id")
        self._log("arm_pending", direction=direction, size=size, entry_trigger=snap.limit_price,
                  stop=snap.stop_price, notional=self.notional)
        self.notify(
            f"🎯 <b>[LIVE] ordem armada {entry_side.upper()}</b>\n"
            f"gatilho={snap.limit_price:.2f}  stop={snap.stop_price:.2f}  size={size}\n"
            f"(dispara no retest, em tempo real na Binance)"
        )

    def _promote_if_filled(self, snap: EngineSnapshot) -> None:
        """If the resting entry-stop has filled on the exchange, flip our mirror to
        in_position. Idempotent: safe to call every reconcile."""
        if self.in_position:
            return
        pos = self.ex.position()
        if pos.side is not None and pos.amount > 0:
            self.armed = False
            self.in_position = True
            self.entry_order_id = None
            self._log("entry_filled", side=pos.side, amount=pos.amount, entry=pos.entry_price)
            self.notify(
                f"📌 <b>[LIVE] entrada preenchida</b>\n{pos.side.upper()} {pos.amount} @~{pos.entry_price:.2f}"
            )

    def _sync_stop(self, new_stop: float, ref_price: float) -> None:
        """Move the protective stop when the engine trails it (TP1 -> breakeven)."""
        if not self.current_stop or abs(new_stop - self.current_stop) <= max(ref_price * 1e-5, 1e-9):
            return
        pos = self.ex.position()
        if pos.side is None or pos.amount <= 0:
            # exchange already flat — the stop fired between reconciles
            self._detect_and_handle_exit()
            return
        close_side = "buy" if self.direction == -1 else "sell"
        self.ex.cancel_all()                                   # drop old protective stop
        stop_order = self.ex.place_stop_close_position(close_side, new_stop)
        self.stop_order_id = stop_order.get("id")
        self.current_stop = new_stop
        self._log("stop_update", new_stop=new_stop)
        self.notify(f"🔧 <b>[LIVE] stop → {new_stop:.2f}</b> (BE/trail)")

    def _detect_and_handle_exit(self) -> bool:
        """If the exchange shows flat while we think we're in position, the protective
        stop fired. Clean up leftovers and reset. Returns True if an exit was detected."""
        pos = self.ex.position()
        if pos.side is None or pos.amount <= 0:
            self.ex.cancel_all()
            self._log("stop_fired_detected", prev_stop=self.current_stop)
            self.notify("🏁 <b>[LIVE] stop disparou na Binance — posição fechada</b>")
            self._reset()
            return True
        return False

    def _teardown(self, reason: str) -> None:
        """Engine says we should be flat: cancel the resting pair and market-close any
        residual position."""
        self.ex.cancel_all()
        pos = self.ex.position()
        if pos.side is not None and pos.amount > 0:
            close_side = "sell" if pos.side == "long" else "buy"
            order = self.ex.market_close(close_side, pos.amount)
            fill = _avg_fill(self.ex, order)
            self._log("close", reason=reason, fill=fill)
            self.notify(f"🏁 <b>[LIVE] saída ({reason})</b>  fill~{fill:.2f}")
        else:
            self._log("teardown_flat", reason=reason)
            if reason == "engine-cancel":
                self.notify("↩️ <b>[LIVE] ordem cancelada (retest timeout / break rejeitado)</b>")
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
        """Open a tiny long at MARKET, move its stop, then close — validates the live
        glue with REAL orders. Uses the configured notional. Leaves the account flat.
        (Uses market_open/place_stop_market directly, not the resting-order path.)"""
        with self._lock:
            price = self.ex.mark_price()
            stop0 = price * (1 - stop_far_pct)     # far below: won't trigger
            log.warning("[LIVE][selftest] open long ref=%.2f stop=%.2f", price, stop0)
            size = self.ex.amount_for_notional(self.notional, price)
            self.ex.market_open("buy", size, price)
            pos = self.ex.position()
            if pos.side is None or pos.amount <= 0:
                log.error("[LIVE][selftest] open failed — aborting selftest")
                return
            self.ex.place_stop_market("sell", price * (1 - stop_far_pct / 2), pos.amount)
            self.ex.cancel_all()
            self.ex.market_close("sell", pos.amount)
            log.warning("[LIVE][selftest] done — account flat=%s", self.ex.position())
            self._reset()
