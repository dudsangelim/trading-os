"""
W5 - Sweep de liquidez diario (SMC codificado)
Mecanica: dia D varre a MINIMA (ou MAXIMA) de D-1 mas fecha de volta dentro
do range de D-1 -> entra na direcao da reversao no close de D, segura H
pregoes. Grid: X in {5,10} bps x H in {1,3} x lado in {long_sweep_low,
short_sweep_high}. Rodado APENAS em futuros reais (WIN/WDO) com OHLC —
niveis de high/low vem da serie _cont_N (nao ajustada, preserva OHLC real
de cada contrato), retorno do trade vem da serie _cont_ADJprop (ajustada
por rolagem, sem gaps artificiais). Redundancia: mesma mecanica agregando
M15 -> D1.

Regra inegociavel (1): remove qualquer linha com data >= 2025-01-01 em
TODOS os parquets B3 carregados, imprime quantas linhas foram removidas.

Auto-suficiente: pandas/numpy/pyarrow apenas. Salva outputs em
b3_factory_v0/W5_sweep/.
"""
import numpy as np
import pandas as pd
import json
import os

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)

HIST_DIR = "C:/Users/Notebook/Documents/Claude/Projects/B3 Futuros/mt5_history"
OUT_DIR = "C:/Users/Notebook/Documents/Claude/Projects/Finanças/mt5/campaigns/b3_factory_v0/W5_sweep"
os.makedirs(OUT_DIR, exist_ok=True)

CUTOFF = pd.Timestamp("2025-01-01")
REAL_START = pd.Timestamp("2021-07-19")
REAL_END = pd.Timestamp("2024-12-31")

RT_COST_BPS_LIST = [1.0, 1.5, 2.0]  # sensibilidade; 1.5 = default
ROLLOVER_BPS_PER_MONTH = 1.2
TRADING_DAYS_PER_MONTH = 21.0

X_GRID = [5, 10]        # bps de folga no rompimento
H_GRID = [1, 3]         # pregoes de hold
SIDES = ["long_sweep_low", "short_sweep_high"]

N_BOOT = 1000
RNG = np.random.default_rng(42)


def load_and_filter(path, label, dt_col="datetime_b3"):
    df = pd.read_parquet(path)
    df[dt_col] = pd.to_datetime(df[dt_col])
    n0 = len(df)
    df = df[df[dt_col] < CUTOFF].copy()
    removed = n0 - len(df)
    print(f"[LOAD] {label}: {n0} linhas -> removed {removed} linhas (>= 2025-01-01) -> {len(df)} restantes")
    return df.sort_values(dt_col).reset_index(drop=True)


def agg_m15_to_d1(df_m15):
    """Agrega M15 (sessao abre 09:00) para D1 OHLC. Dias que nao abrem
    09:00 sao descartados (sessao incompleta / feriado parcial)."""
    df = df_m15.copy()
    df["date"] = df["datetime_b3"].dt.normalize()
    df["time"] = df["datetime_b3"].dt.time
    opens_at_9 = df.groupby("date")["time"].min() == pd.Timestamp("09:00:00").time()
    valid_dates = opens_at_9[opens_at_9].index
    df = df[df["date"].isin(valid_dates)]
    d1 = df.groupby("date").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).reset_index()
    d1 = d1.rename(columns={"date": "datetime_b3"})
    return d1


def build_trade_table(levels_df, adj_df, x_bps, h_days, side):
    """levels_df: colunas datetime_b3, open, high, low, close (niveis reais,
    N ou M15-agg). adj_df: datetime_b3, close (ADJprop, para retorno limpo).
    Retorna DataFrame de trades com entry_date, exit_date, ret_net por
    RT cost em cada nivel de sensibilidade."""
    lv = levels_df[["datetime_b3", "high", "low", "close"]].copy()
    lv = lv.sort_values("datetime_b3").reset_index(drop=True)
    lv["prev_high"] = lv["high"].shift(1)
    lv["prev_low"] = lv["low"].shift(1)

    adj = adj_df[["datetime_b3", "close"]].rename(columns={"close": "adj_close"}).copy()
    adj = adj.sort_values("datetime_b3").reset_index(drop=True)

    # merge por data (inner) para alinhar indices de dia util
    merged = pd.merge(lv, adj, on="datetime_b3", how="inner").reset_index(drop=True)
    n = len(merged)

    x_frac = x_bps / 10000.0

    trades = []
    for i in range(1, n - h_days):
        row = merged.iloc[i]
        prev_low = row["prev_low"]
        prev_high = row["prev_high"]
        if pd.isna(prev_low) or pd.isna(prev_high):
            continue
        entry_date = row["datetime_b3"]
        exit_idx = i + h_days
        if exit_idx >= n:
            continue
        exit_date = merged.iloc[exit_idx]["datetime_b3"]

        triggered = False
        direction = 0
        if side == "long_sweep_low":
            cond = (row["low"] < prev_low * (1 - x_frac)) and (row["close"] > prev_low)
            if cond:
                triggered = True
                direction = 1
        elif side == "short_sweep_high":
            cond = (row["high"] > prev_high * (1 + x_frac)) and (row["close"] < prev_high)
            if cond:
                triggered = True
                direction = -1
        if not triggered:
            continue

        # retorno "limpo" via ADJprop entre entry (close D) e exit (close D+H)
        entry_px = merged.iloc[i]["adj_close"]
        exit_px = merged.iloc[exit_idx]["adj_close"]
        raw_ret = (exit_px / entry_px - 1.0) * direction

        trades.append({
            "entry_date": entry_date,
            "exit_date": exit_date,
            "direction": direction,
            "raw_ret": raw_ret,
            "hold_days": h_days,
        })

    return pd.DataFrame(trades)


def apply_costs(trades, rt_cost_bps, h_days):
    if trades.empty:
        trades = trades.copy()
        trades["ret_net"] = pd.Series(dtype=float)
        return trades
    rollover_cost = ROLLOVER_BPS_PER_MONTH * (h_days / TRADING_DAYS_PER_MONTH) / 10000.0
    rt_cost = rt_cost_bps / 10000.0
    trades = trades.copy()
    trades["ret_net"] = trades["raw_ret"] - rt_cost - rollover_cost
    return trades


def bootstrap_expectancy_ci(rets, n_boot=N_BOOT):
    if len(rets) < 5:
        return (np.nan, np.nan)
    arr = np.asarray(rets)
    boots = np.empty(n_boot)
    n = len(arr)
    for b in range(n_boot):
        sample = arr[RNG.integers(0, n, n)]
        boots[b] = sample.mean()
    return (np.percentile(boots, 2.5), np.percentile(boots, 97.5))


def build_equity_and_metrics(trades, start_date, end_date):
    """Distribui cada trade igualmente pelos dias que fica aberto e soma
    contribuicoes diarias (permite sobreposicao de trades -> soma > 1x
    notional em dias de overlap; simplificacao anotada). Retorna dict de
    metricas: sharpe, maxdd_pct, net_ann_pct, monthly_mean_pct."""
    if trades.empty or len(trades) < 2:
        return dict(sharpe=np.nan, maxdd_pct=np.nan, net_ann_pct=np.nan, monthly_mean_pct=np.nan)

    all_days = pd.date_range(start_date, end_date, freq="B")
    daily_ret = pd.Series(0.0, index=all_days)

    for _, tr in trades.iterrows():
        h = int(tr["hold_days"])
        per_day = tr["ret_net"] / h
        # dias uteis entre entry (exclusive) e exit (inclusive), aproximado por h dias uteis
        idx = all_days[(all_days > tr["entry_date"]) & (all_days <= tr["exit_date"])]
        if len(idx) == 0:
            # fallback: usa proximo dia util disponivel
            continue
        for d in idx[:h]:
            if d in daily_ret.index:
                daily_ret.loc[d] += per_day

    equity = (1 + daily_ret).cumprod()
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    maxdd_pct = dd.min() * 100

    n_days = len(daily_ret)
    years = n_days / 252.0
    net_ann_pct = ((equity.iloc[-1]) ** (1 / years) - 1) * 100 if years > 0 and equity.iloc[-1] > 0 else np.nan

    std = daily_ret.std()
    sharpe = (daily_ret.mean() / std * np.sqrt(252)) if std > 0 else np.nan

    monthly = daily_ret.resample("ME").sum()
    monthly_mean_pct = monthly.mean() * 100

    return dict(sharpe=sharpe, maxdd_pct=maxdd_pct, net_ann_pct=net_ann_pct, monthly_mean_pct=monthly_mean_pct)


def compute_cell_metrics(trades_raw, rt_cost_bps, h_days, start_date, end_date):
    trades = apply_costs(trades_raw, rt_cost_bps, h_days)
    n_trades = len(trades)
    if n_trades == 0:
        return dict(n_trades=0, win_rate=np.nan, pf=np.nan, expectancy_bps=np.nan,
                    ci_lo_bps=np.nan, ci_hi_bps=np.nan, sharpe=np.nan, maxdd_pct=np.nan,
                    net_ann_pct=np.nan, monthly_mean_pct=np.nan)

    wins = trades[trades["ret_net"] > 0]["ret_net"]
    losses = trades[trades["ret_net"] <= 0]["ret_net"]
    win_rate = len(wins) / n_trades * 100
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    pf = (gross_win / gross_loss) if gross_loss > 0 else np.nan
    expectancy_bps = trades["ret_net"].mean() * 10000

    ci_lo, ci_hi = bootstrap_expectancy_ci(trades["ret_net"].values)

    eq_metrics = build_equity_and_metrics(trades, start_date, end_date)

    return dict(
        n_trades=n_trades,
        win_rate=round(win_rate, 2),
        pf=round(pf, 3) if pd.notna(pf) else np.nan,
        expectancy_bps=round(expectancy_bps, 3),
        ci_lo_bps=round(ci_lo * 10000, 3) if pd.notna(ci_lo) else np.nan,
        ci_hi_bps=round(ci_hi * 10000, 3) if pd.notna(ci_hi) else np.nan,
        sharpe=round(eq_metrics["sharpe"], 3) if pd.notna(eq_metrics["sharpe"]) else np.nan,
        maxdd_pct=round(eq_metrics["maxdd_pct"], 2) if pd.notna(eq_metrics["maxdd_pct"]) else np.nan,
        net_ann_pct=round(eq_metrics["net_ann_pct"], 2) if pd.notna(eq_metrics["net_ann_pct"]) else np.nan,
        monthly_mean_pct=round(eq_metrics["monthly_mean_pct"], 3) if pd.notna(eq_metrics["monthly_mean_pct"]) else np.nan,
    )


def run_market(market, levels_source_label, levels_df, adj_df, start_date, end_date):
    rows = []
    for x_bps in X_GRID:
        for h_days in H_GRID:
            for side in SIDES:
                trades_raw = build_trade_table(levels_df, adj_df, x_bps, h_days, side)
                for rt_cost_bps in RT_COST_BPS_LIST:
                    m = compute_cell_metrics(trades_raw, rt_cost_bps, h_days, start_date, end_date)
                    rows.append(dict(
                        market=market, levels_source=levels_source_label,
                        x_bps=x_bps, h_days=h_days, side=side, rt_cost_bps=rt_cost_bps,
                        **m
                    ))
    return pd.DataFrame(rows)


def main():
    print("=" * 100)
    print("W5 - Sweep de liquidez diario (SMC) - WIN e WDO reais 2021-07..2024-12")
    print("=" * 100)

    win_n_d1 = load_and_filter(f"{HIST_DIR}/WIN_cont_N_D1.parquet", "WIN_cont_N_D1")
    wdo_n_d1 = load_and_filter(f"{HIST_DIR}/WDO_cont_N_D1.parquet", "WDO_cont_N_D1")
    win_adj_d1 = load_and_filter(f"{HIST_DIR}/WIN_cont_ADJprop_D1.parquet", "WIN_cont_ADJprop_D1")
    wdo_adj_d1 = load_and_filter(f"{HIST_DIR}/WDO_cont_ADJprop_D1.parquet", "WDO_cont_ADJprop_D1")
    win_n_m15 = load_and_filter(f"{HIST_DIR}/WIN_cont_N_M15.parquet", "WIN_cont_N_M15")
    wdo_n_m15 = load_and_filter(f"{HIST_DIR}/WDO_cont_N_M15.parquet", "WDO_cont_N_M15")

    # restringe explicitamente a janela real-check 2021-07..2024-12
    def clip(df, col="datetime_b3"):
        return df[(df[col] >= REAL_START) & (df[col] <= REAL_END)].reset_index(drop=True)

    win_n_d1 = clip(win_n_d1)
    wdo_n_d1 = clip(wdo_n_d1)
    win_adj_d1 = clip(win_adj_d1)
    wdo_adj_d1 = clip(wdo_adj_d1)
    win_n_m15 = clip(win_n_m15)
    wdo_n_m15 = clip(wdo_n_m15)

    print(f"\n[WINDOW] real-check: {REAL_START.date()} .. {REAL_END.date()}")
    print(f"[N days] WIN D1: {len(win_n_d1)} | WDO D1: {len(wdo_n_d1)}")

    win_m15_d1 = agg_m15_to_d1(win_n_m15)
    wdo_m15_d1 = agg_m15_to_d1(wdo_n_m15)
    print(f"[M15->D1 agg] WIN: {len(win_m15_d1)} dias validos (abrem 09:00) | WDO: {len(wdo_m15_d1)} dias validos")

    start_date, end_date = REAL_START, REAL_END

    all_rows = []
    print("\n--- Rodando WIN (niveis N_D1 nativo) ---")
    all_rows.append(run_market("WIN", "N_D1", win_n_d1, win_adj_d1, start_date, end_date))
    print("--- Rodando WDO (niveis N_D1 nativo) ---")
    all_rows.append(run_market("WDO", "N_D1", wdo_n_d1, wdo_adj_d1, start_date, end_date))
    print("--- Rodando WIN (redundancia: niveis M15 agregado p/ D1) ---")
    all_rows.append(run_market("WIN", "M15_agg", win_m15_d1, win_adj_d1, start_date, end_date))
    print("--- Rodando WDO (redundancia: niveis M15 agregado p/ D1) ---")
    all_rows.append(run_market("WDO", "M15_agg", wdo_m15_d1, wdo_adj_d1, start_date, end_date))

    grid = pd.concat(all_rows, ignore_index=True)

    out_csv = f"{OUT_DIR}/w5_grid_results.csv"
    grid.to_csv(out_csv, index=False)
    print(f"\n[SAVED] {out_csv} ({len(grid)} linhas)")

    # imprime grid completo, sem cherry-pick, default rt_cost=1.5bps primeiro
    default_view = grid[grid["rt_cost_bps"] == 1.5].sort_values(["market", "levels_source", "side", "x_bps", "h_days"])
    print("\n" + "=" * 130)
    print("GRID COMPLETO (custo RT default = 1.5 bps, rollover 1.2bps/mes embutido)")
    print("=" * 130)
    with pd.option_context("display.max_rows", None):
        print(default_view[["market", "levels_source", "side", "x_bps", "h_days", "n_trades", "win_rate",
                             "pf", "expectancy_bps", "ci_lo_bps", "ci_hi_bps", "sharpe", "maxdd_pct",
                             "net_ann_pct", "monthly_mean_pct"]].to_string(index=False))

    print("\n" + "=" * 130)
    print("SENSIBILIDADE DE CUSTO (1.0 / 1.5 / 2.0 bps) - apenas N_D1 nativo")
    print("=" * 130)
    sens_view = grid[grid["levels_source"] == "N_D1"].sort_values(["market", "side", "x_bps", "h_days", "rt_cost_bps"])
    with pd.option_context("display.max_rows", None):
        print(sens_view[["market", "side", "x_bps", "h_days", "rt_cost_bps", "n_trades", "pf", "expectancy_bps",
                          "net_ann_pct", "sharpe"]].to_string(index=False))

    summary = dict(
        n_cells=len(grid),
        window=f"{REAL_START.date()}..{REAL_END.date()}",
        grid_params=dict(x_bps=X_GRID, h_days=H_GRID, side=SIDES, rt_cost_bps_sens=RT_COST_BPS_LIST),
        note="Amostra pequena (~870 dias uteis por mercado); ver bootstrap CI de expectancy por celula no CSV. Sharpe/maxDD/net_ann calculados via equity diaria com alocacao igual por dia de trades sobrepostos (simplificacao).",
    )
    with open(f"{OUT_DIR}/w5_run_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n[SAVED] {OUT_DIR}/w5_run_summary.json")
    print("\nDONE.")


if __name__ == "__main__":
    main()
