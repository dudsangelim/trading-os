"""BTC-lead paper trader — main entry point.

SIGNAL_ONLY paper trader for the round-2 winner of the single-asset 2023+
study: trade ETHUSDT 4h in the direction of BTC's trend+momentum
(roc(40) > 0 AND btc > ema(100) -> long; mirrored short). Fixed config, no
re-optimization. Each 4h close: evaluate the newly closed bar(s) and execute
paper fills at the next bar's real open. P&L net of fees + real funding.

Modes:
  (default)   live loop, triggers ~2 min after each 4h UTC close
  --once      run a single cycle and exit (smoke test)
  --status    print state JSON and exit

Health: GET /healthz  GET /status  on BLP_HEALTH_PORT (default 8106).
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

from trading.btc_lead_paper import config as C
from trading.btc_lead_paper import exchange as X
from trading.btc_lead_paper import engine as E
from trading.btc_lead_paper import notifier as N
from trading.btc_lead_paper import signals as SIG

C.LOG_DIR.mkdir(parents=True, exist_ok=True)
C.DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(C.LOG_DIR / "run.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("btc_lead_paper")

STATE_FILE = C.DATA_DIR / "state.json"
TRADES_CSV = C.DATA_DIR / "trades.csv"
EQUITY_CSV = C.DATA_DIR / "equity.csv"
_startup_ts = datetime.now(timezone.utc)
_health = {"status": "starting"}


# ── persistence ──────────────────────────────────────────────────────────────
class State:
    def __init__(self):
        self.filters = X.load_filters()
        f = self.filters[C.TRADE_SYMBOL]
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text())
            self.sleeve = E.Sleeve(**d["sleeve"])
            self.peak_equity = d.get("peak_equity", C.INITIAL_CAPITAL)
            self.last_daily = d.get("last_daily", "")
            log.info("state loaded: pos=%d eq=$%.2f", self.sleeve.pos, self.sleeve.equity)
        else:
            self.sleeve = E.Sleeve(symbol=C.TRADE_SYMBOL, tick=f["tick"], step=f["step"],
                                   min_notional=f["min_notional"],
                                   equity=C.INITIAL_CAPITAL, peak_equity=C.INITIAL_CAPITAL)
            self.peak_equity = C.INITIAL_CAPITAL
            self.last_daily = ""
            log.info("fresh state @ $%.2f", C.INITIAL_CAPITAL)

    def save(self):
        STATE_FILE.write_text(json.dumps(
            {"saved_at": datetime.now(timezone.utc).isoformat(),
             "sleeve": self.sleeve.to_dict(),
             "peak_equity": self.peak_equity, "last_daily": self.last_daily},
            indent=2, default=str))


def _append_csv(path, header, row):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


# ── one cycle ────────────────────────────────────────────────────────────────
def run_cycle(st: State, first_run: bool = False) -> None:
    sl = st.sleeve

    trade_raw = X.fetch_klines(C.TRADE_SYMBOL, C.FETCH_BARS, C.TIMEFRAME)
    signal_raw = X.fetch_klines(C.SIGNAL_SYMBOL, C.FETCH_BARS, C.TIMEFRAME)
    closed, next_open = X.closed_and_inprogress(trade_raw)
    sig_closed, _ = X.closed_and_inprogress(signal_raw)
    if len(closed) < C.MIN_BARS or len(sig_closed) < C.MIN_BARS:
        log.warning("insufficient history (%d trade / %d signal bars)", len(closed), len(sig_closed))
        return
    mark = float(closed["close"].iloc[-1])

    sig = SIG.build_signals(closed, sig_closed["close"])

    # ---- determine new closed bars to process ----
    ts_index = closed.index
    if sl.last_bar_ts is None:
        start_pos = len(closed) - 1            # first run: process only the latest bar
    else:
        last = pd.Timestamp(sl.last_bar_ts)
        newer = ts_index[ts_index > last]
        start_pos = len(closed) - len(newer)
    start_pos = max(start_pos, 1)              # need a prev bar

    funding_fn = ((lambda a, b: X.fetch_funding_between(C.TRADE_SYMBOL, a, b))
                  if C.FUNDING_ENABLED else None)
    o = closed["open"].values; h = closed["high"].values
    l = closed["low"].values; c = closed["close"].values
    open_ms = (ts_index.view("int64") // 1_000_000).astype("int64")

    for p in range(start_pos, len(closed)):
        j = {"open": o[p], "high": h[p], "low": l[p], "close": c[p],
             "close_ms": int(open_ms[p] + C.TF_MS)}
        prev = {"open": o[p - 1], "high": h[p - 1], "low": l[p - 1], "close": c[p - 1]}
        sig_j = {k: bool(sig[k].iloc[p]) for k in
                 ("long_entry", "long_exit", "short_entry", "short_exit")}
        if p + 1 < len(closed):
            nopen, nopen_ms = o[p + 1], int(open_ms[p + 1])
        else:
            nopen, nopen_ms = next_open, int(open_ms[p] + C.TF_MS)
        events = sl.process_bar(j, prev, sig_j, 0.0, 0.0, nopen, nopen_ms,
                                {}, C.ALLOW_SHORT, funding_fn)
        bar_ts = ts_index[p].strftime("%Y-%m-%d %H:%M")
        for ev in events:
            if ev["type"] == "open":
                N.notify_open(ev, bar_ts)
                _append_csv(TRADES_CSV, ["ts", "sym", "type", "side", "px", "qty", "pnl", "reason"],
                            [bar_ts, C.TRADE_SYMBOL, "open", ev["side"], ev["px"], ev["qty"], "", ""])
            else:
                N.notify_close(ev, sl.realized_pnl, sl.n_trades, sl.n_wins)
                _append_csv(TRADES_CSV, ["ts", "sym", "type", "side", "px", "qty", "pnl", "reason"],
                            [bar_ts, C.TRADE_SYMBOL, "close", ev["side"], ev["exit_px"], ev["qty"],
                             round(ev["pnl"], 4), ev["reason"]])
                if sl.consec_losses >= C.CONSEC_LOSS_ALERT:
                    N.notify_consec_losses(sl.consec_losses, sl.equity)

    sl.last_bar_ts = ts_index[-1].strftime("%Y-%m-%d %H:%M:%S")
    sl.peak_equity = max(sl.peak_equity, sl.equity)

    # ---- accounting + alerts ----
    eq_mtm = sl.mark_equity(mark)
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
        N.notify_daily(eq_mtm, pnl_total, sl.to_dict(), max_dd)
        st.last_daily = today

    last_sig = sig.iloc[-1]
    _health.update({"status": "ok", "equity": round(eq_mtm, 2),
                    "pnl_total": round(pnl_total, 2), "max_dd": round(max_dd, 4),
                    "position": sl.pos, "n_trades": sl.n_trades,
                    "signal": {"roc": round(float(last_sig["roc"]), 5),
                               "btc": float(last_sig["btc"]),
                               "ema": round(float(last_sig["ema"]), 2)},
                    "updated": datetime.now(timezone.utc).isoformat()})
    st.save()
    log.info("cycle done | eq=$%.2f pnl=$%.2f dd=%.1f%% pos=%d | btc_roc=%.4f btc=%.0f ema=%.0f",
             eq_mtm, pnl_total, max_dd * 100, sl.pos,
             float(last_sig["roc"]), float(last_sig["btc"]), float(last_sig["ema"]))


# ── health server ────────────────────────────────────────────────────────────
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


# ── scheduling ───────────────────────────────────────────────────────────────
def _next_trigger(now: datetime) -> datetime:
    """Next 4h boundary (00/04/08/12/16/20 UTC) + 2 min."""
    base = now.replace(minute=2, second=0, microsecond=0)
    h = now.hour - (now.hour % 4)
    cand = base.replace(hour=h)
    if cand <= now:
        cand += timedelta(hours=4)
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
        print(json.dumps({"sleeve": st.sleeve.to_dict(),
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
