"""R021-B Telegram notifier — no-op when env vars not set."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

import requests

from trading.asian_dema_paper.config import (
    NOTIONAL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_TAG,
)

log = logging.getLogger("asian_dema_paper")
_ok = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _send(text: str) -> None:
    if not _ok:
        log.debug("[tg] no-op (env vars not set): %s", text[:80])
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


def notify_startup(pnl_total: float, n_trades: int) -> None:
    from trading.asian_dema_paper.config import SYMBOL, DEMA_FAST, DEMA_SLOW, SLOPE_BARS, FEE_RT
    fee_bps = round(FEE_RT * 10_000)
    _send(
        f"#{TELEGRAM_TAG} #startup\n"
        f"*R021-B ASIAN DEMA PAPER ONLINE*\n"
        f"symbol: `{SYMBOL}` | tf: `1h`\n"
        f"session: `Asian 00:00–07:59 UTC`\n"
        f"dema: `{DEMA_FAST}/{DEMA_SLOW}` | slope: `{SLOPE_BARS} bars`\n"
        f"notional/trade: `${NOTIONAL:.0f}` | fee: `{fee_bps}bps RT`\n"
        f"pnl\\_total: `${pnl_total:+.2f}` | trades: `{n_trades}`"
    )


def notify_open(side: str, bar_ts: str, entry_px: float,
                dema_fast: float, dema_slow: float) -> None:
    emoji = "\U0001f7e2" if side == "long" else "\U0001f534"
    side_upper = side.upper()
    _send(
        f"[R021-B] {emoji} {side_upper} SOLUSDT 1h\n"
        f"Entry: `${entry_px:.2f}` | Bar: `{bar_ts} UTC`\n"
        f"DEMA 13/34 Asian crossover\n"
        f"DEMA\\_fast: `{dema_fast:.4f}` | DEMA\\_slow: `{dema_slow:.4f}`"
    )


def notify_close(side: str, entry_px: float, exit_px: float,
                 pnl_usd: float, pnl_pct: float, duration_h: int,
                 pnl_total: float, n_trades: int, n_wins: int) -> None:
    emoji = "✅" if pnl_usd >= 0 else "❌"
    sign  = "+" if pnl_usd >= 0 else ""
    wr    = (n_wins / n_trades * 100) if n_trades > 0 else 0.0
    _send(
        f"[R021-B] {emoji} Fechado {side.upper()} `{sign}{pnl_pct:.1f}%` | PnL: `{sign}${pnl_usd:.2f}`\n"
        f"Duração: `{duration_h}h` | Entry: `${entry_px:.2f}` Exit: `${exit_px:.2f}`\n"
        f"Acumulado: `${pnl_total:+.2f}` | WR: `{wr:.0f}%` ({n_wins}/{n_trades})"
    )


def notify_daily_status(position: str | None, pnl_total: float,
                        n_trades: int, n_wins: int) -> None:
    wr = (n_wins / n_trades * 100) if n_trades > 0 else 0.0
    pos_str = position if position else "flat"
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(
        f"#{TELEGRAM_TAG} #daily\n"
        f"*R021-B Status Diário*\n"
        f"`{ts_now}`\n"
        f"posição: `{pos_str}`\n"
        f"pnl\\_total: `${pnl_total:+.2f}`\n"
        f"trades: `{n_trades}` | wins: `{n_wins}` | WR: `{wr:.1f}%`"
    )


def notify_phase0_halt(n_trades: int, wr: float) -> None:
    _send(
        f"#{TELEGRAM_TAG} #HALT\n"
        f"*R021-B PHASE 0 GATE — PARADO*\n"
        f"WR < 22% após {n_trades} trades\n"
        f"WR atual: `{wr*100:.1f}%`\n"
        f"Nenhum novo trade será aceito."
    )


def notify_consec_losses(n_consec: int, pnl_total: float) -> None:
    _send(
        f"#{TELEGRAM_TAG} #alert\n"
        f"*R021-B {n_consec} losses consecutivos*\n"
        f"pnl\\_total: `${pnl_total:+.2f}`\n"
        f"(monitorando — não parado)"
    )


def notify_error(where: str, msg: str) -> None:
    _send(
        f"#{TELEGRAM_TAG} #error\n"
        f"*ERROR @ {where}*\n"
        f"```\n{msg[:500]}\n```"
    )
