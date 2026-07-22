#!/usr/bin/env python3
"""
Coletor diario de ticks B3 (WIN/WDO) via MT5 XP — projeto tick_data_audit_v0.

Coleta e persiste em parquet, por dia x simbolo:
  - Continuas WIN$N / WDO$N: ticks de negocio (last/volume; flags sem agressor)
  - Top-2 contratos reais por volume de cada root (WIN*/WDO*): ticks com
    flags de agressor BUY/SELL reais (só o front tem cobertura plena; o 2o
    e' seguranca pra semana de rolagem)

Idempotente: pula (dia, simbolo) que ja tem parquet. Faz catch-up dos
ultimos LOOKBACK_DAYS pregoes (notebook desligado nao perde dia, ate o
limite da janela do servidor).

Agendado via Task Scheduler (ver register_task.ps1). Roda apos o pregao.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

TERM = r"C:\Program Files\MetaTrader 5\terminal64.exe"
BASE = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\tick_history")
LOG = BASE / "collector_log.jsonl"

LOOKBACK_DAYS = 30          # pregoes de catch-up (notebook pode ficar semanas off)
CONT = ["WIN$N", "WDO$N"]
REAL_RE = re.compile(r"^(WIN|WDO)[FGHJKMNQUVXZ]\d{2}$")

# ATENCAO (bug corrigido 2026-07-21): o epoch de tick do servidor XP e'
# wall-clock B3 "fingindo ser UTC". A janela abaixo e' em rotulo wall-clock
# B3, nao UTC de verdade. 06:00-23:00 cobre o pregao inteiro com folga.
DAY_UTC_START_H = 6
DAY_UTC_END_H = 23


def log_event(**kw):
    kw["ts"] = datetime.now(timezone.utc).isoformat()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def top2_real_contracts(root: str) -> list[str]:
    scored = []
    for s in mt5.symbols_get(f"{root}*") or []:
        if not REAL_RE.match(s.name):
            continue
        mt5.symbol_select(s.name, True)
        info = mt5.symbol_info(s.name)
        vol = getattr(info, "session_volume", 0) or 0
        scored.append((vol, s.name))
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
    """Retorna (status, n_ticks). status: saved|skip_exists|empty|error."""
    out_dir = BASE / sym.replace("$", "S")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sym.replace('$', 'S')}_{day.date()}.parquet"
    if out_path.exists():
        return "skip_exists", 0

    a = day.replace(hour=DAY_UTC_START_H)
    b = day.replace(hour=DAY_UTC_END_H)
    ticks = mt5.copy_ticks_range(sym, a, b, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        time.sleep(1.0)
        ticks = mt5.copy_ticks_range(sym, a, b, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return "empty", 0

    df = pd.DataFrame(np.array(ticks))
    keep = [c for c in ["time", "bid", "ask", "last", "volume", "time_msc",
                        "flags", "volume_real"] if c in df.columns]
    df = df[keep]
    # so persiste dia com pregao de verdade (>1000 eventos) pra nao gravar lixo
    if len(df) < 1000:
        return "empty", int(len(df))
    df.to_parquet(out_path, index=False)
    return "saved", int(len(df))


def main():
    if not (mt5.initialize(path=TERM) or mt5.initialize()):
        log_event(event="init_failed", error=str(mt5.last_error()))
        sys.exit(1)
    ai = mt5.account_info()

    symbols = list(CONT)
    for root in ("WIN", "WDO"):
        symbols.extend(top2_real_contracts(root))

    days = business_days_back(LOOKBACK_DAYS)
    summary = {"account": ai.login if ai else None, "symbols": symbols,
               "saved": 0, "skipped": 0, "empty": 0}

    for sym in symbols:
        mt5.symbol_select(sym, True)
        for day in days:
            try:
                status, n = collect_day(sym, day)
            except Exception as e:  # noqa: BLE001 — coletor nao pode morrer no meio
                log_event(event="day_error", symbol=sym, day=str(day.date()), error=repr(e))
                continue
            if status == "saved":
                summary["saved"] += 1
                log_event(event="saved", symbol=sym, day=str(day.date()), n_ticks=n)
                print(f"saved {sym} {day.date()}: {n} ticks")
            elif status == "skip_exists":
                summary["skipped"] += 1
            else:
                summary["empty"] += 1

    log_event(event="run_done", **summary)
    print(f"done: saved={summary['saved']} skipped={summary['skipped']} empty={summary['empty']}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
