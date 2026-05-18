#!/usr/bin/env python3
"""
Checkpoint 7d — Paper Trade 2C v2
Computa métricas e envia relatório via Telegram.

Uso: python3 scripts/paper_2c_checkpoint.py [--days N]
"""
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

# ── config ─────────────────────────────────────────────────────────────────
BASE = Path("/home/agent/trading-os/trading/ny_open_paper")
DATA_DIR = BASE / "data"
LOG_FILE  = BASE / "logs/run.log"
HEALTHZ_URL = "http://127.0.0.1:8094/healthz"
INITIAL_CAPITAL = 100.0
TELEGRAM_BOT_TOKEN = "8541911949:AAHhrPDe-cuVtr8R3Q_k2K9lzR3uwgJZ2YI"
TELEGRAM_CHAT_ID   = "6127917209"
CHECKPOINT_LABEL   = "7d"   # alterar em 28/05 para "30d"


# ── helpers ─────────────────────────────────────────────────────────────────
def telegram(text: str) -> None:
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
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"[telegram] erro: {e}", file=sys.stderr)


def safe_pf(wins, losses):
    w = wins.sum()
    l = abs(losses.sum())
    return f"{w/l:.2f}" if l > 0 else "∞"


def kill_container():
    subprocess.run(
        ["docker", "compose", "-f", "/home/agent/trading-os/docker-compose.yml",
         "stop", "trading-ny-open-paper"],
        capture_output=True,
    )


# ── main ────────────────────────────────────────────────────────────────────
def main(window_days: int = 7):
    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(days=window_days)

    report_lines = [
        f"📊 <b>NY Open 2C v2 — Checkpoint {CHECKPOINT_LABEL}</b>",
        f"🕐 {now_utc.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    # ── 1. state.json ────────────────────────────────────────────────────
    state_path = DATA_DIR / "state.json"
    try:
        state = json.loads(state_path.read_text())
        equity = float(state.get("equity", INITIAL_CAPITAL))
    except Exception as e:
        equity = INITIAL_CAPITAL
        report_lines.append(f"⚠️ state.json não lido: {e}")

    delta_pct = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    delta_icon = "🟢" if delta_pct >= 0 else "🔴"
    report_lines.append(
        f"{delta_icon} <b>Equity:</b> ${equity:.2f}  ({delta_pct:+.2f}% vs $100)"
    )

    # ── 2. healthz ───────────────────────────────────────────────────────
    try:
        with urllib.request.urlopen(HEALTHZ_URL, timeout=5) as r:
            hdata = json.loads(r.read().decode())
        model_v   = hdata.get("model_version", "?")
        eng_state = hdata.get("engine_state", "?")
        uptime_s  = hdata.get("uptime_sec", 0)
        report_lines.append(
            f"🏥 <b>Health:</b> {model_v} | state={eng_state} | uptime={uptime_s//3600}h"
        )
    except Exception as e:
        report_lines.append(f"⚠️ healthz falhou: {e}")
        model_v = "?"

    # ── 3. trades.csv ────────────────────────────────────────────────────
    trades_path = DATA_DIR / "trades.csv"
    max_dd_pct  = 0.0
    max_dd_date = "—"
    n_trades    = 0

    try:
        df = pd.read_csv(trades_path)
        df["ts_exit"] = pd.to_datetime(df["ts_exit"], utc=True)
        recent = df[df["ts_exit"] >= cutoff].copy()
        n_trades = len(recent)

        # equity curve para DD máx (usa equity_after de todas as trades desde início)
        if len(df) > 0:
            eq_series = pd.concat([
                pd.Series([INITIAL_CAPITAL]),
                df["equity_after"],
            ]).reset_index(drop=True)
            rolling_max = eq_series.cummax()
            dd_series   = (eq_series - rolling_max) / rolling_max * 100
            min_idx     = dd_series.idxmin()
            max_dd_pct  = float(dd_series[min_idx])
            # data do DD máx
            if min_idx > 0 and "ts_exit" in df.columns:
                try:
                    max_dd_date = str(df.iloc[min_idx - 1]["ts_exit"])[:10]
                except Exception:
                    max_dd_date = f"idx={min_idx}"

        report_lines.append(f"\n📈 <b>Trades ({window_days}d):</b> {n_trades}")

        if n_trades == 0:
            report_lines.append("  ⚠️ 0 trades — checar ctx, modelo rejeitando tudo ou armed nunca filling")
        else:
            pnl = recent["pnl_pct_net"]
            wins   = pnl[pnl > 0]
            losses = pnl[pnl < 0]
            wr  = (pnl > 0).mean()
            pf  = safe_pf(wins, losses)
            if n_trades >= 5:
                report_lines.append(f"  WR={wr:.0%}  PF={pf}")
            else:
                report_lines.append(f"  WR={wr:.0%}  PF={pf}  (amostra pequena — n={n_trades})")

    except Exception as e:
        report_lines.append(f"⚠️ trades.csv erro: {e}")

    dd_icon = "🔴" if max_dd_pct <= -15 else ("🟡" if max_dd_pct <= -10 else "🟢")
    report_lines.append(f"{dd_icon} <b>DD máx:</b> {max_dd_pct:.2f}%  ({max_dd_date})")

    # ── 4. decisions.csv — fill rate ─────────────────────────────────────
    decisions_path = DATA_DIR / "decisions.csv"
    try:
        dec = pd.read_csv(decisions_path)
        dec["ts"] = pd.to_datetime(dec["ts"], utc=True)
        recent_dec = dec[dec["ts"] >= cutoff]
        n_armed  = (recent_dec["decision"] == "armed").sum()
        n_filled = (recent_dec["decision"] == "filled").sum()
        if n_armed > 0:
            fill_rate = n_filled / n_armed * 100
            fr_icon = "🟢" if fill_rate >= 70 else "🟡"
            report_lines.append(
                f"{fr_icon} <b>Fill rate:</b> {fill_rate:.0f}%  "
                f"(armed={n_armed} filled={n_filled})"
            )
        else:
            report_lines.append("  Fill rate: sem setups armados no período")
    except Exception as e:
        report_lines.append(f"⚠️ decisions.csv erro: {e}")

    # ── 5. últimas 5 linhas do log ───────────────────────────────────────
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()
        tail  = "\n".join(lines[-5:]) if lines else "(vazio)"
        report_lines.append(f"\n📋 <b>Log (tail 5):</b>\n<code>{tail}</code>")
    except Exception as e:
        report_lines.append(f"⚠️ log não lido: {e}")

    # ── 6. avaliação e gatilhos ──────────────────────────────────────────
    report_lines.append("\n<b>Avaliação:</b>")

    triggered_kill = False
    if max_dd_pct <= -25:
        report_lines.append("🚨 DD > 25% → KILL container acionado")
        triggered_kill = True
    elif max_dd_pct <= -15:
        report_lines.append("⚠️ DD > 15% → ALERTA, container mantido")

    if equity > 108:
        report_lines.append("🎯 Equity > $108 — ótimo desempenho, continuar")
    elif equity >= 85:
        report_lines.append("✅ Zona de variabilidade normal ($85-$108)")
    else:
        report_lines.append("⚠️ Equity < $85 — alerta amarelo, investigar trades")

    report_lines.append(
        "\n📅 <b>Próximo checkpoint pleno:</b> 28/05 (30d)\n"
        "⚠️ NÃO escalar leverage/capital antes de 28/05."
    )

    # ── 7. enviar Telegram ───────────────────────────────────────────────
    message = "\n".join(report_lines)
    print(message)
    telegram(message)

    # kill container se DD > 25%
    if triggered_kill:
        kill_container()
        telegram(
            "🚨 <b>KILL ACIONADO — NY Open 2C</b>\n"
            f"DD máx atingiu {max_dd_pct:.2f}% (limite: 25%)\n"
            "Container parado. Revisar antes de religar."
        )

    print(f"\n[checkpoint] concluído — equity=${equity:.2f}  DD_máx={max_dd_pct:.2f}%  trades={n_trades}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    main(args.days)
