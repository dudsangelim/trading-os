"""
Notifier Telegram simples via HTTP API direta. Sem dependencia extra alem de requests.
"""
from __future__ import annotations
import json
import logging
import urllib.request

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token) and bool(chat_id)
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, text: str, silent: bool = False):
        if not self.enabled:
            log.info(f"(telegram disabled) {text}")
            return
        try:
            req = urllib.request.Request(
                self.url,
                data=json.dumps({
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_notification": silent,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                if getattr(r, 'status', 200) != 200:
                    log.warning(f"Telegram API {r.status}")
        except Exception as e:
            log.exception(f"Telegram send failed: {e}")

    # Helpers para os tipos de evento
    def range_computed(self, asset: str, hi: float, lo: float, bps: float, week: str):
        self.send(
            f"📐 *{asset}* range armado (semana {week})\n"
            f"hi = `{hi:g}`\nlo = `{lo:g}`\nrange = {bps:.0f} bps",
            silent=True)

    def entered(self, asset: str, side: str, entry: float, sl: float, tp: float):
        arrow = "📈" if side == "long" else "📉"
        self.send(
            f"{arrow} *{asset}* ENTRY {side.upper()}\n"
            f"entry=`{entry:g}`  sl=`{sl:g}`  tp=`{tp:g}`")

    def exited(self, asset: str, side: str, entry: float, exit_price: float,
               exit_reason: str, pnl_net_bps: float):
        emoji = "✅" if pnl_net_bps > 0 else "❌"
        self.send(
            f"{emoji} *{asset}* EXIT ({exit_reason})\n"
            f"entry=`{entry:g}`  exit=`{exit_price:g}`  side={side}\n"
            f"PnL = {pnl_net_bps:+.1f} bps")

    def cancelled(self, asset: str, reason: str):
        self.send(f"⏭ *{asset}* skip: {reason}", silent=True)

    def heartbeat(self, summary: str):
        self.send(f"💓 heartbeat\n{summary}", silent=True)
