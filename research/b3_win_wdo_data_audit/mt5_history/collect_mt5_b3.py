#!/usr/bin/env python3
"""
Coletor MT5 -> B3 (WIN/WDO) para o projeto b3_win_wdo_data_audit.

Coleta:
  - Continuas NAO AJUSTADAS (executavel): WIN$N, WDO$N  -> M1,M5,M15,M30,H1,D1
  - Continuas AJUSTADAS (indicador longo): WIN$, WDO$    -> H1,D1  (rotulado adjusted)
  - Contratos REAIS do servidor (camada 1): todos WINxx/WDOxx -> M1,M5,M15,M30,H1,D1

Saida: parquet por (simbolo, timeframe) + mt5_manifest.json
Timezone dos timestamps: horario local B3 (America/Sao_Paulo, UTC-3, sem DST pos-2019).
"""
from __future__ import annotations
import json, re, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

TERM = r"C:\Program Files\MetaTrader 5\terminal64.exe"
OUT = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
OUT.mkdir(parents=True, exist_ok=True)

FLOOR = datetime(2021, 1, 1, tzinfo=timezone.utc)
NOW = datetime.now(timezone.utc) + timedelta(days=2)

TF = {
    "M1":  (mt5.TIMEFRAME_M1,  15),   # (const, chunk_days)
    "M5":  (mt5.TIMEFRAME_M5,  90),
    "M15": (mt5.TIMEFRAME_M15, 200),
    "M30": (mt5.TIMEFRAME_M30, 400),
    "H1":  (mt5.TIMEFRAME_H1,  100000),
    "D1":  (mt5.TIMEFRAME_D1,  100000),
}

CONT_NONADJ = ["WIN$N", "WDO$N"]
CONT_ADJ    = ["WIN$", "WDO$"]
REAL_RE = re.compile(r"^(WIN|WDO)[FGHJKMNQUVXZ]\d{2}$")

FNAME = {"WIN$N": "WIN_cont_N", "WDO$N": "WDO_cont_N",
         "WIN$": "WIN_cont_ADJprop", "WDO$": "WDO_cont_ADJprop"}


def fname(sym: str) -> str:
    return FNAME.get(sym, sym)


def fetch(sym: str, tf_const: int, chunk_days: int) -> pd.DataFrame | None:
    """Puxa historico completo de (sym, tf) por chunks; dedupe por epoch."""
    parts = []
    a = FLOOR
    while a < NOW:
        b = min(a + timedelta(days=chunk_days), NOW)
        r = mt5.copy_rates_range(sym, tf_const, a, b)
        if (r is None or len(r) == 0):
            time.sleep(0.8)
            r = mt5.copy_rates_range(sym, tf_const, a, b)  # retry download async
        if r is not None and len(r):
            parts.append(np.array(r))
        a = b
    if not parts:
        return None
    arr = np.concatenate(parts)
    df = pd.DataFrame(arr)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df["datetime_b3"] = pd.to_datetime(df["time"], unit="s")  # wall-clock B3 (UTC-3)
    keep = ["datetime_b3", "time", "open", "high", "low", "close",
            "tick_volume", "real_volume", "spread"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].rename(columns={"time": "epoch"})


def main():
    if not (mt5.initialize(path=TERM) or mt5.initialize()):
        raise SystemExit(f"initialize falhou: {mt5.last_error()}")

    ai = mt5.account_info()
    meta = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "account": getattr(ai, "login", None),
        "server": getattr(ai, "server", None),
        "company": getattr(ai, "company", None),
        "timezone_note": "timestamps = horario local B3 (America/Sao_Paulo, UTC-3, sem DST)",
        "files": [],
    }

    # universo de simbolos reais presentes no servidor
    present = set()
    for grp in ("*WIN*", "*WDO*"):
        for s in (mt5.symbols_get(grp) or []):
            present.add(s.name)
    real = sorted(x for x in present if REAL_RE.match(x))

    plan = []
    for s in CONT_NONADJ:
        for tfn in TF: plan.append((s, tfn, False, "liquidez", "sem_ajuste"))
    for s in CONT_ADJ:
        for tfn in ("H1", "D1"): plan.append((s, tfn, True, "liquidez", "ajuste_proporcional"))
    for s in real:
        for tfn in TF: plan.append((s, tfn, False, "contrato_real", "sem_ajuste"))

    print(f"Simbolos reais no servidor: {len(real)}")
    print(f"Total de tarefas (simbolo x tf): {len(plan)}\n")

    for sym, tfn, adjusted, roll, adjkind in plan:
        tf_const, chunk_days = TF[tfn]
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        try:
            df = fetch(sym, tf_const, chunk_days)
        except Exception as e:
            print(f"  ERRO {sym} {tfn}: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        fn = f"{fname(sym)}_{tfn}.parquet"
        df.to_parquet(OUT / fn, index=False, compression="zstd")
        rec = {
            "file": fn, "symbol": sym, "timeframe": tfn,
            "description": getattr(info, "description", ""),
            "adjusted": adjusted, "roll_type": roll, "adjust_kind": adjkind,
            "digits": getattr(info, "digits", None),
            "tick_size": getattr(info, "trade_tick_size", None),
            "rows": int(len(df)),
            "first": str(df["datetime_b3"].iloc[0]),
            "last": str(df["datetime_b3"].iloc[-1]),
        }
        meta["files"].append(rec)
        tag = "ADJ" if adjusted else ("REAL" if roll == "contrato_real" else "CONT")
        print(f"  [{tag:4s}] {sym:8s} {tfn:3s}: {len(df):>7d} linhas | {rec['first'][:16]} -> {rec['last'][:16]} -> {fn}")

    (OUT / "mt5_manifest.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    mt5.shutdown()

    nfiles = len(meta["files"])
    total_rows = sum(f["rows"] for f in meta["files"])
    print(f"\nOK: {nfiles} arquivos, {total_rows:,} linhas totais em {OUT}")
    print(f"Manifest: {OUT / 'mt5_manifest.json'}")


if __name__ == "__main__":
    main()
