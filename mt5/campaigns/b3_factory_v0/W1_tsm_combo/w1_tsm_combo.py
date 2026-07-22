"""
W1 - TSM WIN + combo WIN/WDO
=============================
Mesma mecanica TSM do W0 (L em {63,126}, F em {5,21}, com e sem vol
targeting 10% / vol20d / cap 3x) aplicada ao proxy WIN (ibov - cdi).
Depois: combo 50/50 (rebalance diario) entre stream TSM WDO L=126/F=5
e TSM WIN L=126/F=5 (A) sem vol targeting nas pernas, e (B) com vol
targeting 10% em cada perna antes de combinar.

Regras invioláveis do projeto:
- PROIBIDO usar dados de 2025-01-01 em diante de qualquer parquet B3
  (proxy ou futuro). Filtra imediatamente apos load, imprime linhas
  removidas.
- Custos B3: 1.5 bps RT base, sensibilidade 1.0 e 2.0 bps tambem
  reportada. + 1.2 bps/mes de rolagem enquanto posicionado (TSM esta
  sempre posicionado, long ou short, exceto raros dias signal==0).
- Split proxies: DISCOVERY 2000-2018 / CONFIRM 2019-2024. Reporta
  tambem FULL 2000-2024 (soma das duas). TODAS as celulas do grid,
  sem cherry-pick.

Mecanica TSM (block-rebalance, sem look-ahead):
- A cada F dias uteis (rebalance day t), calcula sinal = sign(preco
  sintetico[t] / preco sintetico[t-L] - 1) usando dados ate t
  (inclusive).
- Posicao (sinal, e escalar de vol-targeting se aplicavel) fica
  constante e e aplicada aos retornos dos F dias uteis seguintes
  (t+1 .. t+F), ou seja, o sinal decidido no fechamento de t so afeta
  o retorno do dia seguinte em diante (sem look-ahead).
- Vol targeting: scalar = clip(target_vol / vol_realizada_20d, 0, 3),
  vol_realizada_20d = std(ret diario, 20d) * sqrt(252), recalculado a
  cada rebalance day igual ao sinal (mesmo timing, mesma janela ate t).
- Custo de trade: RT bps cobrado integralmente no primeiro dia de cada
  bloco (entrada+saida ja embutidas no RT), ou seja, 1 trade = 1 bloco
  de F dias. n_trades = numero de blocos.
- Rolagem: 1.2 bps/mes (=1.2/21 bps por dia util) cobrada em todo dia
  em que a posicao esta aberta (sign != 0), independente do dia ser ou
  nao dia de rebalance.

Script autossuficiente: pandas/numpy/pyarrow apenas.
"""

import numpy as np
import pandas as pd
from pathlib import Path

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
PROXY_PATH = Path(r"C:/Users/Notebook/Documents/Claude/Projects/Finanças/mt5/campaigns/b3_swing_v1_proxy/proxy_raw.parquet")
OUT_DIR = Path(r"C:/Users/Notebook/Documents/Claude/Projects/Finanças/mt5/campaigns/b3_factory_v0/W1_tsm_combo")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TRADING_DAYS_YEAR = 252
CUTOFF = pd.Timestamp("2025-01-01")

# ----------------------------------------------------------------------
# 1. Load + hard filter 2025+
# ----------------------------------------------------------------------
df = pd.read_parquet(PROXY_PATH)
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date").reset_index(drop=True)

n_before = len(df)
df = df[df["date"] < CUTOFF].reset_index(drop=True)
n_removed = n_before - len(df)
print(f"[FILTRO 2025+] linhas antes={n_before} depois={len(df)} removidas={n_removed}")
print(f"[RANGE] {df['date'].min().date()} -> {df['date'].max().date()}")

# ----------------------------------------------------------------------
# 2. Proxy returns (formulas do pre-registro)
# ----------------------------------------------------------------------
df["ffr_ad"] = (1 + df["ffr_aa"] / 100) ** (1 / 252) - 1
df["win_proxy_ret"] = df["ibov"].pct_change() - df["cdi_ad"] / 100
df["wdo_proxy_ret"] = df["usdbrl"].pct_change() - (df["cdi_ad"] / 100 - df["ffr_ad"])
df = df.dropna(subset=["win_proxy_ret", "wdo_proxy_ret"]).reset_index(drop=True)

df["win_price"] = (1 + df["win_proxy_ret"]).cumprod()
df["wdo_price"] = (1 + df["wdo_proxy_ret"]).cumprod()

print(f"[RETORNOS] n_dias_uteis={len(df)}")

# ----------------------------------------------------------------------
# 3. Custos
# ----------------------------------------------------------------------
RT_BPS_LEVELS = [1.0, 1.5, 2.0]
ROLL_BPS_MONTH = 1.2
ROLL_BPS_DAY = ROLL_BPS_MONTH / 21.0

# ----------------------------------------------------------------------
# 4. Motor TSM (block-rebalance)
# ----------------------------------------------------------------------
def tsm_stream(price: pd.Series, ret: pd.Series, L: int, F: int, vol_target: float | None):
    """
    Retorna (net_ret por RT-bps-level dict, gross_ret, position_scalar, trade_flag)
    price, ret: pd.Series indexado 0..n-1 (retorno diario do ativo subjacente)
    L: lookback dias uteis
    F: holding period dias uteis (bloco)
    vol_target: None = sem vol targeting; float (ex 0.10) = alvo anualizado
    """
    n = len(price)
    pos = np.zeros(n)          # posicao (sinal * scalar) EM VIGOR no retorno do dia i
    is_trade_day = np.zeros(n, dtype=bool)  # dia em que um novo bloco comeca (custo RT)
    vol20 = ret.rolling(20).std() * np.sqrt(TRADING_DAYS_YEAR)

    # rebalance days: 0-indexed dias uteis onde ha historico L suficiente
    # primeiro rebalance possivel: indice L (precisa price[t]/price[t-L])
    t = L
    while t < n:
        # sinal decidido no fechamento do dia t, usa dados ate t (inclusive)
        raw_ret_L = price.iloc[t] / price.iloc[t - L] - 1
        sign = np.sign(raw_ret_L)
        if vol_target is not None:
            v = vol20.iloc[t]
            if pd.isna(v) or v <= 0:
                scalar = 0.0
            else:
                scalar = min(vol_target / v, 3.0)
        else:
            scalar = 1.0
        position = sign * scalar

        block_start = t + 1
        block_end = min(t + F, n - 1)  # aplica aos retornos de t+1 .. t+F (inclusive)
        if block_start <= block_end:
            pos[block_start:block_end + 1] = position
            is_trade_day[block_start] = True
        t += F

    gross_ret = pos * ret.values
    # rolagem: cobrada todo dia com posicao aberta (pos != 0)
    open_mask = (pos != 0)
    roll_cost = open_mask * (ROLL_BPS_DAY / 10000.0)

    net_ret_by_cost = {}
    for rt in RT_BPS_LEVELS:
        trade_cost = is_trade_day * (rt / 10000.0)
        net_ret_by_cost[rt] = gross_ret - trade_cost - roll_cost

    n_trades = int(is_trade_day.sum())
    return net_ret_by_cost, gross_ret, pos, n_trades, is_trade_day


# ----------------------------------------------------------------------
# 5. Metricas
# ----------------------------------------------------------------------
def metrics(ret: np.ndarray, n_trades: int) -> dict:
    ret = np.asarray(ret)
    n = len(ret)
    n_trades = int(n_trades) if n_trades == n_trades else 0  # NaN-safe
    if n == 0 or np.all(ret == 0):
        return dict(net_ann_pct=0.0, sharpe=0.0, maxdd_pct=0.0, pf=np.nan,
                     monthly_mean_pct=0.0, n_trades=n_trades)
    equity = (1 + ret).cumprod()
    total_years = n / TRADING_DAYS_YEAR
    net_ann = (equity[-1] ** (1 / total_years) - 1) if total_years > 0 and equity[-1] > 0 else -1.0
    mu, sigma = ret.mean(), ret.std(ddof=1)
    sharpe = (mu / sigma) * np.sqrt(TRADING_DAYS_YEAR) if sigma > 0 else 0.0
    running_max = np.maximum.accumulate(equity)
    dd = equity / running_max - 1
    maxdd = dd.min()
    gains = ret[ret > 0].sum()
    losses = -ret[ret < 0].sum()
    pf = gains / losses if losses > 0 else np.nan
    monthly_mean = (1 + mu) ** 21 - 1
    return dict(net_ann_pct=net_ann * 100, sharpe=sharpe, maxdd_pct=maxdd * 100,
                 pf=pf, monthly_mean_pct=monthly_mean * 100, n_trades=n_trades)


PERIODS = {
    "DISCOVERY_2000_2018": (pd.Timestamp("2000-01-01"), pd.Timestamp("2018-12-31")),
    "CONFIRM_2019_2024":   (pd.Timestamp("2019-01-01"), pd.Timestamp("2024-12-31")),
    "FULL_2000_2024":      (pd.Timestamp("2000-01-01"), pd.Timestamp("2024-12-31")),
}

def slice_mask(dates, period):
    lo, hi = PERIODS[period]
    return (dates >= lo) & (dates <= hi)


# ----------------------------------------------------------------------
# 6. Grid completo WIN: L x F x vol_target x cost x period
# ----------------------------------------------------------------------
L_GRID = [63, 126]
F_GRID = [5, 21]
VT_GRID = [None, 0.10]

results_rows = []
win_streams = {}   # (L,F,vt) -> net_ret_by_cost dict (full series, gross too)

for L in L_GRID:
    for F in F_GRID:
        for vt in VT_GRID:
            net_by_cost, gross, pos, n_trades, is_trade_day = tsm_stream(df["win_price"], df["win_proxy_ret"], L, F, vt)
            win_streams[(L, F, vt)] = dict(net_by_cost=net_by_cost, gross=gross, pos=pos,
                                             n_trades=n_trades, is_trade_day=is_trade_day)
            for period in PERIODS:
                mask = slice_mask(df["date"], period)
                for rt in RT_BPS_LEVELS:
                    ret_slice = net_by_cost[rt][mask.values]
                    n_trades_period = int(is_trade_day[mask.values].sum())
                    m = metrics(ret_slice, n_trades=n_trades_period)
                    results_rows.append(dict(
                        market="WIN", L=L, F=F, vol_target=("10%" if vt else "off"),
                        period=period, rt_bps=rt, **m
                    ))

res_df = pd.DataFrame(results_rows)
res_df.to_csv(OUT_DIR / "w1_win_full_grid.csv", index=False)

print("\n" + "=" * 100)
print("GRID COMPLETO WIN (TSM L x F x vol_target x custo x periodo) - TODAS AS CELULAS")
print("=" * 100)
with pd.option_context("display.max_rows", None, "display.width", 200):
    print(res_df.to_string(index=False))

# ----------------------------------------------------------------------
# 7. WDO L=126/F=5 (com e sem vol targeting) para o combo
# ----------------------------------------------------------------------
wdo_streams = {}
for vt in VT_GRID:
    net_by_cost, gross, pos, n_trades, is_trade_day = tsm_stream(df["wdo_price"], df["wdo_proxy_ret"], 126, 5, vt)
    wdo_streams[vt] = dict(net_by_cost=net_by_cost, gross=gross, pos=pos,
                             n_trades=n_trades, is_trade_day=is_trade_day)

wdo_rows = []
for vt in VT_GRID:
    for period in PERIODS:
        mask = slice_mask(df["date"], period)
        for rt in RT_BPS_LEVELS:
            ret_slice = wdo_streams[vt]["net_by_cost"][rt][mask.values]
            n_trades_period = int(wdo_streams[vt]["is_trade_day"][mask.values].sum())
            m = metrics(ret_slice, n_trades=n_trades_period)
            wdo_rows.append(dict(market="WDO", L=126, F=5, vol_target=("10%" if vt else "off"),
                                  period=period, rt_bps=rt, **m))
wdo_df = pd.DataFrame(wdo_rows)
wdo_df.to_csv(OUT_DIR / "w1_wdo_l126_f5.csv", index=False)

print("\n" + "=" * 100)
print("WDO L=126/F=5 (referencia p/ combo) - TODAS AS CELULAS")
print("=" * 100)
with pd.option_context("display.max_rows", None, "display.width", 200):
    print(wdo_df.to_string(index=False))

# ----------------------------------------------------------------------
# 8. Combos 50/50
#    A: sem vol targeting nas pernas
#    B: com vol targeting 10% em cada perna (antes de combinar)
# ----------------------------------------------------------------------
def combo_5050(gross_a, gross_b, is_trade_a, is_trade_b, pos_a, pos_b):
    """Combo diario 50/50 de dois streams GROSS; aplica custos RT (por perna,
    no dia de trade daquela perna) e rolagem (por perna, enquanto posicao
    aberta), depois pesa 0.5/0.5."""
    combo_gross = 0.5 * gross_a + 0.5 * gross_b
    open_a = (pos_a != 0)
    open_b = (pos_b != 0)
    roll = 0.5 * open_a * (ROLL_BPS_DAY / 10000.0) + 0.5 * open_b * (ROLL_BPS_DAY / 10000.0)
    net_by_cost = {}
    for rt in RT_BPS_LEVELS:
        trade_cost = 0.5 * is_trade_a * (rt / 10000.0) + 0.5 * is_trade_b * (rt / 10000.0)
        net_by_cost[rt] = combo_gross - trade_cost - roll
    return net_by_cost, combo_gross


# recompute trade-day flags for the two legs used in combo (L=126,F=5)
def trade_flags(price, ret, L, F, vol_target):
    n = len(price)
    is_trade_day = np.zeros(n, dtype=bool)
    pos = np.zeros(n)
    vol20 = ret.rolling(20).std() * np.sqrt(TRADING_DAYS_YEAR)
    t = L
    while t < n:
        raw_ret_L = price.iloc[t] / price.iloc[t - L] - 1
        sign = np.sign(raw_ret_L)
        if vol_target is not None:
            v = vol20.iloc[t]
            scalar = 0.0 if (pd.isna(v) or v <= 0) else min(vol_target / v, 3.0)
        else:
            scalar = 1.0
        position = sign * scalar
        block_start, block_end = t + 1, min(t + F, n - 1)
        if block_start <= block_end:
            pos[block_start:block_end + 1] = position
            is_trade_day[block_start] = True
        t += F
    return is_trade_day, pos

is_trade_win_off, pos_win_off = trade_flags(df["win_price"], df["win_proxy_ret"], 126, 5, None)
is_trade_wdo_off, pos_wdo_off = trade_flags(df["wdo_price"], df["wdo_proxy_ret"], 126, 5, None)
is_trade_win_vt, pos_win_vt = trade_flags(df["win_price"], df["win_proxy_ret"], 126, 5, 0.10)
is_trade_wdo_vt, pos_wdo_vt = trade_flags(df["wdo_price"], df["wdo_proxy_ret"], 126, 5, 0.10)

gross_win_off = win_streams[(126, 5, None)]["gross"]
gross_wdo_off = wdo_streams[None]["gross"]
gross_win_vt = win_streams[(126, 5, 0.10)]["gross"]
gross_wdo_vt = wdo_streams[0.10]["gross"]

comboA_net, comboA_gross = combo_5050(gross_win_off, gross_wdo_off, is_trade_win_off, is_trade_wdo_off, pos_win_off, pos_wdo_off)
comboB_net, comboB_gross = combo_5050(gross_win_vt, gross_wdo_vt, is_trade_win_vt, is_trade_wdo_vt, pos_win_vt, pos_wdo_vt)

combo_rows = []
for name, net_by_cost, trade_a, trade_b in [
    ("Combo_A_50_50_sem_voltgt", comboA_net, is_trade_win_off, is_trade_wdo_off),
    ("Combo_B_50_50_voltgt10pct", comboB_net, is_trade_win_vt, is_trade_wdo_vt),
]:
    for period in PERIODS:
        mask = slice_mask(df["date"], period)
        for rt in RT_BPS_LEVELS:
            ret_slice = net_by_cost[rt][mask.values]
            n_trades_combo = int(trade_a[mask.values].sum() + trade_b[mask.values].sum())
            m = metrics(ret_slice, n_trades=n_trades_combo)
            combo_rows.append(dict(stream=name, period=period, rt_bps=rt, **m))

combo_df = pd.DataFrame(combo_rows)
combo_df.to_csv(OUT_DIR / "w1_combos.csv", index=False)

print("\n" + "=" * 100)
print("COMBOS 50/50 WIN+WDO (L=126/F=5) - TODAS AS CELULAS")
print("=" * 100)
with pd.option_context("display.max_rows", None, "display.width", 200):
    print(combo_df.to_string(index=False))

# ----------------------------------------------------------------------
# 9. Correlacao diaria WIN x WDO (streams L=126/F=5, RT=1.5bps)
# ----------------------------------------------------------------------
ret_win_off = win_streams[(126, 5, None)]["net_by_cost"][1.5]
ret_wdo_off = wdo_streams[None]["net_by_cost"][1.5]
ret_win_vt = win_streams[(126, 5, 0.10)]["net_by_cost"][1.5]
ret_wdo_vt = wdo_streams[0.10]["net_by_cost"][1.5]

corr_off = np.corrcoef(ret_win_off, ret_wdo_off)[0, 1]
corr_vt = np.corrcoef(ret_win_vt, ret_wdo_vt)[0, 1]

print("\n" + "=" * 100)
print("CORRELACAO DIARIA WIN x WDO (streams L=126/F=5, RT=1.5bps, net)")
print("=" * 100)
print(f"Sem vol targeting: corr = {corr_off:.4f}")
print(f"Com vol targeting 10%: corr = {corr_vt:.4f}")

# ----------------------------------------------------------------------
# 10. Salvar series diarias (data, ret) - streams WIN e combos
# ----------------------------------------------------------------------
series_out = pd.DataFrame({"date": df["date"]})
series_out["win_L126_F5_off_ret_rt1.5"] = ret_win_off
series_out["win_L126_F5_voltgt10_ret_rt1.5"] = ret_win_vt
series_out["wdo_L126_F5_off_ret_rt1.5"] = ret_wdo_off
series_out["wdo_L126_F5_voltgt10_ret_rt1.5"] = ret_wdo_vt
series_out["comboA_50_50_off_ret_rt1.5"] = comboA_net[1.5]
series_out["comboB_50_50_voltgt10_ret_rt1.5"] = comboB_net[1.5]
series_out.to_csv(OUT_DIR / "w1_daily_series.csv", index=False)

print(f"\n[OK] CSVs salvos em: {OUT_DIR}")
print(" - w1_win_full_grid.csv")
print(" - w1_wdo_l126_f5.csv")
print(" - w1_combos.csv")
print(" - w1_daily_series.csv (date,ret) WIN streams + combos")
