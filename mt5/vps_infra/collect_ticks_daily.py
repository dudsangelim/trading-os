#!/usr/bin/env python3
"""
Coletor diario de ticks B3 (WIN/WDO) — VERSAO VPS via ponte mt5linux.

Porte fiel de collect_ticks_daily.py (notebook). Diferencas:
  - MetaTrader5 via RPC (mt5linux) no container mt5_b3 (127.0.0.1:8001);
  - copy_ticks_range chamada por conn.eval com EPOCH INTS (o wrapper da
    mt5linux 0.1.9 exige datetime e quebra; e o epoch do servidor XP e'
    wall-clock B3 "fingindo ser UTC" — bug ja documentado 2026-07-21);
  - paths Linux; timeout RPC alto; LOOKBACK_DAYS via env p/ smoke test.

Idempotente: pula (dia, simbolo) que ja tem parquet.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rpyc.utils.classic
from mt5linux import MetaTrader5

BASE = Path("/opt/mt5b3/data/tick_history")
LOG = BASE / "collector_log.jsonl"

LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "30"))
CONT = ["WIN$N", "WDO$N"]
REAL_RE = re.compile(r"^(WIN|WDO)[FGHJKMNQUVXZ]\d{2}$")

# Janela em rotulo wall-clock B3 (mesma semantica do coletor do notebook)
DAY_START_H = 6
DAY_END_H = 23

mt5 = None
conn = None


def log_event(**kw):
    kw["ts"] = datetime.now(timezone.utc).isoformat()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def label_epoch(day: datetime, hour: int) -> int:
    """Epoch do rotulo wall-clock (Y,M,D,H) tratado como UTC — e' assim que o
    servidor XP indexa ticks."""
    return calendar.timegm((day.year, day.month, day.day, hour, 0, 0))


def ticks_range_epoch(sym: str, frm: int, to: int):
    raw = conn.eval(f'mt5.copy_ticks_range("{sym}", {frm}, {to}, mt5.COPY_TICKS_ALL)')
    return rpyc.utils.classic.obtain(raw)


def top2_real_contracts(root: str) -> list[str]:
    scored = []
    for s in mt5.symbols_get(f"{root}*") or []:
        name = str(s.name)
        if not REAL_RE.match(name):
            continue
        mt5.symbol_select(name, True)
        info = mt5.symbol_info(name)
        vol = getattr(info, "session_volume", 0) or 0
        scored.append((vol, name))
    scored.sort(reverse=True)
    return [name for _, name in scored[:2]]


def business_days_back(n: int) -> list[datetime]:
    days, d = [], datetime.now(timezone.utc)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.replace(hour=0, minute=0, second=0, microsecond=0))
        d -= timedelta(days=1)
    return days


def collect_day(sym: str, day: datetime) -> tuple[str, int]:
    out_dir = BASE / sym.replace("$", "S")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sym.replace('$', 'S')}_{day.date()}.parquet"
    if out_path.exists():
        return "skip_exists", 0

    a = label_epoch(day, DAY_START_H)
    b = label_epoch(day, DAY_END_H)
    ticks = ticks_range_epoch(sym, a, b)
    if ticks is None or len(ticks) == 0:
        time.sleep(1.0)
        ticks = ticks_range_epoch(sym, a, b)
    if ticks is None or len(ticks) == 0:
        return "empty", 0

    df = pd.DataFrame(np.array(ticks))
    keep = [c for c in ["time", "bid", "ask", "last", "volume", "time_msc",
                        "flags", "volume_real"] if c in df.columns]
    df = df[keep]
    if len(df) < 1000:
        return "empty", int(len(df))
    df.to_parquet(out_path, index=False)
    return "saved", int(len(df))


def main():
    global mt5, conn
    mt5 = MetaTrader5(host="127.0.0.1", port=8001)
    conn = mt5._MetaTrader5__conn
    conn._config["sync_request_timeout"] = 600
    conn.execute("import datetime")

    if not mt5.initialize():
        log_event(event="init_failed", error=str(mt5.last_error()))
        sys.exit(1)
    ai = mt5.account_info()
    if ai is None:
        log_event(event="no_account")
        sys.exit(1)

    symbols = list(CONT)
    for root in ("WIN", "WDO"):
        symbols.extend(top2_real_contracts(root))

    days = business_days_back(LOOKBACK_DAYS)
    summary = {"account": int(ai.login), "symbols": symbols,
               "saved": 0, "skipped": 0, "empty": 0, "errors": 0}

    for sym in symbols:
        mt5.symbol_select(sym, True)
        for day in days:
            try:
                status, n = collect_day(sym, day)
            except Exception as e:  # noqa: BLE001 — coletor nao pode morrer no meio
                summary["errors"] += 1
                log_event(event="day_error", symbol=sym, day=str(day.date()), error=repr(e))
                continue
            if status == "saved":
                summary["saved"] += 1
                log_event(event="saved", symbol=sym, day=str(day.date()), n_ticks=n)
                print(f"saved {sym} {day.date()}: {n} ticks", flush=True)
            elif status == "skip_exists":
                summary["skipped"] += 1
            else:
                summary["empty"] += 1

    log_event(event="run_done", **summary)
    print(f"done: saved={summary['saved']} skipped={summary['skipped']} "
          f"empty={summary['empty']} errors={summary['errors']}")
    mt5.shutdown()
    if summary["saved"] == 0 and summary["skipped"] == 0:
        sys.exit(2)  # nada coletado e nada existente = algo errado (watchdog ve)


if __name__ == "__main__":
    main()
