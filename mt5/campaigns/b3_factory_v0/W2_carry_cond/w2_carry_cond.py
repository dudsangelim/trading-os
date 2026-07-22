"""
W2 - Carry condicional WDO
===========================
Mecanica: posicao SHORT no proxy/futuro WDO (ganha o carry CDI-FFR) somente
quando gate ativo; flat caso contrario. Grid 3x3:
  - diferencial anualizado (cdi_ad*252 - ffr_aa, em pontos percentuais) em {>4, >6, >8}
  - vol20d anualizada do proxy WDO em {< mediana movel 252d, < p75 movel 252d, sem filtro}
Rebalance diario. Custos so nas mudancas flat<->short (RT bps, split metade
na entrada / metade na saida) + rolagem 1.2 bps/mes (pro-rata diario, ~21
dias uteis/mes) enquanto posicionado.

DESVIO DOCUMENTADO vs instrucao literal:
A instrucao pede diferencial = (cdi_ad*252*100 - ffr_aa). Inspecao dos dados
mostra cdi_ad ja em pontos percentuais AO DIA (ex.: 0.0683 = 0.0683%/dia,
consistente com a formula do proxy wdo_proxy_ret que usa cdi_ad/100 para
obter fracao decimal diaria). cdi_ad*252 sozinho ja da o CDI anualizado em
pontos percentuais (media historica ~11.5pp, ffr_aa media ~2.05pp, diff
media ~9.4pp) - compativel com os thresholds pedidos (>4,>6,>8 pp) e com a
historia conhecida do diferencial de juros Brasil-EUA. Aplicar o fator
*100 extra faria o diferencial saltar para ordem de milhares, tornando os
3 thresholds idênticos (gate sempre ligado) e o grid sem sentido. Por isso
usei diff_pp = cdi_ad*252 - ffr_aa. Reportado explicitamente aqui.

Janelas: DISCOVERY 2000-2018 (proxy), CONFIRM 2019-2024 (proxy),
real-check 2021-07..2024-12 (futuro WDO real, sinal computado do proxy CDI/FFR
mas retorno aplicado sobre o futuro real ADJprop D1).
REGRA (1): remove qualquer linha >= 2025-01-01 de QUALQUER parquet B3.
"""

import numpy as np
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

PROXY_PATH = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy\proxy_raw.parquet"
REAL_WDO_PATH = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history\WDO_cont_ADJprop_D1.parquet"
OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_factory_v0\W2_carry_cond"

CUTOFF = pd.Timestamp("2025-01-01")

DISCOVERY_START = pd.Timestamp("2000-01-01")
DISCOVERY_END = pd.Timestamp("2018-12-31")
CONFIRM_START = pd.Timestamp("2019-01-01")
CONFIRM_END = pd.Timestamp("2024-12-31")
REALCHECK_START = pd.Timestamp("2021-07-01")
REALCHECK_END = pd.Timestamp("2024-12-31")

DIFF_THRESHOLDS = [4, 6, 8]  # pontos percentuais
VOL_FILTERS = ["median252", "p75_252", "none"]
COST_BPS_RT = [1.0, 1.5, 2.0]  # sensibilidade custo roundtrip B3
ROLL_BPS_MONTH = 1.2
TRADING_DAYS_MONTH = 21
TRADING_DAYS_YEAR = 252


# ---------------------------------------------------------------------------
# Load & prep
# ---------------------------------------------------------------------------
def load_proxy():
    df = pd.read_parquet(PROXY_PATH)
    df["date"] = pd.to_datetime(df["date"])
    n0 = len(df)
    df = df[df["date"] < CUTOFF].copy()
    print(f"[proxy] linhas removidas por regra 2025+: {n0 - len(df)} (de {n0})")
    df = df.sort_values("date").reset_index(drop=True)

    df["ffr_ad"] = (1 + df["ffr_aa"] / 100) ** (1 / 252) - 1
    df["wdo_proxy_ret"] = df["usdbrl"].pct_change() - (df["cdi_ad"] / 100 - df["ffr_ad"])
    # diferencial anualizado em pontos percentuais (ver desvio documentado no topo)
    df["diff_pp"] = df["cdi_ad"] * 252 - df["ffr_aa"]
    # vol20d anualizada do proxy WDO
    df["vol20d"] = df["wdo_proxy_ret"].rolling(20).std() * np.sqrt(TRADING_DAYS_YEAR)
    # filtros de vol moveis (trailing, sem look-ahead: usa ate t-1)
    df["vol_median252"] = df["vol20d"].shift(1).rolling(252, min_periods=126).median()
    df["vol_p75_252"] = df["vol20d"].shift(1).rolling(252, min_periods=126).quantile(0.75)
    return df


def load_real_wdo():
    df = pd.read_parquet(REAL_WDO_PATH)
    df["datetime_b3"] = pd.to_datetime(df["datetime_b3"])
    n0 = len(df)
    df = df[df["datetime_b3"] < CUTOFF].copy()
    print(f"[real WDO] linhas removidas por regra 2025+: {n0 - len(df)} (de {n0})")
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    df["real_ret"] = df["close"].pct_change()
    df = df.rename(columns={"datetime_b3": "date"})
    return df[["date", "real_ret"]]


# ---------------------------------------------------------------------------
# Gate / signal
# ---------------------------------------------------------------------------
def build_gate(df, diff_thresh, vol_filter):
    diff_ok = df["diff_pp"] > diff_thresh
    if vol_filter == "median252":
        vol_ok = df["vol20d"] < df["vol_median252"]
    elif vol_filter == "p75_252":
        vol_ok = df["vol20d"] < df["vol_p75_252"]
    else:
        vol_ok = pd.Series(True, index=df.index)
    gate = (diff_ok & vol_ok).fillna(False)
    return gate


# ---------------------------------------------------------------------------
# Backtest engine (short-only carry, daily rebalance)
# ---------------------------------------------------------------------------
def run_backtest(df, ret_col, gate, cost_bps_rt, roll_bps_month=ROLL_BPS_MONTH):
    """gate: bool series aligned with df index. Position decided on gate at t,
    applied to return realized on t+1 (shift(1) -> no lookahead)."""
    pos = gate.shift(1).fillna(False).astype(int)  # 1 = short
    ret = df[ret_col].fillna(0.0)

    strat_ret = np.where(pos == 1, -ret, 0.0)

    # rolagem diaria enquanto posicionado
    roll_drag = (roll_bps_month / 10000.0) / TRADING_DAYS_MONTH
    strat_ret = strat_ret - np.where(pos == 1, roll_drag, 0.0)

    # custos de transicao: half RT em cada perna (abre/fecha)
    pos_shift = pos.shift(1).fillna(0).astype(int)
    transition = (pos != pos_shift).values
    half_cost = (cost_bps_rt / 10000.0) / 2.0
    strat_ret = strat_ret - np.where(transition, half_cost, 0.0)

    out = pd.DataFrame({"date": df["date"].values, "pos": pos.values, "ret": strat_ret})
    return out


def compute_metrics(bt):
    """bt: DataFrame with date, pos, ret (daily strategy returns)."""
    ret = bt["ret"]
    n = len(ret)
    if n == 0 or ret.abs().sum() == 0:
        return dict(net_ann_pct=0.0, sharpe=0.0, maxdd_pct=0.0, pf=np.nan,
                     monthly_mean_pct=0.0, n_trades=0)

    equity = (1 + ret).cumprod()
    total_ret = equity.iloc[-1] - 1
    n_years = n / TRADING_DAYS_YEAR
    net_ann_pct = ((1 + total_ret) ** (1 / n_years) - 1) * 100 if n_years > 0 else np.nan

    mean_d = ret.mean()
    std_d = ret.std(ddof=1)
    sharpe = (mean_d / std_d) * np.sqrt(TRADING_DAYS_YEAR) if std_d > 0 else 0.0

    roll_max = equity.cummax()
    dd = (equity / roll_max) - 1
    maxdd_pct = dd.min() * 100

    monthly = bt.set_index(pd.to_datetime(bt["date"]))["ret"].resample("ME").apply(
        lambda x: (1 + x).prod() - 1
    )
    monthly_mean_pct = monthly.mean() * 100

    # trades = blocos contiguos de pos==1
    pos = bt["pos"].values
    trade_rets = []
    in_trade = False
    cur = 1.0
    for p, r in zip(pos, ret.values):
        if p == 1:
            if not in_trade:
                in_trade = True
                cur = 1.0
            cur *= (1 + r)
        else:
            if in_trade:
                trade_rets.append(cur - 1)
                in_trade = False
    if in_trade:
        trade_rets.append(cur - 1)
    trade_rets = np.array(trade_rets)
    n_trades = len(trade_rets)
    gains = trade_rets[trade_rets > 0].sum()
    losses = -trade_rets[trade_rets < 0].sum()
    pf = gains / losses if losses > 0 else (np.inf if gains > 0 else np.nan)

    return dict(net_ann_pct=net_ann_pct, sharpe=sharpe, maxdd_pct=maxdd_pct,
                 pf=pf, monthly_mean_pct=monthly_mean_pct, n_trades=n_trades)


def window_slice(df, start, end, date_col="date"):
    return df[(df[date_col] >= start) & (df[date_col] <= end)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    proxy = load_proxy()
    real = load_real_wdo()

    # merge real returns onto proxy dates so o sinal (calculado no proxy) possa
    # ser aplicado ao retorno real no periodo real-check
    merged_real = proxy[["date", "diff_pp", "vol20d", "vol_median252", "vol_p75_252"]].merge(
        real, on="date", how="inner"
    )

    windows = {
        "DISCOVERY": (window_slice(proxy, DISCOVERY_START, DISCOVERY_END), "wdo_proxy_ret"),
        "CONFIRM": (window_slice(proxy, CONFIRM_START, CONFIRM_END), "wdo_proxy_ret"),
        "real-check": (window_slice(merged_real, REALCHECK_START, REALCHECK_END), "real_ret"),
    }
    # DISCOVERY/CONFIRM usam colunas do proxy diretamente; real-check usa merged
    windows["DISCOVERY"] = (window_slice(proxy, DISCOVERY_START, DISCOVERY_END), "wdo_proxy_ret")
    windows["CONFIRM"] = (window_slice(proxy, CONFIRM_START, CONFIRM_END), "wdo_proxy_ret")

    rows = []
    cell_series = {}  # (thresh, vfilter, window, cost) -> bt df (only cost=1.5 kept for storage)

    for thresh in DIFF_THRESHOLDS:
        for vfilter in VOL_FILTERS:
            for wname, (wdf, retcol) in windows.items():
                if len(wdf) < 30:
                    continue
                gate = build_gate(wdf, thresh, vfilter)
                for cost in COST_BPS_RT:
                    bt = run_backtest(wdf, retcol, gate, cost)
                    m = compute_metrics(bt)
                    rows.append(dict(
                        gate_diff=f">{thresh}pp", vol_filter=vfilter, window=wname,
                        cost_bps_rt=cost, n_obs=len(wdf), **m
                    ))
                    if cost == 1.5:
                        cell_series[(thresh, vfilter, wname)] = bt

    grid = pd.DataFrame(rows)
    grid_path = f"{OUT_DIR}\\grid_full.csv"
    grid.to_csv(grid_path, index=False)

    print("\n=== GRID COMPLETO (todas as celulas, sem cherry-pick) ===")
    with pd.option_context("display.max_rows", None):
        print(grid.round(4).to_string())

    # ------------------------------------------------------------------
    # Selecao: melhor celula CONSISTENTE (mesmo sinal PF/net_ann em DISCOVERY e CONFIRM)
    # usando cost=1.5bps (base)
    # ------------------------------------------------------------------
    base = grid[grid["cost_bps_rt"] == 1.5]
    disc = base[base["window"] == "DISCOVERY"].set_index(["gate_diff", "vol_filter"])
    conf = base[base["window"] == "CONFIRM"].set_index(["gate_diff", "vol_filter"])

    candidates = []
    for key in disc.index:
        if key not in conf.index:
            continue
        d_row = disc.loc[key]
        c_row = conf.loc[key]
        same_sign = (d_row["net_ann_pct"] > 0) == (c_row["net_ann_pct"] > 0)
        if same_sign and d_row["net_ann_pct"] > 0:
            score = min(d_row["net_ann_pct"], c_row["net_ann_pct"])
            candidates.append((key, score, d_row["net_ann_pct"], c_row["net_ann_pct"]))

    best = None
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        best = candidates[0]
        gate_diff_str, vol_filter_best = best[0]
        thresh_best = int(gate_diff_str.replace(">", "").replace("pp", ""))
        print(f"\n=== MELHOR CELULA CONSISTENTE (mesmo sinal DISCOVERY+CONFIRM) ===")
        print(f"gate_diff={gate_diff_str}, vol_filter={vol_filter_best}, "
              f"net_ann DISCOVERY={best[2]:.2f}%, net_ann CONFIRM={best[3]:.2f}%")

        for wname in windows:
            key = (thresh_best, vol_filter_best, wname)
            if key in cell_series:
                bt = cell_series[key].copy()
                bt["equity"] = (1 + bt["ret"]).cumprod()
                bt["window"] = wname
                out_path = f"{OUT_DIR}\\best_cell_{wname}_series.csv"
                bt.to_csv(out_path, index=False)
                print(f"  serie salva: {out_path} ({len(bt)} linhas)")
    else:
        print("\n=== NENHUMA CELULA CONSISTENTE (mesmo sinal positivo DISC+CONFIRM) ENCONTRADA ===")
        print("Todas as celulas do grid estao reportadas acima; nenhuma passa no criterio de consistencia.")

    print(f"\nGrid completo salvo em: {grid_path}")
    return grid, best


if __name__ == "__main__":
    main()
