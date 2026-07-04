#!/usr/bin/env python3
"""
trading_watcher.py — Monitor proativo do Trading OS.

Roda via cron a cada 10 minutos. Envia notificações Telegram para a Mary
quando eventos relevantes ocorrem:

  - Milestone de shadow filter (50, 100, 200 avaliações)
  - Worker degradado ou offline
  - Derivatives fallback ativo
  - Readiness regrediu para NOT_READY

Estado persistido em: /tmp/trading_watcher_state.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TRADING_API     = "http://127.0.0.1:8091"
NY_OPEN_API     = "http://127.0.0.1:8094"
DOW_3LEGS_API   = "http://127.0.0.1:8096"

# Paper traders monitored via generic check (name, port, health_path, label)
PAPER_TRADERS = [
    ("rsi_reversion_paper",  8093, "/healthz", "RSI Reversion"),
    ("asian_dema_paper",     8095, "/healthz", "Asian DEMA"),
    ("sol_burst_paper",      8101, "/healthz", "SOL Burst"),
    ("btc_lead_paper",       8106, "/healthz", "BTC-Lead ETH 4h"),
    ("taker_cap_paper",      8107, "/healthz", "Taker-Cap H3"),
    ("vrp_paper",            8108, "/healthz", "VRP Straddle"),
]
BOT_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")
CHAT_ID         = os.environ.get("TRADING_ALLOWED_USERS", "6127917209").split(",")[0].strip()
STATE_FILE      = Path("/tmp/trading_watcher_state.json")
TIMEOUT         = 10

# Shadow filter milestones to notify
SHADOW_MILESTONES = [50, 100, 200, 500]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_url(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def _get(path: str) -> dict:
    return _get_url(f"{TRADING_API}{path}")


def _send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
            return resp.get("ok", False)
    except Exception as e:
        print(f"[watcher] telegram send error: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"[watcher] state save error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_shadow_milestones(state: dict, shadow_count: int) -> list[str]:
    """Returns notification texts for newly reached milestones."""
    notified = set(state.get("shadow_milestones_notified", []))
    new_texts = []
    for m in SHADOW_MILESTONES:
        if shadow_count >= m and m not in notified:
            notified.add(m)
            new_texts.append(
                f"🔬 <b>Shadow Filter — Milestone {m} atingido!</b>\n\n"
                f"O shadow filter do Trading OS acumulou <b>{shadow_count} avaliações</b> "
                f"(milestone: {m}).\n\n"
                f"Hora de revisar a correlação de outcomes:\n"
                f"• Mj: <code>derivativos</code> ou <code>shadow</code>\n"
                f"• API: <code>GET /operator/shadow-filter-report</code>"
            )
    state["shadow_milestones_notified"] = sorted(notified)
    return new_texts


def _check_worker_health(state: dict, health: dict) -> list[str]:
    """Returns alert text if worker degraded."""
    if "error" in health:
        prev = state.get("worker_offline_notified", False)
        state["worker_offline_notified"] = True
        if not prev:
            return [
                "🚨 <b>Trading OS — Worker OFFLINE</b>\n\n"
                f"A trading API não está respondendo:\n<code>{health.get('error','?')}</code>\n\n"
                "Verificar: <code>docker ps | grep trading</code>"
            ]
        return []

    state["worker_offline_notified"] = False
    worker = health.get("worker", {})
    w_status = worker.get("status", "UNKNOWN")
    prev_status = state.get("last_worker_status")

    notifs = []
    if w_status not in ("ALIVE", "alive") and prev_status in ("ALIVE", "alive", None):
        notifs.append(
            f"⚠️ <b>Trading OS — Worker {w_status}</b>\n\n"
            f"Status mudou de {prev_status} para {w_status}.\n"
            f"Verificar: <code>docker logs trading_worker --tail=20</code>"
        )
    state["last_worker_status"] = w_status
    return notifs


def _check_derivatives_fallback(state: dict, health: dict) -> list[str]:
    """Alert when derivatives fall back to baseline unexpectedly."""
    if "error" in health:
        return []
    worker = health.get("worker", {})
    runtime = worker.get("runtime_state") or {}
    deriv_mode = runtime.get("derivatives_mode", "")
    fallback = runtime.get("derivatives_fallback_active", False)

    prev_fallback = state.get("derivatives_fallback_active", False)
    state["derivatives_fallback_active"] = fallback

    if fallback and not prev_fallback:
        return [
            "⚠️ <b>Derivatives Fallback Ativo</b>\n\n"
            f"O modo de derivatives mudou para fallback (modo: {deriv_mode}).\n"
            "As features derivativas não estão disponíveis para os engines.\n"
            "Verificar: <code>docker logs derivatives-collector --tail=20</code>"
        ]
    return []


def _check_readiness_regression(state: dict, readiness: dict) -> list[str]:
    """Alert when readiness status regresses to NOT_READY."""
    if "error" in readiness:
        return []
    r_status = readiness.get("current_readiness_status", "")
    prev = state.get("last_readiness_status")
    state["last_readiness_status"] = r_status

    if r_status == "NOT_READY" and prev not in ("NOT_READY", None):
        blocking = readiness.get("blocking_issues", [])
        b_str = "\n".join(f"• {b}" for b in blocking[:5]) if blocking else "—"
        return [
            f"🔴 <b>Trading OS — Readiness: NOT_READY</b>\n\n"
            f"Status regrediu de {prev} para NOT_READY.\n\n"
            f"Bloqueios:\n{b_str}\n\n"
            f"Verificar: <code>mj trading saude</code>"
        ]
    return []


def _check_ny_open_paper(state: dict) -> list[str]:
    """Alert if trading_ny_open_paper healthz is unreachable."""
    health = _get_url(f"{NY_OPEN_API}/healthz")
    was_offline = state.get("ny_open_paper_offline", False)

    if "error" in health:
        state["ny_open_paper_offline"] = True
        if not was_offline:
            return [
                "🚨 <b>NY Open Paper — Container OFFLINE</b>\n\n"
                f"O container <code>trading_ny_open_paper</code> não responde "
                f"em {NY_OPEN_API}/healthz:\n"
                f"<code>{health.get('error', '?')}</code>\n\n"
                "Verificar: <code>docker ps | grep ny_open</code>\n"
                "Restart: <code>docker compose up -d trading-ny-open-paper</code>"
            ]
        return []

    state["ny_open_paper_offline"] = False
    engine_state = health.get("engine_state", "?")
    equity = health.get("equity", "?")
    trades = health.get("processed_trades", "?")
    print(f"[watcher] ny_open_paper: state={engine_state} equity={equity} trades={trades}")
    return []


def _check_dow_3legs_paper(state: dict) -> list[str]:
    """Alert if trading_dow_3legs_paper health is unreachable."""
    health = _get_url(f"{DOW_3LEGS_API}/health")
    was_offline = state.get("dow_3legs_paper_offline", False)

    if "error" in health:
        state["dow_3legs_paper_offline"] = True
        if not was_offline:
            return [
                "🚨 <b>DOW 3-Legs Paper — Container OFFLINE</b>\n\n"
                f"O container <code>trading_dow_3legs_paper</code> não responde "
                f"em {DOW_3LEGS_API}/health:\n"
                f"<code>{health.get('error', '?')}</code>\n\n"
                "Verificar: <code>docker ps | grep dow</code>\n"
                "Restart: <code>docker compose up -d trading-dow-3legs-paper</code>"
            ]
        return []

    state["dow_3legs_paper_offline"] = False
    eng_state = health.get("state", "?")
    equity = health.get("equity", "?")
    n_trades = health.get("n_trades", "?")
    gate = health.get("gate", {})
    print(f"[watcher] dow_3legs_paper: state={eng_state} equity={equity} "
          f"trades={n_trades} gate={gate.get('gate', '?')}")
    return []


def _check_paper_trader(state: dict, container: str, port: int,
                        health_path: str, label: str) -> list[str]:
    """Generic health check for paper trader containers.

    Alerts on: container offline, halted gate triggered.
    """
    url = f"http://127.0.0.1:{port}{health_path}"
    health = _get_url(url)
    state_key_offline = f"{container}_offline"
    state_key_halted  = f"{container}_halted"
    was_offline  = state.get(state_key_offline, False)
    was_halted   = state.get(state_key_halted, False)
    service_name = container.replace("_", "-")
    notifs = []

    if "error" in health:
        state[state_key_offline] = True
        if not was_offline:
            notifs.append(
                f"🚨 <b>{label} — Container OFFLINE</b>\n\n"
                f"<code>trading_{container}</code> não responde em {url}:\n"
                f"<code>{health.get('error', '?')}</code>\n\n"
                f"Restart: <code>docker compose up -d trading-{service_name}</code>"
            )
        return notifs

    state[state_key_offline] = False

    # Phase-0 gate: halted flag
    halted = health.get("halted", False)
    if halted and not was_halted:
        symbol   = health.get("symbol", "?")
        n_trades = health.get("n_trades", "?")
        pnl      = health.get("pnl_total", health.get("pnl_total_bps", "?"))
        notifs.append(
            f"🛑 <b>{label} — Gate HALTED</b>\n\n"
            f"O Phase-0 gate travou o engine <code>trading_{container}</code>.\n"
            f"Symbol: {symbol} | Trades: {n_trades} | PnL: {pnl}\n\n"
            f"Revisar: <code>docker logs trading_{container} --tail=30</code>"
        )
    state[state_key_halted] = halted

    # Log summary
    equity   = health.get("equity", health.get("pnl_total", health.get("pnl_total_bps", "?")))
    n_trades = health.get("n_trades", "?")
    eng_state = health.get("state", "running")
    print(f"[watcher] {container}: state={eng_state} equity/pnl={equity} "
          f"trades={n_trades} halted={halted}")
    return notifs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[watcher] {now} — iniciando verificação")

    state = _load_state()
    notifications: list[str] = []

    # 1. Health check (worker + derivatives)
    health = _get("/system/status")
    notifications.extend(_check_worker_health(state, health))
    notifications.extend(_check_derivatives_fallback(state, health))

    # 2. Readiness check
    readiness = _get("/operator/readiness-report")
    notifications.extend(_check_readiness_regression(state, readiness))

    # 3. Shadow filter milestones
    if "error" not in readiness:
        dr = readiness.get("derivatives_readiness", {})
        shadow_count = dr.get("shadow_filter_evaluated_count", 0)
        print(f"[watcher] shadow_filter_evaluated_count={shadow_count}")
        milestone_notifs = _check_shadow_milestones(state, shadow_count)
        notifications.extend(milestone_notifs)
    else:
        print(f"[watcher] readiness error: {readiness.get('error')}")

    # 4. NY Open paper trader health
    notifications.extend(_check_ny_open_paper(state))

    # 5. DOW 3-legs paper trader health
    notifications.extend(_check_dow_3legs_paper(state))

    # 6. Generic paper traders (RSI, Asian DEMA, BW, SOL Burst, EF3 x2)
    for container, port, health_path, label in PAPER_TRADERS:
        notifications.extend(_check_paper_trader(state, container, port, health_path, label))

    # Send notifications
    for text in notifications:
        print(f"[watcher] sending notification: {text[:80]}…")
        ok = _send_telegram(text)
        print(f"[watcher] telegram: {'ok' if ok else 'FAILED'}")
        time.sleep(0.5)

    if not notifications:
        print("[watcher] tudo ok — nenhuma notificação necessária")

    _save_state(state)


if __name__ == "__main__":
    main()
