#!/usr/bin/env python3
"""Follow-up: (a) data exata da extensao de sessao; (b) emenda intradiaria de contrato?"""
from pathlib import Path
import pandas as pd
import numpy as np

BASE = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")

df = pd.read_parquet(BASE / "WIN_cont_N_M15.parquet")
df["dia"] = df["datetime_b3"].dt.date
df["hhmm"] = df["datetime_b3"].dt.strftime("%H:%M")
last = df.groupby("dia")["hhmm"].max()

print("=== (a) transicao do fechamento 17:45 -> 18:15 (ultima barra M15) ===")
trans = last[last.isin(["17:45", "18:15"])]
switch_days = trans[trans != trans.shift(1)].index.tolist()[:6]
for d in switch_days:
    print(f"  mudanca em {d}: ultima barra passa a ser {trans.loc[d]}")

print("\n=== (b) emenda INTRADIARIA de contrato? (2026, overlap com reais) ===")
cont = df
for real_name in ["WINQ26", "WINV26"]:
    real = pd.read_parquet(BASE / f"{real_name}_M15.parquet")
    m = cont.merge(real[["epoch", "close"]], on="epoch", suffixes=("", "_r"))
    m["dia"] = m["datetime_b3"].dt.date
    match = m.assign(eq=(m["close"] == m["close_r"])).groupby("dia")["eq"].mean()
    partial = match[(match > 0.02) & (match < 0.98)]
    print(f"{real_name}: {len(match)} dias comparados | dias PARCIAIS (0<match<1): {len(partial)}")
    if len(partial):
        print("   ", [(str(d), f"{v:.0%}") for d, v in partial.items()][:10])

print("\n=== (c) WDO rolagem 2026 (WDON26->WDOQ26, vencto 01/07): dia exato da troca ===")
contw = pd.read_parquet(BASE / "WDO_cont_N_M15.parquet")
contw["dia"] = contw["datetime_b3"].dt.date
q = pd.read_parquet(BASE / "WDOQ26_M15.parquet")
mw = contw.merge(q[["epoch", "close"]], on="epoch", suffixes=("", "_q"))
mw["dia"] = mw["datetime_b3"].dt.date
mm = mw.assign(eq=(mw["close"] == mw["close_q"])).groupby("dia")["eq"].mean()
import datetime as dt
foco = {d: v for d, v in mm.items() if dt.date(2026, 6, 22) <= d <= dt.date(2026, 7, 8)}
for d, v in foco.items():
    print(f"  {d}: $N==WDOQ26 em {v:.0%} das barras")
