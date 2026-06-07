"""EF3 R003 / F-A.1.M.f paper trader — main entry point.

Polls Binance Futures every minute; on each newly-closed H4 bar it runs the
R003 entry/exit logic (30-bar H4 close breakout + SMA200 Daily filter +
ATR14×2.0 trailing stop).

Health endpoint: GET /healthz on FA1MF_HEALTH_PORT (default 8102).
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time as time_mod
import traceback
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd

from trading.ef3_fa1mf_paper.config import (
    HEALTH_PORT, INITIAL_CAPITAL, LOG_DIR, STRATEGY_ID, WORKER,
)
from trading.ef3_fa1mf_paper.engine import FA1MfEngine
from trading.ef3_fa1mf_paper.feed import Feed
from trading.ef3_fa1mf_paper.persistence import (
    append_row, init_csvs, load_state, save_state,
)

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ef3_fa1mf")

_startup_ts = datetime.now(timezone.utc)

_health_state: dict = {
    "worker": WORKER,
    "strategy_id": STRATEGY_ID,
    "state": "starting",
    "equity": INITIAL_CAPITAL,
    "n_trades": 0,
    "last_bar_ts": None,
    "price": None,
}


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ("/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "status": "ok",
            "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
            **_health_state,
        }, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def _start_health_server() -> None:
    try:
        srv = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("[health] listening on port %d", HEALTH_PORT)
    except Exception as exc:
        log.warning("[health] could not start server: %s", exc)


def _to_frame(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    return df[["open", "high", "low", "close"]]


class PaperTrader:
    def __init__(self) -> None:
        self.engine = FA1MfEngine()
        self.equity = INITIAL_CAPITAL
        init_csvs()
        self._load_state()
        self._update_health(self.engine.last_price)

    def _load_state(self) -> None:
        st = load_state()
        if not st:
            return
        try:
            self.engine.load_state_dict(st.get("engine", {}))
            self.equity = st.get("equity", INITIAL_CAPITAL)
            log.info("[state loaded] state=%s equity=$%.4f n_trades=%d",
                     self.engine.state, self.equity, len(self.engine.trades))
        except Exception:
            log.error("[state] load error:\n%s", traceback.format_exc())

    def _save_state(self) -> None:
        save_state({"equity": self.equity, "engine": self.engine.to_state_dict()})

    def _update_health(self, price) -> None:
        _health_state.update({
            "state": self.engine.state,
            "equity": round(self.equity, 4),
            "n_trades": len(self.engine.trades),
            "last_bar_ts": self.engine.last_bar_ts,
            "price": price,
        })

    def tick(self) -> None:
        h4 = _to_frame(Feed.h4(limit=60))
        daily = _to_frame(Feed.daily(limit=300))
        n_before = len(self.engine.trades)

        decisions = self.engine.process_bar(h4, daily)
        price = Feed.get_price()
        self.engine.last_price = price

        for d in decisions:
            append_row("decisions.csv", {
                "ts": d["ts"], "action": d["action"], "price": d["price"],
                "state_after": d["state_after"],
                "extra_json": json.dumps(
                    {k: v for k, v in d.items()
                     if k not in ("ts", "action", "price", "state_after")},
                    default=str),
            })
            log.info("[DECISION %s] ts=%s price=%.2f state->%s",
                     d["action"], d["ts"], d["price"], d["state_after"])

        for tr in self.engine.trades[n_before:]:
            self.equity *= (1 + tr["ret_pct_net"] / 100.0)
            append_row("trades.csv", {**tr, "equity_after": round(self.equity, 6)})
            log.info("[TRADE CLOSED] %s %.2f->%.2f ret_net=%+.3f%% equity=$%.4f",
                     tr["exit_type"], tr["entry_price"], tr["exit_price"],
                     tr["ret_pct_net"], self.equity)

        append_row("equity_curve.csv", {
            "ts": datetime.now(timezone.utc).isoformat(),
            "equity": round(self.equity, 6), "state": self.engine.state,
        })
        self._save_state()
        self._update_health(price)
        log.info("[tick] state=%s equity=$%.4f last_bar=%s price=%.2f",
                 self.engine.state, self.equity, self.engine.last_bar_ts, price)


_shutdown = False


def _on_signal(*_) -> None:
    global _shutdown
    _shutdown = True
    log.info("[shutdown] signal received")


def _seconds_until_next_minute() -> float:
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(seconds=60 - now.second + 1)).replace(microsecond=0)
    return max(1.0, (nxt - now).total_seconds())


def run_live(trader: PaperTrader) -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log.info("live mode started (%s / %s)", WORKER, STRATEGY_ID)

    # process immediately on boot so health reflects current state quickly
    try:
        trader.tick()
    except Exception:
        log.error("[boot tick error]\n%s", traceback.format_exc())

    while not _shutdown:
        try:
            wait = _seconds_until_next_minute()
            deadline = time_mod.time() + wait
            while time_mod.time() < deadline and not _shutdown:
                time_mod.sleep(min(1.0, max(0.0, deadline - time_mod.time())))
            if _shutdown:
                break
            trader.tick()
        except Exception:
            log.error("[loop error]\n%s", traceback.format_exc())
            time_mod.sleep(30)

    trader._save_state()
    log.info("[shutdown] state saved, exiting")


def main() -> int:
    ap = argparse.ArgumentParser(description="EF3 FA1Mf paper trader")
    ap.add_argument("--once", action="store_true", help="one tick and exit")
    args = ap.parse_args()

    log.info("engine=%s capital=$%.2f", FA1MfEngine.VERSION, INITIAL_CAPITAL)
    _start_health_server()
    trader = PaperTrader()
    log.info("state=%s equity=$%.4f n_trades=%d",
             trader.engine.state, trader.equity, len(trader.engine.trades))

    if args.once:
        trader.tick()
        return 0
    run_live(trader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
