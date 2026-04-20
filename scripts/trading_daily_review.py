#!/usr/bin/env python3
"""
trading_daily_review.py — Daily performance review do Trading OS.

Roda via cron 1x por dia (07:00 UTC = 04:00 BRT).
Analisa os últimos N dias de trades e envia report consolidado via Telegram.

Seções do report:
  1. Resumo geral — bankroll, PnL, win rate
  2. Performance por engine — quem ganha, quem sangra
  3. Custo real — fees + slippage efetivo vs teórico
  4. Skip analysis — sinais ignorados que teriam sido winners
  5. Exposição cruzada — engines no mesmo par ao mesmo tempo
  6. Alertas — engines com drawdown, streaks negativas, inatividade

Acessa diretamente o PostgreSQL do trading (jarvis DB).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_HOST = os.environ.get("TRADING_DB_HOST", "172.18.0.2")
DB_PORT = int(os.environ.get("TRADING_DB_PORT", "5432"))
DB_NAME = os.environ.get("TRADING_DB_NAME", "jarvis")
DB_USER = os.environ.get("TRADING_DB_USER", "jarvis")
DB_PASS = os.environ.get("TRADING_DB_PASS", "jarvispass")

DERIV_DB_HOST = os.environ.get("DERIV_DB_HOST", "127.0.0.1")
DERIV_DB_PORT = int(os.environ.get("DERIV_DB_PORT", "5433"))
DERIV_DB_NAME = os.environ.get("DERIV_DB_NAME", "derivatives_data")
DERIV_DB_USER = os.environ.get("DERIV_DB_USER", "derivatives")
DERIV_DB_PASS = os.environ.get("DERIV_DB_PASS", "gerar_senha_forte_aqui_32chars")

BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8541911949:AAFdM-_MFKSMXd79hprzUPlVxDkItKOUNeo",
)
CHAT_ID = os.environ.get("TRADING_ALLOWED_USERS", "6127917209").split(",")[0].strip()

STATE_FILE = Path("/tmp/trading_daily_review_state.json")
REVIEW_LOOKBACK_DAYS = int(os.environ.get("REVIEW_LOOKBACK_DAYS", "7"))

# Engine configs (mirrored from settings.py for cost calculations)
ENGINE_META = {
    "m1_eth":    {"symbol": "ETHUSDT", "fee_bps": 10, "slip_bps": 5,  "lev": 10, "capital": 1000},
    "m2_btc":    {"symbol": "BTCUSDT", "fee_bps": 10, "slip_bps": 5,  "lev": 10, "capital": 1000},
    "m3_sol":    {"symbol": "SOLUSDT", "fee_bps": 10, "slip_bps": 8,  "lev": 10, "capital": 1000},
    "m3_eth_shadow":       {"symbol": "ETHUSDT", "fee_bps": 10, "slip_bps": 5, "lev": 10, "capital": 1000},
    "cn1_oi_divergence":   {"symbol": "BTCUSDT", "fee_bps": 10, "slip_bps": 5, "lev": 10, "capital": 1000},
    "cn2_taker_momentum":  {"symbol": "BTCUSDT", "fee_bps": 10, "slip_bps": 5, "lev": 10, "capital": 1000},
    "cn3_funding_reversal":{"symbol": "BTCUSDT", "fee_bps": 10, "slip_bps": 5, "lev": 10, "capital": 1000},
    "cn4_liquidation_cascade":{"symbol":"BTCUSDT","fee_bps":10,"slip_bps": 5, "lev": 10, "capital": 1000},
    "cn5_squeeze_zone":    {"symbol": "BTCUSDT", "fee_bps": 10, "slip_bps": 5, "lev": 10, "capital": 1000},
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect_trading():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _connect_derivatives():
    return psycopg2.connect(
        host=DERIV_DB_HOST, port=DERIV_DB_PORT, dbname=DERIV_DB_NAME,
        user=DERIV_DB_USER, password=DERIV_DB_PASS,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _query(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
            return resp.get("ok", False)
    except Exception as e:
        print(f"[review] telegram error: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Section 1 — Overall summary
# ---------------------------------------------------------------------------

def _section_summary(conn, since: datetime) -> str:
    rows = _query(conn, """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE pnl_usd > 0) AS wins,
            COUNT(*) FILTER (WHERE pnl_usd <= 0) AS losses,
            COALESCE(SUM(pnl_usd), 0) AS net_pnl,
            COALESCE(SUM(pnl_usd) FILTER (WHERE pnl_usd > 0), 0) AS gross_profit,
            COALESCE(SUM(pnl_usd) FILTER (WHERE pnl_usd <= 0), 0) AS gross_loss,
            COALESCE(AVG(pnl_usd), 0) AS avg_pnl,
            COALESCE(MAX(pnl_usd), 0) AS best_trade,
            COALESCE(MIN(pnl_usd), 0) AS worst_trade
        FROM tr_paper_trades
        WHERE status != 'OPEN' AND closed_at >= %s
    """, (since,))
    r = rows[0] if rows else {}
    total = r.get("total", 0)
    if total == 0:
        return "📊 <b>Resumo Geral</b>\nNenhum trade fechado no período.\n"

    wins = r["wins"]
    losses = r["losses"]
    wr = wins / total * 100 if total else 0
    net = float(r["net_pnl"])
    gp = float(r["gross_profit"])
    gl = float(r["gross_loss"])
    pf = abs(gp / gl) if gl != 0 else float("inf")
    avg = float(r["avg_pnl"])
    best = float(r["best_trade"])
    worst = float(r["worst_trade"])

    # Count open positions
    open_rows = _query(conn, "SELECT COUNT(*) AS c FROM tr_paper_trades WHERE status='OPEN'")
    open_count = open_rows[0]["c"] if open_rows else 0

    emoji = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"

    lines = [
        f"📊 <b>Resumo ({REVIEW_LOOKBACK_DAYS}d)</b>",
        f"",
        f"Trades: {total} ({wins}W / {losses}L) | WR: {wr:.0f}%",
        f"PnL: {emoji} ${net:+.2f} | PF: {pf:.2f}",
        f"Média: ${avg:+.2f} | Melhor: ${best:+.2f} | Pior: ${worst:+.2f}",
    ]
    if open_count:
        lines.append(f"Posições abertas: {open_count}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Section 2 — Per-engine performance
# ---------------------------------------------------------------------------

def _section_engines(conn, since: datetime) -> str:
    rows = _query(conn, """
        SELECT
            engine_id,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE pnl_usd > 0) AS wins,
            COALESCE(SUM(pnl_usd), 0) AS net_pnl,
            COALESCE(AVG(pnl_usd), 0) AS avg_pnl,
            COUNT(*) FILTER (WHERE close_reason='CLOSED_SL') AS sl_count,
            COUNT(*) FILTER (WHERE close_reason='CLOSED_TP') AS tp_count,
            COUNT(*) FILTER (WHERE close_reason='CLOSED_TIMEOUT') AS to_count
        FROM tr_paper_trades
        WHERE status != 'OPEN' AND closed_at >= %s
        GROUP BY engine_id
        ORDER BY net_pnl DESC
    """, (since,))

    if not rows:
        return ""

    lines = ["⚙️ <b>Engines</b>", ""]
    for r in rows:
        eid = r["engine_id"]
        total = r["total"]
        wins = r["wins"]
        wr = wins / total * 100 if total else 0
        net = float(r["net_pnl"])
        avg = float(r["avg_pnl"])
        sl = r["sl_count"]
        tp = r["tp_count"]
        to_ = r["to_count"]
        emoji = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        lines.append(
            f"{emoji} <b>{eid}</b> {total}t WR{wr:.0f}% {net:+.2f} · TP{tp} SL{sl} TO{to_} avg{avg:+.2f}"
        )

    # Engines with zero trades in the period
    active_engines = {r["engine_id"] for r in rows}
    silent = [e for e in ENGINE_META if e not in active_engines and not ENGINE_META[e].get("signal_only")]
    if silent:
        lines.append(f"\n😶 Sem trades: {', '.join(silent)}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Section 3 — Cost analysis (actual fees + slippage)
# ---------------------------------------------------------------------------

def _section_costs(conn, since: datetime) -> str:
    rows = _query(conn, """
        SELECT
            engine_id,
            direction,
            entry_price,
            exit_price,
            stake_usd,
            pnl_usd,
            close_reason,
            stop_price,
            target_price
        FROM tr_paper_trades
        WHERE status != 'OPEN' AND closed_at >= %s
        ORDER BY engine_id
    """, (since,))

    if not rows:
        return ""

    total_theoretical_cost = 0.0
    total_gross_pnl = 0.0
    total_net_pnl = 0.0

    for r in rows:
        eid = r["engine_id"]
        meta = ENGINE_META.get(eid, {})
        fee_bps = meta.get("fee_bps", 10)
        slip_bps = meta.get("slip_bps", 5)
        stake = float(r["stake_usd"])
        entry = float(r["entry_price"])
        exit_p = float(r["exit_price"]) if r["exit_price"] else entry
        direction = r["direction"]

        # Theoretical round-trip cost
        rt_fee = stake * 2 * fee_bps / 10000
        slip_cost = stake * slip_bps / 10000
        theoretical = rt_fee + slip_cost
        total_theoretical_cost += theoretical

        # Gross PnL (before fees) = what the PnL would be without fees
        dir_factor = 1.0 if direction == "LONG" else -1.0
        gross = (exit_p - entry) * dir_factor * stake / entry
        total_gross_pnl += gross
        total_net_pnl += float(r["pnl_usd"])

    actual_costs = total_gross_pnl - total_net_pnl
    n = len(rows)
    ratio = actual_costs / total_theoretical_cost if total_theoretical_cost > 0 else 0

    lines = [
        "💰 <b>Custos</b>",
        f"  Gross {total_gross_pnl:+.2f} → fees {actual_costs:.2f} → net {total_net_pnl:+.2f}",
        f"  Custo/trade ${actual_costs/n:.2f} | Real/Teórico {ratio:.1f}x ({n}t)" if n else "",
    ]
    return "\n".join(l for l in lines if l) + "\n"


# ---------------------------------------------------------------------------
# Section 4 — Skip analysis (missed winners)
# ---------------------------------------------------------------------------

def _section_skips(conn, since: datetime) -> str:
    """Check skipped signals and see if they would have been profitable."""
    skips = _query(conn, """
        SELECT engine_id, symbol, bar_timestamp, reason,
               details->>'direction' AS direction
        FROM tr_skip_events
        WHERE created_at >= %s
        ORDER BY created_at DESC
    """, (since,))

    if not skips:
        return "🔍 <b>Skip Analysis</b>\nNenhum skip no período.\n"

    # Count by reason
    reason_counts: dict[str, int] = {}
    for s in skips:
        reason = s["reason"] or "UNKNOWN"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    reasons_str = " · ".join(
        f"{r}:{c}" for r, c in sorted(reason_counts.items(), key=lambda x: -x[1])
    )
    lines = [
        "🔍 <b>Skips</b>",
        f"  {len(skips)} skips: {reasons_str}",
    ]
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  • {reason}: {count}")

    # Try to check what happened after skipped signals using 15m candles
    # (retrospective analysis — would the trade have hit TP or SL?)
    missed_winners = 0
    missed_losers = 0
    checked = 0

    for s in skips[:20]:  # limit to 20 most recent
        symbol = s["symbol"]
        bar_ts = s["bar_timestamp"]
        direction = s.get("direction")
        if not direction or not bar_ts:
            continue

        # Get the next 8 candles (2 hours of 15m) after the skip
        candle_table = {
            "BTCUSDT": "btc_price_5m",
            "ETHUSDT": "eth_price_5m",
            "SOLUSDT": "sol_price_5m",
        }.get(symbol)
        if not candle_table:
            continue

        try:
            candles = _query(conn, f"""
                SELECT open_time, high, low, close
                FROM {candle_table}
                WHERE open_time > %s AND open_time <= %s + INTERVAL '2 hours'
                ORDER BY open_time
                LIMIT 24
            """, (bar_ts, bar_ts))
        except Exception:
            continue

        if len(candles) < 4:
            continue

        entry_price = float(candles[0]["close"])
        # Simple check: did price move 0.5% in the predicted direction?
        threshold = 0.005
        checked += 1

        for c in candles[1:]:
            high = float(c["high"])
            low = float(c["low"])
            if direction == "LONG" and (high - entry_price) / entry_price >= threshold:
                missed_winners += 1
                break
            elif direction == "SHORT" and (entry_price - low) / entry_price >= threshold:
                missed_winners += 1
                break
        else:
            missed_losers += 1

    if checked > 0:
        lines.append(f"\nRetrospectiva ({checked} verificados):")
        lines.append(f"  Teriam sido winners: {missed_winners}")
        lines.append(f"  Teriam sido losers: {missed_losers}")
        if missed_winners > missed_losers:
            lines.append(f"  ⚠️ Skips estão filtrando winners demais")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Section 5 — Cross-engine exposure
# ---------------------------------------------------------------------------

def _section_exposure(conn, since: datetime) -> str:
    """Check for overlapping positions on the same symbol."""
    overlaps = _query(conn, """
        WITH open_intervals AS (
            SELECT engine_id, symbol, direction, opened_at,
                   COALESCE(closed_at, NOW()) AS closed_at
            FROM tr_paper_trades
            WHERE opened_at >= %s
        )
        SELECT a.engine_id AS eng_a, b.engine_id AS eng_b,
               a.symbol, a.direction AS dir_a, b.direction AS dir_b,
               GREATEST(a.opened_at, b.opened_at) AS overlap_start,
               LEAST(a.closed_at, b.closed_at) AS overlap_end
        FROM open_intervals a
        JOIN open_intervals b ON a.symbol = b.symbol
             AND a.engine_id < b.engine_id
             AND a.opened_at < b.closed_at
             AND b.opened_at < a.closed_at
        ORDER BY overlap_start DESC
        LIMIT 20
    """, (since,))

    if not overlaps:
        return ""

    lines = ["🔗 <b>Exposição Cruzada</b>", ""]

    same_dir = 0
    opp_dir = 0
    for o in overlaps:
        if o["dir_a"] == o["dir_b"]:
            same_dir += 1
        else:
            opp_dir += 1

    lines.append(f"Sobreposições: {len(overlaps)}")
    if same_dir:
        lines.append(f"  ⚠️ Mesma direção: {same_dir} (risco concentrado)")
    if opp_dir:
        lines.append(f"  ↔️ Direções opostas: {opp_dir} (hedge natural)")

    # Show top 3
    for o in overlaps[:3]:
        dur = (o["overlap_end"] - o["overlap_start"]).total_seconds() / 60
        lines.append(
            f"  • {o['eng_a']} {o['dir_a']} + {o['eng_b']} {o['dir_b']} "
            f"em {o['symbol']} ({dur:.0f}min)"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Section 6 — Alerts
# ---------------------------------------------------------------------------

def _section_alerts(conn, since: datetime) -> str:
    alerts = []

    # Engines with losing streak >= 3
    for eid in ENGINE_META:
        streak_rows = _query(conn, """
            SELECT pnl_usd FROM tr_paper_trades
            WHERE engine_id = %s AND status != 'OPEN'
            ORDER BY closed_at DESC LIMIT 10
        """, (eid,))
        if len(streak_rows) >= 3:
            losing = 0
            for r in streak_rows:
                if float(r["pnl_usd"]) <= 0:
                    losing += 1
                else:
                    break
            if losing >= 3:
                alerts.append(f"🔴 <b>{eid}</b>: {losing} losses seguidos")

    # Engines with max drawdown > 3% of capital
    for eid, meta in ENGINE_META.items():
        equity_rows = _query(conn, """
            SELECT COALESCE(SUM(pnl_usd), 0) AS total_pnl
            FROM tr_paper_trades
            WHERE engine_id = %s AND status != 'OPEN'
        """, (eid,))
        if equity_rows:
            total_pnl = float(equity_rows[0]["total_pnl"])
            dd_pct = total_pnl / meta["capital"] * 100
            if dd_pct < -3:
                alerts.append(
                    f"⚠️ <b>{eid}</b>: drawdown total {dd_pct:.1f}% "
                    f"(${total_pnl:+.2f})"
                )

    # Circuit breaker events in the period
    cb_rows = _query(conn, """
        SELECT engine_id, COUNT(*) AS c
        FROM tr_risk_events
        WHERE event_type LIKE 'CIRCUIT%%' AND created_at >= %s
        GROUP BY engine_id
    """, (since,))
    for r in cb_rows:
        alerts.append(
            f"🛑 <b>{r['engine_id']}</b>: {r['c']} circuit breakers no período"
        )

    if not alerts:
        return ""

    return "🚨 <b>Alertas</b>\n\n" + "\n".join(alerts) + "\n"


# ---------------------------------------------------------------------------
# Section 7 — Derivatives data quality
# ---------------------------------------------------------------------------

def _section_derivatives_quality() -> str:
    try:
        conn = _connect_derivatives()
    except Exception:
        return "📡 <b>Derivativos</b>: DB indisponível\n"

    try:
        # Check freshness
        rows = _query(conn, """
            SELECT symbol,
                   MAX(timestamp) AS last_ts,
                   COUNT(*) FILTER (
                       WHERE timestamp >= NOW() - INTERVAL '1 hour'
                   ) AS recent_count
            FROM derived_features
            GROUP BY symbol
        """)

        # Check heatmap
        hm_rows = _query(conn, """
            SELECT symbol, COUNT(*) AS bins,
                   MAX(timestamp) AS last_ts
            FROM liquidation_heatmap
            WHERE timestamp >= NOW() - INTERVAL '1 hour'
            GROUP BY symbol
        """)

        lines = ["📡 <b>Derivativos</b>", ""]

        for r in rows:
            sym = r["symbol"]
            last = r["last_ts"]
            recent = r["recent_count"]
            age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60 if last else 999
            status = "🟢" if age_min < 10 else "🟡" if age_min < 30 else "🔴"
            lines.append(f"  {status} {sym}: {recent} amostras/1h | age {age_min:.0f}min")

        if hm_rows:
            hm_total = sum(r["bins"] for r in hm_rows)
            lines.append(f"  Heatmap: {hm_total} bins/1h")
        else:
            lines.append(f"  ⚠️ Heatmap: sem dados recentes")

        return "\n".join(lines) + "\n"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# State management (dedup daily)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _already_sent_today(state: dict) -> bool:
    last = state.get("last_review_date")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return last == today


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"[review] {now.strftime('%Y-%m-%d %H:%M UTC')} — starting daily review")

    state = _load_state()

    # Dedup: only one report per day
    if _already_sent_today(state) and "--force" not in sys.argv:
        print("[review] already sent today — skipping")
        return

    since = now - timedelta(days=REVIEW_LOOKBACK_DAYS)

    try:
        conn = _connect_trading()
    except Exception as e:
        print(f"[review] DB connection failed: {e}", file=sys.stderr)
        return

    try:
        _SEP = "·  ·  ·  ·  ·  ·  ·  ·"
        sections = [
            f"📋 <b>Trading OS — {now.strftime('%d/%m %H:%M UTC')}</b> ({REVIEW_LOOKBACK_DAYS}d)",
            "",
            _section_summary(conn, since),
            _SEP,
            _section_engines(conn, since),
            _SEP,
            _section_costs(conn, since),
            _section_skips(conn, since),
            _SEP,
            _section_exposure(conn, since),
            _section_alerts(conn, since),
            _section_derivatives_quality(),
        ]
    finally:
        conn.close()

    report = "\n".join(s for s in sections if s)

    # Telegram limit is 4096 chars
    if len(report) > 4000:
        report = report[:3990] + "\n\n<i>[truncado]</i>"

    if "--dry-run" in sys.argv:
        print(report)
        print(f"\n[review] dry-run — {len(report)} chars, not sending")
        return

    ok = _send_telegram(report)
    print(f"[review] telegram: {'ok' if ok else 'FAILED'} ({len(report)} chars)")

    if ok:
        state["last_review_date"] = now.strftime("%Y-%m-%d")
        state["last_review_ts"] = now.isoformat()
        _save_state(state)


if __name__ == "__main__":
    main()
