"""
R021-A C1 RSI Reversion paper trader — main loop.

Strategy: RSI(14) extreme reversion on SOLUSDT 5m.
Data: el_candles_1m from jarvis postgres (candle_collector).

Modes:
  live (default)    — triggers every 5m (polls 1m, detects new 5m bars)
  --once            — one tick and exit (smoke test)
  --status          — print state JSON and exit
  --replay-days N   — replay last N days from DB

HTTP:
  GET /healthz  — compact status JSON
  GET /status   — full state
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import threading
import time as time_mod
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict

import asyncpg
import pandas as pd

from trading.rsi_reversion_paper.config import (
    DATA_DIR, DB_HOST, DB_NAME, DB_PASS, DB_PORT, DB_USER,
    HEALTH_PORT, LOG_DIR, LOOKBACK_1M, NOTIONAL, SYMBOL,
    CONSEC_LOSS_ALERT,
)
from trading.rsi_reversion_paper.engine import (
    RsiReversionEngine, add_indicators, resample_1m_to_5m,
)
from trading.rsi_reversion_paper import notifier

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

_STATE_FILE  = DATA_DIR / "state.json"
_TRADES_FILE = DATA_DIR / "trades.csv"
_EQUITY_FILE = DATA_DIR / "equity_curve.csv"

_startup_ts = datetime.now(timezone.utc)


def _init_csvs() -> None:
    if not _TRADES_FILE.exists():
        pd.DataFrame(columns=[
            "entry_ts", "exit_ts", "side", "entry_px", "exit_px",
            "entry_rsi", "exit_rsi", "net_ret_pct", "pnl_usd", "pnl_total",
        ]).to_csv(_TRADES_FILE, index=False)
    if not _EQUITY_FILE.exists():
        pd.DataFrame(columns=["ts", "pnl_total", "state", "n_trades"]).to_csv(
            _EQUITY_FILE, index=False)


def _load_state() -> Dict[str, Any]:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(engine: RsiReversionEngine) -> None:
    _STATE_FILE.write_text(json.dumps(engine.to_state_dict(), indent=2, default=str))


def _append_trade(ev: Dict[str, Any]) -> None:
    row = pd.DataFrame([{
        "entry_ts":    ev.get("entry_ts", "")[:19],
        "exit_ts":     ev.get("ts", "")[:19],
        "side":        ev.get("side"),
        "entry_px":    ev.get("entry_price"),
        "exit_px":     ev.get("exit_price"),
        "entry_rsi":   round(float(ev.get("entry_rsi") or 0), 2),
        "exit_rsi":    round(float(ev.get("exit_rsi") or 0), 2),
        "net_ret_pct": round(float(ev.get("net_ret", 0)) * 100, 4),
        "pnl_usd":     round(float(ev.get("pnl_usd", 0)), 4),
        "pnl_total":   round(float(ev.get("pnl_total", 0)), 4),
    }])
    row.to_csv(_TRADES_FILE, mode="a", header=False, index=False)


def _append_equity(ts: str, pnl_total: float, state: str, n_trades: int) -> None:
    row = pd.DataFrame([{
        "ts": ts[:19], "pnl_total": round(pnl_total, 4),
        "state": state, "n_trades": n_trades,
    }])
    row.to_csv(_EQUITY_FILE, mode="a", header=False, index=False)


# ── DB fetch ──────────────────────────────────────────────────────────────────

async def _fetch_1m_candles(n: int = LOOKBACK_1M) -> pd.DataFrame:
    """Fetch last N 1m candles for SYMBOL from jarvis postgres."""
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


def fetch_5m_with_indicators(n_1m: int = LOOKBACK_1M) -> pd.DataFrame:
    """Sync wrapper: fetch 1m → resample to 5m → add RSI."""
    df_1m = asyncio.run(_fetch_1m_candles(n_1m))
    df_5m = resample_1m_to_5m(df_1m)
    return add_indicators(df_5m)


# ── HTTP server ───────────────────────────────────────────────────────────────

_engine_ref: RsiReversionEngine | None = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        pass

    def do_GET(self):  # noqa: N802
        if self.path in ("/healthz", "/health"):
            body = json.dumps({
                "status":    "ok",
                "worker":    "rsi_reversion_paper",
                "symbol":    SYMBOL,
                "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
                "position":  _engine_ref.position if _engine_ref else None,
                "n_trades":  _engine_ref.n_trades if _engine_ref else 0,
                "pnl_total": _engine_ref.pnl_total if _engine_ref else 0.0,
                "halted":    _engine_ref.halted if _engine_ref else False,
            }, default=str).encode()
            code = 200
        elif self.path == "/status":
            body = json.dumps({
                "status":    "running",
                "worker":    "rsi_reversion_paper",
                "symbol":    SYMBOL,
                "rsi_period":   14,
                "oversold":     10,
                "overbought":   85,
                "exit_level":   50,
                "notional":  NOTIONAL,
                "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
                **(  _engine_ref.to_state_dict() if _engine_ref else {}),
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
        self.engine = RsiReversionEngine()
        state = _load_state()
        if state:
            self.engine.load_state_dict(state)
            log.info(
                "[state] loaded — state=%s pnl_total=$%+.2f n_trades=%d halted=%s",
                self.engine.state_str, self.engine.pnl_total,
                self.engine.n_trades, self.engine.halted,
            )
        else:
            log.info("[state] no existing state — starting fresh")
        _engine_ref = self.engine
        self._last_daily_status: str = ""

    def tick(self) -> None:
        try:
            df = fetch_5m_with_indicators()
        except Exception as e:
            log.error("[tick] feed error: %s", e)
            notifier.error_alert(str(e))
            return

        events = self.engine.replay_bars(df)
        for ev in events:
            if ev["type"] == "open":
                notifier.open_position(
                    ev["side"], ev["entry_price"], ev["entry_rsi"], ev["ts"])
            elif ev["type"] == "close":
                _append_trade(ev)
                _append_equity(
                    ev["ts"], ev["pnl_total"],
                    self.engine.state_str, ev["n_trades"])
                notifier.close_position(
                    ev["side"], ev["entry_price"], ev["exit_price"],
                    ev["net_ret"], ev["pnl_usd"], ev["pnl_total"],
                    ev["exit_rsi"], ev["ts"])
                if ev.get("phase0_halted") and not hasattr(self, "_halt_sent"):
                    self._halt_sent = True
                    notifier.phase0_halt(ev["n_trades"], self.engine.win_rate or 0)
                consec = ev.get("consec_losses", 0)
                if consec >= CONSEC_LOSS_ALERT:
                    notifier.consec_loss_alert(consec, ev["pnl_total"])

        _save_state(self.engine)

    def _maybe_daily_status(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_daily_status:
            hour = datetime.now(timezone.utc).hour
            if hour == 7:  # 07:00 UTC daily
                wr = self.engine.win_rate or 0.0
                notifier.daily_status(
                    self.engine.n_trades, self.engine.pnl_total,
                    wr, self.engine.state_str)
                self._last_daily_status = today


def _next_5m_boundary() -> float:
    """Seconds until 30s after next 5m bar close."""
    now = datetime.now(timezone.utc)
    secs = now.minute * 60 + now.second
    bars_done = secs // 300
    next_close = (bars_done + 1) * 300  # seconds into hour
    wait = next_close - secs + 30       # 30s grace period
    return max(wait, 5)


def run_live(trader: PaperTrader) -> None:
    log.info("[live] started — triggers 30s after each 5m bar close")
    notifier.startup(SYMBOL, NOTIONAL)

    while True:
        wait = _next_5m_boundary()
        log.debug("[live] sleeping %.0fs until next 5m bar", wait)
        time_mod.sleep(wait)

        now = datetime.now(timezone.utc)
        log.info("[live] tick at %s", now.strftime("%H:%M:%S"))
        try:
            trader.tick()
            trader._maybe_daily_status()
        except Exception:
            log.error("[live] unhandled error:\n%s", traceback.format_exc())


# ── Replay mode ───────────────────────────────────────────────────────────────

def run_replay(days: int) -> None:
    n_1m = days * 24 * 60 + 200  # extra for RSI warmup
    log.info("[replay] fetching %d 1m candles (~%d days)...", n_1m, days)
    try:
        df = fetch_5m_with_indicators(n_1m)
    except Exception as e:
        log.error("[replay] feed error: %s", e)
        return

    bars = len(df)
    t0   = df.index[0] if bars > 0 else "?"
    t1   = df.index[-1] if bars > 0 else "?"
    log.info("[replay] %d 5m bars (from %s to %s)", bars, t0, t1)

    engine = RsiReversionEngine()
    # replay_bars needs prev-bar context for idx==0 — feed full df
    # but first bar is always skipped (no prev RSI) — acceptable
    events = engine.replay_bars(df)

    n_trades = engine.n_trades
    wr_str   = f"{engine.win_rate*100:.1f}%" if engine.win_rate is not None else "n/a"
    log.info("[replay] done — %d trades | WR=%s | PnL=$%+.2f",
             n_trades, wr_str, engine.pnl_total)

    for ev in events:
        if ev["type"] == "open":
            log.info("  OPEN  %-5s | %s | entry=%.4f rsi=%.1f",
                     ev["side"].upper(), ev["ts"][:16],
                     ev["entry_price"], ev["entry_rsi"])
        elif ev["type"] == "close":
            dur_str = ""
            try:
                et = pd.Timestamp(ev["entry_ts"], tz="UTC")
                xt = pd.Timestamp(ev["ts"], tz="UTC")
                dur_str = f" | dur={(xt-et).seconds//3600}h{(xt-et).seconds%3600//60}m"
            except Exception:
                pass
            log.info("  CLOSE %-5s | %s → %s | net=%+.3f%% | pnl=$%+.2f%s",
                     ev["side"].upper(), ev["entry_ts"][:16], ev["ts"][:16],
                     ev["net_ret"] * 100, ev["pnl_usd"], dur_str)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info(
        "R021-A C1 RSI Reversion v1.0.0 | symbol=%s tf=5m | "
        "rsi14 oversold=%.0f overbought=%.0f exit=%.0f | notional=$%.0f fee=13bps RT",
        SYMBOL, 10, 85, 50, NOTIONAL,
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

    # Start HTTP health server in background thread
    t = threading.Thread(target=_start_http, daemon=True)
    t.start()

    trader = PaperTrader()

    if args.once:
        log.info("[once] running single tick")
        trader.tick()
        log.info("[once] done")
        return

    def _handle_sig(sig, frame):
        log.info("[live] signal %d — saving state and exiting", sig)
        _save_state(trader.engine)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT,  _handle_sig)

    run_live(trader)


if __name__ == "__main__":
    main()
