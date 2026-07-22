#!/usr/bin/env python3
"""
Backfill de ticks WIN$N (e WDO$N no que o cache tiver) — janela rolante de
~1 ano do servidor XP. Urgente: dias antigos somem conforme a janela anda.

Varre de ontem ate ~400 dias atras, dia a dia, salvando parquet identico ao
do coletor diario (mesma pasta/formato, idempotente). Para de insistir num
simbolo apos 15 pregoes consecutivos vazios ANTES do primeiro dia salvo
(chegou no fim da janela do servidor).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

TERM = r"C:\Program Files\MetaTrader 5\terminal64.exe"
BASE = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\tick_history")
LOG = BASE / "backfill_log.jsonl"

SYMBOLS = ["WIN$N", "WDO$N"]
MAX_DAYS_BACK = 400
GIVEUP_AFTER_CONSEC_EMPTY = 15


def log_event(**kw):
    kw["ts"] = datetime.now(timezone.utc).isoformat()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def collect_day(sym: str, day: datetime) -> tuple[str, int]:
    out_dir = BASE / sym.replace("$", "S")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sym.replace('$', 'S')}_{day.date()}.parquet"
    if out_path.exists():
        return "skip_exists", 0
    # janela em rotulo wall-clock B3 (epoch do servidor finge ser UTC) —
    # bug 11:00-22:00 corrigido em 2026-07-21 (cortava 09:00-11:00)
    a = day.replace(hour=6, minute=0, second=0, microsecond=0)
    b = day.replace(hour=23, minute=0, second=0, microsecond=0)
    ticks = mt5.copy_ticks_range(sym, a, b, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        time.sleep(0.6)
        ticks = mt5.copy_ticks_range(sym, a, b, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) < 1000:
        return "empty", 0
    df = pd.DataFrame(np.array(ticks))
    keep = [c for c in ["time", "bid", "ask", "last", "volume", "time_msc",
                        "flags", "volume_real"] if c in df.columns]
    df[keep].to_parquet(out_path, index=False)
    return "saved", int(len(df))


def main():
    if not (mt5.initialize(path=TERM) or mt5.initialize()):
        raise SystemExit(f"initialize falhou: {mt5.last_error()}")

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
        saved = skipped = empty = 0
        consec_empty_tail = 0
        seen_any = False
        for nb in range(1, MAX_DAYS_BACK + 1):
            day = today - timedelta(days=nb)
            if day.weekday() >= 5:
                continue
            status, n = collect_day(sym, day)
            if status == "saved":
                saved += 1
                seen_any = True
                consec_empty_tail = 0
            elif status == "skip_exists":
                skipped += 1
                seen_any = True
                consec_empty_tail = 0
            else:
                empty += 1
                if seen_any:
                    consec_empty_tail += 1
                    # buracos no meio existem (WDO$N); so desiste se ja achou
                    # o inicio da janela: muitos vazios seguidos depois de
                    # dados = fim da janela do servidor
                    if consec_empty_tail >= GIVEUP_AFTER_CONSEC_EMPTY:
                        log_event(event="window_end", symbol=sym, last_probe=str(day.date()))
                        break
        log_event(event="backfill_done", symbol=sym, saved=saved,
                  skipped=skipped, empty=empty)
        print(f"{sym}: saved={saved} skipped={skipped} empty={empty}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
