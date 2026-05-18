"""
EF3 F-B.2.A.f paper trader — main entry point.

Strategy: 20-bar H4 breakout + range compression <12% + SMA200 Daily
          + SL 1×ATR14 + TP 8:1 fixed.

Modes:
  live (default)   — wakes up at XX:02 UTC every 4 hours (H4 close trigger)
  --once           — one tick and exit (smoke test)
  --replay-days N  — replay last N days (resets state, stale entries enabled)
  --status         — print current state and exit

Health endpoint: GET /healthz on FB2_HEALTH_PORT (default 8099)
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
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd

from trading.ef3_fb2af_paper.config import (
    DATA_DIR, HEALTH_PORT, INITIAL_CAPITAL, LOG_DIR,
    STRATEGY_ID, SYMBOL,
)
from trading.ef3_fb2af_paper.engine import Fb2afEngine
from trading.ef3_fb2af_paper.feed import fetch_bars, get_current_price
from trading.ef3_fb2af_paper import notifier
from trading.ef3_fb2af_paper.persistence import (
    append_equity, append_trade, init_csvs, load_state, save_state,
)

# ── logging ────────────────────────────────────────────────────────────────────

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
log = logging.getLogger("ef3_fb2af_paper")

_startup_ts = datetime.now(timezone.utc)

# ── health server ──────────────────────────────────────────────────────────────

_health: dict = {
    "state":       "init",
    "equity":      INITIAL_CAPITAL,
    "n_trades":    0,
    "last_bar_ts": None,
    "price":       None,
}


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ("/healthz", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "status":      "ok",
            "worker":      "ef3_fb2af_paper",
            "strategy_id": STRATEGY_ID,
            "symbol":      SYMBOL,
            "uptime_sec":  int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
            **_health,
        }, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _start_health() -> None:
    try:
        srv = HTTPServer(("0.0.0.0", HEALTH_PORT), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("[health] listening on port %d", HEALTH_PORT)
    except Exception as e:
        log.warning("[health] could not start: %s", e)


# ── PaperTrader ────────────────────────────────────────────────────────────────

class PaperTrader:
    def __init__(self) -> None:
        self.engine = Fb2afEngine(initial_equity=INITIAL_CAPITAL)
        init_csvs()
        self._load()
        _health.update({
            "state":       self.engine.state_str,
            "equity":      round(self.engine.equity, 4),
            "n_trades":    self.engine.n_trades,
            "last_bar_ts": str(self.engine.last_bar_ts) if self.engine.last_bar_ts else None,
        })

    def _load(self) -> None:
        st = load_state()
        if not st:
            log.info("[state] no existing state — starting fresh (capital=$%.2f)", INITIAL_CAPITAL)
            return
        if st.get("strategy_id") != STRATEGY_ID or st.get("symbol") != SYMBOL:
            log.warning(
                "[state] ignoring incompatible state — found strategy=%s symbol=%s; expected %s %s",
                st.get("strategy_id"), st.get("symbol"), STRATEGY_ID, SYMBOL,
            )
            return
        try:
            self.engine.load_state_dict(st.get("engine", {}))
            log.info(
                "[state] loaded — state=%s equity=$%.2f n_trades=%d last_bar=%s",
                self.engine.state_str, self.engine.equity,
                self.engine.n_trades, self.engine.last_bar_ts,
            )
        except Exception:
            log.error("[state] load error:\n%s", traceback.format_exc())

    def _save(self) -> None:
        if self.engine.last_bar_ts is None:
            return
        save_state({
            "strategy_id": STRATEGY_ID,
            "symbol":      SYMBOL,
            "engine":      self.engine.to_state_dict(),
        })

    def tick(self, trade_stale_entries: bool = False) -> None:
        try:
            df = fetch_bars()
        except Exception as e:
            log.error("[tick] feed error: %s", e)
            notifier.notify_error("feed", str(e))
            return

        try:
            price = get_current_price()
        except Exception as e:
            log.error("[tick] price error: %s", e)
            price = None

        events = self.engine.replay_bars(df, trade_stale_entries=trade_stale_entries)
        ts_str = df.index[-1].isoformat()

        for ev in events:
            action = ev["action"]
            if action == "signal":
                notifier.notify_signal(
                    str(ev["ts"])[:19],
                    ev["pending_atr"],
                    self.engine.equity,
                )
            elif action == "open_long":
                notifier.notify_open(
                    str(ev["ts"])[:19],
                    ev["entry_px"],
                    ev["sl_price"],
                    ev["tp_price"],
                    ev["atr_signal"],
                    ev["equity"],
                )
            elif action == "close_long":
                tr = ev["trade"]
                append_trade(tr)
                notifier.notify_close(tr)

        append_equity(ts_str, self.engine.equity, self.engine.state_str, self.engine.n_trades)
        self._save()

        _health.update({
            "state":       self.engine.state_str,
            "equity":      round(self.engine.equity, 4),
            "n_trades":    self.engine.n_trades,
            "last_bar_ts": ts_str,
            "price":       price,
        })

        log.info(
            "[tick] bar=%s state=%s equity=$%.2f n_trades=%d",
            ts_str[:19], self.engine.state_str, self.engine.equity, self.engine.n_trades,
        )


# ── timing helpers ─────────────────────────────────────────────────────────────

def _seconds_until_next_h4_trigger() -> float:
    now = datetime.now(timezone.utc)
    h4_hours = {0, 4, 8, 12, 16, 20}
    next_close = now.replace(minute=2, second=0, microsecond=0)
    while next_close.hour not in h4_hours or next_close <= now:
        next_close += timedelta(hours=1)
        next_close = next_close.replace(minute=2, second=0, microsecond=0)
    return max(1.0, (next_close - now).total_seconds())


# ── live loop ──────────────────────────────────────────────────────────────────

_shutdown = False


def _on_signal(*_) -> None:
    global _shutdown
    _shutdown = True
    log.info("[shutdown] signal received")


def run_live(trader: PaperTrader) -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log.info("[live] started — triggers at XX:02 UTC on H4 closes (00/04/08/12/16/20 UTC)")
    notifier.notify_startup(trader.engine.equity)

    while not _shutdown:
        try:
            wait = _seconds_until_next_h4_trigger()
            log.info("[live] sleeping %.0fs until next H4 trigger", wait)
            deadline = time_mod.time() + wait
            while time_mod.time() < deadline and not _shutdown:
                time_mod.sleep(min(5.0, deadline - time_mod.time()))
            if _shutdown:
                break
            trader.tick()
        except KeyboardInterrupt:
            break
        except Exception:
            err = traceback.format_exc()
            log.error("[loop error]\n%s", err)
            notifier.notify_error("run_live", err)
            time_mod.sleep(60)

    trader._save()
    log.info("[shutdown] state saved")


# ── replay mode ────────────────────────────────────────────────────────────────

def run_replay(trader: PaperTrader, days: int) -> None:
    log.info("[replay] fetching bars for last %d days...", days)
    try:
        df = fetch_bars()
    except Exception as e:
        log.error("[replay] feed error: %s", e)
        return

    trader.engine = Fb2afEngine(initial_equity=INITIAL_CAPITAL)

    cutoff = df.index[-1] - pd.Timedelta(days=days)
    df_replay = df[df.index >= cutoff]

    events = trader.engine.replay_bars(df_replay, trade_stale_entries=True)

    wins  = sum(1 for ev in events if ev["action"] == "close_long" and ev["trade"]["pnl_usd"] > 0)
    total = sum(1 for ev in events if ev["action"] == "close_long")
    pnl   = trader.engine.equity - INITIAL_CAPITAL
    log.info(
        "[replay] done — %d trades | WR=%d/%d | equity=$%.2f (PnL=$%+.2f)",
        total, wins, total, trader.engine.equity, pnl,
    )
    for ev in events:
        if ev["action"] == "open_long":
            log.info("  OPEN  long | %s | entry=%.4f sl=%.4f tp=%.4f",
                     str(ev["ts"])[:19], ev["entry_px"], ev["sl_price"], ev["tp_price"])
        elif ev["action"] == "close_long":
            tr = ev["trade"]
            log.info("  CLOSE long | entry=%.4f exit=%.4f | type=%s | net=%+.2fbps | pnl=$%+.2f",
                     tr["entry_px"], tr["exit_px"], tr["exit_type"],
                     tr["net_ret_bps"], tr["pnl_usd"])


# ── entry point ────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="EF3 F-B.2.A.f paper trader")
    ap.add_argument("--once",        action="store_true", help="one tick and exit")
    ap.add_argument("--replay-days", type=int, default=0, metavar="N",
                    help="replay last N days (resets state)")
    ap.add_argument("--status",      action="store_true", help="print state and exit")
    args = ap.parse_args()

    log.info(
        "EF3 FB2Af v%s | strategy=%s symbol=%s capital=$%.0f",
        Fb2afEngine.VERSION, STRATEGY_ID, SYMBOL, INITIAL_CAPITAL,
    )

    _start_health()
    trader = PaperTrader()

    if args.status:
        e = trader.engine
        print(f"state:    {e.state_str}")
        print(f"equity:   ${e.equity:.2f}  (start ${INITIAL_CAPITAL:.2f})")
        print(f"n_trades: {e.n_trades}")
        print(f"last_bar: {e.last_bar_ts}")
        if e.position_side == 1 and e.position_entry_px:
            try:
                px = get_current_price()
                gross = (px - e.position_entry_px) / e.position_entry_px
                print(f"unrealised: {gross*100:+.3f}%  (mark={px:.4f})")
            except Exception:
                pass
        return 0

    if args.replay_days > 0:
        run_replay(trader, args.replay_days)
        return 0

    if args.once:
        trader.tick()
        return 0

    run_live(trader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
