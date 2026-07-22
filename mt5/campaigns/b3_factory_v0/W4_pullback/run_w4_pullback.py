"""
W4 - Pullback multi-timeframe (Connors RSI2 + tendencia)
Campanha b3_factory_v0 - worker W4

Mecanica:
  WIN (proxy long-only, real futures long-only):
    trend filter: preco > SMA(N), N in {100,200}
    trigger: RSI2 < K, K in {5,10}
    exit: RSI2 cruza 50 de baixo pra cima (>=50) OU timeout 10 pregoes
  WDO (proxy SHORT-USD-only = direcao do carry, real futures short-only):
    trend filter: preco < SMA(N), N in {100,200}
    trigger: RSI2 > (100-K), K in {5,10}  -> thresholds 90/95
    exit: RSI2 cruza 50 de cima pra baixo (<=50) OU timeout 10 pregoes

Janelas: DISCOVERY 2000-2018 (proxy), CONFIRM 2019-2024 (proxy),
real-check 2021-07..2024-12 (futuros ADJprop reais).

REGRA INEGOCIAVEL: nenhum dado B3 (proxy ou futuro) de 2025-01-01 em diante.
Filtragem aplicada logo apos load, com contagem impressa.

Custos B3: 1.5 bps RT default (sensibilidade 1.0 e 2.0 bps tambem reportada)
           + 1.2 bps/mes de rolagem enquanto posicionado (prorateado por dia
           util, 1.2/21 bps por pregao em posicao).

Autor: worker W4 (agente Sonnet), campanha b3_factory_v0
"""

import numpy as np
import pandas as pd

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)

# ------------------------------------------------------------------
# PATHS
# ------------------------------------------------------------------
PROXY_PATH = r"C:/Users/Notebook/Documents/Claude/Projects/Finanças/mt5/campaigns/b3_swing_v1_proxy/proxy_raw.parquet"
WIN_REAL_PATH = r"C:/Users/Notebook/Documents/Claude/Projects/B3 Futuros/mt5_history/WIN_cont_ADJprop_D1.parquet"
WDO_REAL_PATH = r"C:/Users/Notebook/Documents/Claude/Projects/B3 Futuros/mt5_history/WDO_cont_ADJprop_D1.parquet"
OUT_DIR = r"C:/Users/Notebook/Documents/Claude/Projects/Finanças/mt5/campaigns/b3_factory_v0/W4_pullback"

CUTOFF = pd.Timestamp("2025-01-01")

DISCOVERY = (pd.Timestamp("2000-01-01"), pd.Timestamp("2018-12-31"))
CONFIRM = (pd.Timestamp("2019-01-01"), pd.Timestamp("2024-12-31"))
REALCHECK = (pd.Timestamp("2021-07-01"), pd.Timestamp("2024-12-31"))

SMA_GRID = [100, 200]
RSI_GRID = [5, 10]  # WIN: RSI2 < K ; WDO: RSI2 > (100-K)
TIMEOUT_DAYS = 10
RT_COST_BPS_DEFAULT = 1.5
RT_COST_BPS_SENS = [1.0, 1.5, 2.0]
ROLLOVER_BPS_PER_MONTH = 1.2
TRADING_DAYS_PER_MONTH = 21
TRADING_DAYS_PER_YEAR = 252


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------
def filter_cutoff(df, date_col, label):
    n0 = len(df)
    df2 = df[df[date_col] < CUTOFF].copy()
    removed = n0 - len(df2)
    print(f"[FILTRO 2025+] {label}: removidas {removed} linhas (de {n0} -> {len(df2)})")
    return df2


def rsi2(price, period=2):
    """RSI classico (Wilder smoothing, alpha=1/period) sobre serie de precos."""
    delta = price.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(avg_loss != 0, 100.0)  # sem perdas -> RSI=100
    rsi = rsi.where(avg_gain != 0, 0.0).where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi


def build_signals(df, sma_n, rsi_k, side):
    """
    df: DataFrame com colunas date, price (ordenado, sem gaps de indice)
    side: 'long' (WIN) ou 'short' (WDO)
    Retorna df com colunas sma, rsi2, trend_ok, trigger
    """
    d = df.copy().reset_index(drop=True)
    d["sma"] = d["price"].rolling(sma_n, min_periods=sma_n).mean()
    d["rsi2"] = rsi2(d["price"], period=2)

    if side == "long":
        d["trend_ok"] = d["price"] > d["sma"]
        d["trigger"] = d["rsi2"] < rsi_k
    else:  # short
        d["trend_ok"] = d["price"] < d["sma"]
        d["trigger"] = d["rsi2"] > (100 - rsi_k)

    d["entry_signal"] = d["trend_ok"] & d["trigger"]
    return d


def run_backtest(d, side, timeout=TIMEOUT_DAYS):
    """
    Non-overlapping trade scan. Entrada no fechamento do dia do sinal,
    saida no fechamento do dia em que RSI2 cruza 50 (direcao oposta) ou timeout.
    Retorna lista de dicts trade-level e serie diaria de equity (gross, sem custo).
    """
    n = len(d)
    price = d["price"].values
    rsi = d["rsi2"].values
    entry_sig = d["entry_signal"].values
    dates = d["date"].values

    trades = []
    i = 0
    # primeiro indice valido (sma e rsi2 nao-NaN)
    valid_start = d[["sma", "rsi2"]].dropna().index.min()
    if pd.isna(valid_start):
        return trades
    i = max(i, int(valid_start))

    while i < n:
        if not entry_sig[i] or np.isnan(rsi[i]):
            i += 1
            continue
        entry_idx = i
        entry_price = price[entry_idx]
        entry_date = dates[entry_idx]

        exit_idx = None
        for k in range(1, timeout + 1):
            j = entry_idx + k
            if j >= n:
                break
            r = rsi[j]
            if np.isnan(r):
                continue
            if side == "long" and r >= 50:
                exit_idx = j
                break
            if side == "short" and r <= 50:
                exit_idx = j
                break
        if exit_idx is None:
            exit_idx = min(entry_idx + timeout, n - 1)
            exit_reason = "timeout"
        else:
            exit_reason = "rsi_cross"

        if exit_idx <= entry_idx:
            i = entry_idx + 1
            continue

        exit_price = price[exit_idx]
        exit_date = dates[exit_idx]
        hold_days = exit_idx - entry_idx

        if side == "long":
            gross_ret = exit_price / entry_price - 1.0
        else:  # short: ganha quando preco cai
            gross_ret = 1.0 - exit_price / entry_price

        trades.append(
            {
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "hold_days": hold_days,
                "gross_ret": gross_ret,
                "exit_reason": exit_reason,
            }
        )
        i = exit_idx + 1  # non-overlapping

    return trades


def apply_costs(trades, rt_cost_bps):
    out = []
    for t in trades:
        rt_cost = rt_cost_bps / 10000.0
        rollover_cost = (ROLLOVER_BPS_PER_MONTH / TRADING_DAYS_PER_MONTH / 10000.0) * t["hold_days"]
        net_ret = t["gross_ret"] - rt_cost - rollover_cost
        t2 = dict(t)
        t2["net_ret"] = net_ret
        out.append(t2)
    return out


def compute_metrics(trades, window_start, window_end):
    if len(trades) == 0:
        return dict(
            n_trades=0, pf=np.nan, expectancy_bps=np.nan, sharpe=np.nan,
            net_ann_pct=np.nan, maxdd_pct=np.nan, monthly_mean_pct=np.nan,
        )

    df = pd.DataFrame(trades)
    net = df["net_ret"].values
    n_trades = len(net)

    gains = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = gains / losses if losses > 0 else np.inf

    expectancy_bps = net.mean() * 10000.0

    # equity curve trade-a-trade (compounding)
    equity = np.cumprod(1 + net)
    equity_full = np.concatenate([[1.0], equity])
    running_max = np.maximum.accumulate(equity_full)
    dd = (equity_full - running_max) / running_max
    maxdd_pct = dd.min() * 100.0

    # anualizacao: usa n dias uteis totais do periodo p/ escalar
    total_days = (pd.Timestamp(window_end) - pd.Timestamp(window_start)).days
    years = max(total_days / 365.25, 1e-6)
    total_ret = equity[-1] - 1.0
    net_ann_pct = ((1 + total_ret) ** (1.0 / years) - 1.0) * 100.0 if (1 + total_ret) > 0 else -100.0

    # Sharpe do stream de trades, anualizado por trades/ano
    trades_per_year = n_trades / years
    if net.std(ddof=1) > 0 and n_trades > 1:
        sharpe = (net.mean() / net.std(ddof=1)) * np.sqrt(trades_per_year)
    else:
        sharpe = np.nan

    # media mensal %: agrega retornos por mes de exit_date
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    monthly = df.set_index("exit_date")["net_ret"].groupby(pd.Grouper(freq="ME")).apply(
        lambda x: (np.prod(1 + x) - 1) if len(x) > 0 else 0.0
    )
    monthly_mean_pct = monthly.mean() * 100.0 if len(monthly) > 0 else np.nan

    return dict(
        n_trades=n_trades,
        pf=pf,
        expectancy_bps=expectancy_bps,
        sharpe=sharpe,
        net_ann_pct=net_ann_pct,
        maxdd_pct=maxdd_pct,
        monthly_mean_pct=monthly_mean_pct,
    )


def slice_window(d, start, end):
    return d[(d["date"] >= start) & (d["date"] <= end)].reset_index(drop=True)


# ------------------------------------------------------------------
# LOAD DATA
# ------------------------------------------------------------------
print("=" * 90)
print("W4 - Pullback multi-timeframe (Connors RSI2 + tendencia) - b3_factory_v0")
print("=" * 90)

proxy = pd.read_parquet(PROXY_PATH)
proxy["date"] = pd.to_datetime(proxy["date"])
proxy = proxy.sort_values("date").reset_index(drop=True)
proxy = filter_cutoff(proxy, "date", "proxy_raw.parquet")

win_real = pd.read_parquet(WIN_REAL_PATH)
win_real["datetime_b3"] = pd.to_datetime(win_real["datetime_b3"])
win_real = win_real.sort_values("datetime_b3").reset_index(drop=True)
win_real = filter_cutoff(win_real, "datetime_b3", "WIN_cont_ADJprop_D1.parquet")

wdo_real = pd.read_parquet(WDO_REAL_PATH)
wdo_real["datetime_b3"] = pd.to_datetime(wdo_real["datetime_b3"])
wdo_real = wdo_real.sort_values("datetime_b3").reset_index(drop=True)
wdo_real = filter_cutoff(wdo_real, "datetime_b3", "WDO_cont_ADJprop_D1.parquet")

# construir retornos/precos sinteticos proxy
proxy["cdi_ad_frac"] = proxy["cdi_ad"] / 100.0
proxy["ffr_ad"] = (1 + proxy["ffr_aa"] / 100.0) ** (1.0 / 252.0) - 1.0

proxy["win_proxy_ret"] = proxy["ibov"].pct_change() - proxy["cdi_ad_frac"]
proxy["wdo_proxy_ret"] = proxy["usdbrl"].pct_change() - (proxy["cdi_ad_frac"] - proxy["ffr_ad"])

proxy = proxy.dropna(subset=["win_proxy_ret", "wdo_proxy_ret"]).reset_index(drop=True)

proxy["win_price"] = (1 + proxy["win_proxy_ret"]).cumprod()
proxy["wdo_price"] = (1 + proxy["wdo_proxy_ret"]).cumprod()

win_proxy_df = proxy[["date", "win_price"]].rename(columns={"win_price": "price"})
wdo_proxy_df = proxy[["date", "wdo_price"]].rename(columns={"wdo_price": "price"})

win_real_df = win_real[["datetime_b3", "close"]].rename(columns={"datetime_b3": "date", "close": "price"})
wdo_real_df = wdo_real[["datetime_b3", "close"]].rename(columns={"datetime_b3": "date", "close": "price"})

print(f"\nProxy WIN/WDO: {len(proxy)} linhas, {proxy['date'].min().date()} -> {proxy['date'].max().date()}")
print(f"WIN real ADJprop D1: {len(win_real_df)} linhas, {win_real_df['date'].min().date()} -> {win_real_df['date'].max().date()}")
print(f"WDO real ADJprop D1: {len(wdo_real_df)} linhas, {wdo_real_df['date'].min().date()} -> {wdo_real_df['date'].max().date()}")

# ------------------------------------------------------------------
# RUN GRID
# ------------------------------------------------------------------
MARKETS = {
    "WIN": {"proxy": win_proxy_df, "real": win_real_df, "side": "long"},
    "WDO": {"proxy": wdo_proxy_df, "real": wdo_real_df, "side": "short"},
}

WINDOWS_PROXY = {"DISCOVERY": DISCOVERY, "CONFIRM": CONFIRM}

all_rows = []
saved_series = []

for mkt, cfg in MARKETS.items():
    side = cfg["side"]
    full_proxy = build_signals_input = cfg["proxy"]
    full_real = cfg["real"]

    for sma_n in SMA_GRID:
        for rsi_k in RSI_GRID:
            cell_id = f"{mkt}_SMA{sma_n}_RSI{rsi_k}"

            # ---- PROXY: compute signals on full proxy history, then slice per window ----
            sig_full = build_signals(full_proxy, sma_n, rsi_k, side)

            window_metrics = {}
            for wname, (wstart, wend) in WINDOWS_PROXY.items():
                sig_w = sig_full[(sig_full["date"] >= wstart) & (sig_full["date"] <= wend)].reset_index(drop=True)
                trades_raw = run_backtest(sig_w, side)
                trades = apply_costs(trades_raw, RT_COST_BPS_DEFAULT)
                m = compute_metrics(trades, wstart, wend)
                window_metrics[wname] = m

                row = dict(cell_id=cell_id, market=mkt, sma_n=sma_n, rsi_k=rsi_k,
                           dataset="proxy", window=wname, rt_cost_bps=RT_COST_BPS_DEFAULT, **m)
                all_rows.append(row)

                # sensibilidade de custo (1.0 e 2.0 bps) tambem reportada
                for rt_bps in RT_COST_BPS_SENS:
                    if rt_bps == RT_COST_BPS_DEFAULT:
                        continue
                    trades_s = apply_costs(trades_raw, rt_bps)
                    m_s = compute_metrics(trades_s, wstart, wend)
                    row_s = dict(cell_id=cell_id, market=mkt, sma_n=sma_n, rsi_k=rsi_k,
                                 dataset="proxy", window=wname, rt_cost_bps=rt_bps, **m_s)
                    all_rows.append(row_s)

            # ---- REAL-CHECK: futuros ADJprop 2021-07..2024-12 ----
            sig_real_full = build_signals(full_real, sma_n, rsi_k, side)
            sig_real_w = sig_real_full[
                (sig_real_full["date"] >= REALCHECK[0]) & (sig_real_full["date"] <= REALCHECK[1])
            ].reset_index(drop=True)
            trades_real_raw = run_backtest(sig_real_w, side)
            trades_real = apply_costs(trades_real_raw, RT_COST_BPS_DEFAULT)
            m_real = compute_metrics(trades_real, REALCHECK[0], REALCHECK[1])
            row_real = dict(cell_id=cell_id, market=mkt, sma_n=sma_n, rsi_k=rsi_k,
                             dataset="real_futures", window="REALCHECK",
                             rt_cost_bps=RT_COST_BPS_DEFAULT, **m_real)
            all_rows.append(row_real)

            for rt_bps in RT_COST_BPS_SENS:
                if rt_bps == RT_COST_BPS_DEFAULT:
                    continue
                trades_real_s = apply_costs(trades_real_raw, rt_bps)
                m_real_s = compute_metrics(trades_real_s, REALCHECK[0], REALCHECK[1])
                row_real_s = dict(cell_id=cell_id, market=mkt, sma_n=sma_n, rsi_k=rsi_k,
                                   dataset="real_futures", window="REALCHECK",
                                   rt_cost_bps=rt_bps, **m_real_s)
                all_rows.append(row_real_s)

            # ---- criterio de consistencia p/ salvar serie diaria (custo default) ----
            disc = window_metrics.get("DISCOVERY", {})
            conf = window_metrics.get("CONFIRM", {})
            disc_pf = disc.get("pf", np.nan)
            conf_pf = conf.get("pf", np.nan)
            disc_exp = disc.get("expectancy_bps", np.nan)
            conf_exp = conf.get("expectancy_bps", np.nan)
            disc_n = disc.get("n_trades", 0)

            same_sign = (not np.isnan(disc_exp)) and (not np.isnan(conf_exp)) and (
                np.sign(disc_exp) == np.sign(conf_exp)
            ) and disc_exp > 0
            consistent = (
                same_sign
                and (not np.isnan(disc_pf)) and disc_pf >= 1.2
                and (not np.isnan(conf_pf)) and conf_pf >= 1.2
                and disc_n >= 100
            )

            if consistent:
                trades_full_raw = run_backtest(sig_full, side)
                trades_full = apply_costs(trades_full_raw, RT_COST_BPS_DEFAULT)
                tdf = pd.DataFrame(trades_full)
                out_csv = f"{OUT_DIR}/{cell_id}_daily_series.csv"
                tdf.to_csv(out_csv, index=False)
                saved_series.append(cell_id)
                print(f"[CONSISTENTE] {cell_id}: disc_pf={disc_pf:.2f} conf_pf={conf_pf:.2f} disc_n={disc_n} -> salvo {out_csv}")

# ------------------------------------------------------------------
# REPORT - TODAS AS CELULAS, SEM CHERRY-PICK
# ------------------------------------------------------------------
grid = pd.DataFrame(all_rows)
grid_out_csv = f"{OUT_DIR}/w4_pullback_grid_full.csv"
grid.to_csv(grid_out_csv, index=False)

print("\n" + "=" * 90)
print("GRID COMPLETO (custo default 1.5 bps RT, todas as celulas)")
print("=" * 90)
default_grid = grid[grid["rt_cost_bps"] == RT_COST_BPS_DEFAULT].copy()
cols = ["cell_id", "market", "sma_n", "rsi_k", "dataset", "window", "n_trades",
        "pf", "expectancy_bps", "sharpe", "net_ann_pct", "maxdd_pct", "monthly_mean_pct"]
with pd.option_context("display.float_format", lambda x: f"{x:.3f}"):
    print(default_grid[cols].to_string(index=False))

print("\n" + "=" * 90)
print("SENSIBILIDADE DE CUSTO (1.0 / 1.5 / 2.0 bps RT) - PF por celula/janela")
print("=" * 90)
sens_pivot = grid.pivot_table(
    index=["cell_id", "dataset", "window"], columns="rt_cost_bps", values="pf"
)
print(sens_pivot.to_string())

print(f"\nGrid completo salvo em: {grid_out_csv}")
print(f"Series diarias salvas para celulas consistentes: {saved_series if saved_series else 'NENHUMA'}")
print("\nFIM.")
