#!/usr/bin/env python3
"""
Verificacao de alinhamento/homogeneidade das barras da serie continua $N (WIN/WDO).

Checa: (1) grade de barras por pregao; (2) alinhamento de sessao (primeira/ultima
barra do dia); (3) rolagens do $N vs vencimentos nominais; (4) $N == contrato mais
liquido (prova 2026 com contratos reais); (5) volume nas janelas de rolagem.
"""
from pathlib import Path
import pandas as pd
import numpy as np

BASE = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")

def load(name):
    df = pd.read_parquet(BASE / name)
    df["dia"] = df["datetime_b3"].dt.date
    df["hhmm"] = df["datetime_b3"].dt.strftime("%H:%M")
    return df

print("#" * 70)
print("# 1) GRADE DE BARRAS POR PREGAO (M15 continuo, 5 anos)")
print("#" * 70)
for sym in ["WIN", "WDO"]:
    df = load(f"{sym}_cont_N_M15.parquet")
    g = df.groupby("dia").size()
    print(f"\n--- {sym}$N M15: {len(g)} pregoes ---")
    print(f"barras/dia: media={g.mean():.1f} mediana={g.median():.0f} p5={g.quantile(.05):.0f} min={g.min()} max={g.max()}")
    # dias esparsos: menos de 60% da mediana
    thr = g.median() * 0.6
    sparse = g[g < thr]
    print(f"dias esparsos (<{thr:.0f} barras): {len(sparse)} ({100*len(sparse)/len(g):.1f}%)")
    if len(sparse):
        print("  piores 10:", [(str(d), int(n)) for d, n in sparse.sort_values().head(10).items()])
    # distribuicao por ano da contagem tipica
    df["ano"] = df["datetime_b3"].dt.year
    by = df.groupby(["ano", "dia"]).size().groupby("ano").agg(["median", "min", "max"])
    print(by.to_string())

print()
print("#" * 70)
print("# 2) ALINHAMENTO DE SESSAO (primeira/ultima barra por dia, M15)")
print("#" * 70)
for sym in ["WIN", "WDO"]:
    df = load(f"{sym}_cont_N_M15.parquet")
    first = df.groupby("dia")["hhmm"].min()
    last = df.groupby("dia")["hhmm"].max()
    print(f"\n--- {sym}$N ---")
    print("primeira barra do dia (top freq):")
    print(first.value_counts().head(5).to_string())
    print("ultima barra do dia (top freq):")
    print(last.value_counts().head(5).to_string())
    odd_first = first[~first.isin(first.value_counts().head(2).index)]
    print(f"dias com abertura fora dos 2 padroes: {len(odd_first)} ({100*len(odd_first)/len(first):.1f}%)")
    if len(odd_first):
        print("  exemplos:", [(str(d), h) for d, h in odd_first.head(8).items()])

print()
print("#" * 70)
print("# 3) ROLAGENS DO $N vs VENCIMENTOS NOMINAIS (gaps overnight anomalos, D1)")
print("#" * 70)
chain = pd.read_csv(BASE.parent / ".." / "Finanças" / "backtests" / "_tmp_chain.csv") if False else None
# vencimentos nominais recalculados aqui (mesma regra do build_futures_chain.py)
from datetime import date, timedelta
def win_expiries(y0, y1):
    out = []
    for y in range(y0, y1 + 1):
        for m in (2, 4, 6, 8, 10, 12):
            anchor = date(y, m, 15)
            cands = [anchor + timedelta(days=o) for o in range(-3, 4)
                     if (anchor + timedelta(days=o)).weekday() == 2]
            out.append(min(cands, key=lambda c: abs((c - anchor).days)))
    return out
def wdo_expiries(y0, y1):
    out = []
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            d = date(y, m, 1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            out.append(d)
    return out

for sym, expfn in [("WIN", win_expiries), ("WDO", wdo_expiries)]:
    d1 = load(f"{sym}_cont_N_D1.parquet").sort_values("epoch").reset_index(drop=True)
    d1["gap_pct"] = (d1["open"] / d1["close"].shift(1) - 1) * 100
    d1 = d1.dropna(subset=["gap_pct"])
    mad = (d1["gap_pct"] - d1["gap_pct"].median()).abs().median()
    thr = d1["gap_pct"].median() + 6 * 1.4826 * mad  # ~6 sigma robusto
    big = d1[d1["gap_pct"].abs() > max(abs(thr), 1.0)]
    exps = set(expfn(2021, 2026))
    print(f"\n--- {sym}$N D1: {len(big)} gaps overnight >|{max(abs(thr),1.0):.2f}%| ---")
    near = 0
    for _, row in big.iterrows():
        dd = row["dia"]
        is_near = any(abs((dd - e).days) <= 7 for e in exps)
        near += is_near
        tag = "PERTO_VENCTO" if is_near else "longe"
        print(f"  {dd}  gap={row['gap_pct']:+.2f}%  [{tag}]")
    print(f"resumo: {near}/{len(big)} gaps grandes caem a ate 7d de um vencimento nominal")

print()
print("#" * 70)
print("# 4) PROVA 2026: $N segue o contrato mais liquido? (M15, overlap real)")
print("#" * 70)
cont = load("WIN_cont_N_M15.parquet")
q26 = load("WINQ26_M15.parquet")
v26 = load("WINV26_M15.parquet")
# volume diario por contrato
vq = q26.groupby("dia")["real_volume"].sum()
vv = v26.groupby("dia")["real_volume"].sum()
# em que dias o continuo casa com cada contrato? (merge por epoch, compara close)
m_q = cont.merge(q26[["epoch", "close"]], on="epoch", suffixes=("", "_q"))
m_v = cont.merge(v26[["epoch", "close"]], on="epoch", suffixes=("", "_v"))
eq_q = m_q.assign(match=(m_q["close"] == m_q["close_q"])).groupby("dia")["match"].mean()
eq_v = m_v.assign(match=(m_v["close"] == m_v["close_v"])).groupby("dia")["match"].mean()
# ultimos 25 pregoes ate 17/07 + janela de rolagem WINQ26 (vencto 2026-08-12 — rolagem ainda nao ocorreu)
# janela mais interessante ja ocorrida: WINM26 -> WINQ26 (vencto WINM26 2026-06-17)
import datetime as _dt
foco = [d for d in sorted(set(cont["dia"])) if _dt.date(2026, 6, 8) <= d <= _dt.date(2026, 6, 24)]
print("\njanela rolagem WINM26->WINQ26 (vencto nominal 17/06):")
print(f"{'dia':12s} {'vol WINQ26':>12s} {'%barras $N==Q26':>16s}")
for d in foco:
    pq = eq_q.get(d, np.nan)
    print(f"{str(d):12s} {vq.get(d, 0):>12,.0f} {100*pq if pq==pq else float('nan'):>15.0f}%")
print("\n(se %==Q26 salta de ~0% para ~100% alguns dias ANTES de 17/06, a rolagem por liquidez esta correta)")

print()
print("#" * 70)
print("# 5) VOLUME DO $N NAS JANELAS DE ROLAGEM (M15 agregado por dia)")
print("#" * 70)
for sym, expfn in [("WIN", win_expiries), ("WDO", wdo_expiries)]:
    df = load(f"{sym}_cont_N_M15.parquet")
    vol = df.groupby("dia")["real_volume"].sum()
    med = vol.median()
    exps = [e for e in expfn(2021, 2026) if vol.index.min() <= e <= vol.index.max()]
    drops = []
    for e in exps:
        win_ = vol[(vol.index >= e - timedelta(days=5)) & (vol.index <= e + timedelta(days=5))]
        if len(win_) and win_.min() < 0.3 * med:
            drops.append((e, int(win_.min())))
    print(f"\n--- {sym}$N: mediana vol/dia={med:,.0f} | vencimentos no periodo={len(exps)} ---")
    if drops:
        print(f">>> {len(drops)} janelas de rolagem com vol < 30% da mediana (SUSPEITO):")
        for e, v in drops: print(f"    vencto {e}: vol min na janela = {v:,}")
    else:
        print("OK: nenhuma janela de rolagem com colapso de volume (serie nao fica no contrato morrendo)")
