"""
R021-B Asian DEMA paper trader — main entry point.

Strategy: DEMA(13/34) crossover on SOLUSDT 1h, Asian session (00:00-07:59 UTC).
Data: el_candles_1m from jarvis postgres (candle_collector).

Modes:
  live (default)    — triggers at XX:02 UTC for Asian hours (00:02-07:02)
                      and hourly while a position is open, so exits are
                      evaluated outside the entry session.
  --once            — one tick and exit (smoke test)
  --status          — print state JSON and exit
  --replay-days N   — replay last N days of 1h bars from DB

Health endpoints:
  GET /healthz  — {"status": "ok", "position": ..., "n_trades": ..., "pnl_total": ...}
  GET /status   — full state JSON + metrics
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

from trading.asian_dema_paper.config import (
    ASIAN_HOURS, CONSEC_LOSS_ALERT, DATA_DIR, DB_HOST, DB_NAME,
    DB_PASS, DB_PORT, DB_USER, DEMA_FAST, DEMA_SLOW, HEALTH_PORT,
    LOG_DIR, LOOKBACK_1M, NOTIONAL, PHASE0_MIN_TRADES, PHASE0_MIN_WR,
    SLOPE_BARS, SYMBOL,
)
from trading.asian_dema_paper.engine import (
    AsianDemaEngine, add_indicators, resample_1m_to_1h,
)
from trading.asian_dema_paper import notifier

# ── Logging ────────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("asian_dema_paper")

_startup_ts = datetime.now(timezone.utc)

# ── Persistence ────────────────────────────────────────────────────────────────

_STATE_FILE = DATA_DIR / "state.json"
_TRADES_FILE = DATA_DIR / "trades.csv"
_EQUITY_FILE = DATA_DIR / "equity_curve.csv"

_TRADE_COLS = [
    "entry_ts", "exit_ts", "side", "entry_px", "exit_px",
    "gross_ret_pct", "net_ret_pct", "pnl_usd", "notional_usd",
    "duration_h", "pnl_total_after",
]


def _init_csvs() -> None:
    if not _TRADES_FILE.exists():
        pd.DataFrame(columns=_TRADE_COLS).to_csv(_TRADES_FILE, index=False)
    if not _EQUITY_FILE.exists():
        pd.DataFrame(columns=["ts", "pnl_total", "state", "n_trades"]).to_csv(
            _EQUITY_FILE, index=False
        )


def _load_state() -> Dict[str, Any]:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(engine: AsianDemaEngine) -> None:
    state = {
        "engine": engine.to_state_dict(),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    _STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _append_trade(trade: Dict[str, Any]) -> None:
    pd.DataFrame([trade])[_TRADE_COLS].to_csv(
        _TRADES_FILE, mode="a", header=False, index=False
    )


def _append_equity(ts: str, pnl_total: float, state: str, n_trades: int) -> None:
    pd.DataFrame([{
        "ts": ts,
        "pnl_total": round(pnl_total, 4),
        "state": state,
        "n_trades": n_trades,
    }]).to_csv(_EQUITY_FILE, mode="a", header=False, index=False)


# ── DB feed ────────────────────────────────────────────────────────────────────

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
    if "volume" in df.columns:
        df["volume"] = df["volume"].astype(float)
    return df


def fetch_1h_with_indicators(n_1m: int = LOOKBACK_1M) -> pd.DataFrame:
    """Sync wrapper: fetch 1m, resample to 1h, compute indicators."""
    df_1m = asyncio.run(_fetch_1m_candles(n_1m))
    df_1h = resample_1m_to_1h(df_1m)
    df_1h = add_indicators(df_1h)
    log.debug("[feed] 1h bars: %d (last: %s)", len(df_1h),
              df_1h.index[-1].isoformat() if len(df_1h) > 0 else "n/a")
    return df_1h


# ── Health server ──────────────────────────────────────────────────────────────

_health: Dict[str, Any] = {
    "status":    "init",
    "position":  None,
    "n_trades":  0,
    "pnl_total": 0.0,
    "state":     "flat",
    "halted":    False,
    "last_bar_ts": None,
}


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path in ("/healthz", "/health"):
            body = json.dumps({
                "status": "ok",
                "worker": "asian_dema_paper",
                "symbol": SYMBOL,
                "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
                "position":  _health.get("position"),
                "n_trades":  _health.get("n_trades", 0),
                "pnl_total": _health.get("pnl_total", 0.0),
                "halted":    _health.get("halted", False),
            }, default=str).encode()
            self._respond(200, body)

        elif self.path == "/status":
            body = json.dumps({
                **_health,
                "worker": "asian_dema_paper",
                "symbol": SYMBOL,
                "dema_fast": DEMA_FAST,
                "dema_slow": DEMA_SLOW,
                "slope_bars": SLOPE_BARS,
                "notional": NOTIONAL,
                "uptime_sec": int((datetime.now(timezone.utc) - _startup_ts).total_seconds()),
            }, default=str).encode()
            self._respond(200, body)

        else:
            self.send_response(404)
            self.end_headers()

    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _start_health_server() -> None:
    try:
        srv = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("[health] listening on port %d", HEALTH_PORT)
    except Exception as e:
        log.warning("[health] could not start: %s", e)


# ── PaperTrader ────────────────────────────────────────────────────────────────

class PaperTrader:
    def __init__(self) -> None:
        self.engine = AsianDemaEngine()
        _init_csvs()
        self._load()
        self._update_health()

    def _load(self) -> None:
        st = _load_state()
        if not st:
            log.info("[state] no existing state — starting fresh")
            return
        try:
            self.engine.load_state_dict(st.get("engine", {}))
            log.info(
                "[state] loaded — state=%s pnl_total=$%+.2f n_trades=%d halted=%s",
                self.engine.state_str, self.engine.pnl_total,
                self.engine.n_trades, self.engine.halted,
            )
        except Exception:
            log.error("[state] load error:\n%s", traceback.format_exc())

    def _save(self) -> None:
        try:
            _save_state(self.engine)
        except Exception:
            log.error("[state] save error:\n%s", traceback.format_exc())

    def _update_health(self) -> None:
        _health.update({
            "status":      "running",
            "position":    self.engine.position,
            "n_trades":    self.engine.n_trades,
            "pnl_total":   round(self.engine.pnl_total, 4),
            "state":       self.engine.state_str,
            "halted":      self.engine.halted,
            "last_bar_ts": self.engine.last_bar_ts.isoformat()
                           if self.engine.last_bar_ts else None,
        })

    def tick(self) -> None:
        """Fetch latest bars, run engine, persist, notify."""
        try:
            df_1h = fetch_1h_with_indicators()
        except Exception as e:
            log.error("[tick] feed error: %s", e)
            notifier.notify_error("feed", str(e))
            return

        if df_1h.empty:
            log.warning("[tick] empty 1h dataframe — skipping")
            return

        last_ts = df_1h.index[-1]
        events = self.engine.replay_bars(df_1h)

        for ev in events:
            action = ev["action"]

            if action in ("open_long", "open_short"):
                notifier.notify_open(
                    ev["side"],
                    str(ev["ts"])[:19],
                    ev["entry_px"],
                    ev["dema_fast"],
                    ev["dema_slow"],
                )

            elif action in ("close_long", "close_short"):
                tr = ev["trade"]
                _append_trade(tr)
                _append_equity(
                    ev["ts"], self.engine.pnl_total,
                    self.engine.state_str, self.engine.n_trades,
                )
                notifier.notify_close(
                    ev["side"],
                    tr["entry_px"],
                    tr["exit_px"],
                    tr["pnl_usd"],
                    tr["net_ret_pct"],
                    tr["duration_h"],
                    self.engine.pnl_total,
                    self.engine.n_trades,
                    self.engine.n_wins,
                )

                # Phase 0 halt alert
                if ev.get("halt_triggered"):
                    notifier.notify_phase0_halt(
                        self.engine.n_trades, self.engine.win_rate
                    )
                    log.warning(
                        "[PHASE0 GATE] HALTED — WR=%.1f%% after %d trades",
                        self.engine.win_rate * 100, self.engine.n_trades,
                    )

                # Consecutive losses alert
                consec = ev.get("consec_losses", 0)
                if consec >= CONSEC_LOSS_ALERT:
                    notifier.notify_consec_losses(consec, self.engine.pnl_total)
                    log.warning("[alert] %d consecutive losses", consec)

        self._save()
        self._update_health()

        log.info(
            "[tick] last_bar=%s state=%s pnl=$%+.2f n_trades=%d halted=%s",
            str(last_ts)[:19], self.engine.state_str,
            self.engine.pnl_total, self.engine.n_trades, self.engine.halted,
        )

    def send_daily_status(self) -> None:
        notifier.notify_daily_status(
            self.engine.position,
            self.engine.pnl_total,
            self.engine.n_trades,
            self.engine.n_wins,
        )


# ── Timing helpers ─────────────────────────────────────────────────────────────

def _is_hourly_bar_trigger_time(now: datetime) -> bool:
    """Return True at HH:02 UTC, after the previous 1h bar has closed."""
    return now.minute == 2


def _is_asian_trigger_time(now: datetime) -> bool:
    """
    Return True if now is at minute==2 and the hour just closed is Asian (0-7).
    Trigger time: HH:02 UTC where HH in {0..7}.
    The bar that just closed has open_time = HH:00, so hour=HH.
    """
    return now.minute == 2 and now.hour in ASIAN_HOURS


def _should_run_strategy_tick(now: datetime, trader: PaperTrader) -> bool:
    """
    Run entries only on Asian-session triggers, but keep evaluating open
    positions hourly so reverse-crossover exits cannot be missed.
    """
    if not _is_hourly_bar_trigger_time(now):
        return False
    return now.hour in ASIAN_HOURS or trader.engine.position is not None


def _is_daily_status_time(now: datetime) -> bool:
    """Daily status at 00:30 UTC."""
    return now.hour == 0 and now.minute == 30


def _seconds_until_next_minute() -> float:
    now = datetime.now(timezone.utc)
    next_min = (now + timedelta(seconds=60)).replace(second=0, microsecond=0)
    return max(1.0, (next_min - now).total_seconds())


# ── Live loop ──────────────────────────────────────────────────────────────────

_shutdown = False


def _on_signal(*_) -> None:
    global _shutdown
    _shutdown = True
    log.info("[shutdown] signal received")


def run_live(trader: PaperTrader) -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log.info(
        "[live] started — entry triggers at HH:02 UTC (HH in {0..7}); "
        "exit checks hourly while position is open"
    )
    notifier.notify_startup(trader.engine.pnl_total, trader.engine.n_trades)

    _daily_status_sent_hour: int = -1

    while not _shutdown:
        try:
            wait = _seconds_until_next_minute()
            deadline = time_mod.time() + wait
            while time_mod.time() < deadline and not _shutdown:
                time_mod.sleep(min(5.0, deadline - time_mod.time()))

            if _shutdown:
                break

            now = datetime.now(timezone.utc)

            # Daily status at 00:30 UTC (send once per day)
            if _is_daily_status_time(now) and _daily_status_sent_hour != now.day:
                trader.send_daily_status()
                _daily_status_sent_hour = now.day

            # Strategy trigger: Asian entries, plus hourly exit checks while open.
            if _should_run_strategy_tick(now, trader):
                log.info(
                    "[live] trigger at %s UTC (hour=%d, position=%s)",
                    now.strftime("%H:%M"), now.hour, trader.engine.position,
                )
                trader.tick()

        except KeyboardInterrupt:
            break
        except Exception:
            err = traceback.format_exc()
            log.error("[loop error]\n%s", err)
            notifier.notify_error("run_live", err)
            time_mod.sleep(30)

    trader._save()
    log.info("[shutdown] state saved")


# ── Replay mode ────────────────────────────────────────────────────────────────

def run_replay(trader: PaperTrader, days: int) -> None:
    """
    Replay last N days of 1h bars fetched from jarvis DB.
    Resets engine state for a clean replay.
    """
    n_1m = min(days * 24 * 60 + 120, 14400)  # max 10 days of 1m bars at once
    log.info("[replay] fetching %d 1m candles (~%d days)...", n_1m, days)

    try:
        df_1h = fetch_1h_with_indicators(n_1m)
    except Exception as e:
        log.error("[replay] feed error: %s", e)
        return

    log.info("[replay] %d 1h bars (from %s to %s)",
             len(df_1h),
             df_1h.index[0].isoformat() if len(df_1h) > 0 else "n/a",
             df_1h.index[-1].isoformat() if len(df_1h) > 0 else "n/a")

    # Reset for clean replay
    trader.engine = AsianDemaEngine()
    events = trader.engine.replay_bars(df_1h)

    trades = [ev for ev in events if "close" in ev.get("action", "")]
    wins   = sum(1 for ev in trades if ev["trade"]["pnl_usd"] > 0)
    total  = len(trades)
    wr     = (wins / total * 100) if total > 0 else 0.0

    log.info(
        "[replay] done — %d trades | WR=%.1f%% (%d/%d) | PnL=$%+.2f",
        total, wr, wins, total, trader.engine.pnl_total,
    )

    for ev in events:
        action = ev.get("action", "")
        if "open" in action:
            log.info("  OPEN  %-6s | %s | entry=%.4f",
                     ev["side"].upper(), str(ev["ts"])[:19], ev["entry_px"])
        elif "close" in action:
            tr = ev["trade"]
            log.info("  CLOSE %-6s | entry=%.4f → exit=%.4f | net=%+.3f%% | pnl=$%+.2f | dur=%dh",
                     tr["side"].upper(), tr["entry_px"], tr["exit_px"],
                     tr["net_ret_pct"], tr["pnl_usd"], tr["duration_h"])


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="R021-B Asian DEMA paper trader")
    ap.add_argument("--once",        action="store_true",
                    help="one tick and exit (smoke test)")
    ap.add_argument("--status",      action="store_true",
                    help="print state and exit")
    ap.add_argument("--replay-days", type=int, default=0, metavar="N",
                    help="replay last N days from DB (resets state)")
    args = ap.parse_args()

    from trading.asian_dema_paper.config import FEE_RT
    fee_bps = round(FEE_RT * 10_000)
    log.info(
        "R021-B Asian DEMA v%s | symbol=%s tf=1h | dema=%d/%d slope=%db "
        "| session=Asian(00-07 UTC) | notional=$%.0f fee=%dbps RT",
        AsianDemaEngine.VERSION, SYMBOL, DEMA_FAST, DEMA_SLOW, SLOPE_BARS,
        NOTIONAL, fee_bps,
    )

    _start_health_server()
    trader = PaperTrader()

    if args.status:
        e = trader.engine
        state = e.to_state_dict()
        print(json.dumps(state, indent=2, default=str))
        print(f"\nstate:     {e.state_str}")
        print(f"pnl_total: ${e.pnl_total:+.2f}")
        print(f"n_trades:  {e.n_trades} | n_wins: {e.n_wins} | WR: {e.win_rate*100:.1f}%")
        print(f"halted:    {e.halted}")
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
