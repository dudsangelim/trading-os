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

TRADING_API   = "http://127.0.0.1:8091"
BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")
CHAT_ID       = os.environ.get("TRADING_ALLOWED_USERS", "6127917209").split(",")[0].strip()
STATE_FILE    = Path("/tmp/trading_watcher_state.json")
TIMEOUT       = 10

# Shadow filter milestones to notify
SHADOW_MILESTONES = [50, 100, 200, 500]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(path: str) -> dict:
    url = f"{TRADING_API}{path}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


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
