"""
W0 -- Fase 1 TSM (Time Series Momentum) WDO
Campanha b3_factory_v0.

Mecanica: estado = sinal(retorno acumulado L pregoes) na serie proxy WDO
(e real-check nos futuros); posicao = estado (long/short), reavaliada a
cada F pregoes; posicao definida no fechamento de t vale a partir de t+1.

Grid: L in {63,126} x F in {5,21} x vol_targeting in {off, on}.
Vol targeting: peso = min(3, 0.10/vol20d_anualizada), recalculado no rebalance.

Janelas: proxy DISCOVERY (2000-2018), proxy CONFIRM (2019-2024),
real-check WDO ADJprop 2021-07..2024-12.

Regras inegociaveis:
 - PROIBIDO dado >= 2025-01-01 em qualquer parquet B3 (proxy ou futuro).
   Filtrar imediatamente apos load, imprimir linhas removidas.
 - Custos B3: 1.5 bps RT (sensibilidade 1.0 e 2.0 tambem reportadas) +
   1.2 bps/mes de rolagem enquanto posicionado (aplicado diariamente
   como 1.2/21 bps por dia posicionado).
 - Reportar TODAS as celulas do grid, sem cherry-pick.
"""

import numpy as np
import pandas as pd
import json
import os

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 20)

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
PROXY_PATH = r"C:/Users/Notebook/Documents/Claude/Projects/Finanças/mt5/campaigns/b3_swing_v1_proxy/proxy_raw.parquet"
REAL_WDO_PATH = r"C:/Users/Notebook/Documents/Claude/Projects/B3 Futuros/mt5_history/WDO_cont_ADJprop_D1.parquet"
OUT_DIR = r"C:/Users/Notebook/Documents/Claude/Projects/Finanças/mt5/campaigns/b3_factory_v0/W0_tsm_wdo"
os.makedirs(OUT_DIR, exist_ok=True)

CUTOFF = pd.Timestamp("2025-01-01")

RT_BASE_BPS = 1.5
RT_SENS_BPS = [1.0, 1.5, 2.0]
ROLLOVER_BPS_PER_MONTH = 1.2
TRADING_DAYS_PER_MONTH = 21
ROLLOVER_BPS_PER_DAY = ROLLOVER_BPS_PER_MONTH / TRADING_DAYS_PER_MONTH

L_GRID = [63, 126]
F_GRID = [5, 21]
VOLT_GRID = [False, True]

VOL_LOOKBACK = 20
VOL_TARGET_ANN = 0.10
VOL_TARGET_CAP = 3.0

DISCOVERY_START, DISCOVERY_END = pd.Timestamp("2000-01-01"), pd.Timestamp("2018-12-31")
CONFIRM_START, CONFIRM_END = pd.Timestamp("2019-01-01"), pd.Timestamp("2024-12-31")
REALCHECK_START, REALCHECK_END = pd.Timestamp("2021-07-01"), pd.Timestamp("2024-12-31")


# ----------------------------------------------------------------------
# Load & filter data
# ----------------------------------------------------------------------
def load_proxy():
    df = pd.read_parquet(PROXY_PATH)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    n_before = len(df)
    df = df[df["date"] < CUTOFF].reset_index(drop=True)
    n_removed = n_before - len(df)
    print(f"[PROXY] linhas removidas por corte >=2025-01-01: {n_removed} (de {n_before} -> {len(df)})")

    df["ffr_ad"] = (1 + df["ffr_aa"] / 100.0) ** (1 / 252.0) - 1
    df["wdo_proxy_ret"] = df["usdbrl"].pct_change() - (df["cdi_ad"] / 100.0 - df["ffr_ad"])
    df = df.dropna(subset=["wdo_proxy_ret"]).reset_index(drop=True)
    df["wdo_proxy_price"] = (1 + df["wdo_proxy_ret"]).cumprod()
    return df[["date", "wdo_proxy_ret", "wdo_proxy_price"]].rename(
        columns={"wdo_proxy_ret": "ret", "wdo_proxy_price": "price"}
    )


def load_real_wdo():
    df = pd.read_parquet(REAL_WDO_PATH)
    df = df.rename(columns={"datetime_b3": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    n_before = len(df)
    df = df[df["date"] < CUTOFF].reset_index(drop=True)
    n_removed = n_before - len(df)
    print(f"[REAL WDO ADJprop] linhas removidas por corte >=2025-01-01: {n_removed} (de {n_before} -> {len(df)})")

    df["ret"] = df["close"].pct_change()
    df = df.dropna(subset=["ret"]).reset_index(drop=True)
    df["price"] = (1 + df["ret"]).cumprod()
    return df[["date", "ret", "price"]]


# ----------------------------------------------------------------------
# TSM engine
# ----------------------------------------------------------------------
def run_tsm(df, L, F, vol_target):
    """
    df: DataFrame with columns date, ret, price (sorted ascending, daily).
    Returns df with added columns: state, weight, pos, gross_ret, net_ret_<bps...>, block_id
    """
    n = len(df)
    ret = df["ret"].values
    price = df["price"].values

    cum_ret_L = np.full(n, np.nan)
    for i in range(L, n):
        cum_ret_L[i] = price[i] / price[i - L] - 1.0

    vol20 = pd.Series(ret).rolling(VOL_LOOKBACK).std().values * np.sqrt(252)

    state = np.full(n, np.nan)
    weight = np.full(n, np.nan)
    pos = np.zeros(n)  # position applied ON day t (decided at close of t-1, or earlier rebalance)

    start_idx = L  # first day with valid L-day lookback
    cur_state = 0.0
    cur_weight = 0.0
    day_counter = 0

    for i in range(start_idx, n):
        is_rebalance = (day_counter % F == 0)
        if is_rebalance:
            s = np.sign(cum_ret_L[i])
            if s == 0:
                s = cur_state  # flat cum ret -> keep prior state (rare)
            cur_state = s
            if vol_target:
                v = vol20[i]
                if np.isnan(v) or v <= 0:
                    w = 0.0
                else:
                    w = min(VOL_TARGET_CAP, VOL_TARGET_ANN / v)
            else:
                w = 1.0
            cur_weight = w
        state[i] = cur_state
        weight[i] = cur_weight
        day_counter += 1
        # position decided at close of day i applies from day i+1
        if i + 1 < n:
            pos[i + 1] = cur_state * cur_weight

    out = df.copy()
    out["state"] = state
    out["weight"] = weight
    out["pos"] = pos
    out["gross_ret"] = out["pos"] * out["ret"]

    turnover = np.abs(np.diff(pos, prepend=0.0))
    out["turnover"] = turnover

    for rt in RT_SENS_BPS:
        cost = turnover * (rt / 10000.0)
        rollover = np.where(pos != 0, ROLLOVER_BPS_PER_DAY / 10000.0, 0.0)
        out[f"net_ret_{rt}"] = out["gross_ret"] - cost - rollover

    # block id: increments each time position value changes (new rebalance regime)
    pos_rounded = np.round(pos, 10)
    block_change = np.zeros(n, dtype=bool)
    block_change[0] = True
    block_change[1:] = pos_rounded[1:] != pos_rounded[:-1]
    out["block_id"] = np.cumsum(block_change)

    out = out.iloc[start_idx + 1:].reset_index(drop=True)  # drop warmup / first no-position day
    return out


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------
def compute_metrics(window_df, net_col):
    r = window_df[net_col].dropna()
    n = len(r)
    if n < 5:
        return dict(net_ann_pct=np.nan, sharpe=np.nan, maxdd_pct=np.nan, pf=np.nan,
                     monthly_mean_pct=np.nan, n_trades=np.nan, n_days=n)

    equity = (1 + r).cumprod()
    total_ret = equity.iloc[-1] - 1.0
    n_years = n / 252.0
    net_ann_pct = ((1 + total_ret) ** (1 / n_years) - 1) * 100 if n_years > 0 else np.nan

    mu = r.mean()
    sd = r.std()
    sharpe = (mu / sd) * np.sqrt(252) if sd > 0 else np.nan

    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    maxdd_pct = dd.min() * 100

    # PF por bloco de trade (segmentos de posicao constante)
    block_pnl = window_df.groupby("block_id").apply(
        lambda g: (1 + g[net_col]).prod() - 1.0
    )
    gains = block_pnl[block_pnl > 0].sum()
    losses = block_pnl[block_pnl < 0].sum()
    pf = gains / abs(losses) if losses < 0 else np.nan
    n_trades = block_pnl.shape[0]

    # media mensal
    tmp = window_df.set_index("date")[net_col]
    monthly = (1 + tmp).resample("ME").prod() - 1.0
    monthly_mean_pct = monthly.mean() * 100 if len(monthly) > 0 else np.nan

    return dict(
        net_ann_pct=round(net_ann_pct, 3),
        sharpe=round(sharpe, 3) if not np.isnan(sharpe) else np.nan,
        maxdd_pct=round(maxdd_pct, 3),
        pf=round(pf, 3) if not np.isnan(pf) else np.nan,
        monthly_mean_pct=round(monthly_mean_pct, 4),
        n_trades=int(n_trades),
        n_days=n,
    )


def slice_window(df, start, end):
    return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    proxy = load_proxy()
    real = load_real_wdo()

    print(f"\n[PROXY] range: {proxy['date'].min().date()} .. {proxy['date'].max().date()}  n={len(proxy)}")
    print(f"[REAL WDO] range: {real['date'].min().date()} .. {real['date'].max().date()}  n={len(real)}\n")

    results = []
    cell_cache = {}  # (L,F,volt) -> full backtest df (proxy)
    cell_cache_real = {}

    for L in L_GRID:
        for F in F_GRID:
            for volt in VOLT_GRID:
                bt_proxy = run_tsm(proxy, L, F, volt)
                cell_cache[(L, F, volt)] = bt_proxy
                bt_real = run_tsm(real, L, F, volt)
                cell_cache_real[(L, F, volt)] = bt_real

                for rt in RT_SENS_BPS:
                    net_col = f"net_ret_{rt}"

                    disc_win = slice_window(bt_proxy, DISCOVERY_START, DISCOVERY_END)
                    conf_win = slice_window(bt_proxy, CONFIRM_START, CONFIRM_END)
                    real_win = slice_window(bt_real, REALCHECK_START, REALCHECK_END)

                    for label, win in [
                        ("proxy_DISCOVERY_2000_2018", disc_win),
                        ("proxy_CONFIRM_2019_2024", conf_win),
                        ("real_check_WDO_ADJprop_2021H2_2024", real_win),
                    ]:
                        m = compute_metrics(win, net_col)
                        results.append(dict(
                            L=L, F=F, vol_targeting=volt, rt_bps=rt, window=label, **m
                        ))

    res_df = pd.DataFrame(results)
    res_df_path = os.path.join(OUT_DIR, "grid_results.csv")
    res_df.to_csv(res_df_path, index=False)

    print("=" * 110)
    print("GRID COMPLETO (todas as celulas, sem cherry-pick) -- custo base 1.5 bps RT")
    print("=" * 110)
    base = res_df[res_df["rt_bps"] == RT_BASE_BPS]
    with pd.option_context("display.max_rows", 200):
        print(base.to_string(index=False))

    print("\n" + "=" * 110)
    print("SENSIBILIDADE DE CUSTO (1.0 / 1.5 / 2.0 bps RT) -- todas celulas todas janelas")
    print("=" * 110)
    with pd.option_context("display.max_rows", 300):
        print(res_df.sort_values(["window", "L", "F", "vol_targeting", "rt_bps"]).to_string(index=False))

    # ------------------------------------------------------------------
    # Tabela ANO A ANO para L=126, F=5, com e sem vol targeting (proxy, custo base 1.5bps)
    # ------------------------------------------------------------------
    print("\n" + "=" * 110)
    print("TABELA ANO A ANO -- proxy WDO, L=126 F=5, custo 1.5bps RT (retorno liquido anual)")
    print("=" * 110)

    yearly_rows = []
    for volt in [False, True]:
        bt = cell_cache[(126, 5, volt)]
        tmp = bt.set_index("date")["net_ret_1.5"]
        yearly = (1 + tmp).groupby(tmp.index.year).prod() - 1.0
        for year, val in yearly.items():
            yearly_rows.append(dict(vol_targeting=volt, year=year, net_ret_pct=round(val * 100, 3)))

    yearly_df = pd.DataFrame(yearly_rows)
    yearly_pivot = yearly_df.pivot(index="year", columns="vol_targeting", values="net_ret_pct")
    yearly_pivot.columns = ["off", "on"]
    yearly_pivot_path = os.path.join(OUT_DIR, "L126_F5_yearly_returns.csv")
    yearly_pivot.to_csv(yearly_pivot_path)
    print(yearly_pivot.to_string())

    # ------------------------------------------------------------------
    # Melhor celula: maior Sharpe liquido (1.5bps) na janela proxy CONFIRM (out-of-sample-like)
    # ------------------------------------------------------------------
    conf_rows = res_df[(res_df["window"] == "proxy_CONFIRM_2019_2024") & (res_df["rt_bps"] == RT_BASE_BPS)]
    conf_rows_valid = conf_rows.dropna(subset=["sharpe"])
    if len(conf_rows_valid) > 0:
        best_row = conf_rows_valid.sort_values("sharpe", ascending=False).iloc[0]
        best_L, best_F, best_volt = int(best_row["L"]), int(best_row["F"]), bool(best_row["vol_targeting"])
    else:
        best_L, best_F, best_volt = 126, 5, False
        best_row = None

    print("\n" + "=" * 110)
    print(f"MELHOR CELULA (maior Sharpe liq. 1.5bps na janela proxy CONFIRM 2019-2024): "
          f"L={best_L} F={best_F} vol_targeting={best_volt}")
    print("=" * 110)
    if best_row is not None:
        print(best_row.to_string())

    best_bt = cell_cache[(best_L, best_F, best_volt)]
    best_series = best_bt[["date", "net_ret_1.5"]].rename(columns={"net_ret_1.5": "ret"})
    best_series_path = os.path.join(OUT_DIR, "best_cell_daily_net_returns.csv")
    best_series.to_csv(best_series_path, index=False)
    print(f"\nSerie diaria da melhor celula salva em: {best_series_path}  (n={len(best_series)})")

    # ------------------------------------------------------------------
    # Salvar metrics.json resumo
    # ------------------------------------------------------------------
    summary = dict(
        grid_L=L_GRID,
        grid_F=F_GRID,
        grid_vol_targeting=VOLT_GRID,
        rt_bps_sensitivity=RT_SENS_BPS,
        rollover_bps_per_month=ROLLOVER_BPS_PER_MONTH,
        windows=dict(
            discovery=[str(DISCOVERY_START.date()), str(DISCOVERY_END.date())],
            confirm=[str(CONFIRM_START.date()), str(CONFIRM_END.date())],
            real_check=[str(REALCHECK_START.date()), str(REALCHECK_END.date())],
        ),
        best_cell=dict(L=best_L, F=best_F, vol_targeting=best_volt,
                        selection_rule="maior Sharpe liquido 1.5bps em proxy_CONFIRM_2019_2024"),
        n_cells=len(res_df) // len(RT_SENS_BPS) // 3,
        output_files=dict(
            grid_results_csv=res_df_path,
            yearly_L126_F5_csv=yearly_pivot_path,
            best_cell_daily_returns_csv=best_series_path,
        ),
    )
    metrics_path = os.path.join(OUT_DIR, "metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\nArquivos salvos em: {OUT_DIR}")
    print(f" - {res_df_path}")
    print(f" - {yearly_pivot_path}")
    print(f" - {best_series_path}")
    print(f" - {metrics_path}")


if __name__ == "__main__":
    main()
