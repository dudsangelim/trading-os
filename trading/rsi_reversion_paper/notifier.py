"""R021-A C1 RSI Reversion paper trader — Telegram notifications."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict

log = logging.getLogger(__name__)

_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "") or \
             os.environ.get("TRADING_ALLOWED_USERS", "").split(",")[0].strip()
_TAG = "RSI-REV"


def _send(text: str) -> None:
    if not _BOT_TOKEN or not _CHAT_ID:
        log.debug("[notifier] no token/chat — skipping: %s", text[:60])
        return
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": _CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning("[notifier] send failed: %s", e)


def startup(symbol: str, notional: float) -> None:
    _send(
        f"🟡 <b>[{_TAG}] Iniciando</b>\n"
        f"R021-A C1 RSI Reversion (SOLUSDT 5m) online\n"
        f"Notional: ${notional:.0f} | Fee: 13bps RT\n"
        f"Entry: RSI&lt;10 (LONG) / RSI&gt;85 (SHORT) → Exit: RSI=50"
    )


def open_position(side: str, entry_px: float, rsi_val: float, bar_ts: str) -> None:
    icon = "🟢" if side == "long" else "🔴"
    _send(
        f"{icon} <b>[{_TAG}] {side.upper()} aberto</b>\n"
        f"Entry: ${entry_px:.4f} | RSI: {rsi_val:.1f}\n"
        f"Bar: {bar_ts[:16]}"
    )


def close_position(side: str, entry_px: float, exit_px: float,
                   net_ret: float, pnl_usd: float, pnl_total: float,
                   rsi_exit: float, bar_ts: str) -> None:
    icon = "✅" if pnl_usd >= 0 else "❌"
    sign = "+" if net_ret >= 0 else ""
    _send(
        f"{icon} <b>[{_TAG}] {side.upper()} fechado</b> {sign}{net_ret*100:.2f}%\n"
        f"Entry: ${entry_px:.4f} → Exit: ${exit_px:.4f}\n"
        f"PnL: ${pnl_usd:+.2f} | Total: ${pnl_total:+.2f}\n"
        f"RSI saída: {rsi_exit:.1f} | Bar: {bar_ts[:16]}"
    )


def daily_status(n_trades: int, pnl_total: float, wr: float, position: str) -> None:
    _send(
        f"📊 <b>[{_TAG}] Status diário</b>\n"
        f"Trades: {n_trades} | WR: {wr*100:.1f}%\n"
        f"PnL total: ${pnl_total:+.2f}\n"
        f"Posição: {position}"
    )


def phase0_halt(n_trades: int, wr: float) -> None:
    _send(
        f"⛔ <b>[{_TAG}] Phase 0 HALT</b>\n"
        f"WR={wr*100:.1f}% após {n_trades} trades está abaixo do limiar.\n"
        f"Novas entradas suspensas. Verificar edge."
    )


def consec_loss_alert(count: int, pnl_total: float) -> None:
    _send(
        f"⚠️ <b>[{_TAG}] {count} losses consecutivos</b>\n"
        f"PnL total: ${pnl_total:+.2f}\n"
        f"Monitorar de perto."
    )


def error_alert(msg: str) -> None:
    _send(f"🚨 <b>[{_TAG}] Erro</b>\n<code>{msg[:300]}</code>")
