"""
NY Open Momentum Long paper trader — Phase 0.

Strategy: BTCUSDT 5m. Upside break of 30m NY open range with strong close
(os_close >= 0.10%). Entry LONG at range high on retest. Phase 0 WR gate.

Modes:
  live (default)      — polls Binance 5m klines, runs FSM
  --replay-days N     — fetches last N days of klines, replays offline
  --once              — process one candle and exit (smoke test)
  --status            — print state summary and exit

Health: GET /healthz on NYOM_HEALTH_PORT (default 8104)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time as time_mod
import traceback
from datetime import datetime, timezone, time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import pandas as pd

from trading.ny_open_mom_paper.config import (
    DATA_DIR, HEALTH_PORT, INITIAL_CAPITAL, LEVERAGE, LOG_DIR,
    OS_THR_PCT, PHASE0_MIN_TRADES, PHASE0_WR_FLOOR, POLL_OFFSET_SEC,
    SESSION_CLOSE_UTC, SESSION_OPEN_UTC, SL_MULT, STRATEGY_ID, SYMBOL, TP_MULT,
)
from trading.ny_open_mom_paper.engine import StrategyEngine
from trading.ny_open_mom_paper.feed import BinanceFeed
from trading.ny_open_mom_paper import notifier
from trading.ny_open_mom_paper.persistence import (
    append_row, init_csvs, load_state, save_state,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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
log = logging.getLogger("ny_open_mom_paper")

_startup_ts = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Health server
# ---------------------------------------------------------------------------

_health_state: dict = {
    "engine_state": "IDLE",
    "equity": INITIAL_CAPITAL,
    "total_trades": 0,
    "total_wins": 0,
    "strategy_id": STRATEGY_ID,
}


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "status": "ok",
            "worker": "trading_ny_open_mom_paper",
            "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
            **_health_state,
        }).encode()
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
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        log.info("[health] listening on port %d", HEALTH_PORT)
    except Exception as exc:
        log.warning("[health] could not start server: %s", exc)


# ---------------------------------------------------------------------------
# PaperTrader
# ---------------------------------------------------------------------------

class PaperTrader:
    def __init__(self):
        self.engine = StrategyEngine()
        self.equity = INITIAL_CAPITAL
        self.peak_equity = INITIAL_CAPITAL
        self.last_processed_candle_ts: Optional[str] = None
        self._lock = threading.Lock()
        init_csvs()
        self._load_state()
        self._update_health_state()

    def _load_state(self) -> None:
        st = load_state()
        if not st:
            return
        self.equity = float(st.get("equity", INITIAL_CAPITAL))
        self.peak_equity = float(st.get("peak_equity", self.equity))
        total_trades = int(st.get("total_trades", 0))
        total_wins = int(st.get("total_wins", 0))
        halted = bool(st.get("halted", False))
        self.engine.restore_phase0(total_trades, total_wins, halted)
        self.engine.load_runtime_state(st.get("runtime", {}))
        self.last_processed_candle_ts = st.get("last_processed_candle_ts")
        log.info(
            "[state loaded] equity=$%.4f  peak=$%.4f  trades=%d  wins=%d  halted=%s",
            self.equity, self.peak_equity, total_trades, total_wins, halted,
        )

    def _update_health_state(self) -> None:
        _health_state.update({
            "engine_state": self.engine.state,
            "equity": round(self.equity, 4),
            "total_trades": self.engine.total_trades,
            "total_wins": self.engine.total_wins,
        })

    def _save_state(self) -> None:
        save_state({
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "total_trades": self.engine.total_trades,
            "total_wins": self.engine.total_wins,
            "halted": self.engine._halted,
            "engine_state": self.engine.state,
            "runtime": self.engine.to_runtime_state(),
            "last_processed_candle_ts": getattr(self, "last_processed_candle_ts", None),
        })
        self._update_health_state()

    # ------------------------------------------------------------------
    # 5-minute bar handler
    # ------------------------------------------------------------------

    def on_new_candle(self, ts: pd.Timestamp, o: float, h: float,
                      l: float, c: float) -> None:
        with self._lock:
            trades_before = len(self.engine.trades)
            decisions_before = len(self.engine.decisions)
            tp1_before = self.engine.pos.hit_tp1 if self.engine.pos else False

            self.engine.on_candle_5m(ts, o, h, l, c)

            new_decisions = list(self.engine.decisions[decisions_before:])
            new_trades = list(self.engine.trades[trades_before:])
            tp1_now = self.engine.pos.hit_tp1 if self.engine.pos else False
            pos_entry = self.engine.pos.entry_price if self.engine.pos else None
            pos_tp1 = self.engine.pos.tp1 if self.engine.pos else None
            engine_state = self.engine.state

        # --- I/O outside lock ---

        for d in new_decisions:
            append_row("decisions.csv", {
                "ts": ts.isoformat(),
                "date": str(d.get("date")),
                "decision": d.get("decision"),
                "details": json.dumps(
                    {k: v for k, v in d.items() if k not in ("date", "decision")},
                    default=str,
                ),
            })
            decision = d.get("decision")
            if decision == "armed":
                log.info("[NYOM armed %s] high=%.2f low=%.2f range=%.3f%% break=%.2f",
                         d["date"], d["high_first"], d["low_first"],
                         d["range_pct"], d["break_level"])
                notifier.notify_armed(
                    d["date"], d["high_first"], d["low_first"],
                    d["range_pct"], d["break_level"],
                    d["tp1"], d["tp2"], d["stop_price"],
                )
            elif decision == "break_detected":
                verdict = "ACCEPT" if d["accept"] else "REJECT"
                log.info("[NYOM break %s] os_close=%.3f%% thr=%.2f%% ttb=%.1fmin %s",
                         d["date"], d["os_close"], d["os_thr"], d["ttb_min"], verdict)
                notifier.notify_break(d["date"], d["os_close"], d["os_thr"],
                                      d["ttb_min"], d["accept"])
            elif decision == "filled":
                log.info("[NYOM FILLED %s] entry=%.2f stop=%.2f tp1=%.2f tp2=%.2f equity=$%.2f",
                         d["date"], d["entry"], d["stop"], d["tp1"], d["tp2"], self.equity)
                notifier.notify_filled(d["date"], d["entry"], d["stop"],
                                       d["tp1"], d["tp2"], self.equity)
            elif decision == "retest_timeout":
                log.info("[NYOM retest_timeout %s] order cancelled", d.get("date"))

        # TP1 notification (first transition only — tick loop may have caught it)
        if not tp1_before and tp1_now and pos_entry is not None and pos_tp1 is not None:
            log.info("[NYOM TP1] partial @ %.2f | stop→BE %.2f", pos_tp1, pos_entry)
            notifier.notify_tp1(pos_entry, pos_tp1)

        for tr in new_trades:
            self._persist_trade(tr, ts)

        # halted transition
        if engine_state == "HALTED" and not _health_state.get("_halt_notified"):
            _health_state["_halt_notified"] = True
            notifier.notify_halted(self.engine.total_trades, self.engine.total_wins)

        append_row("equity.csv", {
            "ts": ts.isoformat(),
            "state": engine_state,
            "close": c,
            "equity": self.equity,
        })
        self.last_processed_candle_ts = ts.isoformat()
        self._save_state()

    # ------------------------------------------------------------------
    # Tick handler
    # ------------------------------------------------------------------

    def on_tick(self, ts: pd.Timestamp, price: float) -> None:
        with self._lock:
            if self.engine.state != "IN_POSITION" or self.engine.pos is None:
                return
            tp1_before = self.engine.pos.hit_tp1
            pos_entry = self.engine.pos.entry_price
            pos_tp1 = self.engine.pos.tp1
            trades_before = len(self.engine.trades)

            self.engine.on_tick(price, ts)

            tp1_now = self.engine.pos.hit_tp1 if self.engine.pos else False
            new_trades = list(self.engine.trades[trades_before:])

        if not tp1_before and tp1_now:
            log.info("[NYOM TP1/tick] partial @ %.2f | stop→BE %.2f", pos_tp1, pos_entry)
            notifier.notify_tp1(pos_entry, pos_tp1)

        for tr in new_trades:
            self._persist_trade(tr, ts)

    # ------------------------------------------------------------------
    # Trade persistence
    # ------------------------------------------------------------------

    def _persist_trade(self, tr: dict, ts: pd.Timestamp) -> None:
        pnl_fraction = tr["pnl_pct_net"] / 100.0 * LEVERAGE
        eq_before = self.equity
        self.equity *= (1 + pnl_fraction)
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        append_row("trades.csv", {
            "ts_exit": ts.isoformat(),
            "date": str(tr["date"].date()),
            "entry_price": tr["entry_price"],
            "exit_price": tr["exit_price"],
            "exit_reason": tr["exit_reason"],
            "pnl_pct_net": tr["pnl_pct_net"],
            "equity_before": eq_before,
            "equity_after": self.equity,
            "total_trades": tr["total_trades"],
            "total_wins": tr["total_wins"],
        })

        log.info(
            "[NYOM EXIT %s] reason=%s pnl=%+.3f%% equity=$%.2f→$%.2f",
            tr["date"].date(), tr["exit_reason"], tr["pnl_pct_net"],
            eq_before, self.equity,
        )
        notifier.notify_close(
            tr["date"].date(), tr["entry_price"], tr["exit_price"],
            tr["exit_reason"], tr["pnl_pct_net"], eq_before, self.equity,
            tr["total_trades"], tr["total_wins"],
        )
        self._save_state()


# ---------------------------------------------------------------------------
# Tick loop
# ---------------------------------------------------------------------------

_TICK_INTERVAL_IN_POSITION = 0.25
_TICK_INTERVAL_IDLE = 1.0


def _run_tick_loop(trader: PaperTrader, stop_event: threading.Event) -> None:
    log.info("[tick] loop started")
    while not stop_event.is_set():
        try:
            if trader.engine.state == "IN_POSITION":
                price = BinanceFeed.ticker_price()
                ts = pd.Timestamp.utcnow()
                trader.on_tick(ts, price)
                stop_event.wait(_TICK_INTERVAL_IN_POSITION)
            else:
                stop_event.wait(_TICK_INTERVAL_IDLE)
        except Exception:
            log.error("[tick] error:\n%s", traceback.format_exc())
            stop_event.wait(5.0)
    log.info("[tick] loop stopped")


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def _seconds_until_next_5m_boundary(offset_sec: int = POLL_OFFSET_SEC) -> float:
    now_epoch = datetime.now(timezone.utc).timestamp()
    next_boundary = (now_epoch // 300 + 1) * 300 + offset_sec
    return max(next_boundary - now_epoch, 1.0)


# ---------------------------------------------------------------------------
# Live loop
# ---------------------------------------------------------------------------

_shutdown = False


def _on_signal(*_):
    global _shutdown
    _shutdown = True
    log.info("[shutdown] signal received")


def run_live(trader: PaperTrader) -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    _tick_stop = threading.Event()
    tick_thread = threading.Thread(
        target=_run_tick_loop, args=(trader, _tick_stop),
        daemon=True, name="tick-loop",
    )
    tick_thread.start()

    log.info("[NYOM] live mode started — waiting for next 5m candle...")
    notifier.notify_startup(trader.equity, trader.engine.state)

    last_ts: Optional[pd.Timestamp] = (
        pd.Timestamp(trader.last_processed_candle_ts)
        if getattr(trader, "last_processed_candle_ts", None) else None
    )

    while not _shutdown:
        try:
            wait = _seconds_until_next_5m_boundary()
            log.info("[sleep] %.1fs until next candle...", wait)
            deadline = time_mod.time() + wait
            while time_mod.time() < deadline and not _shutdown:
                sleep_for = deadline - time_mod.time()
                if sleep_for <= 0:
                    break
                time_mod.sleep(min(1.0, sleep_for))

            if _shutdown:
                break

            kline = BinanceFeed.last_closed_5m()
            ts = pd.Timestamp(kline["open_time"]).tz_convert("UTC").tz_localize(None)

            if last_ts is not None and ts <= last_ts:
                log.warning("[dup] %s already processed, skipping", ts)
                time_mod.sleep(20)
                continue

            trader.on_new_candle(
                ts,
                float(kline["open"]), float(kline["high"]),
                float(kline["low"]), float(kline["close"]),
            )
            last_ts = ts

        except KeyboardInterrupt:
            break
        except Exception:
            log.error("[loop error]\n%s", traceback.format_exc())
            time_mod.sleep(30)

    _tick_stop.set()
    tick_thread.join(timeout=5)
    log.info("[shutdown] done")
    notifier.notify_shutdown()


# ---------------------------------------------------------------------------
# Replay (--replay-days N)
# ---------------------------------------------------------------------------

def run_replay(days: int) -> None:
    log.info("[replay] fetching last %d days of 5m klines...", days)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    chunks = []

    while True:
        chunk = BinanceFeed.klines("5m", limit=1500, end_time_ms=end_ms)
        if chunk.empty:
            break
        chunks.append(chunk)
        earliest = chunk["open_time"].min()
        span_days = (pd.Timestamp.now(tz="UTC") - earliest).days
        if span_days >= days:
            break
        end_ms = int(earliest.timestamp() * 1000) - 1

    if not chunks:
        log.warning("[replay] no candles returned")
        return

    df = (
        pd.concat(chunks, ignore_index=True)
        .drop_duplicates("open_time")
        .sort_values("open_time")
    )
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    df = df[df["open_time"] >= cutoff].reset_index(drop=True)
    log.info("[replay] %d candles", len(df))

    engine = StrategyEngine()

    for _, row in df.iterrows():
        ts = pd.Timestamp(row["open_time"]).tz_convert("UTC").tz_localize(None)
        engine.on_candle_5m(ts, row["open"], row["high"], row["low"], row["close"])

    equity = INITIAL_CAPITAL
    for tr in engine.trades:
        equity *= (1 + (tr["pnl_pct_net"] / 100.0 * LEVERAGE))

    wr = (engine.total_wins / engine.total_trades * 100
          if engine.total_trades > 0 else 0)
    log.info(
        "[replay] done — trades=%d  wins=%d  WR=%.1f%%  equity=$%.2f",
        engine.total_trades, engine.total_wins, wr, equity,
    )


# ---------------------------------------------------------------------------
# Status (--status)
# ---------------------------------------------------------------------------

def run_status() -> None:
    st = load_state()
    if not st:
        print("No state.json found.")
        return

    trades_path = DATA_DIR / "trades.csv"
    if trades_path.exists():
        import pandas as _pd
        df = _pd.read_csv(trades_path)
        n = len(df)
        wins = int((df["pnl_pct_net"] > 0).sum()) if n > 0 else 0
        wr = wins / n * 100 if n > 0 else 0
        last = df.iloc[-1].to_dict() if n > 0 else {}
    else:
        n, wins, wr, last = 0, 0, 0.0, {}

    print(
        f"Strategy : {STRATEGY_ID}\n"
        f"Symbol   : {SYMBOL}\n"
        f"State    : {st.get('engine_state', '?')}\n"
        f"Equity   : ${st.get('equity', INITIAL_CAPITAL):.4f}\n"
        f"Peak     : ${st.get('peak_equity', INITIAL_CAPITAL):.4f}\n"
        f"Halted   : {st.get('halted', False)}\n"
        f"Trades   : {n} (wins={wins}, WR={wr:.1f}%)\n"
        f"Phase0   : min_trades={PHASE0_MIN_TRADES}, wr_floor={PHASE0_WR_FLOOR*100:.0f}%\n"
        f"Last trade: {last.get('ts_exit','—')} reason={last.get('exit_reason','—')} "
        f"pnl={last.get('pnl_pct_net',0):+.3f}%"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="NY Open Momentum Long paper trader")
    ap.add_argument("--replay-days", type=int, default=0, metavar="DAYS",
                    help="replay last N days from Binance REST")
    ap.add_argument("--once", action="store_true",
                    help="process one candle and exit (smoke test)")
    ap.add_argument("--status", action="store_true",
                    help="print state summary and exit")
    args = ap.parse_args()

    if args.status:
        run_status()
        return 0

    log.info(
        "[NYOM] strategy=%s  symbol=%s  capital=$%.2f  leverage=%.1fx",
        STRATEGY_ID, SYMBOL, INITIAL_CAPITAL, LEVERAGE,
    )
    log.info(
        "[NYOM] session=%s–%s UTC  os_thr=%.2f%%  sl_mult=%.1f  tp_mult=%.1f",
        SESSION_OPEN_UTC, SESSION_CLOSE_UTC, OS_THR_PCT, SL_MULT, TP_MULT,
    )
    log.info(
        "[NYOM] phase0: halt if WR<%.0f%% after %d trades",
        PHASE0_WR_FLOOR * 100, PHASE0_MIN_TRADES,
    )

    if args.replay_days > 0:
        run_replay(args.replay_days)
        return 0

    _start_health_server()
    trader = PaperTrader()

    if args.once:
        kline = BinanceFeed.last_closed_5m()
        ts = pd.Timestamp(kline["open_time"]).tz_convert("UTC").tz_localize(None)
        log.info("[once] processing candle %s  o=%.2f h=%.2f l=%.2f c=%.2f",
                 ts, kline["open"], kline["high"], kline["low"], kline["close"])
        trader.on_new_candle(
            ts,
            float(kline["open"]), float(kline["high"]),
            float(kline["low"]), float(kline["close"]),
        )
        log.info("[once] engine_state=%s  equity=$%.4f", trader.engine.state, trader.equity)
        return 0

    run_live(trader)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
