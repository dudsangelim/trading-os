"""Swing walk-forward paper trader — main entry point.

Trades the validated 4h walk-forward portfolio (BTC/ETH/SOL) on paper money.
Each 4h close: re-select config if due, then evaluate the strategy on the newly
closed bar(s) and execute paper fills at the next bar's real open (or stop/target
price for stop exits). All P&L is net of fees + funding.

Modes:
  (default)   live loop, triggers ~2 min after each 4h UTC close
  --once      run a single cycle and exit (smoke test)
  --status    print state JSON and exit
  --reopt     force re-optimization on this cycle

Health: GET /healthz  GET /status  on SWF_HEALTH_PORT (default 8105).
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
from typing import Dict

import pandas as pd

from trading.swing_wf_paper import config as C
from trading.swing_wf_paper import exchange as X
from trading.swing_wf_paper import engine as E
from trading.swing_wf_paper import notifier as N
from trading.swing_wf_paper import strategies as S

C.LOG_DIR.mkdir(parents=True, exist_ok=True)
C.DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(C.LOG_DIR / "run.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("swing_wf_paper")

STATE_FILE = C.DATA_DIR / "state.json"
TRADES_CSV = C.DATA_DIR / "trades.csv"
EQUITY_CSV = C.DATA_DIR / "equity.csv"
_startup_ts = datetime.now(timezone.utc)
_health = {"status": "starting"}


# ── persistence ──────────────────────────────────────────────────────────────
class Portfolio:
    def __init__(self):
        self.filters = X.load_filters()
        self.sleeves: Dict[str, E.Sleeve] = {}
        self.bars_since_reopt: Dict[str, int] = {}
        self.peak_equity = C.INITIAL_CAPITAL
        self.last_daily = ""
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text())
            for sym, sd in d["sleeves"].items():
                self.sleeves[sym] = E.Sleeve(**sd)
            self.bars_since_reopt = d.get("bars_since_reopt", {})
            self.peak_equity = d.get("peak_equity", C.INITIAL_CAPITAL)
            self.last_daily = d.get("last_daily", "")
            log.info("state loaded: %d sleeves", len(self.sleeves))
        else:
            for sym in C.SYMBOLS:
                f = self.filters[sym]
                self.sleeves[sym] = E.Sleeve(symbol=sym, tick=f["tick"], step=f["step"],
                                             min_notional=f["min_notional"],
                                             equity=C.SLEEVE_CAPITAL, peak_equity=C.SLEEVE_CAPITAL)
                self.bars_since_reopt[sym] = C.REOPT_BARS  # force reopt on first cycle
            log.info("fresh portfolio: %d sleeves @ $%.2f", len(self.sleeves), C.SLEEVE_CAPITAL)

    def _save(self):
        d = {"saved_at": datetime.now(timezone.utc).isoformat(),
             "sleeves": {s: sl.to_dict() for s, sl in self.sleeves.items()},
             "bars_since_reopt": self.bars_since_reopt,
             "peak_equity": self.peak_equity, "last_daily": self.last_daily}
        STATE_FILE.write_text(json.dumps(d, indent=2, default=str))

    def total_equity(self, marks: Dict[str, float]) -> float:
        return sum(sl.mark_equity(marks.get(s, sl.entry_px or 0))
                   for s, sl in self.sleeves.items())

    def total_realized(self) -> float:
        return sum(sl.equity for sl in self.sleeves.values()) - C.INITIAL_CAPITAL


def _append_csv(path, header, row):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


# ── one cycle ────────────────────────────────────────────────────────────────
def run_cycle(pf: Portfolio, force_reopt: bool = False, first_run: bool = False) -> None:
    marks: Dict[str, float] = {}
    reopt_picks = {}
    need = C.TRAIN_BARS + C.WARMUP_BARS + 5

    for sym, sl in pf.sleeves.items():
        df = X.fetch_klines(sym, need, C.TIMEFRAME)
        closed, next_open = X.closed_and_inprogress(df)
        if len(closed) < C.WARMUP_BARS + 50:
            log.warning("%s: insufficient history (%d)", sym, len(closed))
            continue
        marks[sym] = float(closed["close"].iloc[-1])

        # ---- re-optimization (walk-forward selection) ----
        # fresh sleeves start with bars_since_reopt = REOPT_BARS, so this fires
        # on the first cycle. We never key the trigger off `config is None`
        # (that would retry — and Telegram-spam — every 4h until a config
        # qualifies); the counter alone schedules retries.
        if force_reopt or pf.bars_since_reopt.get(sym, 0) >= C.REOPT_BARS:
            df_ww = closed.iloc[-(C.TRAIN_BARS + C.WARMUP_BARS):]
            pick = E.optimize_sleeve(df_ww)
            pf.bars_since_reopt[sym] = 0
            if pick is not None:
                sl.config = pick
                reopt_picks[sym] = pick
                log.info("%s reopt -> %s", sym, pick)
            else:
                # keep the prior config if we have one; otherwise stay flat and
                # retry at the next scheduled reopt (notify once).
                log.warning("%s reopt: no qualifying config (keeping %s)", sym, sl.config)
                if sl.config is None:
                    reopt_picks[sym] = None

        if sl.config is None:
            continue

        # ---- build signals on full closed history ----
        builder = S.STRATS[sl.config["strat"]][0]
        sig = builder(closed, **sl.config["params"])
        eopt = sl.config["exec"]
        allow_short = bool(sl.config["short"])

        # ---- determine new closed bars to process ----
        ts_index = closed.index
        if sl.last_bar_ts is None:
            start_pos = len(closed) - 1            # first run: process only the latest bar
        else:
            last = pd.Timestamp(sl.last_bar_ts)
            newer = ts_index[ts_index > last]
            start_pos = len(closed) - len(newer)
        start_pos = max(start_pos, 1)              # need a prev bar

        funding_fn = (lambda a, b: X.fetch_funding_between(sym, a, b)) if C.FUNDING_ENABLED else None
        o = closed["open"].values; h = closed["high"].values
        l = closed["low"].values; c = closed["close"].values
        atr = sig["atr"].values
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
            events = sl.process_bar(j, prev, sig_j, atr[p], atr[p - 1], nopen, nopen_ms,
                                    eopt, allow_short, funding_fn)
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

            pf.bars_since_reopt[sym] = pf.bars_since_reopt.get(sym, 0) + 1

        sl.last_bar_ts = ts_index[-1].strftime("%Y-%m-%d %H:%M:%S")
        sl.peak_equity = max(sl.peak_equity, sl.equity)

    # ---- portfolio-level accounting + alerts ----
    eq_total = pf.total_equity(marks)
    pf.peak_equity = max(pf.peak_equity, eq_total)
    max_dd = eq_total / pf.peak_equity - 1
    pnl_total = eq_total - C.INITIAL_CAPITAL   # mark-to-market (incl. open positions)
    _append_csv(EQUITY_CSV, ["ts", "equity", "pnl_total", "dd"],
                [datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                 round(eq_total, 2), round(pnl_total, 2), round(max_dd, 4)])

    if reopt_picks:
        N.notify_reopt(reopt_picks)
    if first_run:
        N.notify_startup(eq_total, {s: sl.to_dict() for s, sl in pf.sleeves.items()})
    if max_dd <= -C.DD_ALERT_PCT:
        N.notify_dd_alert(max_dd, eq_total)

    # daily status on the 08:00 UTC boundary
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if datetime.now(timezone.utc).hour == 8 and pf.last_daily != today:
        N.notify_daily(eq_total, pnl_total, {s: sl.to_dict() for s, sl in pf.sleeves.items()}, max_dd)
        pf.last_daily = today

    _health.update({"status": "ok", "equity": round(eq_total, 2),
                    "pnl_total": round(pnl_total, 2), "max_dd": round(max_dd, 4),
                    "positions": {s: sl.pos for s, sl in pf.sleeves.items()},
                    "updated": datetime.now(timezone.utc).isoformat()})
    pf._save()
    log.info("cycle done | equity=$%.2f pnl=$%.2f dd=%.1f%%", eq_total, pnl_total, max_dd * 100)


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


def run_live(pf: Portfolio):
    signal.signal(signal.SIGTERM, _on_sig)
    signal.signal(signal.SIGINT, _on_sig)
    try:
        run_cycle(pf, first_run=not STATE_FILE.exists() or all(
            sl.config is None for sl in pf.sleeves.values()))
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
            run_cycle(pf)
        except Exception:
            log.error("cycle failed:\n%s", traceback.format_exc())
            N.notify_error("cycle", traceback.format_exc())
    log.info("stopped")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reopt", action="store_true")
    args = ap.parse_args()

    if C.ORDER_TYPE != "MARKET":
        log.warning("ORDER_TYPE=%s not implemented; using MARKET (fill at next bar open)", C.ORDER_TYPE)
    pf = Portfolio()
    if args.status:
        print(json.dumps({s: sl.to_dict() for s, sl in pf.sleeves.items()}, indent=2, default=str))
        return 0
    _start_health()
    if args.once:
        run_cycle(pf, force_reopt=args.reopt,
                  first_run=all(sl.config is None for sl in pf.sleeves.values()))
        return 0
    run_live(pf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
