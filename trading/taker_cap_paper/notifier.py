"""Telegram notifier for the taker_cap paper trader.
No-op when env vars are unset (mirrors the other paper traders)."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

import requests

from trading.taker_cap_paper import config as C

log = logging.getLogger("taker_cap_paper")
_ok = bool(C.TELEGRAM_BOT_TOKEN and C.TELEGRAM_CHAT_ID)


def _send(text: str) -> None:
    if not _ok:
        log.debug("[tg] no-op: %s", text[:80])
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": C.TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("[tg] send failed %d: %s", r.status_code, r.text[:200])
    except Exception:
        log.warning("[tg] error:\n%s", traceback.format_exc())


def _tag(kind: str) -> str:
    return f"#{C.TELEGRAM_TAG} #{kind}"


def notify_startup(equity: float) -> None:
    _send("\n".join([
        f"{_tag('startup')}",
        "*TAKER-CAP PAPER ONLINE* (SIGNAL\\_ONLY)",
        f"LONG 4h após capitulação de taker: z(taker LSR 24h, 7d) < `{C.Z_TH:g}`",
        f"universo: `{','.join(C.SYMBOLS)}` 1h | hold fixo `{C.HOLD_BARS}` barras | lev `{C.LEVERAGE:g}x`",
        f"capital: `${equity:.2f}` (`${C.SLEEVE_CAPITAL:.0f}`/símbolo)",
        f"costs: `{C.FEE_BPS:g}+{C.SLIP_BPS:g}bps/lado` | funding: `{'on' if C.FUNDING_ENABLED else 'off'}`",
        "validado 3,5 anos (fase 2 liqflow): `+17,6bps`/trade t=`2,8` hit `58%` · 4/4 anos+ · corr `0` c/ ensemble",
    ]))


def notify_open(ev: dict, bar_ts: str) -> None:
    _send(f"[{C.TELEGRAM_TAG}] \U0001f7e2 LONG `{ev['symbol']}` 1h (hold {C.HOLD_BARS}h)\n"
          f"Entry: `${ev['px']:g}` | Qty: `{ev['qty']:g}` | Notional: `${ev['notional']:.2f}`\n"
          f"Bar: `{bar_ts} UTC` | fee: `${ev['fee']:.4f}`")


def notify_close(ev: dict, pnl_total: float, n_trades: int, n_wins: int) -> None:
    emoji = "✅" if ev["pnl"] >= 0 else "❌"
    sign = "+" if ev["pnl"] >= 0 else ""
    wr = (n_wins / n_trades * 100) if n_trades else 0
    _send(f"[{C.TELEGRAM_TAG}] {emoji} Fechado LONG `{ev['symbol']}` "
          f"`{sign}{ev['pnl_pct']:.2f}%` | PnL: `{sign}${ev['pnl']:.2f}` ({ev['reason']})\n"
          f"Entry `${ev['entry_px']:g}` → Exit `${ev['exit_px']:g}` | {ev['bars_held']} barras\n"
          f"funding `${ev['funding']:.4f}`\n"
          f"PnL total `{('+' if pnl_total>=0 else '')}${pnl_total:.2f}` | WR `{wr:.0f}%` ({n_wins}/{n_trades})")


def notify_daily(equity: float, pnl_total: float, sleeves: dict, max_dd: float) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n_tr = sum(s["n_trades"] for s in sleeves.values())
    n_w = sum(s["n_wins"] for s in sleeves.values())
    wr = (n_w / n_tr * 100) if n_tr else 0
    pos = " ".join(f"{sym.replace('USDT','')}:{'L' if s['pos']==1 else '·'}"
                   for sym, s in sleeves.items())
    _send("\n".join([
        f"{_tag('daily')}", "*Status Diário — Taker-Cap Paper*", f"`{ts}`",
        f"equity: `${equity:.2f}` | PnL: `{('+' if pnl_total>=0 else '')}${pnl_total:.2f}` "
        f"({pnl_total/C.INITIAL_CAPITAL*100:+.2f}%)",
        f"pos: {pos} | {n_tr}tr WR `{wr:.0f}%` | maxDD: `{max_dd*100:.1f}%`",
    ]))


def notify_dd_alert(max_dd: float, equity: float) -> None:
    _send(f"{_tag('alert')}\n*⚠️ DRAWDOWN {max_dd*100:.1f}%*\n"
          f"equity `${equity:.2f}` (limiar `{C.DD_ALERT_PCT*100:.0f}%` — backtest maxDD `-12%`)\n"
          f"(monitorando — não parado)")


def notify_consec_losses(sym: str, n: int, equity: float) -> None:
    _send(f"{_tag('alert')}\n*{sym}: {n} losses consecutivos*\neq `${equity:.2f}`")


def notify_error(where: str, msg: str) -> None:
    safe = msg.replace("`", "'")[:500]
    _send(f"{_tag('error')}\n*ERROR @ {where}*\n```\n{safe}\n```")
