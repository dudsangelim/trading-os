"""VRP paper trader — main loop.

Cycle every hour at :05 UTC (marks + health). On the 08:05 cycle:
  1. settle an expired straddle (official venue price; retries if unpublished);
  2. re-hedge net delta via paper perp;
  3. Fridays, if flat: sell the next-Friday ATM straddle at the real best bid.

Modes: (default) live loop | --once single cycle | --status print state.
Health: GET /healthz and /status on VRP_HEALTH_PORT (default 8108).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import signal as sig_mod
import sys
import threading
import time as time_mod
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from trading.vrp_paper import config as C
from trading.vrp_paper import engine as E
from trading.vrp_paper import notifier as N
from trading.vrp_paper import volsurface as VS

C.LOG_DIR.mkdir(parents=True, exist_ok=True)
C.DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(C.LOG_DIR / "run.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("vrp_paper")

STATE_FILE = C.DATA_DIR / "state.json"
TRADES_CSV = C.DATA_DIR / "trades.csv"
EQUITY_CSV = C.DATA_DIR / "equity.csv"
_startup_ts = datetime.now(timezone.utc)
_health = {"status": "starting"}


def load_book() -> E.Book:
    if STATE_FILE.exists():
        d = json.loads(STATE_FILE.read_text())
        pos = d.pop("position", None)
        book = E.Book(**{k: v for k, v in d.items() if k in E.Book.__dataclass_fields__ and k != "position"})
        if pos:
            book.position = E.Position(**pos)
        log.info("state loaded: eq=%.2f pos=%s", book.equity, bool(book.position))
        return book
    log.info("fresh book @ $%.2f", C.INITIAL_CAPITAL)
    return E.Book()


def save_book(book: E.Book, extra: dict) -> None:
    d = book.to_dict()
    d.update({"saved_at": datetime.now(timezone.utc).isoformat(), **extra})
    STATE_FILE.write_text(json.dumps(d, indent=2, default=str))


def _append_csv(path, header, row):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def run_cycle(book: E.Book, first_run: bool = False) -> None:
    now = datetime.now(timezone.utc)
    is_action_hour = now.hour == C.ACTION_HOUR_UTC

    # 1. settlement (any cycle at/after expiry — delivery may lag a few minutes)
    if book.position is not None:
        expiry = datetime.strptime(book.position.expiry_ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        if now >= expiry:
            ev = book.settle(now)
            if ev is not None:
                log.info("settled: %s", {k: ev[k] for k in ("settle", "source", "pnl")})
                N.notify_close(ev, book)
                _append_csv(TRADES_CSV,
                            ["entry", "expiry", "legs", "settle", "source", "pnl", "ret_pct", "equity"],
                            [ev["entry"], ev["expiry"], "|".join(ev["legs"]), round(ev["settle"], 1),
                             ev["source"], round(ev["pnl"], 4), round(ev["ret_pct"], 3),
                             round(ev["equity"], 2)])
                if book.consec_losses >= C.CONSEC_LOSS_ALERT:
                    N.notify_consec_losses(book.consec_losses, book.equity)

    # 1b. daily vol-surface sample (slope conditioning research, options_edge2)
    if is_action_hour:
        try:
            VS.record_daily(now)
        except Exception:
            log.warning("volsurface sample failed:\n%s", traceback.format_exc())

    # 2. entry — Fridays on the action cycle, when flat, once per week
    week = now.strftime("%G-W%V")
    if (book.position is None and now.weekday() == C.ENTRY_DOW and is_action_hour
            and book.last_entry_week != week):
        vol_sig = None
        try:
            vol_sig = VS.signal_now()
            if vol_sig is not None:
                _append_csv(C.DATA_DIR / "vol_signal.csv",
                            ["date", "iv7", "iv30", "slope", "dvol", "z_slope",
                             "mult_slope", "mult_dvol_iv7", "applied"],
                            [vol_sig["date"], vol_sig["iv7"], vol_sig["iv30"],
                             vol_sig["slope"], vol_sig["dvol"], vol_sig["z_slope"],
                             vol_sig["mult_slope"], vol_sig["mult_dvol_iv7"],
                             vol_sig["applied"]])
                log.info("vol signal: slope=%+.3f z=%s mult=%.1fx (dvol_iv7 %.1fx) applied=%s",
                         vol_sig["slope"], vol_sig["z_slope"], vol_sig["mult_slope"],
                         vol_sig["mult_dvol_iv7"], vol_sig["applied"])
        except Exception:
            log.warning("vol signal failed:\n%s", traceback.format_exc())
        ev = book.open_straddle(now, vol_signal=vol_sig)
        book.last_entry_week = week      # one attempt per week, found or not
        if ev is not None:
            log.info("opened: %.4f contratos, prêmio $%.2f (%.1f%%)",
                     ev["contracts"], ev["prem_usd"], ev["prem_pct"])
            N.notify_open(ev)
            _append_csv(TRADES_CSV,
                        ["entry", "expiry", "legs", "settle", "source", "pnl", "ret_pct", "equity"],
                        [book.position.entry_ts, ev["expiry"],
                         "|".join(l["instrument"] for l in ev["legs"]),
                         "", "open", "", "", round(book.equity, 2)])

    # 3. mark (+ hedge on the action cycle)
    hedge_info = {}
    if book.position is not None:
        try:
            hedge_info = book.hedge_and_mark(do_hedge=is_action_hour)
            equity_mtm = hedge_info["equity_mtm"]
        except Exception:
            log.error("mark failed:\n%s", traceback.format_exc())
            equity_mtm = book.equity
    else:
        equity_mtm = book.equity

    book.peak_equity = max(book.peak_equity, equity_mtm)
    max_dd = equity_mtm / book.peak_equity - 1
    _append_csv(EQUITY_CSV, ["ts", "equity_mtm", "pnl_total", "dd"],
                [now.strftime("%Y-%m-%d %H:%M"), round(equity_mtm, 2),
                 round(equity_mtm - C.INITIAL_CAPITAL, 2), round(max_dd, 4)])

    if first_run:
        N.notify_startup(equity_mtm)
    if max_dd <= -C.DD_ALERT_PCT:
        N.notify_dd_alert(max_dd, equity_mtm)
    if is_action_hour:
        N.notify_daily(equity_mtm, book, hedge_info, max_dd)

    _health.update({"status": "ok", "equity": round(equity_mtm, 2),
                    "venue": C.VENUE,
                    "pnl_total": round(equity_mtm - C.INITIAL_CAPITAL, 2),
                    "max_dd": round(max_dd, 4),
                    "position": bool(book.position),
                    "net_delta": round(hedge_info.get("net_delta", 0.0), 4),
                    "updated": now.isoformat()})
    save_book(book, {"equity_mtm": equity_mtm, "max_dd": max_dd})
    log.info("cycle done | eqMtM=$%.2f dd=%.1f%% pos=%s",
             equity_mtm, max_dd * 100, bool(book.position))


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/healthz"):
            body = json.dumps({"status": _health.get("status", "?"),
                               "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
                               "equity": _health.get("equity"),
                               "position": _health.get("position")}).encode()
        else:
            body = json.dumps(_health, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _start_health():
    srv = HTTPServer(("0.0.0.0", C.HEALTH_PORT), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("health server on :%d", C.HEALTH_PORT)


_stop = False


def _on_sig(*_):
    global _stop
    _stop = True
    log.info("shutdown signal received")


def _next_trigger(now: datetime) -> datetime:
    cand = now.replace(minute=C.CYCLE_MINUTE, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(hours=1)
    return cand


def run_live(book: E.Book):
    sig_mod.signal(sig_mod.SIGTERM, _on_sig)
    sig_mod.signal(sig_mod.SIGINT, _on_sig)
    try:
        run_cycle(book, first_run=not STATE_FILE.exists())
    except Exception:
        log.error("first cycle failed:\n%s", traceback.format_exc())
        N.notify_error("first_cycle", traceback.format_exc())
    while not _stop:
        nxt = _next_trigger(datetime.now(timezone.utc))
        while not _stop and datetime.now(timezone.utc) < nxt:
            time_mod.sleep(min(15.0, (nxt - datetime.now(timezone.utc)).total_seconds()))
        if _stop:
            break
        try:
            run_cycle(book)
        except Exception:
            log.error("cycle failed:\n%s", traceback.format_exc())
            N.notify_error("cycle", traceback.format_exc())
    log.info("stopped")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    book = load_book()
    if args.status:
        print(json.dumps(book.to_dict(), indent=2, default=str))
        return 0
    _start_health()
    if args.once:
        run_cycle(book, first_run=not STATE_FILE.exists())
        return 0
    run_live(book)
    return 0


if __name__ == "__main__":
    sys.exit(main())
