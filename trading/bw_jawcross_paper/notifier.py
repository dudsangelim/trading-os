"""Telegram notifier — no-op when env vars not set."""
from __future__ import annotations

import logging
import traceback

import requests

from trading.bw_jawcross_paper.config import (
    LEVERAGE, STAKE_PCT, STRATEGY_ID, SYMBOL, TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID, TELEGRAM_TAG, TIMEFRAME,
)

log = logging.getLogger("bw_jawcross_paper")
_ok = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _send(text: str) -> None:
    if not _ok:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("[tg] send failed %d: %s", r.status_code, r.text[:200])
    except Exception:
        log.warning("[tg] error:\n%s", traceback.format_exc())


def notify_startup(equity: float) -> None:
    _send(
        f"#{TELEGRAM_TAG} #startup\n"
        f"*ALLIGATOR PAPER ONLINE*\n"
        f"strategy: `{STRATEGY_ID}`\n"
        f"symbol: `{SYMBOL}` | tf: `{TIMEFRAME}`\n"
        f"capital: `${equity:.2f}`\n"
        f"leverage: `{LEVERAGE}x` | stake: `{STAKE_PCT*100:.0f}%`\n"
        f"notional/trade: `{STAKE_PCT*LEVERAGE*100:.0f}% of equity`\n"
        f"entry: alligator bull order\\_flip long-only\n"
        f"exit: close below lips | no hard SL"
    )


def notify_open(action: str, ts: str, entry_px: float,
                notional: float, jaw: float, lips: float, equity: float) -> None:
    side = "LONG" if "long" in action else "SHORT"
    emoji = "🟢" if side == "LONG" else "🔴"
    _send(
        f"#{TELEGRAM_TAG} #open\\_{side.lower()}\n"
        f"*OPEN {side}* {emoji}\n"
        f"ts: `{ts}` UTC\n"
        f"entry: `{entry_px:.4f}`\n"
        f"notional: `${notional:.2f}`\n"
        f"jaw: `{jaw:.4f}` | lips: `{lips:.4f}`\n"
        f"equity: `${equity:.2f}`"
    )


def notify_close(trade: dict) -> None:
    side  = trade["side"].upper()
    pnl   = trade["pnl_usd"]
    emoji = "✅" if pnl >= 0 else "❌"
    _send(
        f"#{TELEGRAM_TAG} #close\\_{trade['side']}\n"
        f"*CLOSE {side}* {emoji}\n"
        f"entry: `{trade['entry_px']:.4f}` → exit: `{trade['exit_px']:.4f}`\n"
        f"bars held: `{trade['bars_held']}`\n"
        f"net: `{trade['net_ret_pct']:+.3f}%` | pnl: `${pnl:+.2f}`\n"
        f"equity: `${trade['equity_after']:.2f}`"
    )


def notify_error(where: str, msg: str) -> None:
    _send(f"#{TELEGRAM_TAG} #error\n*ERROR @ {where}*\n```\n{msg[:500]}\n```")
