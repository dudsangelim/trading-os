"""
Telegram alert sender for the trading system.
Uses TELEGRAM_BOT_TOKEN and ALLOWED_USERS from settings.
Sends to ALL configured users.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..config.settings import TELEGRAM_BOT_TOKEN, ALLOWED_USERS

logger = logging.getLogger(__name__)


async def send_alert(message: str, parse_mode: str = "HTML") -> None:
    """Send ``message`` to every user in ALLOWED_USERS."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — alert suppressed")
        return
    if not ALLOWED_USERS:
        logger.warning("ALLOWED_USERS empty — alert suppressed")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for user_id in ALLOWED_USERS:
            try:
                await client.post(
                    url,
                    json={"chat_id": user_id, "text": message, "parse_mode": parse_mode},
                )
            except Exception as exc:
                logger.error("Failed to send Telegram alert to %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Alert templates
# ---------------------------------------------------------------------------

def fmt_signal_accepted(
    engine_id: str,
    symbol: str,
    direction: str,
    timeframe: str,
    bar_ts: str,
    action: str,
    stake_usd: float,
) -> str:
    return (
        f"📶 <b>SINAL ACEITO</b>\n"
        f"Motor: {engine_id} | {symbol} | {direction} | {timeframe}\n"
        f"Bar: {bar_ts}\n"
        f"Decisão: {action} | stake=${stake_usd:.2f}"
    )


def fmt_trade_opened(
    engine_id: str,
    symbol: str,
    direction: str,
    entry_price: float,
    stake_usd: float,
    stop_price: float,
    target_price: float,
    timeout_bars: int,
    timeframe: str,
) -> str:
    return (
        f"📈 <b>TRADE ABERTO</b>\n"
        f"Motor: {engine_id} | {symbol} | {direction}\n"
        f"Entrada: ${entry_price:,.2f} | Stake: ${stake_usd:.2f}\n"
        f"Stop: ${stop_price:,.2f} | Target: ${target_price:,.2f}\n"
        f"Timeout: {timeout_bars} barras ({timeframe})"
    )


def fmt_trade_closed(
    engine_id: str,
    symbol: str,
    direction: str,
    exit_price: float,
    close_reason: str,
    pnl_usd: float,
    pnl_pct: float,
    bankroll: float,
) -> str:
    sign = "+" if pnl_usd >= 0 else ""
    reason_map = {
        "CLOSED_SL": "STOP",
        "CLOSED_TP": "TARGET",
        "CLOSED_TIMEOUT": "TIMEOUT",
    }
    reason_display = reason_map.get(close_reason, close_reason)
    return (
        f"📉 <b>TRADE FECHADO</b>\n"
        f"Motor: {engine_id} | {symbol} | {direction}\n"
        f"Saída: ${exit_price:,.2f} | Motivo: {reason_display}\n"
        f"PnL: {sign}${pnl_usd:.2f} ({sign}{pnl_pct*100:.1f}%) | "
        f"Bankroll: ${bankroll:.2f}"
    )


def fmt_signal_decision(
    engine_id: str,
    symbol: str,
    direction: str,
    action: str,
    reason: str,
) -> str:
    reason_text = reason.strip() if reason else "—"
    return (
        f"🧭 <b>SINAL / GOVERNOR</b>\n"
        f"Motor: {engine_id} | {symbol} | {direction}\n"
        f"Decisão: {action}\n"
        f"Motivo: {reason_text}"
    )


def fmt_critical_error(engine_id: str, description: str) -> str:
    return (
        f"⚠️ <b>ERRO CRÍTICO</b>\n"
        f"Motor: {engine_id} | {description}\n"
        f"Ação: motor pausado"
    )


def fmt_stale_data(symbol: str, minutes: float) -> str:
    return (
        f"🚨 <b>STALE DATA</b>\n"
        f"el_candles_1m ({symbol}) sem atualização há {minutes:.0f}min\n"
        f"Todos os motores bloqueados"
    )


def fmt_daily_dd_limit(engine_id: str, dd_pct: float) -> str:
    return (
        f"🔴 <b>DD LIMIT</b>\n"
        f"Motor: {engine_id} | DD diário: {dd_pct*100:.1f}%\n"
        f"Motor pausado até 00:00 UTC"
    )


def fmt_overlay_divergence(
    engine_id: str,
    symbol: str,
    base_action: str,
    overlay_action: str,
    comparison_label: str,
    reason: str,
) -> str:
    return (
        f"🧠 <b>OVERLAY LIQUIDEZ</b>\n"
        f"Motor: {engine_id} | {symbol}\n"
        f"Base: {base_action} | Overlay: {overlay_action}\n"
        f"Delta: {comparison_label}\n"
        f"Motivo: {reason or '—'}"
    )
