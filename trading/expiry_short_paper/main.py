"""Expiry-short paper trader — main loop.

Hourly cycle at :10 UTC. On Thursday's 08:10 cycle, if flat: SHORT the two
perps (paper fill at last price + slippage). On Friday's 08:10 cycle (or the
first cycle after), close, booking price P&L + realized funding (short
RECEIVES positive funding) - fees. Stale-position guard force-closes >30h.
Health: /healthz and /status on :8109. State in /data/state.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import signal as sig_mod
import sys
import threading
import time as time_mod
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from trading.expiry_short_paper import config as C

C.LOG_DIR.mkdir(parents=True, exist_ok=True)
C.DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(C.LOG_DIR / "run.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("expiry_short")

STATE_FILE = C.DATA_DIR / "state.json"
TRADES_CSV = C.DATA_DIR / "trades.csv"
EQUITY_CSV = C.DATA_DIR / "equity.csv"
_S = requests.Session()
_health = {"status": "starting"}
_startup = datetime.now(timezone.utc)


def _get(path, params=None):
    for i in range(3):
        try:
            r = _S.get(f"{C.BINANCE_FAPI}{path}", params=params or {}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == 2:
                raise
            time_mod.sleep(2 * (i + 1))


def price(sym) -> float:
    return float(_get("/fapi/v1/ticker/price", {"symbol": sym})["price"])


def funding_sum(sym, t0: datetime, t1: datetime) -> float:
    rows = _get("/fapi/v1/fundingRate", {"symbol": sym,
                                         "startTime": int(t0.timestamp() * 1000),
                                         "endTime": int(t1.timestamp() * 1000), "limit": 100})
    return sum(float(r["fundingRate"]) for r in rows)


def tg(text):
    if not (C.TELEGRAM_BOT_TOKEN and C.TELEGRAM_CHAT_ID):
        return
    try:
        _S.post(f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": C.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=10)
    except Exception:
        log.warning("tg failed:\n%s", traceback.format_exc())


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"equity": C.INITIAL_CAPITAL, "peak": C.INITIAL_CAPITAL,
            "n_trades": 0, "n_wins": 0, "consec_losses": 0,
            "last_open_week": "", "position": None}


def save_state(st):
    st["saved_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(st, indent=2))


def _csv(path, header, row):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def open_position(st, now):
    legs = {}
    for sym in C.SYMBOLS:
        px = price(sym) * (1 - C.SLIP_SIDE)          # short: sell at bid-ish
        legs[sym] = {"entry_px": px,
                     "notional": st["equity"] * C.LEVERAGE / len(C.SYMBOLS)}
    st["position"] = {"opened": now.isoformat(), "legs": legs}
    st["last_open_week"] = now.strftime("%G-W%V")
    log.info("OPEN short %s", {s: round(l["entry_px"], 2) for s, l in legs.items()})
    tg(f"[{C.TELEGRAM_TAG}] \U0001f534 SHORT pré-expiry "
       + " + ".join(f"`{s} @{l['entry_px']:,.0f}`" for s, l in legs.items())
       + f"\nnotional: `${st['equity'] * C.LEVERAGE:,.0f}` | fecha sex 08:00 UTC")


def close_position(st, now, reason="expiry"):
    pos = st["position"]
    t0 = datetime.fromisoformat(pos["opened"])
    pnl = 0.0
    detail = []
    for sym, lg in pos["legs"].items():
        px = price(sym) * (1 + C.SLIP_SIDE)          # buy back at ask-ish
        r_px = 1 - px / lg["entry_px"]
        try:
            fund = funding_sum(sym, t0, now)          # short receives positive
        except Exception:
            fund = 0.0
            log.warning("funding fetch failed for %s", sym)
        r = r_px + fund - 2 * C.FEE_SIDE
        pnl += r * lg["notional"]
        detail.append(f"{sym} {r*100:+.2f}%")
    st["equity"] += pnl
    st["n_trades"] += 1
    win = pnl > 0
    st["n_wins"] += int(win)
    st["consec_losses"] = 0 if win else st["consec_losses"] + 1
    st["position"] = None
    _csv(TRADES_CSV, ["opened", "closed", "reason", "pnl", "equity", "detail"],
         [pos["opened"], now.isoformat(), reason, round(pnl, 2),
          round(st["equity"], 2), " | ".join(detail)])
    emoji = "✅" if win else "❌"
    wr = st["n_wins"] / st["n_trades"] * 100
    tg(f"[{C.TELEGRAM_TAG}] {emoji} CLOSED {reason} PnL `{pnl:+.2f}` "
       f"({' , '.join(detail)})\neq `${st['equity']:.2f}` | WR `{wr:.0f}%` "
       f"({st['n_wins']}/{st['n_trades']})")
    if st["consec_losses"] >= C.CONSEC_LOSS_ALERT:
        tg(f"[{C.TELEGRAM_TAG}] ⚠️ {st['consec_losses']} perdas seguidas")
    log.info("CLOSE pnl=%.2f eq=%.2f", pnl, st["equity"])


def mtm(st):
    if not st["position"]:
        return st["equity"]
    v = st["equity"]
    for sym, lg in st["position"]["legs"].items():
        v += (1 - price(sym) / lg["entry_px"]) * lg["notional"]
    return v


def run_cycle(st, first=False):
    now = datetime.now(timezone.utc)
    week = now.strftime("%G-W%V")

    if st["position"]:
        t0 = datetime.fromisoformat(st["position"]["opened"])
        is_close = (now.weekday() == C.CLOSE_DOW and now.hour >= C.CLOSE_HOUR) or \
                   (now - t0) > timedelta(hours=30)
        if is_close:
            close_position(st, now, "expiry" if (now - t0) <= timedelta(hours=30) else "stale-guard")
    elif (now.weekday() == C.OPEN_DOW and now.hour == C.OPEN_HOUR
          and st["last_open_week"] != week):
        open_position(st, now)

    eq = mtm(st)
    st["peak"] = max(st["peak"], eq)
    dd = eq / st["peak"] - 1
    _csv(EQUITY_CSV, ["ts", "equity_mtm", "dd"],
         [now.strftime("%Y-%m-%d %H:%M"), round(eq, 2), round(dd, 4)])
    if dd <= -C.DD_ALERT_PCT:
        tg(f"[{C.TELEGRAM_TAG}] ⚠️ DD {dd*100:.1f}% eq ${eq:.2f}")
    if first:
        tg(f"[{C.TELEGRAM_TAG}] #startup *EXPIRY-SHORT PAPER ONLINE*\n"
           f"short BTC+ETH qui 08:00 → sex 08:00 UTC | capital `${eq:.2f}`\n"
           "validado: `1,64%/mês` PFmo `1.90` DD `-18.3%` (23-26, líquido)")
    _health.update({"status": "ok", "equity": round(eq, 2), "dd": round(dd, 4),
                    "position": bool(st["position"]), "updated": now.isoformat()})
    save_state(st)
    log.info("cycle done | eq=%.2f dd=%.1f%% pos=%s", eq, dd * 100, bool(st["position"]))


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(_health if not self.path.startswith("/healthz") else
                          {"status": _health.get("status"), "equity": _health.get("equity"),
                           "position": _health.get("position"),
                           "uptime_sec": int((datetime.now(timezone.utc) - _startup).total_seconds())}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


_stop = False


def _on_sig(*_):
    global _stop
    _stop = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    st = load_state()
    if args.status:
        print(json.dumps(st, indent=2))
        return 0
    threading.Thread(target=HTTPServer(("0.0.0.0", C.HEALTH_PORT), _H).serve_forever,
                     daemon=True).start()
    log.info("health server on :%d", C.HEALTH_PORT)
    first = not STATE_FILE.exists()
    if args.once:
        run_cycle(st, first=first)
        return 0
    sig_mod.signal(sig_mod.SIGTERM, _on_sig)
    sig_mod.signal(sig_mod.SIGINT, _on_sig)
    try:
        run_cycle(st, first=first)
    except Exception:
        log.error("first cycle failed:\n%s", traceback.format_exc())
    while not _stop:
        nxt = datetime.now(timezone.utc).replace(minute=C.CYCLE_MINUTE, second=0, microsecond=0)
        if nxt <= datetime.now(timezone.utc):
            nxt += timedelta(hours=1)
        while not _stop and datetime.now(timezone.utc) < nxt:
            time_mod.sleep(15)
        if _stop:
            break
        try:
            run_cycle(st)
        except Exception:
            log.error("cycle failed:\n%s", traceback.format_exc())
            tg(f"[{C.TELEGRAM_TAG}] #error\n```\n{traceback.format_exc()[-400:]}\n```")
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
