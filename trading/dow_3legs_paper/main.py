"""
DOW 3-legs skip_low paper trader — main entry point.

Modes:
  live (default)      — polls Binance REST every minute; acts at trigger windows
  --once              — one tick and exit (smoke test / healthcheck probe)
  --replay-days N     — replay last N days via 1m klines (gate uses current ATR state)

Health endpoint: GET /health on DOW_HEALTH_PORT (default 8096)
Returns JSON: status, state, equity, n_trades, gate, ts, uptime_sec.
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
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from trading.dow_3legs_paper.atr_gate import AtrGate
from trading.dow_3legs_paper.config import (
    DATA_DIR, DISABLE_GATE, HEALTH_PORT, INITIAL_CAPITAL, LEVERAGE, LOG_DIR, SYMBOL,
    BINANCE_FAPI,
)
from trading.dow_3legs_paper.engine import Dow3LegsSkipLowEngine
from trading.dow_3legs_paper.feed import Feed
from trading.dow_3legs_paper import notifier
from trading.dow_3legs_paper.persistence import (
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
log = logging.getLogger("dow_3legs_paper")

_startup_ts = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Health server
# ---------------------------------------------------------------------------

_health_state: dict = {
    "state": "init",
    "equity": INITIAL_CAPITAL,
    "n_trades": 0,
    "gate": {},
    "ts": None,
}


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ("/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "status": "ok",
            "worker": "trading_dow_3legs_paper",
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
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        log.info("[health] server listening on port %d", HEALTH_PORT)
    except Exception as exc:
        log.warning("[health] could not start server: %s", exc)


# ---------------------------------------------------------------------------
# PaperTrader
# ---------------------------------------------------------------------------

class PaperTrader:
    def __init__(self) -> None:
        self.engine = Dow3LegsSkipLowEngine(leverage=LEVERAGE)
        self.gate = AtrGate()
        self.equity = INITIAL_CAPITAL
        init_csvs()
        self._load_state()

    def _load_state(self) -> None:
        st = load_state()
        if not st:
            return
        try:
            self.engine.load_state_dict(st.get("engine", {}))
            self.equity = st.get("equity", INITIAL_CAPITAL)
            gate_st = st.get("gate", {})
            if gate_st.get("last_refresh"):
                self.gate.last_refresh = pd.Timestamp(gate_st["last_refresh"])
            if gate_st.get("atr_pct") is not None:
                self.gate.atr_pct_today = float(gate_st["atr_pct"])
            if gate_st.get("p20") is not None:
                self.gate.p20_today = float(gate_st["p20"])
            log.info(
                "[state loaded] state=%s equity=$%.4f n_trades=%d",
                self.engine.state, self.equity, len(self.engine.trades),
            )
        except Exception:
            log.error("[state] load error:\n%s", traceback.format_exc())

    def _save_state(self) -> None:
        save_state({
            "equity": self.equity,
            "engine": self.engine.to_state_dict(),
            "gate": self.gate.to_dict(),
        })

    def on_tick(self, ts: pd.Timestamp, price: float) -> None:
        n_trades_before = len(self.engine.trades)

        gate_pass, gate_info = self.gate.is_pass(ts)
        d = self.engine.on_tick(ts, price, pass_atr_gate=gate_pass)

        if d is not None:
            action = d["action"]
            leg = d.get("leg") or d.get("intended_leg") or d.get("leg_closed") or ""
            extras = {k: v for k, v in d.items()
                      if k not in ("ts", "action", "price", "state_after", "leg")}
            extras.update(gate_info)
            append_row("decisions.csv", {
                "ts": d["ts"],
                "action": action,
                "price": d["price"],
                "leg": leg,
                "state_after": d["state_after"],
                "gate_status": gate_info.get("gate", "unknown"),
                "extra_json": json.dumps(extras, default=str),
            })
            log.info(
                "[DECISION %s] ts=%s price=%.2f leg=%s state->%s gate=%s",
                action, d["ts"], price, leg or "-", d["state_after"],
                gate_info.get("gate", "?"),
            )

        # apply closed trades to equity
        new_trades = self.engine.trades[n_trades_before:]
        for tr in new_trades:
            self.equity *= (1 + tr["ret_pct_net"] / 100.0)
            append_row("trades.csv", {**tr, "equity_after": round(self.equity, 6)})
            log.info(
                "[TRADE CLOSED] %s %s %.2f->%.2f ret_net=%+.3f%% equity=$%.4f",
                tr["side"], tr["leg"], tr["entry_price"], tr["exit_price"],
                tr["ret_pct_net"], self.equity,
            )

        # telegram notifications
        if d is not None:
            action = d["action"]
            ts_str = d["ts"]
            if action == "open_long":
                notifier.notify_open(d.get("leg", "?"), "long", ts_str, price,
                                     self.equity, gate_info.get("gate", "?"))
            elif action == "open_short":
                notifier.notify_open(d.get("leg", "?"), "short", ts_str, price,
                                     self.equity, gate_info.get("gate", "?"))
            elif action == "close_long":
                notifier.notify_close(d.get("leg", "?"), "long", ts_str, price,
                                      d.get("long_ret_net_pct", 0.0), self.equity)
            elif action == "close_short":
                notifier.notify_close(d.get("leg", "?"), "short", ts_str, price,
                                      d.get("short_ret_net_pct", 0.0), self.equity)
            elif action == "close_long_open_short":
                notifier.notify_close_open(ts_str, price,
                                           d.get("long_ret_net_pct", 0.0), self.equity)
            elif action == "skip_open":
                notifier.notify_skip(ts_str, d.get("intended_leg", "?"),
                                     gate_info.get("atr_pct", float("nan")),
                                     gate_info.get("p20", float("nan")))
            if d.get("stale_close"):
                notifier.notify_error(
                    "stale_close",
                    f"Posição {d.get('leg','?')} fechada à força após "
                    f"{d.get('hours_open','?')}h aberta (close event perdido).",
                )

        append_row("equity_curve.csv", {
            "ts": ts.isoformat(),
            "equity": round(self.equity, 6),
            "state": self.engine.state,
        })
        self._save_state()

        _health_state.update({
            "state": self.engine.state,
            "equity": round(self.equity, 4),
            "n_trades": len(self.engine.trades),
            "gate": gate_info,
            "ts": ts.isoformat(),
        })

        log.info(
            "[tick] state=%s equity=$%.4f gate=%s",
            self.engine.state, self.equity, gate_info.get("gate", "?"),
        )


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------

def _seconds_until_next_minute() -> float:
    now = datetime.now(timezone.utc)
    next_min = (now + timedelta(seconds=60 - now.second + 1)).replace(microsecond=0)
    return max(1.0, (next_min - now).total_seconds())


# ---------------------------------------------------------------------------
# Live loop
# ---------------------------------------------------------------------------

_shutdown = False


def _on_signal(*_) -> None:
    global _shutdown
    _shutdown = True
    log.info("[shutdown] signal received")


def run_live(trader: PaperTrader) -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    log.info("live mode started. gatilhos: Sun/Mon/Tue/Wed/Thu 23:55 UTC")
    notifier.notify_startup(trader.equity, LEVERAGE, mode="live",
                            gate_enabled=not DISABLE_GATE)

    while not _shutdown:
        try:
            wait = _seconds_until_next_minute()
            deadline = time_mod.time() + wait
            while time_mod.time() < deadline and not _shutdown:
                time_mod.sleep(min(1.0, deadline - time_mod.time()))

            if _shutdown:
                break

            ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
            in_trigger_window = (
                (ts.hour == 23 and ts.minute >= 50)
                or (ts.hour == 0 and ts.minute < 15)
            )
            # gate refresh window: 00:30–01:00 UTC (daily)
            in_gate_window = ts.hour == 0 and 30 <= ts.minute < 60

            if in_trigger_window or in_gate_window:
                price = Feed.get_price()
                trader.on_tick(ts, price)

        except KeyboardInterrupt:
            break
        except Exception:
            err = traceback.format_exc()
            log.error("[loop error]\n%s", err)
            notifier.notify_error("run_live", err)
            time_mod.sleep(30)

    trader._save_state()
    notifier.notify_stopped()
    log.info("[shutdown] state saved, exiting")


# ---------------------------------------------------------------------------
# Replay (--replay-days N)
# ---------------------------------------------------------------------------

def run_replay(trader: PaperTrader, days: int) -> None:
    """Replay last N days via 1m klines.

    NOTE: ATR gate uses current regime data, not historical. Replay is approximate
    for skip_low logic. Use assets/replay_engine_parquet.py for fidelity.
    """
    log.info("[replay] fetching last %d days of 1m klines...", days)
    end = pd.Timestamp.now(tz="UTC").tz_localize(None).replace(microsecond=0)
    start = end - pd.Timedelta(days=days)
    cur_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    all_dfs = []

    while cur_ms < end_ms:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": SYMBOL, "interval": "1m",
                    "startTime": cur_ms, "limit": 1500},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
        if not raw:
            break
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        df["open_time"] = (
            pd.to_datetime(df["open_time"], unit="ms", utc=True)
            .dt.tz_convert("UTC").dt.tz_localize(None)
        )
        df["close"] = df["close"].astype(float)
        all_dfs.append(df)
        cur_ms = int(raw[-1][0]) + 60_000
        if len(raw) < 1500:
            break

    if not all_dfs:
        log.warning("[replay] no klines fetched")
        return

    df = pd.concat(all_dfs).drop_duplicates("open_time").sort_values("open_time")
    log.info("[replay] %d 1m candles", len(df))

    mask = (
        ((df["open_time"].dt.hour == 23) & (df["open_time"].dt.minute >= 55))
        | ((df["open_time"].dt.hour == 0) & (df["open_time"].dt.minute < 10))
    )
    df_filt = df[mask].sort_values("open_time")
    log.info("[replay] %d candles in trigger window", len(df_filt))

    for _, row in df_filt.iterrows():
        trader.on_tick(row["open_time"], float(row["close"]))

    log.info(
        "[replay] done — trades=%d equity_final=$%.4f",
        len(trader.engine.trades), trader.equity,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="DOW 3-legs skip_low paper trader")
    ap.add_argument("--once", action="store_true",
                    help="process one tick and exit (smoke test)")
    ap.add_argument("--replay-days", type=int, default=0, metavar="DAYS",
                    help="replay last N days via 1m klines")
    ap.add_argument("--port", type=int, default=HEALTH_PORT,
                    help="health server port override")
    args = ap.parse_args()

    log.info(
        "engine version: %s, lev=%.1fx, gate=%s, capital=$%.2f",
        Dow3LegsSkipLowEngine.VERSION, LEVERAGE,
        "OFF" if DISABLE_GATE else "ON", INITIAL_CAPITAL,
    )

    _start_health_server()

    trader = PaperTrader()

    log.info("state=%s equity=$%.4f n_trades=%d",
             trader.engine.state, trader.equity, len(trader.engine.trades))

    if args.replay_days > 0:
        run_replay(trader, args.replay_days)
        return 0

    if args.once:
        ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
        price = Feed.get_price()
        trader.on_tick(ts, price)
        return 0

    run_live(trader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
