"""
LiveBroker — mirrors the StrategyEngine's (shadow) intent onto a REAL Binance
(sub)account when EXECUTION_MODE == "live".

It does NOT re-implement the strategy. The engine stays the single source of truth
for *intent*; the exchange is the source of truth for *fills*. This broker keeps the
real account consistent with the engine's intent.

### Why v3 (protection placed AFTER the fill)
v2 pre-placed a resting **protective stop with `closePosition=true`** ALONGSIDE the
resting entry, while the account was still FLAT. Binance REJECTS that:

    -4509  "Time in Force (TIF) GTE can only be used with open positions"

You simply CANNOT rest a reduceOnly / closePosition protective order on Binance USDM
without an open position. In v2 that rejection threw inside `reconcile`, which then
triggered an emergency flatten every time we tried to arm — so live never worked.

v3 fixes the lifecycle to respect that constraint:

  1. **Arm** — place ONLY a resting **LIMIT entry** at the retest extreme
     (`place_entry_limit`). Nothing reduceOnly is placed (the account is flat).
     The planned stop / TP1 / TP2 prices and the size are stored for later.
  2. **On entry fill** (detected by polling the position; the ~250ms tick loop calls
     `monitor()` constantly) — immediately place, reduceOnly, in this order (stop
     FIRST to minimise the naked window):
        * STOP_MARKET at `stop_price`, full size.
        * TP1 TAKE_PROFIT at `mid`,      50% of size.
        * TP2 TAKE_PROFIT at `opposite`, remaining 50%.
     Flip to in_position.
  3. **On TP1 fill** (position size drops to ~half) — cancel the current stop and
     re-place a STOP_MARKET at **breakeven = actual entry fill price**, sized to the
     remaining position. Idempotent (`tp1_done`).
  4. **Exit** — when the stop or TP2 flattens the position on the exchange, cancel any
     leftover orders and reset to flat.
  5. **Engine flat while armed (unfilled)** — cancel the resting entry (retest timeout
     / break rejected).
  6. **Engine flat while in position** — teardown: cancel orders + market_close any
     residual.

The naked window between fill and stop is bounded by how often `monitor()` runs (the
tick loop, ~250ms) plus the stop being placed FIRST of the three protective orders.

Safety posture (REAL MONEY):
  * NEVER place a reduceOnly / closePosition order while the exchange position is flat.
    All protective orders (stop/TP1/TP2) are placed strictly AFTER a confirmed open
    position (this is the whole point of the v3 rewrite).
  * Any exception inside `reconcile` OR `monitor` triggers an emergency flatten.
  * Startup flattens the exchange (a restart forgets intraday state; a resurrected
    live position/order would be an orphan).
  * A monotonic `seq` guard makes reconciles apply in order.
  * Sizing is a FIXED notional (config), not the drifting paper equity.
  * One-way (net) position mode is assumed (enforced by the executor at init).

`place_stop_close_position` is intentionally left in executor_live.py for the isolated
`selftest`, but the ORDER PATH never uses it any more — all stops are reduceOnly, sized
`place_stop_market`, placed only against an open position.
"""
from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone

from trading.ny_open_paper.executor_live import BinanceLiveExecutor

log = logging.getLogger("ny_open_paper.live_broker")


@dataclass
class EngineSnapshot:
    """Immutable view of the engine's intent, captured atomically under the trader lock."""
    phase: str            # "pending" (WAITING_RETEST) | "in_position" | "flat"
    direction: int        # -1 short, +1 long, 0 flat
    limit_price: float    # the extreme / retest limit price (pending only)
    stop_price: float     # protective stop
    ref_price: float      # last close or tick price, for immediate-trigger guards
    tp1: float = 0.0      # TP1 target (mid). Backward-compat default.
    tp2: float = 0.0      # TP2 target (opposite). Backward-compat default.


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

        # builds the ccxt client, enforces one-way mode + leverage, fail-fast on creds
        self.ex = BinanceLiveExecutor(symbol, leverage, account=account)
        self._reset()
        self._last_seq = -1
        log.warning("[LIVE] broker armed  account=%s  notional=$%.0f", account, self.notional)
        self.startup_flatten()

    # ------------------------------------------------------------------
    # lifecycle state
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self.armed = False           # LIMIT entry resting on the exchange, not yet filled
        self.in_position = False     # entry filled; protective stop/TPs resting
        self.direction = 0           # -1 short, +1 long, 0 flat

        # planned prices/size captured at arm time
        self.entry_limit_price = 0.0
        self.stop_price = 0.0
        self.tp1_price = 0.0
        self.tp2_price = 0.0
        self.planned_size = 0.0

        # live fill facts
        self.filled_amount = 0.0
        self.entry_fill_price = 0.0
        self.current_stop = 0.0
        self.tp1_done = False        # BE stop-move done (or TP1 was skipped in fallback)

        # order ids
        self.entry_order_id = None
        self.stop_order_id = None
        self.tp1_order_id = None
        self.tp2_order_id = None

    def _log(self, event: str, **fields) -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        row.update({k: fields[k] for k in fields})
        try:
            # imported lazily so the broker module is importable without the pandas-backed
            # persistence layer (keeps unit tests runnable off a bare interpreter).
            from trading.ny_open_paper.persistence import append_row
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
    # reconcile — the candle-path entry point
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
                        # happen (engine can't re-arm same session); service defensively.
                        self._service_position()
                    elif not self.armed:
                        self._arm_pending(snap)
                    else:
                        self._service_armed()

                elif snap.phase == "in_position":
                    if not self.armed and not self.in_position:
                        # engine filled but we never armed (e.g. restart mid-retest). We do
                        # NOT chase — a retro market entry would be a different trade at a
                        # different price. Log the divergence and stay out.
                        self._log("divergence_unarmed_fill", direction=snap.direction,
                                  stop=snap.stop_price)
                        log.warning("[LIVE] engine IN_POSITION but broker unarmed — skipping (no retro-entry)")
                        return
                    if self.armed:
                        self._service_armed()
                    if self.in_position:
                        self._service_position()

                elif snap.phase == "flat":
                    if self.in_position:
                        # engine exited — the exchange stop/TP2 may already have fired
                        self._teardown("engine-exit")
                    elif self.armed:
                        # break rejected / retest timeout — cancel the resting entry
                        self._teardown("engine-cancel")
            except Exception:
                log.error("[LIVE] reconcile error — emergency flatten:\n%s", traceback.format_exc())
                self._safe_flatten("reconcile-error")

    # ------------------------------------------------------------------
    # monitor — the fast (~250ms) tick-path entry point
    # ------------------------------------------------------------------

    def monitor(self) -> None:
        """Cheap, idempotent, side-effect-safe servicing called by the tick loop every
        ~250ms. It (a) arms protection the instant the entry fills, (b) moves the stop to
        breakeven when TP1 fills, and (c) detects the exit. It must NOT throw — any
        unexpected error triggers an emergency flatten, exactly like reconcile."""
        with self._lock:
            try:
                if self.in_position:
                    self._service_position()
                elif self.armed:
                    self._service_armed()
            except Exception:
                log.error("[LIVE] monitor error — emergency flatten:\n%s", traceback.format_exc())
                self._safe_flatten("monitor-error")

    # ------------------------------------------------------------------
    # primitives (assume caller holds self._lock)
    # ------------------------------------------------------------------

    def _arm_pending(self, snap: EngineSnapshot) -> None:
        """Place ONLY a resting LIMIT entry. The account is flat, so NO reduceOnly /
        closePosition protective order can be placed here (Binance -4509). The planned
        stop/TP1/TP2 and size are stored; protection is armed after the fill."""
        direction = snap.direction
        entry_side = "sell" if direction == -1 else "buy"

        # degenerate-setup guard: if the planned stop is already on the wrong side of the
        # current price, the trade would stop out immediately on fill — refuse to arm.
        unsafe = (
            (direction == -1 and snap.ref_price >= snap.stop_price) or
            (direction == 1 and snap.ref_price <= snap.stop_price)
        )
        if unsafe:
            log.warning("[LIVE] refusing to arm: stop %.2f already breached at ref %.2f "
                        "(dir=%d) — staying flat", snap.stop_price, snap.ref_price, direction)
            self._log("arm_refused_unsafe_stop", stop=snap.stop_price, ref=snap.ref_price,
                      direction=direction)
            self.notify("⚠️ <b>[LIVE] setup degenerado — não armei (stop já rompido)</b>")
            return

        size = self.ex.amount_for_notional(self.notional, snap.limit_price)   # cap-checked
        try:
            entry_order = self.ex.place_entry_limit(entry_side, snap.limit_price, size)
        except Exception as exc:
            log.error("[LIVE] entry LIMIT placement FAILED (%s) — staying flat", exc)
            self.ex.cancel_all()
            self._reset()
            self._log("arm_failed", err=str(exc))
            self.notify("🚨 <b>[LIVE] falha ao armar entrada — cancelei tudo, flat</b>")
            return

        self.armed = True
        self.in_position = False
        self.direction = direction
        self.entry_limit_price = snap.limit_price
        self.stop_price = snap.stop_price
        self.tp1_price = snap.tp1
        self.tp2_price = snap.tp2
        self.planned_size = size
        self.tp1_done = False
        self.entry_order_id = entry_order.get("id")
        self.stop_order_id = None
        self.tp1_order_id = None
        self.tp2_order_id = None
        self._log("arm_pending", direction=direction, size=size, entry=snap.limit_price,
                  stop=snap.stop_price, tp1=snap.tp1, tp2=snap.tp2, notional=self.notional)
        self.notify(
            f"🎯 <b>[LIVE] entrada LIMIT armada {entry_side.upper()}</b>\n"
            f"limit={snap.limit_price:.2f}  stop={snap.stop_price:.2f}  "
            f"tp1={snap.tp1:.2f}  tp2={snap.tp2:.2f}  size={size}\n"
            f"(proteção só entra APÓS o fill — Binance recusa reduceOnly com conta flat)"
        )

    def _service_armed(self) -> None:
        """If the resting LIMIT entry has filled, arm protection (stop first, then the two
        TPs) and flip to in_position. Idempotent: a no-op until the exchange shows a
        position, and a no-op once we are already in_position."""
        if self.in_position:
            return
        pos = self.ex.position()
        if pos.side is None or pos.amount <= 0:
            return   # entry not filled yet — nothing to do, nothing reduceOnly placed

        self.armed = False
        self.in_position = True
        self.entry_order_id = None
        self.filled_amount = pos.amount
        self.entry_fill_price = float(pos.entry_price) or self.entry_limit_price
        self._log("entry_filled", side=pos.side, amount=pos.amount, entry=self.entry_fill_price)
        self.notify(
            f"📌 <b>[LIVE] entrada preenchida</b>\n"
            f"{pos.side.upper()} {pos.amount} @~{self.entry_fill_price:.2f} — armando proteção"
        )
        self._place_protection()

    def _place_protection(self) -> None:
        """Place the reduceOnly protective orders AGAINST the now-open position: STOP
        first (minimise the naked window), then TP1 (50%) + TP2 (50%)."""
        close_side = "buy" if self.direction == -1 else "sell"
        amt = self.filled_amount

        # 1) protective STOP, full size, FIRST.
        stop = self.ex.place_stop_market(close_side, self.stop_price, amt)
        self.stop_order_id = stop.get("id")
        self.current_stop = self.stop_price

        # 2) take-profits. 50/50 split, rounded to exchange precision so the two legs sum
        #    EXACTLY to the filled amount (tp2 = filled - half).
        if self.tp2_price and self.tp2_price > 0:
            half = self.ex.round_amount(amt / 2.0) if (self.tp1_price and self.tp1_price > 0) else 0.0
            rest = self.ex.round_amount(amt - half)
            split_ok = (
                half > 0 and rest > 0
                and self.ex.min_tradeable(half, self.tp1_price)
                and self.ex.min_tradeable(rest, self.tp2_price)
            )
            if split_ok:
                tp1 = self.ex.place_take_profit_limit(close_side, self.tp1_price, half)
                tp2 = self.ex.place_take_profit_limit(close_side, self.tp2_price, rest)
                self.tp1_order_id = tp1.get("id")
                self.tp2_order_id = tp2.get("id")
                self.tp1_done = False
                self._log("protection_armed", stop=self.stop_price, tp1=self.tp1_price,
                          tp1_amt=half, tp2=self.tp2_price, tp2_amt=rest)
            else:
                # Half falls below the market min amount/notional (e.g. tiny notional near
                # Binance's ~$100 floor). Rather than error, fall back to a SINGLE full-size
                # TP at TP2 and skip the TP1 partial. tp1_done=True so we never try the BE
                # move (there is no partial to leave a remainder behind).
                log.warning("[LIVE] TP split below market min (half=%s) — single full-size TP2 fallback",
                            half)
                tp2 = self.ex.place_take_profit_limit(close_side, self.tp2_price, amt)
                self.tp2_order_id = tp2.get("id")
                self.tp1_done = True
                self._log("protection_armed_single_tp", stop=self.stop_price, tp2=self.tp2_price,
                          tp2_amt=amt, reason="split_below_min")
                self.notify("⚠️ <b>[LIVE] parcial abaixo do mínimo — TP único (full) no TP2</b>")
        else:
            # no TP target (shouldn't happen once main.py populates tp1/tp2) — stop only.
            log.warning("[LIVE] no TP target provided — protecting with STOP only")
            self.tp1_done = True
            self._log("protection_armed_stop_only", stop=self.stop_price)

        self.notify(f"🛡️ <b>[LIVE] proteção armada</b>  stop={self.stop_price:.2f}")

    def _service_position(self) -> None:
        """Manage the open position: detect the exit (flat) and, on the TP1 partial fill,
        move the stop to breakeven for the remaining size. Idempotent."""
        pos = self.ex.position()
        if pos.side is None or pos.amount <= 0:
            self._detect_and_handle_exit()
            return

        # TP1 partial fill → position dropped to ~half. Move the stop to breakeven
        # (actual entry fill price), sized to the remaining position. The threshold sits
        # between "half remaining" and "full" so it fires once TP1 closes its leg.
        if not self.tp1_done and self.filled_amount > 0 and pos.amount <= self.filled_amount * 0.75:
            remaining = pos.amount
            close_side = "buy" if self.direction == -1 else "sell"
            # cancel ONLY the current stop (NOT cancel_all — that would drop TP2 too).
            # conditional=True: the stop is an ALGO order (separate Binance endpoint).
            if self.stop_order_id is not None:
                self.ex.cancel(self.stop_order_id, conditional=True)
            new_stop = self.ex.place_stop_market(close_side, self.entry_fill_price, remaining)
            self.stop_order_id = new_stop.get("id")
            self.current_stop = self.entry_fill_price
            self.tp1_done = True
            self._log("tp1_fill_stop_be", be=self.entry_fill_price, remaining=remaining)
            self.notify(
                f"⚡ <b>[LIVE] TP1 parcial preenchido</b>\n"
                f"stop → BE {self.entry_fill_price:.2f}  (restante {remaining})"
            )

    def _detect_and_handle_exit(self) -> bool:
        """Exchange shows flat while we think we hold a position: the stop or TP2 closed
        it. Cancel any leftover orders and reset. Returns True if an exit was detected."""
        self.ex.cancel_all()
        self._log("exit_detected", prev_stop=self.current_stop)
        self.notify("🏁 <b>[LIVE] posição fechada na Binance (stop/TP2) — limpo</b>")
        self._reset()
        return True

    def _teardown(self, reason: str) -> None:
        """Engine says we should be flat: cancel any resting orders and market-close any
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
                self.notify("↩️ <b>[LIVE] entrada cancelada (retest timeout / break rejeitado)</b>")
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
