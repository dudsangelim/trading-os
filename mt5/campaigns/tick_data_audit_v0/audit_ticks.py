#!/usr/bin/env python3
"""
tick_data_audit_v0 — Auditoria de disponibilidade/qualidade de tick data
na XP via MT5 (copy_ticks_range). Objetivo: saber se da pra construir
OFI/fluxo de agressao (flags BUY/SELL) e qual a profundidade historica.

Read-only: nenhuma ordem, nenhuma escrita no terminal.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import MetaTrader5 as mt5

TERM = r"C:\Program Files\MetaTrader 5\terminal64.exe"
OUT = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\tick_data_audit_v0")
OUT.mkdir(parents=True, exist_ok=True)

# candidatos: continuas e contratos reais vigentes (detectados no smoke test)
SYMBOLS = ["WIN$N", "WDO$N", "WINQ26", "WDOQ26", "WINV26", "WDOU26"]

# dias de probe (D-atras a partir de hoje); pregoes ~seg-sex
PROBE_DAYS_BACK = [2, 7, 14, 30, 60, 90, 120, 180, 270, 365, 540, 730]

FLAG_NAMES = {
    getattr(mt5, "TICK_FLAG_BID", 2): "BID",
    getattr(mt5, "TICK_FLAG_ASK", 4): "ASK",
    getattr(mt5, "TICK_FLAG_LAST", 8): "LAST",
    getattr(mt5, "TICK_FLAG_VOLUME", 16): "VOLUME",
    getattr(mt5, "TICK_FLAG_BUY", 32): "BUY",
    getattr(mt5, "TICK_FLAG_SELL", 64): "SELL",
}


def business_day_back(n_back: int) -> datetime:
    d = datetime.now(timezone.utc) - timedelta(days=n_back)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def probe_day(sym: str, day_utc: datetime) -> dict:
    a = day_utc.replace(hour=11, minute=0, second=0, microsecond=0)  # ~08h BRT
    b = day_utc.replace(hour=22, minute=0, second=0, microsecond=0)  # ~19h BRT
    ticks = mt5.copy_ticks_range(sym, a, b, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return {"date": str(day_utc.date()), "n_ticks": 0}
    t = np.array(ticks)
    flags = t["flags"].astype(int)
    n = len(t)
    res = {
        "date": str(day_utc.date()),
        "n_ticks": int(n),
        "n_last_nonzero": int((t["last"] > 0).sum()),
        "n_volume_nonzero": int((t["volume"] > 0).sum()),
    }
    for bit, name in FLAG_NAMES.items():
        res[f"pct_flag_{name}"] = round(100.0 * float((flags & bit > 0).mean()), 1)
    return res


def main():
    if not (mt5.initialize(path=TERM) or mt5.initialize()):
        raise SystemExit(f"initialize falhou: {mt5.last_error()}")
    ai = mt5.account_info()
    print(f"conta={ai.login} servidor={ai.server}")

    report = {"probed_at": datetime.now(timezone.utc).isoformat(), "symbols": {}}

    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"\n### {sym}: symbol_info None — pulando")
            report["symbols"][sym] = {"error": "symbol_info None"}
            continue
        print(f"\n### {sym} ###")
        rows = []
        earliest_with_ticks = None
        for nb in PROBE_DAYS_BACK:
            day = business_day_back(nb)
            r = probe_day(sym, day)
            rows.append(r)
            status = f"{r['n_ticks']:>8} ticks"
            if r["n_ticks"] > 0:
                earliest_with_ticks = r["date"]
                extra = (f"  last>0: {r['n_last_nonzero']:>7}  vol>0: {r['n_volume_nonzero']:>7}  "
                         f"BUY {r.get('pct_flag_BUY', 0):>5}%  SELL {r.get('pct_flag_SELL', 0):>5}%  "
                         f"LAST {r.get('pct_flag_LAST', 0):>5}%")
            else:
                extra = "  (vazio)"
            print(f"  D-{nb:>3} ({r['date']}): {status}{extra}")
        report["symbols"][sym] = {"probes": rows, "earliest_probe_with_ticks": earliest_with_ticks}

    out_json = OUT / "tick_audit_report.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSalvo: {out_json}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
