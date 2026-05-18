"""R012 SOL Extreme Burst Reversal paper trader — main loop.

Strategy: SOLUSDT 5m. Signal |ret_5m|>2% → fade.
Entry: open of first 1m candle in t = sig_ts + 5min (next 5m bar).
TP=0.5%, SL=0.25%, Timeout=30min. Phase 0 gate: WR<42% after 50 trades → halt.

Data: el_candles_1m from jarvis postgres (candle_collector).

Modes:
  live (default)    — polls every minute, processes new 1m + closed 5m bars
  --once            — one tick and exit (smoke test)
  --status          — print state JSON and exit
  --replay-days N   — replay last N days from DB
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal as sig_mod
import sys
import threading
import time as time_mod
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict

import asyncpg
import pandas as pd

from trading.sol_burst_paper.config import (
    CONSEC_LOSS_ALERT, DATA_DIR, DB_HOST, DB_NAME, DB_PASS, DB_PORT,
    DB_USER, HEALTH_PORT, LOG_DIR, LOOKBACK_1M, NOTIONAL, SYMBOL,
)
from trading.sol_burst_paper.engine import BurstReversalEngine
from trading.sol_burst_paper import notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

_STATE_FILE  = DATA_DIR / "state.json"
_TRADES_FILE = DATA_DIR / "trades.csv"
_EQUITY_FILE = DATA_DIR / "equity_curve.csv"

_startup_ts = datetime.now(timezone.utc)


def _init_csvs() -> None:
    if not _TRADES_FILE.exists():
        pd.DataFrame(columns=[
            "ts_signal", "direction", "anchor", "entry_ts", "entry_px",
            "tp", "sl", "slippage_bps", "outcome", "ts_close", "exit_px",
            "pnl_bps", "pnl_total_bps",
        ]).to_csv(_TRADES_FILE, index=False)
    if not _EQUITY_FILE.exists():
        pd.DataFrame(columns=["ts", "pnl_total_bps", "state", "n_trades"]).to_csv(
            _EQUITY_FILE, index=False)


def _load_state() -> Dict[str, Any]:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(engine: BurstReversalEngine) -> None:
    _STATE_FILE.write_text(json.dumps(engine.to_state_dict(), indent=2, default=str))


def _append_trade(ev: Dict[str, Any]) -> None:
    row = pd.DataFrame([{
        "ts_signal":     (ev.get("signal_ts") or "")[:19],
        "direction":     ev.get("direction"),
        "anchor":        ev.get("anchor"),
        "entry_ts":      (ev.get("entry_ts") or "")[:19],
        "entry_px":      ev.get("entry_price"),
        "tp":            None,
        "sl":            None,
        "slippage_bps":  ev.get("slippage_bps"),
        "outcome":       ev.get("outcome"),
        "ts_close":      (ev.get("ts_close") or "")[:19],
        "exit_px":       ev.get("exit_price"),
        "pnl_bps":       round(float(ev.get("pnl_bps", 0)), 2),
        "pnl_total_bps": round(float(ev.get("pnl_total_bps", 0)), 2),
    }])
    row.to_csv(_TRADES_FILE, mode="a", header=False, index=False)


def _append_equity(ts: str, pnl_total_bps: float, state: str, n_trades: int) -> None:
    row = pd.DataFrame([{
        "ts": ts[:19], "pnl_total_bps": round(pnl_total_bps, 2),
        "state": state, "n_trades": n_trades,
    }])
    row.to_csv(_EQUITY_FILE, mode="a", header=False, index=False)


# ── DB fetch ──────────────────────────────────────────────────────────────────

async def _fetch_1m_candles(n: int = LOOKBACK_1M) -> pd.DataFrame:
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME,
    )
    try:
        rows = await conn.fetch(
            """
            SELECT open_time AS ts, open, high, low, close, volume
            FROM el_candles_1m
            WHERE symbol = $1
            ORDER BY open_time DESC LIMIT $2
            """,
            SYMBOL, n,
        )
    finally:
        await conn.close()

    if not rows:
        raise RuntimeError(f"No 1m candles found for {SYMBOL} in DB")

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df


def fetch_1m_sync(n_1m: int = LOOKBACK_1M) -> pd.DataFrame:
    return asyncio.run(_fetch_1m_candles(n_1m))


# ── HTTP server ───────────────────────────────────────────────────────────────

_engine_ref: BurstReversalEngine | None = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path in ("/healthz", "/health"):
            body = json.dumps({
                "status":       "ok",
                "worker":       "sol_burst_paper",
                "symbol":       SYMBOL,
                "uptime_sec":   int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
                "state":        _engine_ref.state_str if _engine_ref else "unknown",
                "position":     _engine_ref.position if _engine_ref else None,
                "n_trades":     _engine_ref.n_trades if _engine_ref else 0,
                "pnl_total_bps": _engine_ref.pnl_total_bps if _engine_ref else 0.0,
                "halted":       _engine_ref.halted if _engine_ref else False,
            }, default=str).encode()
            code = 200
        elif self.path == "/status":
            body = json.dumps({
                "status":      "running",
                "worker":      "sol_burst_paper",
                "symbol":      SYMBOL,
                "tf":          "5m",
                "notional":    NOTIONAL,
                "uptime_sec":  int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
                **(_engine_ref.to_state_dict() if _engine_ref else {}),
            }, default=str).encode()
            code = 200
        else:
            body = b'{"error":"not found"}'
            code = 404

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def _start_http() -> None:
    try:
        srv = HTTPServer(("0.0.0.0", HEALTH_PORT), _Handler)
        log.info("[health] listening on port %d", HEALTH_PORT)
        srv.serve_forever()
    except Exception as e:
        log.warning("[health] could not start: %s", e)


# ── Paper trader ──────────────────────────────────────────────────────────────

class PaperTrader:
    def __init__(self) -> None:
        global _engine_ref
        _init_csvs()
        self.engine = BurstReversalEngine()
        state = _load_state()
        if state:
            self.engine.load_state_dict(state)
            log.info(
                "[state] loaded — state=%s pnl_total=%+.1fbps n_trades=%d halted=%s",
                self.engine.state_str, self.engine.pnl_total_bps,
                self.engine.n_trades, self.engine.halted,
            )
        else:
            log.info("[state] no existing state — starting fresh")
        _engine_ref = self.engine
        self._last_daily_status: str = ""
        self._halt_sent = False

    def tick(self) -> None:
        try:
            df = fetch_1m_sync()
        except Exception as e:
            log.error("[tick] feed error: %s", e)
            notifier.error_alert(str(e))
            return

        now = datetime.now(timezone.utc)
        events = self.engine.process(df, now)
        for ev in events:
            t = ev["type"]
            if t == "signal":
                notifier.signal_detected(
                    ev["direction"], ev["ret_pct"], ev["anchor"], ev["ts_signal"])
            elif t == "open":
                notifier.open_position(
                    ev["direction"], ev["entry_price"],
                    ev["slippage_bps"], ev["entry_ts"])
            elif t == "close":
                _append_trade(ev)
                _append_equity(
                    ev["ts_close"], ev["pnl_total_bps"],
                    self.engine.state_str, ev["n_trades"])
                notifier.close_position(
                    ev["direction"], ev["entry_price"], ev["exit_price"],
                    ev["outcome"], ev["pnl_bps"], ev["pnl_total_bps"],
                    ev["ts_close"])
                if ev.get("phase0_halted") and not self._halt_sent:
                    self._halt_sent = True
                    notifier.phase0_halt(ev["n_trades"], self.engine.win_rate or 0)
                consec = ev.get("consec_losses", 0)
                if consec >= CONSEC_LOSS_ALERT:
                    notifier.consec_loss_alert(consec, ev["pnl_total_bps"])

        _save_state(self.engine)

    def _maybe_daily_status(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_daily_status:
            hour = datetime.now(timezone.utc).hour
            if hour == 7:
                wr = self.engine.win_rate or 0.0
                notifier.daily_status(
                    self.engine.n_trades, self.engine.pnl_total_bps,
                    wr, self.engine.state_str)
                self._last_daily_status = today


def run_live(trader: PaperTrader) -> None:
    log.info("[live] started — polls every 60s")
    notifier.startup(SYMBOL, NOTIONAL)

    while True:
        now = datetime.now(timezone.utc)
        log.info("[live] tick at %s", now.strftime("%H:%M:%S"))
        try:
            trader.tick()
            trader._maybe_daily_status()
        except Exception:
            log.error("[live] unhandled error:\n%s", traceback.format_exc())
        time_mod.sleep(60)


def run_replay(days: int) -> None:
    n_1m = days * 24 * 60 + 200
    log.info("[replay] fetching %d 1m candles (~%d days)...", n_1m, days)
    try:
        df = fetch_1m_sync(n_1m)
    except Exception as e:
        log.error("[replay] feed error: %s", e)
        return

    log.info("[replay] %d 1m bars (from %s to %s)", len(df), df.index[0], df.index[-1])

    engine = BurstReversalEngine()
    now = df.index[-1].to_pydatetime() if len(df) else datetime.now(timezone.utc)
    events = engine.process(df, now)
    wr_str = f"{engine.win_rate*100:.1f}%" if engine.win_rate is not None else "n/a"
    log.info("[replay] done — %d trades | WR=%s | PnL=%+.1fbps",
             engine.n_trades, wr_str, engine.pnl_total_bps)


def main() -> None:
    log.info(
        "R012 SOL Burst Reversal v1.0.0 | symbol=%s tf=5m | "
        "thr=2%% TP=0.5%% SL=0.25%% timeout=30m | notional=$%.0f fee=13bps",
        SYMBOL, NOTIONAL,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--once",        action="store_true")
    parser.add_argument("--status",      action="store_true")
    parser.add_argument("--replay-days", type=int, default=0)
    args = parser.parse_args()

    if args.status:
        state = _load_state()
        print(json.dumps(state, indent=2, default=str))
        return

    if args.replay_days > 0:
        run_replay(args.replay_days)
        return

    t = threading.Thread(target=_start_http, daemon=True)
    t.start()

    trader = PaperTrader()

    if args.once:
        log.info("[once] running single tick")
        trader.tick()
        log.info("[once] done")
        return

    def _handle_sig(s, frame):
        log.info("[live] signal %d — saving state and exiting", s)
        _save_state(trader.engine)
        sys.exit(0)

    sig_mod.signal(sig_mod.SIGTERM, _handle_sig)
    sig_mod.signal(sig_mod.SIGINT,  _handle_sig)

    run_live(trader)


if __name__ == "__main__":
    main()
