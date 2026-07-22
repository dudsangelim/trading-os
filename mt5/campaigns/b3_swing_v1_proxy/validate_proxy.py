import pandas as pd

BASE = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy"
MT5 = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"

px = pd.read_parquet(BASE + r"\proxy_raw.parquet").set_index("date")
px["win_proxy"] = px["ibov"].pct_change() - px["cdi_ad"] / 100
px["ffr_ad"] = (1 + px["ffr_aa"] / 100) ** (1 / 252) - 1
px["wdo_proxy"] = px["usdbrl"].pct_change() - (px["cdi_ad"] / 100 - px["ffr_ad"])

for sym, col in [("WIN", "win_proxy"), ("WDO", "wdo_proxy")]:
    real = pd.read_parquet(MT5 + rf"\{sym}_cont_ADJprop_D1.parquet")
    real["date"] = pd.to_datetime(real["datetime_b3"]).dt.normalize()
    real = real.set_index("date")["close"].pct_change().rename("real")
    m = pd.concat([px[col], real], axis=1).dropna()
    corr = m[col].corr(m["real"])
    print(f"{sym}: overlap n={len(m)} | corr diaria proxy vs real = {corr:.4f}")
    print(f"  drift medio (bps/dia): proxy={m[col].mean()*1e4:+.2f}  real={m['real'].mean()*1e4:+.2f}")
    print(f"  vol diaria (bps): proxy={m[col].std()*1e4:.0f}  real={m['real'].std()*1e4:.0f}")
    for w in (5, 21):
        pw = (1 + m[col]).rolling(w).apply(lambda x: x.prod()) - 1
        rw = (1 + m["real"]).rolling(w).apply(lambda x: x.prod()) - 1
        print(f"  corr retorno {w}d: {pw.corr(rw):.4f}")
