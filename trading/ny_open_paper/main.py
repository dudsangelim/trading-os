"""
NY Open 2C paper trader — main entry point.

Modes:
  live (default)          — polls Binance 5m klines, runs FSM, logs events
  --dry-run-replay N      — fetches last N days of klines via REST, replays offline

Usage:
  python -m trading.ny_open_paper.main                   # live
  python -m trading.ny_open_paper.main --dry-run-replay 30
  python -m trading.ny_open_paper.main --threshold 0.65  # override threshold

The process exposes GET /healthz on HEALTH_PORT (default 8094) for the watcher.
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
import urllib.request
from datetime import datetime, timezone, time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import pandas as pd

from trading.ny_open_paper.config import (
    FALLBACK_MODEL_PATH, HEALTH_PORT, INITIAL_CAPITAL,
    LEGACY_MODEL_PATH, LOG_DIR, MODEL_PATH, POLL_OFFSET_SEC,
    TRADING_ALLOWED_USERS, TRADING_BOT_TOKEN,
)
from trading.ny_open_paper.engine import StrategyEngine
from trading.ny_open_paper.features import compute_context_live
from trading.ny_open_paper.feed import BinanceFeed
from trading.ny_open_paper.model import FrozenModel
from trading.ny_open_paper.persistence import append_row, init_csvs, load_state, save_state


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
log = logging.getLogger("ny_open_paper")

_startup_ts = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _telegram_send(text: str) -> None:
    """Non-blocking best-effort Telegram notification."""
    if not TRADING_BOT_TOKEN:
        return
    chat_id = TRADING_ALLOWED_USERS.split(",")[0].strip() if TRADING_ALLOWED_USERS else ""
    if not chat_id:
        return
    url = f"https://api.telegram.org/bot{TRADING_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
            if not resp.get("ok"):
                log.warning("[telegram] send failed: %s", resp)
    except Exception as exc:
        log.warning("[telegram] send error: %s", exc)


# ---------------------------------------------------------------------------
# Health server
# ---------------------------------------------------------------------------

_DEFAULT_LEVERAGE = 5.0  # fallback if model has no execution_notes.leverage_target

_health_state: dict = {"engine_state": "IDLE", "equity": INITIAL_CAPITAL,
                        "processed_trades": 0, "model_version": "?"}


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "status": "ok",
            "worker": "trading_ny_open_paper",
            "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
            **_health_state,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # suppress access log noise
        pass


def _start_health_server() -> None:
    try:
        srv = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        log.info("[health] server listening on port %d", HEALTH_PORT)
    except Exception as exc:
        log.warning("[health] could not start server: %s", exc)


# ---------------------------------------------------------------------------
# PaperTrader
# ---------------------------------------------------------------------------

SESSION_OPEN = dtime(13, 30)
SESSION_CLOSE = dtime(20, 0)


class PaperTrader:
    def __init__(self, model: FrozenModel, threshold: float, leverage: float):
        self.engine = StrategyEngine(model, threshold)
        self.threshold = threshold
        self.model = model
        self.leverage = leverage
        self.equity = INITIAL_CAPITAL
        self.peak_equity = INITIAL_CAPITAL   # for DD tracking
        self.last_ctx_date = None
        self.processed_trades = 0
        self._lock = threading.Lock()
        # DD thresholds from model execution_notes (defaults: alert 15%, kill 25%)
        _notes = model.d.get("execution_notes", {})
        self._dd_alert_pct = float(_notes.get("max_dd_alert_pct", 15))
        self._dd_kill_pct  = float(_notes.get("max_dd_kill_pct",  25))
        self._dd_alert_fired = False  # reset when DD recovers above alert threshold
        init_csvs()
        self._load_state()

    def _load_state(self) -> None:
        st = load_state()
        if not st:
            return
        self.equity          = float(st.get("equity",          INITIAL_CAPITAL))
        self.peak_equity     = float(st.get("peak_equity",     self.equity))
        self.processed_trades = int(st.get("processed_trades", 0))
        lctx = st.get("last_ctx_date")
        if lctx:
            try:
                from datetime import date as _date
                self.last_ctx_date = _date.fromisoformat(lctx)
            except Exception:
                pass
        log.info(
            "[state loaded] equity=$%.4f  peak=$%.4f  trades=%d",
            self.equity, self.peak_equity, self.processed_trades,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_dd(self, ts: pd.Timestamp) -> None:
        """Alert or kill if drawdown exceeds configured thresholds.

        Alert fires once when DD first crosses the threshold; resets if equity
        recovers above it.  Kill always fires (SIGTERM → clean shutdown).
        """
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        if self.peak_equity <= 0:
            return
        dd_pct = (self.equity - self.peak_equity) / self.peak_equity * 100

        if dd_pct <= -self._dd_kill_pct:
            msg = (
                f"🚨 <b>NY Open 2C — DD KILL {dd_pct:.1f}%</b>\n"
                f"Limite={self._dd_kill_pct:.0f}%  equity=${self.equity:.2f}  peak=${self.peak_equity:.2f}\n"
                f"Container será parado."
            )
            log.error("[DD-KILL] %.1f%% — parando container", dd_pct)
            _telegram_send(msg)
            os.kill(os.getpid(), signal.SIGTERM)
        elif dd_pct <= -self._dd_alert_pct:
            if not self._dd_alert_fired:
                msg = (
                    f"⚠️ <b>NY Open 2C — DD ALERTA {dd_pct:.1f}%</b>\n"
                    f"Limite alerta={self._dd_alert_pct:.0f}%  equity=${self.equity:.2f}  peak=${self.peak_equity:.2f}\n"
                    f"Monitorar. Kill automático em {self._dd_kill_pct:.0f}%."
                )
                log.warning("[DD-ALERT] %.1f%%", dd_pct)
                _telegram_send(msg)
                self._dd_alert_fired = True
        else:
            # recovered above alert threshold — re-arm for next DD episode
            self._dd_alert_fired = False

    def _persist_trade(self, tr: dict, ts: pd.Timestamp) -> None:
        """Write a closed trade to CSV, update equity, notify Telegram, and check DD."""
        pnl_fraction = tr["pnl_pct_net"] / 100.0 * self.leverage
        equity_before = self.equity
        self.equity *= (1 + pnl_fraction)
        self.processed_trades += 1

        append_row("trades.csv", {
            "ts_exit": ts.isoformat(),
            "date": str(tr["date"].date()),
            "direction": tr["direction"],
            "entry_price": tr["entry_price"],
            "exit_price": tr["exit_price"],
            "exit_reason": tr["exit_reason"],
            "pnl_pct_net": tr["pnl_pct_net"],
            "equity_before": equity_before,
            "equity_after": self.equity,
            "leverage": self.leverage,
        })

        msg = (
            f"[2C EXIT {tr['date'].date()}] reason={tr['exit_reason']} "
            f"pnl={tr['pnl_pct_net']:+.3f}%  "
            f"equity=${equity_before:.2f} → ${self.equity:.2f}"
        )
        log.info(msg)
        icon = "🟢" if tr["pnl_pct_net"] > 0 else "🔴"
        _telegram_send(
            f"{icon} <b>NY Open 2C — Exit</b>\n{msg}\n"
            f"entry={tr['entry_price']:.2f}  exit={tr['exit_price']:.2f}"
        )
        self._check_dd(ts)

        _health_state.update({
            "engine_state": self.engine.state,
            "equity": round(self.equity, 4),
            "processed_trades": self.processed_trades,
        })
        save_state({
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "last_ctx_date": str(self.last_ctx_date) if self.last_ctx_date else None,
            "processed_trades": self.processed_trades,
            "engine_state": self.engine.state,
            "engine_current_date": str(self.engine.current_date) if self.engine.current_date else None,
        })

    # ------------------------------------------------------------------
    # 5-minute candle handler (signal detection + position management)
    # ------------------------------------------------------------------

    def on_new_candle(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float) -> None:
        session_date = ts.date()
        t = ts.time()

        # compute contextual features (potentially slow HTTP call — outside lock)
        ctx = None
        if SESSION_OPEN <= t < SESSION_CLOSE and self.last_ctx_date != session_date:
            try:
                ctx = compute_context_live(session_date)
            except Exception:
                log.error("[ctx] failed to compute context:\n%s", traceback.format_exc())
                return

        with self._lock:
            if ctx is not None:
                self.engine.on_session_context(session_date, ctx)
                self.last_ctx_date = session_date

            decisions_before = len(self.engine.decisions)
            tp1_before = self.engine.pos.hit_tp1 if self.engine.pos else False
            trades_before = len(self.engine.trades)

            self.engine.on_candle_5m(ts, o, h, l, c)

            new_decisions = list(self.engine.decisions[decisions_before:])
            new_trades = list(self.engine.trades[trades_before:])
            tp1_now = self.engine.pos.hit_tp1 if self.engine.pos else False
            tp1_partial = self.engine.pos.partial_fill if self.engine.pos else None
            tp1_entry = self.engine.pos.entry_price if self.engine.pos else None
            engine_state = self.engine.state

        # --- notifications and I/O outside lock ---

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
                msg = (
                    f"[2C armed {d.get('date')}] dir={'SHORT' if d.get('direction') == -1 else 'LONG'} "
                    f"extreme={d.get('extreme'):.2f} range={d.get('range_first_pct'):.3f}%"
                )
                log.info(msg)
                _telegram_send(f"⚔️ <b>NY Open 2C — Armed</b>\n{msg}")
            elif decision == "break_p":
                p_win = d.get("p_win", 0)
                verdict = "ACCEPT" if p_win >= self.threshold else "REJECT"
                msg = (
                    f"[2C break {d.get('date')}] p_win={p_win:.3f} thr={self.threshold} "
                    f"{verdict}  os={d.get('os_close'):.3f}%  ttb={d.get('ttb'):.1f}min"
                )
                log.info(msg)
                icon = "✅" if verdict == "ACCEPT" else "❌"
                _telegram_send(f"{icon} <b>NY Open 2C — {'break_accept' if verdict == 'ACCEPT' else 'break_reject'}</b>\n{msg}")
            elif decision == "filled":
                msg = (
                    f"[2C FILLED {d.get('date')}] entry={d.get('entry'):.2f} "
                    f"equity=${self.equity:.2f}"
                )
                log.info(msg)
                _telegram_send(f"📌 <b>NY Open 2C — Filled</b>\n{msg}")
            elif decision == "retest_timeout":
                log.info("[2C retest_timeout %s] order cancelled", d.get("date"))

        # TP1 hit notification (only on the first transition — tick loop may have caught it first)
        if not tp1_before and tp1_now and tp1_partial is not None:
            msg = f"[2C TP1/BE] parcial em {tp1_partial:.2f} | stop→BE {tp1_entry:.2f}"
            log.info(msg)
            _telegram_send(f"⚡ <b>NY Open 2C — TP1 + BE armado</b>\n{msg}")

        for tr in new_trades:
            self._persist_trade(tr, ts)

        append_row("session_log.csv", {
            "ts": ts.isoformat(),
            "state": engine_state,
            "close": c,
            "equity": self.equity,
        })

        _health_state.update({
            "engine_state": engine_state,
            "equity": round(self.equity, 4),
            "processed_trades": self.processed_trades,
            "model_version": self.model.model_version,
        })
        save_state({
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "last_ctx_date": str(self.last_ctx_date) if self.last_ctx_date else None,
            "processed_trades": self.processed_trades,
            "engine_state": engine_state,
            "engine_current_date": str(self.engine.current_date) if self.engine.current_date else None,
        })

    # ------------------------------------------------------------------
    # Tick handler (position management only)
    # ------------------------------------------------------------------

    def on_tick(self, ts: pd.Timestamp, price: float) -> None:
        """Called by the tick loop for each price sample when IN_POSITION."""
        with self._lock:
            if self.engine.state != "IN_POSITION" or self.engine.pos is None:
                return

            tp1_before = self.engine.pos.hit_tp1
            trades_before = len(self.engine.trades)

            self.engine.on_tick(price, ts)

            tp1_now = self.engine.pos.hit_tp1 if self.engine.pos else False
            tp1_partial = self.engine.pos.partial_fill if self.engine.pos else None
            tp1_entry = self.engine.pos.entry_price if self.engine.pos else None
            new_trades = list(self.engine.trades[trades_before:])

        if not tp1_before and tp1_now and tp1_partial is not None:
            msg = f"[2C TP1/BE] parcial em {tp1_partial:.2f} | stop→BE {tp1_entry:.2f}"
            log.info(msg)
            _telegram_send(f"⚡ <b>NY Open 2C — TP1 + BE armado</b>\n{msg}")

        for tr in new_trades:
            self._persist_trade(tr, ts)


# ---------------------------------------------------------------------------
# Tick loop (position management at ~250 ms resolution)
# ---------------------------------------------------------------------------

_TICK_INTERVAL_IN_POSITION = 0.25   # seconds between price polls when IN_POSITION
_TICK_INTERVAL_IDLE = 1.0           # seconds when not in position


def _run_tick_loop(trader: PaperTrader, stop_event: threading.Event) -> None:
    """Poll Binance ticker and feed ticks to the engine while IN_POSITION."""
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
# Scheduling helpers
# ---------------------------------------------------------------------------

def _seconds_until_next_5m_boundary(offset_sec: int = POLL_OFFSET_SEC) -> float:
    """Returns seconds until the next 5-minute UTC epoch boundary + offset."""
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

    # start tick loop thread
    _tick_stop = threading.Event()
    tick_thread = threading.Thread(
        target=_run_tick_loop, args=(trader, _tick_stop),
        daemon=True, name="tick-loop",
    )
    tick_thread.start()

    log.info("Paper trade live started. Waiting for next 5m candle close...")
    _telegram_send("🚀 <b>NY Open 2C paper trader started</b>\nlive mode — BTCUSDT 5m")

    last_processed_ts: Optional[pd.Timestamp] = None

    while not _shutdown:
        try:
            wait = _seconds_until_next_5m_boundary()
            log.info("[sleep] %.1fs until next candle...", wait)
            # sleep in 1s chunks to remain responsive to shutdown signal
            deadline = time_mod.time() + wait
            while time_mod.time() < deadline and not _shutdown:
                time_mod.sleep(min(1.0, deadline - time_mod.time()))

            if _shutdown:
                break

            kline = BinanceFeed.last_closed_5m()
            ts = pd.Timestamp(kline["open_time"]).tz_convert("UTC").tz_localize(None)

            if last_processed_ts is not None and ts <= last_processed_ts:
                log.warning("[dup] candle %s already processed, skipping", ts)
                time_mod.sleep(20)
                continue

            trader.on_new_candle(
                ts,
                float(kline["open"]), float(kline["high"]),
                float(kline["low"]), float(kline["close"]),
            )
            last_processed_ts = ts

        except KeyboardInterrupt:
            break
        except Exception:
            log.error("[loop error]\n%s", traceback.format_exc())
            time_mod.sleep(30)

    _tick_stop.set()
    tick_thread.join(timeout=5)
    log.info("[shutdown] saving state and exiting")
    _telegram_send("🛑 <b>NY Open 2C paper trader stopped</b>")


# ---------------------------------------------------------------------------
# Replay (--dry-run-replay N)
# ---------------------------------------------------------------------------

def run_replay(trader: PaperTrader, days: int) -> None:
    """Fetch last N days of 5m klines from Binance and replay through the engine."""
    log.info("[replay] fetching last %d days of 5m klines...", days)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    all_chunks = []

    while True:
        chunk = BinanceFeed.klines("5m", limit=1500, end_time_ms=end_ms)
        if len(chunk) == 0:
            break
        all_chunks.append(chunk)
        earliest = chunk["open_time"].min()
        span_days = (pd.Timestamp.now(tz="UTC") - earliest).days
        if span_days >= days:
            break
        end_ms = int(earliest.timestamp() * 1000) - 1

    df = (
        pd.concat(all_chunks, ignore_index=True)
        .drop_duplicates("open_time")
        .sort_values("open_time")
    )
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    df = df[df["open_time"] >= cutoff].reset_index(drop=True)
    log.info("[replay] %d candles in range", len(df))

    for _, row in df.iterrows():
        ts = pd.Timestamp(row["open_time"]).tz_convert("UTC").tz_localize(None)
        trader.on_new_candle(ts, row["open"], row["high"], row["low"], row["close"])

    log.info(
        "[replay] done — trades=%d  equity=$%.2f",
        trader.processed_trades, trader.equity,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="NY Open 2C paper trader")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override model threshold (default: model's recommended_threshold)")
    ap.add_argument("--leverage", type=float, default=None,
                    help="override leverage (default: execution_notes.leverage_target or 5.0)")
    ap.add_argument("--model", type=str, default=None,
                    help="path to frozen model JSON (default: v2, fallback v1)")
    ap.add_argument("--dry-run-replay", type=int, default=0, metavar="DAYS",
                    help="replay last N days from Binance REST instead of live polling")
    args = ap.parse_args()

    if args.model:
        model_path = Path(args.model)
    elif MODEL_PATH.exists():
        model_path = MODEL_PATH
    elif FALLBACK_MODEL_PATH.exists():
        log.warning("primary model not found at %s, falling back to %s", MODEL_PATH, FALLBACK_MODEL_PATH)
        model_path = FALLBACK_MODEL_PATH
    elif LEGACY_MODEL_PATH.exists():
        log.warning("fallback model not found at %s, falling back to %s", FALLBACK_MODEL_PATH, LEGACY_MODEL_PATH)
        model_path = LEGACY_MODEL_PATH
    else:
        log.error("No model found at %s, %s or %s", MODEL_PATH, FALLBACK_MODEL_PATH, LEGACY_MODEL_PATH)
        return 1

    model = FrozenModel(model_path)
    thr = args.threshold if args.threshold is not None else model.threshold

    if args.leverage is not None:
        leverage = float(args.leverage)
    else:
        leverage = float(model.d.get("execution_notes", {}).get("leverage_target", _DEFAULT_LEVERAGE))

    stop_kind = model.strat.get("stop_kind", "fixed")
    if stop_kind == "alpha":
        stop_desc = (
            f"stop_kind=alpha  alpha={model.strat['stop_alpha']}  "
            f"clamp=[{model.strat['stop_min_pct']*100:.2f}%, {model.strat['stop_max_pct']*100:.2f}%]"
        )
    else:
        stop_desc = f"stop_kind=fixed  stop_pct={model.strat['stop_pct']:.4f}"

    log.info(
        "modelo=%s (%s)  treino=%s",
        model.model_version, model_path.name, model.d.get("train_date_utc", "?"),
    )
    log.info(
        "threshold=%.2f  leverage=%.1fx  capital=$%.2f  n_features=%d",
        thr, leverage, INITIAL_CAPITAL, model.n_features,
    )
    log.info(
        "session=%s–%s UTC  break_buffer=%.4f  %s  max_retest_min=%d",
        model.strat["session_open_utc"], model.strat["session_close_utc"],
        model.strat["break_buffer"], stop_desc,
        model.strat["max_wait_retest_min"],
    )
    if leverage >= 3:
        log.warning(
            "[ALERTA] leverage %.1fx — slippage em fills LIMIT pode ser material. "
            "Monitorar fill quality nos primeiros dias.", leverage,
        )

    _health_state.update({
        "model_version": model.model_version,
        "equity": INITIAL_CAPITAL,
        "processed_trades": 0,
        "engine_state": "IDLE",
    })

    _start_health_server()

    trader = PaperTrader(model, thr, leverage)

    if args.dry_run_replay > 0:
        run_replay(trader, args.dry_run_replay)
    else:
        run_live(trader)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
