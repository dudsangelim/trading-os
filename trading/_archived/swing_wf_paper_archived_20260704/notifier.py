"""Telegram notifier for the swing walk-forward paper trader.
No-op when env vars are unset (mirrors the other paper traders)."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

import requests

from trading.swing_wf_paper import config as C

log = logging.getLogger("swing_wf_paper")
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


def notify_startup(equity_total: float, sleeves: dict) -> None:
    lines = [f"{_tag('startup')}",
             "*SWING WALK-FORWARD PAPER ONLINE*",
             f"univ: `{'/'.join(C.SYMBOLS)}` | tf: `4h` | lev: `{C.LEVERAGE:g}x`",
             f"capital: `${equity_total:.2f}` ({len(C.SYMBOLS)}× `${C.SLEEVE_CAPITAL:.2f}`)",
             f"costs: `{C.FEE_BPS:g}+{C.SLIP_BPS:g}bps/lado` | funding: `{'on' if C.FUNDING_ENABLED else 'off'}` | order: `{C.ORDER_TYPE}`",
             f"reopt: cada `{C.REOPT_BARS}` barras (~90d) | treino `{C.TRAIN_BARS}` barras"]
    _send("\n".join(lines))


def notify_reopt(picks: dict) -> None:
    lines = [f"{_tag('reopt')}", "*Walk-forward re-otimização*"]
    for sym, cfg in picks.items():
        if cfg:
            # wrap dynamic strings in backticks so underscores (ema_cross,
            # adx_min, …) are literal and don't break Markdown parsing
            lines.append(f"`{sym}`: `{cfg['strat']}` `{cfg['params']}` "
                         f"short=`{cfg['short']}` (Sharpe `{cfg['train_sharpe']}`, "
                         f"PF `{cfg['train_pf']}`, {cfg['train_trades']}tr)")
        else:
            lines.append(f"`{sym}`: nenhuma config qualificada → FLAT")
    _send("\n".join(lines))


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
          f"funding `${ev['funding']:.4f}` | sleeve eq `${ev['equity']:.2f}`\n"
          f"PnL total `{('+' if pnl_total>=0 else '')}${pnl_total:.2f}` | WR `{wr:.0f}%` ({n_wins}/{n_trades})")


def notify_daily(equity_total: float, pnl_total: float, sleeves: dict,
                 max_dd: float) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"{_tag('daily')}", "*Status Diário — Swing WF Paper*", f"`{ts}`",
             f"equity: `${equity_total:.2f}` | PnL: `{('+' if pnl_total>=0 else '')}${pnl_total:.2f}` "
             f"({pnl_total/C.INITIAL_CAPITAL*100:+.2f}%)",
             f"maxDD: `{max_dd*100:.1f}%`"]
    for sym, s in sleeves.items():
        pos = {1: "LONG", -1: "SHORT", 0: "flat"}[s["pos"]]
        lines.append(f"`{sym}`: {pos} | eq `${s['equity']:.2f}` | "
                     f"{s['n_trades']}tr WR `{(s['n_wins']/s['n_trades']*100) if s['n_trades'] else 0:.0f}%`")
    _send("\n".join(lines))


def notify_dd_alert(max_dd: float, equity_total: float) -> None:
    _send(f"{_tag('alert')}\n*⚠️ DRAWDOWN {max_dd*100:.1f}%*\n"
          f"equity `${equity_total:.2f}` (limiar `{C.DD_ALERT_PCT*100:.0f}%`)\n(monitorando — não parado)")


def notify_consec_losses(symbol: str, n: int, equity: float) -> None:
    _send(f"{_tag('alert')}\n*{symbol}: {n} losses consecutivos*\nsleeve eq `${equity:.2f}`")


def notify_error(where: str, msg: str) -> None:
    safe = msg.replace("`", "'")[:500]   # backticks would break the code fence
    _send(f"{_tag('error')}\n*ERROR @ {where}*\n```\n{safe}\n```")
