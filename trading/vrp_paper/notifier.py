"""Telegram notifier for the VRP paper trader (no-op without env vars)."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

import requests

from trading.vrp_paper import config as C

log = logging.getLogger("vrp_paper")
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
        "*VRP PAPER ONLINE* (Deribit, paper-only)",
        "straddle ATM semanal short, hedge de delta diário via perp",
        f"capital: `${equity:.2f}` | size: `{C.SIZE_MULT:g}x` vol-scaled (ref DVOL {C.DVOL_REF:.0%})",
        "validado: `2,56%/mês` · PFtrade `1.80` · Sharpe `1.73` · DD `-17.9%` (2022-26, pós-fix)",
    ]))


def notify_open(ev: dict) -> None:
    legs = " + ".join(l["instrument"] for l in ev["legs"])
    _send(f"[{C.TELEGRAM_TAG}] \U0001f7e0 SHORT STRADDLE `{legs}`\n"
          f"S: `${ev['S']:,.0f}` | DVOL: `{ev['dvol']:.0%}` | contratos: `{ev['contracts']:.4f}`\n"
          f"prêmio: `${ev['prem_usd']:.2f}` (`{ev['prem_pct']:.1f}%` do capital) | "
          f"vence: `{ev['expiry']} UTC`")


def notify_close(ev: dict, book) -> None:
    emoji = "✅" if ev["pnl"] >= 0 else "❌"
    sign = "+" if ev["pnl"] >= 0 else ""
    wr = (book.n_wins / book.n_trades * 100) if book.n_trades else 0
    _send(f"[{C.TELEGRAM_TAG}] {emoji} SETTLED `{sign}{ev['ret_pct']:.2f}%` | "
          f"PnL `{sign}${ev['pnl']:.2f}`\n"
          f"settle: `${ev['settle']:,.0f}` ({ev['source']})\n"
          f"eq `${ev['equity']:.2f}` | WR `{wr:.0f}%` ({book.n_wins}/{book.n_trades})")


def notify_daily(equity_mtm: float, book, hedge_info: dict, max_dd: float) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pnl = equity_mtm - C.INITIAL_CAPITAL
    pos = "straddle aberto" if book.position else "flat"
    _send("\n".join([
        f"{_tag('daily')}", "*Status Diário — VRP Paper*", f"`{ts}`",
        f"equity(MtM): `${equity_mtm:.2f}` | PnL: `{('+' if pnl>=0 else '')}${pnl:.2f}` "
        f"({pnl/C.INITIAL_CAPITAL*100:+.2f}%)",
        f"pos: {pos} | hedge: `{hedge_info.get('hedge_qty', 0):+.4f}` BTC/ct | "
        f"{book.n_trades}tr | maxDD `{max_dd*100:.1f}%`",
    ]))


def notify_dd_alert(max_dd: float, equity: float) -> None:
    _send(f"{_tag('alert')}\n*⚠️ DRAWDOWN {max_dd*100:.1f}%*\n"
          f"equity `${equity:.2f}` (limiar `{C.DD_ALERT_PCT*100:.0f}%`)\n(monitorando — não parado)")


def notify_consec_losses(n: int, equity: float) -> None:
    _send(f"{_tag('alert')}\n*{n} semanas perdedoras consecutivas*\neq `${equity:.2f}`")


def notify_error(where: str, msg: str) -> None:
    safe = msg.replace("`", "'")[:500]
    _send(f"{_tag('error')}\n*ERROR @ {where}*\n```\n{safe}\n```")
