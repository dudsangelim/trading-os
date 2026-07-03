"""Taker-capitulation paper trader — main entry point.

SIGNAL_ONLY paper trader for H3 of the liqflow phase-2 study (2026-07-03):
LONG for exactly 4 hours after a taker-flow capitulation — z of the 24h mean
of log(taker buy/sell volume ratio) vs its 7-day norm drops below -2.
BTC/ETH/SOL, 1h bars, $1000 total (3 × $333.33 sleeves), 1x, fixed config.
Each 1h close: evaluate the newly closed bar(s), fill at the next bar's real
open. P&L net of fees + slippage + real funding.

Modes:
  (default)   live loop, triggers ~3 min after each 1h UTC close
  --once      run a single cycle and exit (smoke test)
  --status    print state JSON and exit

Health: GET /healthz  GET /status  on TCP_HEALTH_PORT (default 8107).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import signal
import sys
import threading
import time as time_mod
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd

from trading.taker_cap_paper import config as C
from trading.taker_cap_paper import exchange as X
from trading.taker_cap_paper import engine as E
from trading.taker_cap_paper import notifier as N
from trading.taker_cap_paper import signals as SIG

C.LOG_DIR.mkdir(parents=True, exist_ok=True)
C.DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(C.LOG_DIR / "run.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("taker_cap_paper")

STATE_FILE = C.DATA_DIR / "state.json"
TRADES_CSV = C.DATA_DIR / "trades.csv"
EQUITY_CSV = C.DATA_DIR / "equity.csv"
_startup_ts = datetime.now(timezone.utc)
_health = {"status": "starting"}


# ── persistence ──────────────────────────────────────────────────────────────
class State:
    def __init__(self):
        self.filters = X.load_filters()
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text())
            self.sleeves = {sym: E.Sleeve(**sd) for sym, sd in d["sleeves"].items()}
            self.peak_equity = d.get("peak_equity", C.INITIAL_CAPITAL)
            self.last_daily = d.get("last_daily", "")
            log.info("state loaded: %s",
                     {s: f"pos={sl.pos} eq=${sl.equity:.2f}" for s, sl in self.sleeves.items()})
        else:
            self.sleeves = {}
            for sym in C.SYMBOLS:
                f = self.filters[sym]
                self.sleeves[sym] = E.Sleeve(
                    symbol=sym, tick=f["tick"], step=f["step"],
                    min_notional=f["min_notional"],
                    equity=C.SLEEVE_CAPITAL, peak_equity=C.SLEEVE_CAPITAL)
            self.peak_equity = C.INITIAL_CAPITAL
            self.last_daily = ""
            log.info("fresh state @ $%.2f (%d sleeves)", C.INITIAL_CAPITAL, len(self.sleeves))

    def save(self):
        STATE_FILE.write_text(json.dumps(
            {"saved_at": datetime.now(timezone.utc).isoformat(),
             "sleeves": {s: sl.to_dict() for s, sl in self.sleeves.items()},
             "peak_equity": self.peak_equity, "last_daily": self.last_daily},
            indent=2, default=str))


def _append_csv(path, header, row):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


# ── one sleeve, one cycle ────────────────────────────────────────────────────
def run_sleeve(sl: E.Sleeve, diag: dict) -> None:
    sym = sl.symbol
    raw = X.fetch_klines(sym, C.FETCH_BARS, C.TIMEFRAME)
    closed, next_open = X.closed_and_inprogress(raw)
    taker = X.fetch_taker_5m(sym, C.TK_FETCH_HOURS)
    tk_hours = (taker.index.max() - taker.index.min()).total_seconds() / 3600
    if len(closed) < C.MIN_TK_HOURS or tk_hours < C.MIN_TK_HOURS:
        log.warning("%s: insufficient history (%d bars / %.0fh taker)", sym, len(closed), tk_hours)
        return

    sig = SIG.build_signals(closed, taker)
    mark = float(closed["close"].iloc[-1])

    ts_index = closed.index
    if sl.last_bar_ts is None:
        start_pos = len(closed) - 1            # first run: only the latest bar
    else:
        last = pd.Timestamp(sl.last_bar_ts)
        newer = ts_index[ts_index > last]
        start_pos = len(closed) - len(newer)
    start_pos = max(start_pos, 1)              # need a prev bar

    funding_fn = ((lambda a, b: X.fetch_funding_between(sym, a, b))
                  if C.FUNDING_ENABLED else None)
    o = closed["open"].values; h = closed["high"].values
    l = closed["low"].values; c = closed["close"].values
    open_ms = (ts_index.view("int64") // 1_000_000).astype("int64")
    eopt = {"max_hold": C.HOLD_BARS}

    for p in range(start_pos, len(closed)):
        j = {"open": o[p], "high": h[p], "low": l[p], "close": c[p],
             "close_ms": int(open_ms[p] + C.TF_MS)}
        prev = {"open": o[p - 1], "high": h[p - 1], "low": l[p - 1], "close": c[p - 1]}
        was_holding = sl.pos != 0   # research non-overlap: no signal on the exit bar
        sig_j = {"long_entry": bool(sig["long_entry"].iloc[p]) and not was_holding,
                 "long_exit": False, "short_entry": False, "short_exit": False}
        if p + 1 < len(closed):
            nopen, nopen_ms = o[p + 1], int(open_ms[p + 1])
        else:
            nopen, nopen_ms = next_open, int(open_ms[p] + C.TF_MS)
        events = sl.process_bar(j, prev, sig_j, 0.0, 0.0, nopen, nopen_ms,
                                eopt, False, funding_fn)
        bar_ts = ts_index[p].strftime("%Y-%m-%d %H:%M")
        for ev in events:
            if ev["type"] == "open":
                N.notify_open(ev, bar_ts)
                _append_csv(TRADES_CSV, ["ts", "sym", "type", "side", "px", "qty", "pnl", "reason"],
                            [bar_ts, sym, "open", ev["side"], ev["px"], ev["qty"], "", ""])
            else:
                N.notify_close(ev, sl.realized_pnl, sl.n_trades, sl.n_wins)
                _append_csv(TRADES_CSV, ["ts", "sym", "type", "side", "px", "qty", "pnl", "reason"],
                            [bar_ts, sym, "close", ev["side"], ev["exit_px"], ev["qty"],
                             round(ev["pnl"], 4), ev["reason"]])
                if sl.consec_losses >= C.CONSEC_LOSS_ALERT:
                    N.notify_consec_losses(sym, sl.consec_losses, sl.equity)

    sl.last_bar_ts = ts_index[-1].strftime("%Y-%m-%d %H:%M:%S")
    sl.peak_equity = max(sl.peak_equity, sl.equity)
    z_last = sig["z"].iloc[-1]
    diag[sym] = {"z": round(float(z_last), 3) if pd.notna(z_last) else None,
                 "pos": sl.pos, "mark": mark, "eq_mtm": sl.mark_equity(mark)}


# ── one cycle ────────────────────────────────────────────────────────────────
def run_cycle(st: State, first_run: bool = False) -> None:
    diag = {}
    for sym, sl in st.sleeves.items():
        try:
            run_sleeve(sl, diag)
        except Exception:
            log.error("sleeve %s failed:\n%s", sym, traceback.format_exc())
            N.notify_error(f"sleeve {sym}", traceback.format_exc())

    eq_mtm = sum(d["eq_mtm"] for d in diag.values()) if len(diag) == len(st.sleeves) else \
        sum(sl.mark_equity(sl.entry_px or 0) if sl.pos else sl.equity for sl in st.sleeves.values())
    st.peak_equity = max(st.peak_equity, eq_mtm)
    max_dd = eq_mtm / st.peak_equity - 1
    pnl_total = eq_mtm - C.INITIAL_CAPITAL
    _append_csv(EQUITY_CSV, ["ts", "equity", "pnl_total", "dd"],
                [datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                 round(eq_mtm, 2), round(pnl_total, 2), round(max_dd, 4)])

    if first_run:
        N.notify_startup(eq_mtm)
    if max_dd <= -C.DD_ALERT_PCT:
        N.notify_dd_alert(max_dd, eq_mtm)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if datetime.now(timezone.utc).hour == 8 and st.last_daily != today:
        N.notify_daily(eq_mtm, pnl_total,
                       {s: sl.to_dict() for s, sl in st.sleeves.items()}, max_dd)
        st.last_daily = today

    n_tr = sum(sl.n_trades for sl in st.sleeves.values())
    _health.update({"status": "ok", "equity": round(eq_mtm, 2),
                    "pnl_total": round(pnl_total, 2), "max_dd": round(max_dd, 4),
                    "positions": {s: sl.pos for s, sl in st.sleeves.items()},
                    "n_trades": n_tr,
                    "signal": {s: d.get("z") for s, d in diag.items()},
                    "updated": datetime.now(timezone.utc).isoformat()})
    st.save()
    log.info("cycle done | eq=$%.2f pnl=$%.2f dd=%.1f%% | pos=%s | z=%s",
             eq_mtm, pnl_total, max_dd * 100,
             {s: sl.pos for s, sl in st.sleeves.items()},
             {s: d.get("z") for s, d in diag.items()})


# ── health server ────────────────────────────────────────────────────────────
class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/healthz"):
            body = json.dumps({"status": _health.get("status", "?"),
                               "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
                               "equity": _health.get("equity"),
                               "positions": _health.get("positions")}).encode()
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


# ── scheduling ───────────────────────────────────────────────────────────────
def _next_trigger(now: datetime) -> datetime:
    """Next 1h boundary + 3 min (taker 5m bucket needs a moment to publish)."""
    cand = now.replace(minute=3, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(hours=1)
    return cand


_stop = False


def _on_sig(*_):
    global _stop
    _stop = True
    log.info("shutdown signal received")


def run_live(st: State):
    signal.signal(signal.SIGTERM, _on_sig)
    signal.signal(signal.SIGINT, _on_sig)
    try:
        run_cycle(st, first_run=not STATE_FILE.exists())
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
            run_cycle(st)
        except Exception:
            log.error("cycle failed:\n%s", traceback.format_exc())
            N.notify_error("cycle", traceback.format_exc())
    log.info("stopped")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    st = State()
    if args.status:
        print(json.dumps({"sleeves": {s: sl.to_dict() for s, sl in st.sleeves.items()},
                          "peak_equity": st.peak_equity}, indent=2, default=str))
        return 0
    _start_health()
    if args.once:
        run_cycle(st, first_run=not STATE_FILE.exists())
        return 0
    run_live(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
