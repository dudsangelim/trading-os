"""Estimativa de expectancy anual da mecanica TSM WDO simples (pre-Fase 1).
Estado = sinal(ret 126d); posicao = estado; reavaliacao a cada 5 pregoes.
Custos: 6 bps RT por VIRADA de posicao + 2 bps por rolagem mensal (12/ano).
Janelas: proxy confirm 2019-2024 e futuros reais 2021-2024. SEM 2025+."""
import numpy as np
import pandas as pd

BASE = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy"
MT5 = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"

def run(ret: pd.Series, label: str, L=126, F=5, cost_flip_bps=6.0, roll_bps_yr=24.0):
    p = (1 + ret).cumprod()
    n = len(p)
    pos = np.zeros(n)
    state = 0.0
    flips = 0
    for i in range(L, n):
        if (i - L) % F == 0:  # reavalia a cada F pregoes
            new_state = np.sign(p.iloc[i] / p.iloc[i - L] - 1)
            if new_state != 0 and new_state != state:
                flips += 1
                state = new_state
        pos[i] = state
    # retorno da estrategia: posicao definida ao fim de i vale para i+1
    strat = pd.Series(pos, index=ret.index).shift(1).fillna(0) * ret
    yrs = len(strat) / 252
    gross_ann = strat.mean() * 252
    cost_ann = (flips * cost_flip_bps / 1e4) / yrs + roll_bps_yr / 1e4
    net_ann = gross_ann - cost_ann
    vol_ann = strat.std() * np.sqrt(252)
    eq = (1 + strat).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    time_in = (pos != 0).mean()
    print(f"{label}: {yrs:.1f} anos | flips/ano={flips/yrs:.1f} | t_no_mercado={time_in:.0%}")
    print(f"  alpha gross={gross_ann*100:+.2f}%/ano  custos={cost_ann*100:.2f}%  "
          f"NET={net_ann*100:+.2f}%/ano  vol={vol_ann*100:.1f}%  sharpe_net={net_ann/vol_ann:.2f}  maxDD={dd*100:.1f}%")

px = pd.read_parquet(BASE + r"\proxy_raw.parquet").set_index("date").sort_index()
px = px[px.index <= "2024-12-31"]
ffr_ad = (1 + px["ffr_aa"] / 100) ** (1 / 252) - 1
wdo = (px["usdbrl"].pct_change() - (px["cdi_ad"] / 100 - ffr_ad)).dropna()

print("== Proxy WDO ==")
run(wdo["2018-07-01":], "confirm 2019-24 (c/ warmup 126d)")
run(wdo["2009-07-01":], "2010-2024 (regime moderno inteiro)")
run(wdo, "2000-2024 (tudo)")

real = pd.read_parquet(MT5 + r"\WDO_cont_ADJprop_D1.parquet")
real["date"] = pd.to_datetime(real["datetime_b3"]).dt.normalize()
rr = real.set_index("date")["close"].pct_change().dropna()
rr = rr[rr.index <= "2024-12-31"]
print("== Futuros reais ADJprop (warmup dentro da janela) ==")
run(rr, "real 2021-07 -> 2024-12")
