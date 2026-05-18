"""Telegram notifier for DOW 3-legs paper trader.

All methods are no-ops when TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are not set.
Ported from assets/paper_trade_dow.py (Telegram class).
"""
from __future__ import annotations

import logging
import traceback

import requests

from trading.dow_3legs_paper.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_TAG

log = logging.getLogger("dow_3legs_paper")

_enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _send(text: str) -> None:
    if not _enabled:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("[telegram] send failed: %d %s", r.status_code, r.text[:200])
    except Exception:
        log.warning("[telegram] send error:\n%s", traceback.format_exc())


def notify_startup(capital: float, leverage: float, mode: str, gate_enabled: bool) -> None:
    _send(
        f"#{TELEGRAM_TAG} #startup\n"
        f"*PAPER TRADER ONLINE (v2 3-legs)*\n"
        f"capital: `${capital:.2f}`\n"
        f"leverage: `{leverage}x`\n"
        f"fee_rt: `0.10%`\n"
        f"mode: `{mode}`\n"
        f"atr_gate: `{'ON' if gate_enabled else 'OFF'}`\n"
        f"trigger: Sun/Mon/Tue/Wed/Thu 23:55 UTC"
    )


def notify_open(leg: str, side: str, ts: str, price: float, equity: float,
                gate_status: str) -> None:
    emoji = "🟢" if side == "long" else "🔴"
    _send(
        f"#{TELEGRAM_TAG} #open_{side} #{leg}\n"
        f"*OPEN {side.upper()}* ({leg}) {emoji}\n"
        f"ts: `{ts}` UTC\n"
        f"price: `{price:.2f}`\n"
        f"gate: `{gate_status}`\n"
        f"equity: `${equity:.4f}`"
    )


def notify_close(leg: str, side: str, ts: str, price: float,
                 ret_pct: float, equity: float) -> None:
    emoji = "✅" if ret_pct > 0 else "❌"
    _send(
        f"#{TELEGRAM_TAG} #close_{side} #{leg}\n"
        f"*CLOSE {side.upper()}* ({leg})\n"
        f"ts: `{ts}` UTC\n"
        f"price: `{price:.2f}`\n"
        f"pnl_net: {emoji} `{ret_pct:+.3f}%`\n"
        f"equity: `${equity:.4f}`"
    )


def notify_close_open(ts: str, price: float, ret_pct: float, equity: float) -> None:
    emoji = "✅" if ret_pct > 0 else "❌"
    _send(
        f"#{TELEGRAM_TAG} #close_long #open_short\n"
        f"*CLOSE L_Wed -> OPEN S_Thu*\n"
        f"ts: `{ts}` UTC\n"
        f"price: `{price:.2f}`\n"
        f"L_Wed pnl_net: {emoji} `{ret_pct:+.3f}%`\n"
        f"equity: `${equity:.4f}`"
    )


def notify_skip(ts: str, intended_leg: str, atr_pct: float, p20: float) -> None:
    import math
    atr_str = f"{atr_pct:.3f}" if not math.isnan(atr_pct) else "n/a"
    p20_str = f"{p20:.3f}" if not math.isnan(p20) else "n/a"
    _send(
        f"#{TELEGRAM_TAG} #skip\n"
        f"*SKIP* ({intended_leg}) — vol baixa\n"
        f"ts: `{ts}` UTC\n"
        f"atr%: `{atr_str}` < p20: `{p20_str}`"
    )


def notify_error(where: str, err_msg: str) -> None:
    _send(f"#{TELEGRAM_TAG} #error\n*ERROR @ {where}*\n```\n{err_msg[:500]}\n```")


def notify_stopped() -> None:
    _send(f"#{TELEGRAM_TAG} #stopped\n*PAPER TRADER STOPPED*")
