"""R012 SOL Burst paper trader — Telegram notifications."""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger(__name__)

_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "") or \
             os.environ.get("TRADING_ALLOWED_USERS", "").split(",")[0].strip()
_TAG = "SOL-BURST"


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
        f"R012 SOL Extreme Burst Reversal (5m) online\n"
        f"Notional: ${notional:.0f} | Fee: 13bps RT\n"
        f"Signal: |ret_5m|&gt;2% → fade | TP=0.5% SL=0.25% Timeout=30m"
    )


def signal_detected(direction: str, ret_pct: float, anchor: float, bar_ts: str) -> None:
    _send(
        f"🔔 <b>[{_TAG}] Sinal detectado</b> {direction}\n"
        f"ret_5m: {ret_pct:+.2f}% | anchor: ${anchor:.4f}\n"
        f"Bar: {bar_ts[:16]} — aguardando entry no próximo bar"
    )


def open_position(direction: str, entry_px: float, slippage_bps: float, ts_entry: str) -> None:
    icon = "🟢" if direction == "LONG" else "🔴"
    _send(
        f"{icon} <b>[{_TAG}] {direction} aberto</b>\n"
        f"Entry: ${entry_px:.4f} | Slippage: {slippage_bps:+.1f} bps\n"
        f"Time: {ts_entry[:16]}"
    )


def close_position(direction: str, entry_px: float, exit_px: float,
                   outcome: str, pnl_bps: float, pnl_total_bps: float,
                   ts_close: str) -> None:
    icon = "✅" if pnl_bps >= 0 else "❌"
    _send(
        f"{icon} <b>[{_TAG}] {direction} fechado</b> ({outcome})\n"
        f"Entry: ${entry_px:.4f} → Exit: ${exit_px:.4f}\n"
        f"PnL: {pnl_bps:+.1f} bps | Total: {pnl_total_bps:+.1f} bps\n"
        f"Time: {ts_close[:16]}"
    )


def daily_status(n_trades: int, pnl_total_bps: float, wr: float, position: str) -> None:
    _send(
        f"📊 <b>[{_TAG}] Status diário</b>\n"
        f"Trades: {n_trades} | WR: {wr*100:.1f}%\n"
        f"PnL total: {pnl_total_bps:+.1f} bps\n"
        f"Posição: {position}"
    )


def phase0_halt(n_trades: int, wr: float) -> None:
    _send(
        f"⛔ <b>[{_TAG}] Phase 0 HALT</b>\n"
        f"WR={wr*100:.1f}% após {n_trades} trades está abaixo do gate (42%).\n"
        f"Novas entradas suspensas. Verificar edge."
    )


def consec_loss_alert(count: int, pnl_total_bps: float) -> None:
    _send(
        f"⚠️ <b>[{_TAG}] {count} losses consecutivos</b>\n"
        f"PnL total: {pnl_total_bps:+.1f} bps\n"
        f"Monitorar de perto."
    )


def error_alert(msg: str) -> None:
    _send(f"🚨 <b>[{_TAG}] Erro</b>\n<code>{msg[:300]}</code>")
