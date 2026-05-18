"""Telegram notifier for EF3 F-A.1.M.f — no-op when env vars not set."""
from __future__ import annotations

import logging
import traceback

import requests

from trading.ef3_fa1mf_paper.config import (
    STRATEGY_ID, SYMBOL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_TAG,
)

log = logging.getLogger("ef3_fa1mf_paper")
_ok = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _send(text: str) -> None:
    if not _ok:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("[tg] send failed %d: %s", r.status_code, r.text[:200])
    except Exception:
        log.warning("[tg] error:\n%s", traceback.format_exc())


def notify_startup(equity: float) -> None:
    _send(
        f"#{TELEGRAM_TAG} #startup\n"
        f"*EF3 FA1Mf PAPER ONLINE*\n"
        f"strategy: `{STRATEGY_ID}`\n"
        f"symbol: `{SYMBOL}` | tf: `H4`\n"
        f"capital: `${equity:.2f}`\n"
        f"mode: `1x no-leverage`\n"
        f"entry: 30\\-bar H4 breakout + SMA200 regime\n"
        f"exit: ATR14x2\\.0 trailing stop"
    )


def notify_signal(ts: str, atr14: float, equity: float) -> None:
    _send(
        f"#{TELEGRAM_TAG} #signal\n"
        f"*SIGNAL DETECTED* — PENDING entry\n"
        f"bar\\_ts: `{ts}` UTC\n"
        f"atr14: `{atr14:.4f}`\n"
        f"equity: `${equity:.2f}`"
    )


def notify_open(ts: str, entry_px: float, atr_signal: float,
                trailing_stop: float, equity: float) -> None:
    _send(
        f"#{TELEGRAM_TAG} #open\\_long\n"
        f"*OPEN LONG* \n"
        f"ts: `{ts}` UTC\n"
        f"entry: `{entry_px:.4f}`\n"
        f"init\\_stop: `{trailing_stop:.4f}`\n"
        f"atr14: `{atr_signal:.4f}`\n"
        f"equity: `${equity:.2f}`"
    )


def notify_close(trade: dict) -> None:
    pnl   = trade["net_ret_bps"]
    emoji = "UP" if pnl >= 0 else "DOWN"
    _send(
        f"#{TELEGRAM_TAG} #close\\_long\n"
        f"*CLOSE LONG* [{emoji}]\n"
        f"entry: `{trade['entry_px']:.4f}` exit: `{trade['exit_px']:.4f}`\n"
        f"type: `{trade['exit_type']}`\n"
        f"net: `{trade['net_ret_bps']:+.2f}bps` | pnl: `${trade['pnl_usd']:+.2f}`\n"
        f"equity: `${trade['equity_after']:.2f}`"
    )


def notify_error(where: str, msg: str) -> None:
    _send(f"#{TELEGRAM_TAG} #error\n*ERROR @ {where}*\n```\n{msg[:500]}\n```")
