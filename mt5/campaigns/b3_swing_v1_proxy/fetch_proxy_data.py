#!/usr/bin/env python3
"""
b3_swing_v1_proxy — coleta de séries públicas p/ proxies de futuros ajustados.

Fontes:
  - BCB SGS (api.bcb.gov.br): série 7 = Ibovespa fechamento; série 1 =
    USD/BRL PTAX venda; série 12 = CDI diário (% a.d.).
  - FRED (fredgraph.csv): DFF = Effective Federal Funds Rate (% a.a.).

Saída: proxy_raw.parquet (uma linha por data, colunas ibov, usdbrl, cdi_ad,
ffr_aa) + PRINT de QA (cobertura, gaps).

Proxies (construídas na fase 0, aqui só coleta):
  win_proxy_ret  = ret(ibov) − cdi_diario
  wdo_proxy_ret  = ret(usdbrl) − (cdi_diario − ffr_diario)
"""
from __future__ import annotations

import io
import json
import time
from datetime import date

import pandas as pd
import requests

OUT = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy\proxy_raw.parquet"

START = date(2000, 1, 1)
END = date(2026, 7, 21)

# SGS 7 (Ibovespa) foi descontinuada pelo BCB (~2021) -> Ibov vem do stooq
SGS = {"usdbrl": 1, "cdi_ad": 12}


def fetch_stooq_ibov() -> pd.Series:
    """Ibov via Yahoo chart API (stooq bloqueia com JS challenge)."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%5EBVSP"
           "?range=30y&interval=1d")
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = pd.to_datetime(res["timestamp"], unit="s")
    close = pd.Series(res["indicators"]["quote"][0]["close"], index=ts.normalize())
    s = close.dropna().astype(float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[(s.index >= pd.Timestamp(START)) & (s.index <= pd.Timestamp(END))]


def fetch_sgs(code: int, start: date, end: date) -> pd.Series:
    """SGS limita ~10 anos por request em séries diárias — busca em blocos."""
    parts = []
    a = start
    while a <= end:
        b = min(date(a.year + 9, 12, 31), end)
        url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
               f"?formato=json&dataInicial={a.strftime('%d/%m/%Y')}"
               f"&dataFinal={b.strftime('%d/%m/%Y')}")
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
        if data:
            df = pd.DataFrame(data)
            df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y")
            df["valor"] = pd.to_numeric(df["valor"].astype(str).str.replace(",", "."))
            parts.append(df.set_index("data")["valor"])
        a = date(b.year + 1, 1, 1)
        time.sleep(0.4)
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated(keep="last")]


def fetch_fred_dff() -> pd.Series:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    dcol = df.columns[0]
    df[dcol] = pd.to_datetime(df[dcol])
    df["DFF"] = pd.to_numeric(df["DFF"], errors="coerce")
    s = df.set_index(dcol)["DFF"].dropna()
    return s[(s.index >= pd.Timestamp(START)) & (s.index <= pd.Timestamp(END))]


def main():
    series = {}
    print("buscando Ibovespa (stooq ^bvp)...")
    series["ibov"] = fetch_stooq_ibov()
    print(f"  ibov: {len(series['ibov'])} obs, {series['ibov'].index.min().date()} -> {series['ibov'].index.max().date()}")
    for name, code in SGS.items():
        print(f"buscando SGS {code} ({name})...")
        series[name] = fetch_sgs(code, START, END)
        print(f"  {name}: {len(series[name])} obs, {series[name].index.min().date()} -> {series[name].index.max().date()}")

    print("buscando FRED DFF...")
    series["ffr_aa"] = fetch_fred_dff()
    print(f"  ffr_aa: {len(series['ffr_aa'])} obs, {series['ffr_aa'].index.min().date()} -> {series['ffr_aa'].index.max().date()}")

    df = pd.DataFrame(series)
    # calendario = dias com Ibov (pregao B3); ffill só p/ taxas (CDI/FFR/PTAX)
    df = df.dropna(subset=["ibov"])
    df[["cdi_ad", "ffr_aa", "usdbrl"]] = df[["cdi_ad", "ffr_aa", "usdbrl"]].ffill()
    df = df.dropna()
    df.index.name = "date"

    print("\nQA:")
    print(f"  linhas finais (dias de pregao c/ tudo): {len(df)}")
    print(f"  range: {df.index.min().date()} -> {df.index.max().date()}")
    gaps = df.index.to_series().diff().dt.days
    print(f"  maior gap entre pregoes: {gaps.max():.0f} dias (feriados longos ~4-5 ok)")
    print(df.describe().to_string())

    df.reset_index().to_parquet(OUT, index=False)
    print(f"\nsalvo: {OUT}")


if __name__ == "__main__":
    main()
