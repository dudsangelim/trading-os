"""
R2 - TSM multi-ativos B3 (cross-asset time-series momentum, a la Moskowitz-Ooi-Pedersen)

Universo: WIN, WDO, IND, DOL, DI1, BGI, CCM, ICF, BIT (*_cont_ADJprop_D1.parquet)
Janela comum: 2021-07-19 a 2024-12-31 (dados 2025+ PROIBIDOS - filtrados pos-load)

Mecanica por sleeve:
  estado = sign(ret acumulado L pregoes)
  peso   = min(3, 0.10 / vol20d_anualizada)
  posicao = estado * peso, reavaliada a cada F=21 pregoes, aplicada em t+1

Custos:
  - 1.5 bps RT por mudanca de posicao (3.0 bps para BGI/CCM/ICF)
  - 1.2 bps/mes de rolagem enquanto posicionado (pro-rated ~21 pregoes/mes)

Carteira: equal-weight dos sleeves {WDO, DI1, BGI, CCM, ICF, WIN}
(exclui IND, DOL por redundancia com WIN/WDO; exclui BIT por historico curto)

Output: metrics.json, correlation_matrix.csv, portfolio_L126_daily_returns.csv,
        bootstrap results, e print de sumario.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_factory_v0\R2_tsm_multi")
OUT_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE = ["WIN", "WDO", "IND", "DOL", "DI1", "BGI", "CCM", "ICF", "BIT"]
PORTFOLIO_SLEEVES = ["WDO", "DI1", "BGI", "CCM", "ICF", "WIN"]
HIGH_FEE_ASSETS = {"BGI", "CCM", "ICF"}

WINDOW_START = pd.Timestamp("2021-07-19")
WINDOW_END = pd.Timestamp("2024-12-31")
CUTOFF_2025 = pd.Timestamp("2025-01-01")

L_GRID = [63, 126]
F_REBAL = 21
VOL_WINDOW = 20
VOL_TARGET = 0.10
WEIGHT_CAP = 3.0
COST_BPS_NORMAL = 1.5
COST_BPS_HIGH = 3.0
ROLL_BPS_MONTH = 1.2
TRADING_DAYS_YEAR = 252
DAYS_PER_MONTH = 21

RNG_SEED = 42
N_BOOT = 1000


def load_asset(ticker):
    path = DATA_DIR / f"{ticker}_cont_ADJprop_D1.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df[["datetime_b3", "close"]].copy()
    df["datetime_b3"] = pd.to_datetime(df["datetime_b3"])
    df = df.sort_values("datetime_b3").drop_duplicates("datetime_b3").reset_index(drop=True)

    n_before = len(df)
    max_date_before = df["datetime_b3"].max()
    df = df[df["datetime_b3"] < CUTOFF_2025].reset_index(drop=True)
    n_after = len(df)
    n_dropped = n_before - n_after

    df = df[(df["datetime_b3"] >= WINDOW_START) & (df["datetime_b3"] <= WINDOW_END)].reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    return {
        "df": df,
        "n_dropped_2025plus": n_dropped,
        "max_date_raw": str(max_date_before.date()),
        "n_rows_window": len(df),
        "date_min": str(df["datetime_b3"].min().date()) if len(df) else None,
        "date_max": str(df["datetime_b3"].max().date()) if len(df) else None,
    }


def build_sleeve(df, L):
    """Return DataFrame indexed by date with columns: ret (asset daily ret), position, sleeve_ret, cost."""
    d = df.copy().reset_index(drop=True)
    d["vol20"] = d["ret"].rolling(VOL_WINDOW).std() * np.sqrt(TRADING_DAYS_YEAR)
    d["cum_L"] = d["close"] / d["close"].shift(L) - 1.0

    n = len(d)
    position = np.full(n, np.nan)

    # rebalance indices: every F_REBAL trading days, starting once L and VOL_WINDOW lookback available
    first_valid = max(L, VOL_WINDOW)
    rebal_idxs = list(range(first_valid, n, F_REBAL))

    current_weight = 0.0
    last_rebal_pos_idx = None
    for idx in rebal_idxs:
        cum = d["cum_L"].iloc[idx]
        vol = d["vol20"].iloc[idx]
        if pd.isna(cum) or pd.isna(vol) or vol == 0:
            w = 0.0
        else:
            state = np.sign(cum)
            size = min(WEIGHT_CAP, VOL_TARGET / vol)
            w = state * size
        current_weight = w
        # apply from idx+1 onward until next rebalance
        start_apply = idx + 1
        if last_rebal_pos_idx is None:
            pass
        last_rebal_pos_idx = idx
        end_apply = min(idx + F_REBAL + 1, n)  # will be overwritten by next loop iter's rebal
        position[start_apply:end_apply] = current_weight

    d["position"] = position
    d["position"] = d["position"].fillna(0.0)

    ticker = None
    return d


def apply_costs(d, cost_bps):
    pos = d["position"].values
    ret = d["ret"].fillna(0.0).values
    n = len(d)

    pos_change = np.zeros(n)
    pos_change[0] = abs(pos[0] - 0.0)
    pos_change[1:] = np.abs(pos[1:] - pos[:-1])

    turnover_cost = pos_change * (cost_bps / 10000.0)
    roll_cost = np.abs(pos) * (ROLL_BPS_MONTH / 10000.0) / DAYS_PER_MONTH

    gross_ret = pos * ret
    net_ret = gross_ret - turnover_cost - roll_cost

    d = d.copy()
    d["gross_ret"] = gross_ret
    d["turnover_cost"] = turnover_cost
    d["roll_cost"] = roll_cost
    d["net_ret"] = net_ret
    return d


def compute_metrics(net_ret_series):
    r = net_ret_series.dropna()
    if len(r) < 20:
        return None
    ann_factor = TRADING_DAYS_YEAR
    mean_d = r.mean()
    std_d = r.std()
    net_ann_pct = ((1 + mean_d) ** ann_factor - 1) * 100 if not np.isnan(mean_d) else np.nan
    # simple arithmetic annualization (also report) - use compounding equity for maxDD
    equity = (1 + r).cumprod()
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    max_dd_pct = dd.min() * 100
    sharpe = (mean_d / std_d) * np.sqrt(ann_factor) if std_d > 0 else np.nan

    monthly = r.resample("ME").apply(lambda x: (1 + x).prod() - 1) if isinstance(r.index, pd.DatetimeIndex) else None
    monthly_mean_pct = monthly.mean() * 100 if monthly is not None else np.nan
    worst_month_pct = monthly.min() * 100 if monthly is not None else np.nan

    # PF by month-block
    if monthly is not None:
        gains = monthly[monthly > 0].sum()
        losses = -monthly[monthly < 0].sum()
        pf_month = gains / losses if losses > 0 else np.nan
    else:
        pf_month = np.nan

    return {
        "n_days": int(len(r)),
        "net_ann_pct": round(float(net_ann_pct), 2),
        "sharpe": round(float(sharpe), 3) if not np.isnan(sharpe) else None,
        "maxdd_pct": round(float(max_dd_pct), 2),
        "monthly_mean_pct": round(float(monthly_mean_pct), 3) if not np.isnan(monthly_mean_pct) else None,
        "worst_month_pct": round(float(worst_month_pct), 2) if not np.isnan(worst_month_pct) else None,
        "pf_month_blocks": round(float(pf_month), 2) if pf_month is not None and not np.isnan(pf_month) else None,
    }


def block_bootstrap_ann_return(daily_ret, n_boot=N_BOOT, block_size=21, seed=RNG_SEED):
    r = daily_ret.dropna().values
    n = len(r)
    if n < block_size * 2:
        return None
    rng = np.random.default_rng(seed)
    n_blocks_needed = int(np.ceil(n / block_size))
    ann_returns = []
    for _ in range(n_boot):
        start_idxs = rng.integers(0, n - block_size, size=n_blocks_needed)
        sample = np.concatenate([r[s:s + block_size] for s in start_idxs])[:n]
        mean_d = sample.mean()
        ann = (1 + mean_d) ** TRADING_DAYS_YEAR - 1
        ann_returns.append(ann)
    ann_returns = np.array(ann_returns)
    return {
        "n_boot": n_boot,
        "block_size": block_size,
        "seed": seed,
        "mean_ann_pct": round(float(ann_returns.mean() * 100), 2),
        "ci_2.5_pct": round(float(np.percentile(ann_returns, 2.5) * 100), 2),
        "ci_50_pct": round(float(np.percentile(ann_returns, 50) * 100), 2),
        "ci_97.5_pct": round(float(np.percentile(ann_returns, 97.5) * 100), 2),
        "pct_positive": round(float((ann_returns > 0).mean() * 100), 1),
    }


def main():
    print("=" * 70)
    print("R2 - TSM multi-ativos B3 - carregando dados")
    print("=" * 70)

    raw_data = {}
    load_report = {}
    for ticker in UNIVERSE:
        info = load_asset(ticker)
        if info is None:
            print(f"  {ticker}: arquivo nao encontrado, pulando")
            continue
        raw_data[ticker] = info["df"]
        load_report[ticker] = {
            "n_dropped_2025plus": info["n_dropped_2025plus"],
            "max_date_raw_before_filter": info["max_date_raw"],
            "n_rows_in_window": info["n_rows_window"],
            "date_min": info["date_min"],
            "date_max": info["date_max"],
        }
        print(f"  {ticker}: {info['n_rows_window']} pregoes na janela "
              f"[{info['date_min']} .. {info['date_max']}] "
              f"(dropados 2025+: {info['n_dropped_2025plus']}, raw max date: {info['max_date_raw']})")

    print()
    print("REGRA: dados 2025+ filtrados apos load, conforme instrucao. Ver load_report acima.")
    print()

    all_results = {}
    all_daily_returns_by_L = {}  # L -> {ticker: pd.Series indexed by date of net_ret}

    for L in L_GRID:
        print("=" * 70)
        print(f"L = {L} pregoes")
        print("=" * 70)

        sleeve_metrics = {}
        sleeve_daily = {}

        for ticker, df in raw_data.items():
            cost_bps = COST_BPS_HIGH if ticker in HIGH_FEE_ASSETS else COST_BPS_NORMAL
            d = build_sleeve(df, L)
            d = apply_costs(d, cost_bps)
            d = d.set_index("datetime_b3")
            sleeve_daily[ticker] = d["net_ret"]
            m = compute_metrics(d["net_ret"])
            sleeve_metrics[ticker] = m
            if m:
                print(f"  {ticker:5s} net_ann={m['net_ann_pct']:>8.2f}%  Sharpe={m['sharpe']}"
                      f"  maxDD={m['maxdd_pct']:>7.2f}%  n_days={m['n_days']}")
            else:
                print(f"  {ticker:5s} dados insuficientes")

        # Portfolio: equal-weight average of sleeve net_ret across PORTFOLIO_SLEEVES available that day
        port_cols = {t: sleeve_daily[t] for t in PORTFOLIO_SLEEVES if t in sleeve_daily}
        port_df = pd.DataFrame(port_cols)
        # only average across sleeves with data on a given day (avoid NaN treated as 0)
        port_ret = port_df.mean(axis=1, skipna=True)
        port_ret = port_ret.dropna()

        port_metrics = compute_metrics(port_ret)
        print(f"  {'CARTEIRA':5s} net_ann={port_metrics['net_ann_pct']:>8.2f}%  "
              f"Sharpe={port_metrics['sharpe']}  maxDD={port_metrics['maxdd_pct']:>7.2f}%  "
              f"n_days={port_metrics['n_days']}  monthly_mean={port_metrics['monthly_mean_pct']}%  "
              f"worst_month={port_metrics['worst_month_pct']}%  PF_month_blocks={port_metrics['pf_month_blocks']}")

        all_results[f"L{L}"] = {
            "sleeves": sleeve_metrics,
            "portfolio": port_metrics,
            "portfolio_sleeves_used": [t for t in PORTFOLIO_SLEEVES if t in sleeve_daily],
        }
        all_daily_returns_by_L[L] = {"sleeve_daily": sleeve_daily, "portfolio_ret": port_ret}
        print()

    # Correlation matrix (daily net returns, all available sleeves) - use L=126 as reference
    print("=" * 70)
    print("Matriz de correlacao diaria entre sleeves (L=126, net returns)")
    print("=" * 70)
    ref_L = 126 if 126 in all_daily_returns_by_L else L_GRID[-1]
    sleeve_daily_ref = all_daily_returns_by_L[ref_L]["sleeve_daily"]
    corr_df = pd.DataFrame(sleeve_daily_ref).corr()
    print(corr_df.round(2).to_string())
    corr_df.round(4).to_csv(OUT_DIR / "correlation_matrix_L126.csv")

    # Save L=126 portfolio daily series
    port_ret_126 = all_daily_returns_by_L.get(126, all_daily_returns_by_L[L_GRID[-1]])["portfolio_ret"]
    port_csv = port_ret_126.reset_index()
    port_csv.columns = ["date", "ret"]
    port_csv.to_csv(OUT_DIR / "portfolio_L126_daily_returns.csv", index=False)
    print(f"\nSerie diaria da carteira L=126 salva em portfolio_L126_daily_returns.csv ({len(port_csv)} linhas)")

    # Bootstrap on portfolio L=126
    print()
    print("=" * 70)
    print("Bootstrap em blocos (1000x, block=21 dias, seed=42) - carteira L=126")
    print("=" * 70)
    boot_126 = block_bootstrap_ann_return(port_ret_126)
    print(json.dumps(boot_126, indent=2))

    # Also bootstrap L=63 portfolio for comparison
    port_ret_63 = all_daily_returns_by_L.get(63, {}).get("portfolio_ret")
    boot_63 = None
    if port_ret_63 is not None:
        boot_63 = block_bootstrap_ann_return(port_ret_63)
        print("\nBootstrap carteira L=63 (comparacao):")
        print(json.dumps(boot_63, indent=2))

    # BIT separate note
    bit_note = None
    if "BIT" in raw_data:
        bit_note = f"BIT presente com {load_report['BIT']['n_rows_in_window']} pregoes na janela " \
                   f"[{load_report['BIT']['date_min']} .. {load_report['BIT']['date_max']}] - historico curto (~2024+), reportado separadamente, NAO entra na carteira."
        print(f"\n{bit_note}")

    final_output = {
        "meta": {
            "window_start": str(WINDOW_START.date()),
            "window_end": str(WINDOW_END.date()),
            "cutoff_2025_rule": "dados >= 2025-01-01 filtrados pos-load, PROIBIDOS por instrucao",
            "L_grid": L_GRID,
            "F_rebal": F_REBAL,
            "vol_window": VOL_WINDOW,
            "vol_target": VOL_TARGET,
            "weight_cap": WEIGHT_CAP,
            "cost_bps_normal": COST_BPS_NORMAL,
            "cost_bps_high_fee": COST_BPS_HIGH,
            "high_fee_assets": sorted(HIGH_FEE_ASSETS),
            "roll_bps_month": ROLL_BPS_MONTH,
            "portfolio_sleeves": PORTFOLIO_SLEEVES,
            "excluded_from_portfolio": ["IND", "DOL", "BIT"],
            "exclusion_reason": "IND correlaciona ~1 com WIN; DOL correlaciona ~1 com WDO; BIT tem historico curto (~2024+)",
        },
        "load_report": load_report,
        "results_by_L": all_results,
        "correlation_matrix_L126": corr_df.round(4).to_dict(),
        "bootstrap_portfolio_L126": boot_126,
        "bootstrap_portfolio_L63": boot_63,
        "bit_note": bit_note,
    }

    with open(OUT_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(final_output, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 70)
    print(f"metrics.json salvo em {OUT_DIR / 'metrics.json'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
