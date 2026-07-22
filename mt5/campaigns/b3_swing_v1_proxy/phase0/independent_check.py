"""Replicacao independente (Fable) dos 2 achados da Fase 0 — codigo proprio,
sem reusar build_proxy_map.py. S3 WIN P=5 F=3 e S2 WDO L=126 F=5."""
import numpy as np
import pandas as pd

BASE = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy"
px = pd.read_parquet(BASE + r"\proxy_raw.parquet").set_index("date").sort_index()
px = px[px.index <= "2024-12-31"]

win_ret = (px["ibov"].pct_change() - px["cdi_ad"] / 100).dropna()
ffr_ad = (1 + px["ffr_aa"] / 100) ** (1 / 252) - 1
wdo_ret = (px["usdbrl"].pct_change() - (px["cdi_ad"] / 100 - ffr_ad)).dropna()

def grid_stats(ret, past, fwd, mode, split_lo, split_hi, label):
    r = ret[(ret.index >= split_lo) & (ret.index <= split_hi)]
    p = (1 + r).cumprod()
    n = len(p)
    idx = list(range(past, n - fwd, fwd))
    sig = np.array([p.iloc[i] / p.iloc[i - past] - 1 for i in idx])
    f = np.array([(p.iloc[i + fwd] / p.iloc[i] - 1) / fwd * 1e4 for i in idx])
    if mode == "tsm":
        up, dn = f[sig > 0], f[sig < 0]
        spread = up.mean() - dn.mean()
        print(f"{label}: n_grid={len(idx)} n_up={len(up)} n_dn={len(dn)} spread_up-dn={spread:+.2f} bps/dia")
    else:
        q = pd.qcut(sig, 5, labels=False)
        q1, q5 = f[q == 0], f[q == 4]
        spread = q1.mean() - q5.mean()
        print(f"{label}: n_grid={len(idx)} n_Q1={len(q1)} n_Q5={len(q5)} spread_Q1-Q5={spread:+.2f} bps/dia")
    return spread

print("== S3 WIN reversao P=5 F=3 ==")
grid_stats(win_ret, 5, 3, "rev", "2000-01-01", "2018-12-31", "  DISCOVERY 2000-18")
grid_stats(win_ret, 5, 3, "rev", "2000-01-01", "2009-12-31", "  H1 2000-09")
grid_stats(win_ret, 5, 3, "rev", "2010-01-01", "2018-12-31", "  H2 2010-18")
grid_stats(win_ret, 5, 3, "rev", "2019-01-01", "2024-12-31", "  CONFIRM 2019-24")

print("== S2 WDO TSM L=126 F=5 ==")
grid_stats(wdo_ret, 126, 5, "tsm", "2000-01-01", "2018-12-31", "  DISCOVERY 2000-18")
grid_stats(wdo_ret, 126, 5, "tsm", "2000-01-01", "2009-12-31", "  H1 2000-09")
grid_stats(wdo_ret, 126, 5, "tsm", "2010-01-01", "2018-12-31", "  H2 2010-18")
grid_stats(wdo_ret, 126, 5, "tsm", "2019-01-01", "2024-12-31", "  CONFIRM 2019-24")

print("== REAL-CHECK futuros ADJprop 2021-07 -> 2024-12 ==")
MT5 = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
for sym, past, fwd, mode in [("WIN", 5, 3, "rev"), ("WDO", 126, 5, "tsm")]:
    real = pd.read_parquet(MT5 + rf"\{sym}_cont_ADJprop_D1.parquet")
    real["date"] = pd.to_datetime(real["datetime_b3"]).dt.normalize()
    ret = real.set_index("date")["close"].pct_change().dropna()
    ret = ret[(ret.index >= "2021-07-19") & (ret.index <= "2024-12-31")]
    grid_stats(ret, past, fwd, mode, ret.index.min(), ret.index.max(), f"  {sym} real")
