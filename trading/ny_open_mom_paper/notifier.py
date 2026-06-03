"""Telegram notifier — no-op when env vars not set."""
from __future__ import annotations

import json
import logging
import urllib.request

from trading.ny_open_mom_paper.config import (
    INITIAL_CAPITAL, LEVERAGE, PHASE0_MIN_TRADES, PHASE0_WR_FLOOR,
    STRATEGY_ID, SYMBOL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_TAG,
)

log = logging.getLogger("ny_open_mom_paper")


def _send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
            if not resp.get("ok"):
                log.warning("[tg] send failed: %s", resp)
    except Exception as exc:
        log.warning("[tg] send error: %s", exc)


def notify_startup(equity: float, state: str) -> None:
    halted = state == "HALTED"
    icon = "🔴" if halted else "🚀"
    extra = "\n<b>⚠️ PHASE 0 HALTED — gate triggered</b>" if halted else ""
    _send(
        f"{icon} <b>[{TELEGRAM_TAG}] NY Open Momentum Long ONLINE</b>{extra}\n"
        f"strategy: <code>{STRATEGY_ID}</code>\n"
        f"symbol: <code>{SYMBOL}</code> | 5m\n"
        f"equity: <code>${equity:.2f}</code> | leverage: <code>{LEVERAGE}x</code>\n"
        f"phase0 gate: WR &lt; {PHASE0_WR_FLOOR*100:.0f}% after {PHASE0_MIN_TRADES} trades → HALT\n"
        f"state: <code>{state}</code>"
    )


def notify_armed(date, high_first: float, low_first: float, range_pct: float,
                 break_level: float, tp1: float, tp2: float, stop: float) -> None:
    _send(
        f"⚔️ <b>[{TELEGRAM_TAG}] Armed</b>\n"
        f"date: <code>{date}</code>\n"
        f"range high: <code>{high_first:.2f}</code> | low: <code>{low_first:.2f}</code> "
        f"({range_pct:.3f}%)\n"
        f"break level: <code>{break_level:.2f}</code>\n"
        f"entry (retest): <code>{high_first:.2f}</code> | stop: <code>{stop:.2f}</code>\n"
        f"TP1: <code>{tp1:.2f}</code> | TP2: <code>{tp2:.2f}</code>"
    )


def notify_break(date, os_close: float, os_thr: float, ttb: float, accepted: bool) -> None:
    icon = "✅" if accepted else "❌"
    verdict = "ACCEPT" if accepted else "REJECT (weak break)"
    _send(
        f"{icon} <b>[{TELEGRAM_TAG}] Break detected — {verdict}</b>\n"
        f"date: <code>{date}</code>\n"
        f"os_close: <code>{os_close:+.3f}%</code> | threshold: <code>{os_thr:.2f}%</code>\n"
        f"time to break: <code>{ttb:.1f} min</code>"
    )


def notify_filled(date, entry: float, stop: float, tp1: float, tp2: float,
                  equity: float) -> None:
    _send(
        f"📌 <b>[{TELEGRAM_TAG}] LONG Filled</b>\n"
        f"date: <code>{date}</code>\n"
        f"entry: <code>{entry:.2f}</code> | stop: <code>{stop:.2f}</code>\n"
        f"TP1: <code>{tp1:.2f}</code> | TP2: <code>{tp2:.2f}</code>\n"
        f"equity: <code>${equity:.2f}</code>"
    )


def notify_tp1(entry: float, tp1: float) -> None:
    _send(
        f"⚡ <b>[{TELEGRAM_TAG}] TP1 + Breakeven armed</b>\n"
        f"partial fill: <code>{tp1:.2f}</code> | stop → BE: <code>{entry:.2f}</code>"
    )


def notify_close(date, entry: float, exit_price: float, exit_reason: str,
                 pnl_pct: float, equity_before: float, equity_after: float,
                 total_trades: int, total_wins: int) -> None:
    icon = "🟢" if pnl_pct > 0 else "🔴"
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    _send(
        f"{icon} <b>[{TELEGRAM_TAG}] LONG Exit</b>\n"
        f"date: <code>{date}</code> | reason: <code>{exit_reason}</code>\n"
        f"entry: <code>{entry:.2f}</code> → exit: <code>{exit_price:.2f}</code>\n"
        f"pnl: <code>{pnl_pct:+.3f}%</code>\n"
        f"equity: <code>${equity_before:.2f}</code> → <code>${equity_after:.2f}</code>\n"
        f"trades: <code>{total_trades}</code> | WR: <code>{wr:.1f}%</code>"
    )


def notify_halted(total_trades: int, total_wins: int) -> None:
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    _send(
        f"🚨 <b>[{TELEGRAM_TAG}] PHASE 0 HALTED</b>\n"
        f"WR {wr:.1f}% &lt; {PHASE0_WR_FLOOR*100:.0f}% after {total_trades} trades\n"
        f"wins: {total_wins} | losses: {total_trades - total_wins}\n"
        f"Container remains running but strategy is frozen. Manual reset required."
    )


def notify_error(where: str, msg: str) -> None:
    _send(
        f"⚠️ <b>[{TELEGRAM_TAG}] ERROR @ {where}</b>\n"
        f"<code>{msg[:500]}</code>"
    )


def notify_shutdown() -> None:
    _send(f"🛑 <b>[{TELEGRAM_TAG}] NY Open Momentum Long STOPPED</b>")
