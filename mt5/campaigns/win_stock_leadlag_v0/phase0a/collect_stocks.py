#!/usr/bin/env python3
"""
Coletor MT5 -> acoes (VALE3, PETR4, ITUB4) para o premise check
win_stock_leadlag_v0 Phase 0A.

Segue o mesmo padrao do coletor existente
`B3 Futuros/mt5_history/collect_mt5_b3.py`: mesma inicializacao, mesmo
schema de parquet (colunas e nome da coluna de timestamp identicos).

Diferenca: usa copy_rates_from_pos(0..100000) por instrucao explicita do
pre-registro (maximiza o historico disponivel por simbolo, evita
depender de um FLOOR de data que pode nao existir pro simbolo).

Saida: {SYM}_M1.parquet / {SYM}_M5.parquet em
C:\\Users\\Notebook\\Documents\\Claude\\Projects\\B3 Futuros\\mt5_history\\
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

TERM = r"C:\Program Files\MetaTrader 5\terminal64.exe"
OUT = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
OUT.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["VALE3", "PETR4", "ITUB4"]
# copy_rates_from_pos(..., 0, 100000) exato retorna "Invalid params" neste
# terminal (testado empiricamente); 99999 funciona -- mesmo teto de ~100k
# ja documentado para os outros parquets M1/M5 do projeto B3.
MAXBARS = 99999

TF = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
}


def fetch(sym: str, tf_const: int, maxbars: int) -> pd.DataFrame | None:
    """Puxa o maximo de barras via copy_rates_from_pos, posicao 0 = mais recente."""
    r = mt5.copy_rates_from_pos(sym, tf_const, 0, maxbars)
    if r is None or len(r) == 0:
        time.sleep(0.8)
        r = mt5.copy_rates_from_pos(sym, tf_const, 0, maxbars)
    if r is None or len(r) == 0:
        return None
    arr = np.array(r)
    df = pd.DataFrame(arr)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df["datetime_b3"] = pd.to_datetime(df["time"], unit="s")  # wall-clock B3 (UTC-3)
    keep = ["datetime_b3", "time", "open", "high", "low", "close",
            "tick_volume", "real_volume", "spread"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].rename(columns={"time": "epoch"})


def main():
    ok = mt5.initialize(path=TERM)
    if not ok:
        # terminal pode ja estar aberto/conectado sem precisar do path,
        # ou pode precisar de mais tempo pra subir
        for attempt in range(6):
            time.sleep(10)
            ok = mt5.initialize(path=TERM) or mt5.initialize()
            if ok:
                break
    if not ok:
        raise SystemExit(f"initialize falhou apos retries: {mt5.last_error()}")

    ai = mt5.account_info()
    meta = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "account": getattr(ai, "login", None),
        "server": getattr(ai, "server", None),
        "company": getattr(ai, "company", None),
        "timezone_note": "timestamps = horario local B3 (America/Sao_Paulo, UTC-3, sem DST)",
        "method": "copy_rates_from_pos(sym, tf, 0, 100000)",
        "files": [],
    }

    print(f"Conta: {meta['account']} @ {meta['server']} ({meta['company']})\n")

    for sym in SYMBOLS:
        sel = mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        if not sel or info is None:
            print(f"  ERRO {sym}: symbol_select={sel}, symbol_info={info} "
                  f"last_error={mt5.last_error()}")
            continue
        for tfn, tf_const in TF.items():
            try:
                df = fetch(sym, tf_const, MAXBARS)
            except Exception as e:
                print(f"  ERRO {sym} {tfn}: {e}")
                continue
            if df is None or len(df) == 0:
                print(f"  VAZIO {sym} {tfn}: last_error={mt5.last_error()}")
                continue
            fn = f"{sym}_{tfn}.parquet"
            df.to_parquet(OUT / fn, index=False, compression="zstd")
            zero_vol_pct = float((df["real_volume"] == 0).mean() * 100) if "real_volume" in df.columns else None
            rec = {
                "file": fn, "symbol": sym, "timeframe": tfn,
                "description": getattr(info, "description", ""),
                "digits": getattr(info, "digits", None),
                "rows": int(len(df)),
                "first": str(df["datetime_b3"].iloc[0]),
                "last": str(df["datetime_b3"].iloc[-1]),
                "real_volume_zero_pct": zero_vol_pct,
            }
            meta["files"].append(rec)
            print(f"  {sym:6s} {tfn:3s}: {len(df):>7d} linhas | "
                  f"{rec['first'][:16]} -> {rec['last'][:16]} | "
                  f"real_volume==0: {zero_vol_pct:.1f}% -> {fn}")

    (OUT / "mt5_manifest_stocks.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    mt5.shutdown()

    nfiles = len(meta["files"])
    total_rows = sum(f["rows"] for f in meta["files"])
    print(f"\nOK: {nfiles} arquivos, {total_rows:,} linhas totais em {OUT}")
    print(f"Manifest: {OUT / 'mt5_manifest_stocks.json'}")


if __name__ == "__main__":
    main()
