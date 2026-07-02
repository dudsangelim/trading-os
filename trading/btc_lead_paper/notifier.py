"""Telegram notifier for the btc_lead paper trader.
No-op when env vars are unset (mirrors the other paper traders)."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

import requests

from trading.btc_lead_paper import config as C

log = logging.getLogger("btc_lead_paper")
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
        "*BTC-LEAD PAPER ONLINE* (SIGNAL\\_ONLY)",
        f"trade: `{C.TRADE_SYMBOL}` 4h | sinal: `{C.SIGNAL_SYMBOL}` "
        f"roc(`{C.ROC_N}`)+ema(`{C.EMA_N}`) | lev: `{C.LEVERAGE:g}x`",
        f"capital: `${equity:.2f}` | short: `{'on' if C.ALLOW_SHORT else 'off'}`",
        f"costs: `{C.FEE_BPS:g}+{C.SLIP_BPS:g}bps/lado` | funding: `{'on' if C.FUNDING_ENABLED else 'off'}`",
        "validado: PF `1.53` · `4,9%/mês` · DD `-33%` (2023+, engine corrigido)",
    ]))


def notify_open(ev: dict, bar_ts: str) -> None:
    emoji = "\U0001f7e2" if ev["side"] == "long" else "\U0001f534"
    _send(f"[{C.TELEGRAM_TAG}] {emoji} {ev['side'].upper()} `{ev['symbol']}` 4h\n"
          f"Entry: `${ev['px']:g}` | Qty: `{ev['qty']:g}` | Notional: `${ev['notional']:.2f}`\n"
          f"Bar: `{bar_ts} UTC` | fee: `${ev['fee']:.4f}`")


def notify_close(ev: dict, pnl_total: float, n_trades: int, n_wins: int) -> None:
    emoji = "✅" if ev["pnl"] >= 0 else "❌"
    sign = "+" if ev["pnl"] >= 0 else ""
    wr = (n_wins / n_trades * 100) if n_trades else 0
    _send(f"[{C.TELEGRAM_TAG}] {emoji} Fechado {ev['side'].upper()} `{ev['symbol']}` "
          f"`{sign}{ev['pnl_pct']:.2f}%` | PnL: `{sign}${ev['pnl']:.2f}` ({ev['reason']})\n"
          f"Entry `${ev['entry_px']:g}` → Exit `${ev['exit_px']:g}` | {ev['bars_held']} barras\n"
          f"funding `${ev['funding']:.4f}` | eq `${ev['equity']:.2f}`\n"
          f"PnL total `{('+' if pnl_total>=0 else '')}${pnl_total:.2f}` | WR `{wr:.0f}%` ({n_wins}/{n_trades})")


def notify_daily(equity: float, pnl_total: float, sleeve: dict, max_dd: float) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pos = {1: "LONG", -1: "SHORT", 0: "flat"}[sleeve["pos"]]
    wr = (sleeve["n_wins"] / sleeve["n_trades"] * 100) if sleeve["n_trades"] else 0
    _send("\n".join([
        f"{_tag('daily')}", "*Status Diário — BTC-Lead Paper*", f"`{ts}`",
        f"equity: `${equity:.2f}` | PnL: `{('+' if pnl_total>=0 else '')}${pnl_total:.2f}` "
        f"({pnl_total/C.INITIAL_CAPITAL*100:+.2f}%)",
        f"pos: {pos} | {sleeve['n_trades']}tr WR `{wr:.0f}%` | maxDD: `{max_dd*100:.1f}%`",
    ]))


def notify_dd_alert(max_dd: float, equity: float) -> None:
    _send(f"{_tag('alert')}\n*⚠️ DRAWDOWN {max_dd*100:.1f}%*\n"
          f"equity `${equity:.2f}` (limiar `{C.DD_ALERT_PCT*100:.0f}%`)\n(monitorando — não parado)")


def notify_consec_losses(n: int, equity: float) -> None:
    _send(f"{_tag('alert')}\n*{C.TRADE_SYMBOL}: {n} losses consecutivos*\neq `${equity:.2f}`")


def notify_error(where: str, msg: str) -> None:
    safe = msg.replace("`", "'")[:500]
    _send(f"{_tag('error')}\n*ERROR @ {where}*\n```\n{safe}\n```")
