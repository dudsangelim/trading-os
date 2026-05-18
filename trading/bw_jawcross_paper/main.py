"""
Alligator BTC 6h paper trader — main entry point.

Modes:
  live (default)   — wakes up at XX:02 UTC every hour, processes the latest closed 6h bar
  --once           — one tick and exit (smoke test)
  --replay-days N  — replay last N days of closed bars (using Binance historical klines)
  --status         — print current state and exit

Health endpoint: GET /healthz on BW_HEALTH_PORT (default 8097)
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

from trading.bw_jawcross_paper.config import (
    DATA_DIR, FEE_RT, HEALTH_PORT, INITIAL_CAPITAL, LEVERAGE, LOG_DIR,
    LOOKBACK_BARS, SLIPPAGE_PCT, STAKE_PCT, STRATEGY_ID, SYMBOL, TIMEFRAME,
)
from trading.bw_jawcross_paper.engine import BwJawCrossEngine, add_indicators
from trading.bw_jawcross_paper.feed import fetch_closed_bars, get_current_price
from trading.bw_jawcross_paper import notifier
from trading.bw_jawcross_paper.persistence import (
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
log = logging.getLogger("bw_jawcross_paper")

_startup_ts = datetime.now(timezone.utc)

# ── health server ──────────────────────────────────────────────────────────────

_health: dict = {
    "state": "init",
    "equity": INITIAL_CAPITAL,
    "n_trades": 0,
    "last_bar_ts": None,
    "price": None,
}


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ("/healthz", "/health"):
            self.send_response(404); self.end_headers(); return
        body = json.dumps({
            "status": "ok",
            "worker": "bw_jawcross_paper",
            "strategy_id": STRATEGY_ID,
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "leverage": LEVERAGE,
            "stake_pct": STAKE_PCT,
            "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
            **_health,
        }, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_): pass


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
        self.engine = BwJawCrossEngine(initial_equity=INITIAL_CAPITAL)
        init_csvs()
        self._load()
        _health.update({
            "state":    self.engine.state_str,
            "equity":   round(self.engine.equity, 4),
            "n_trades": self.engine.n_trades,
            "last_bar_ts": str(self.engine.last_bar_ts) if self.engine.last_bar_ts else None,
        })

    def _load(self) -> None:
        st = load_state()
        if not st:
            log.info("[state] no existing state — starting fresh (capital=$%.2f)", INITIAL_CAPITAL)
            return
        if st.get("strategy_id") != STRATEGY_ID or st.get("symbol") != SYMBOL or st.get("timeframe") != TIMEFRAME:
            log.warning(
                "[state] ignoring incompatible state — found strategy=%s symbol=%s tf=%s; expected strategy=%s symbol=%s tf=%s",
                st.get("strategy_id"), st.get("symbol"), st.get("timeframe"),
                STRATEGY_ID, SYMBOL, TIMEFRAME,
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
            return  # no bars processed — don't overwrite existing state on clean shutdown
        save_state({
            "strategy_id": STRATEGY_ID,
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "engine": self.engine.to_state_dict(),
        })

    def tick(self) -> None:
        """Fetch latest bars, run engine, persist, notify."""
        try:
            df_raw = fetch_closed_bars(limit=LOOKBACK_BARS + 1)
            df     = add_indicators(df_raw)
        except Exception as e:
            log.error("[tick] feed error: %s", e)
            notifier.notify_error("feed", str(e))
            return

        try:
            price = get_current_price()
        except Exception as e:
            log.error("[tick] price error: %s", e)
            notifier.notify_error("price", str(e))
            return

        execution_ts = datetime.now(timezone.utc)
        events = self.engine.replay_bars(
            df,
            execution_price=price,
            execution_ts=pd.Timestamp(execution_ts),
        )
        ts_str = df.index[-1].isoformat()

        for ev in events:
            action = ev["action"]
            if action in ("open_long", "open_short"):
                notifier.notify_open(
                    action, str(ev["ts"])[:19],
                    ev["entry_px"], ev["notional"],
                    ev["jaw"], ev["lips"], ev["equity"],
                )
            elif action in ("close_long", "close_short"):
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

def _seconds_until_next_trigger() -> float:
    """Return seconds until 2 minutes past the next UTC hour close."""
    now  = datetime.now(timezone.utc)
    next_hour = (now + timedelta(hours=1)).replace(minute=2, second=0, microsecond=0)
    # if already past XX:02 of current hour, wait for next hour's XX:02
    current_trigger = now.replace(minute=2, second=0, microsecond=0)
    if now < current_trigger:
        return max(1.0, (current_trigger - now).total_seconds())
    return max(1.0, (next_hour - now).total_seconds())


# ── live loop ──────────────────────────────────────────────────────────────────

_shutdown = False


def _on_signal(*_) -> None:
    global _shutdown
    _shutdown = True
    log.info("[shutdown] signal received")


def run_live(trader: PaperTrader) -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log.info("[live] started — triggers at XX:02 UTC every hour")
    notifier.notify_startup(trader.engine.equity)

    while not _shutdown:
        try:
            wait = _seconds_until_next_trigger()
            log.info("[live] sleeping %.0fs until next trigger", wait)
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
    """Replay last N days using Binance historical klines."""
    limit = min(days * 24 + 5, 1500)
    log.info("[replay] fetching last %d bars (≈%d days)...", limit, days)
    try:
        df_raw = fetch_closed_bars(limit=limit)
        df     = add_indicators(df_raw)
    except Exception as e:
        log.error("[replay] feed error: %s", e)
        return

    # reset state for a clean replay
    trader.engine = BwJawCrossEngine(initial_equity=INITIAL_CAPITAL)
    events = []
    for ts, row in df.iterrows():
        # Historical replay has no tick-level executable price. Use the bar
        # close as an explicit close-fill approximation rather than the old
        # optimistic Lips fill.
        evs = trader.engine.replay_bars(
            df.loc[[ts]],
            execution_price=float(row["close"]),
            execution_ts=ts,
            trade_stale_entries=True,
        )
        events.extend(evs)

    wins  = sum(1 for ev in events if "close" in ev.get("action","")
                and ev["trade"]["pnl_usd"] > 0)
    total = sum(1 for ev in events if "close" in ev.get("action",""))
    pnl   = trader.engine.equity - INITIAL_CAPITAL
    log.info(
        "[replay] done — %d trades | WR=%d/%d | equity=$%.2f (PnL=$%+.2f)",
        total, wins, total, trader.engine.equity, pnl,
    )
    for ev in events:
        action = ev.get("action", "")
        if "open" in action:
            log.info("  OPEN  %s | %s | entry=%.4f",
                     action, str(ev["ts"])[:19], ev["entry_px"])
        elif "close" in action:
            tr = ev["trade"]
            log.info("  CLOSE %s | entry=%.4f → exit=%.4f | net=%+.3f%% | pnl=$%+.2f",
                     tr["side"], tr["entry_px"], tr["exit_px"],
                     tr["net_ret_pct"], tr["pnl_usd"])


# ── entry point ────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="BW Jaw-Cross V3_1st paper trader")
    ap.add_argument("--once",         action="store_true", help="one tick and exit")
    ap.add_argument("--replay-days",  type=int, default=0, metavar="N",
                    help="replay last N days (resets state)")
    ap.add_argument("--status",       action="store_true", help="print state and exit")
    args = ap.parse_args()

    log.info(
        "Alligator v%s | strategy=%s symbol=%s tf=%s lev=%.0fx stake=%.0f%% "
        "capital=$%.0f fee=%.2f%%RT slip=%.1fbps/side",
        BwJawCrossEngine.VERSION, STRATEGY_ID, SYMBOL, TIMEFRAME, LEVERAGE,
        STAKE_PCT * 100, INITIAL_CAPITAL,
        FEE_RT * 100, SLIPPAGE_PCT * 10_000,
    )

    _start_health()
    trader = PaperTrader()

    if args.status:
        e = trader.engine
        print(f"state:    {e.state_str}")
        print(f"equity:   ${e.equity:.2f}  (start ${INITIAL_CAPITAL:.2f})")
        print(f"n_trades: {e.n_trades}")
        print(f"last_bar: {e.last_bar_ts}")
        if e.position_side != 0:
            try:
                px = get_current_price()
                side = e.position_side
                gross = side * (px - e.position_entry_px) / e.position_entry_px
                notional = e.equity * STAKE_PCT * LEVERAGE
                print(f"unrealised: {gross*100:+.3f}%  ${gross*notional:+.2f}  (mark={px:.4f})")
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
