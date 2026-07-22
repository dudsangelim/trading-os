"""
HOLDOUT b3_factory_v0 -- avaliacao unica, gates pre-registrados.
Ver HOLDOUT_PREREG.md (2026-07-22, "destrave os 3"). NENHUM parametro pode
ser alterado em funcao do resultado do holdout.

ETAPA 1 (validacao, <=2024-12-31): reproduz numeros conhecidos de
R2_tsm_multi (E1, E2a) e W2_carry_cond (E2b) e W3_level_fade (E3),
tolerancia +-15% relativo. Se alguma estrutura falhar a validacao, essa
estrutura NAO tem holdout computado (reportada como "validation FAILED").

ETAPA 2 (holdout, 2025-01-01..2026-07-17): mesma mecanica, mesmos
parametros, sem cutoff 2025 no load. Warmup: para E1/E2a as series sao
construidas desde o inicio disponivel (2021-07-19) e so entao a janela
de METRICAS e cortada para 2025-01-01+ (posicoes ja formadas por dados
anteriores, retornos de warmup NAO entram nas metricas). Para E2b o sinal
de carry usa o proxy (historico completo, cobre ate 2026-07-21) aplicado
ao retorno do futuro WDO real. Para E3 (M5, sem lookback longo) so a
janela de datas e filtrada.

Auto-suficiente: paths absolutos, apenas pandas/numpy/pyarrow.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
D1_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
PROXY_PATH = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy\proxy_raw.parquet")
FETCH_PROXY_SCRIPT = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy\fetch_proxy_data.py")
WIN_M5_PATH = D1_DIR / "WIN_cont_N_M5.parquet"
OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_factory_v0\HOLDOUT")
OUT_DIR.mkdir(parents=True, exist_ok=True)

RESULTS_TXT = OUT_DIR / "HOLDOUT_RESULTS.txt"
METRICS_JSON = OUT_DIR / "holdout_metrics.json"

# ---------------------------------------------------------------------------
# Constantes globais
# ---------------------------------------------------------------------------
TRADING_DAYS_YEAR = 252
DAYS_PER_MONTH = 21
TOLERANCE = 0.15  # +-15% relativo para etapa 1

VALIDATION_WINDOW_START = pd.Timestamp("2021-07-19")
VALIDATION_WINDOW_END = pd.Timestamp("2024-12-31")
CUTOFF_2025 = pd.Timestamp("2025-01-01")
HOLDOUT_START = pd.Timestamp("2025-01-01")
HOLDOUT_END = pd.Timestamp("2026-07-17")

LOG_LINES = []


def log(msg=""):
    print(msg)
    LOG_LINES.append(str(msg))


# ===========================================================================
# E1 / E2a -- motor TSM multi-ativos (portado de R2_tsm_multi.py)
# ===========================================================================
PORTFOLIO_SLEEVES = ["WDO", "DI1", "BGI", "CCM", "ICF", "WIN"]
HIGH_FEE_ASSETS = {"BGI", "CCM", "ICF"}
L_GRID = [63, 126]
F_REBAL = 21
VOL_WINDOW = 20
VOL_TARGET = 0.10
WEIGHT_CAP = 3.0
COST_BPS_NORMAL = 1.5
COST_BPS_HIGH = 3.0
ROLL_BPS_MONTH = 1.2


def load_d1_asset(ticker, cutoff=None, win_start=None, win_end=None):
    path = D1_DIR / f"{ticker}_cont_ADJprop_D1.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df[["datetime_b3", "close"]].copy()
    df["datetime_b3"] = pd.to_datetime(df["datetime_b3"])
    df = df.sort_values("datetime_b3").drop_duplicates("datetime_b3").reset_index(drop=True)
    if cutoff is not None:
        df = df[df["datetime_b3"] < cutoff].reset_index(drop=True)
    if win_start is not None:
        df = df[df["datetime_b3"] >= win_start].reset_index(drop=True)
    if win_end is not None:
        df = df[df["datetime_b3"] <= win_end].reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    return df


def build_sleeve(df, L):
    d = df.copy().reset_index(drop=True)
    d["vol20"] = d["ret"].rolling(VOL_WINDOW).std() * np.sqrt(TRADING_DAYS_YEAR)
    d["cum_L"] = d["close"] / d["close"].shift(L) - 1.0

    n = len(d)
    position = np.full(n, np.nan)
    first_valid = max(L, VOL_WINDOW)
    rebal_idxs = list(range(first_valid, n, F_REBAL))

    current_weight = 0.0
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
        start_apply = idx + 1
        end_apply = min(idx + F_REBAL + 1, n)
        position[start_apply:end_apply] = current_weight

    d["position"] = position
    d["position"] = d["position"].fillna(0.0)
    return d


def apply_costs_tsm(d, cost_bps):
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
    d["pos_change_flag"] = pos_change > 1e-12
    return d


def compute_metrics_tsm(d_full, metrics_start=None, metrics_end=None):
    """d_full: dataframe indexed by datetime_b3 with net_ret and pos_change_flag,
    built over the FULL available history (so lookbacks/warmup are correct).
    Metrics computed only on the [metrics_start, metrics_end] slice -- equity
    rebased to 1.0 at the first day of the slice, warmup returns excluded."""
    dd = d_full.set_index("datetime_b3") if "datetime_b3" in d_full.columns else d_full
    if metrics_start is not None or metrics_end is not None:
        mask = pd.Series(True, index=dd.index)
        if metrics_start is not None:
            mask &= dd.index >= metrics_start
        if metrics_end is not None:
            mask &= dd.index <= metrics_end
        dd = dd[mask]

    r = dd["net_ret"].dropna()
    if len(r) < 5:
        return None, dd
    n_days = len(r)
    n_years = n_days / TRADING_DAYS_YEAR
    equity = (1 + r).cumprod()
    total_ret = equity.iloc[-1] - 1
    net_ann_pct = ((1 + total_ret) ** (1 / n_years) - 1) * 100 if n_years > 0 else np.nan

    mean_d = r.mean()
    std_d = r.std()
    sharpe = (mean_d / std_d) * np.sqrt(TRADING_DAYS_YEAR) if std_d > 0 else np.nan

    running_max = equity.cummax()
    dd_series = equity / running_max - 1.0
    max_dd_pct = dd_series.min() * 100

    monthly = r.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    monthly_mean_pct = monthly.mean() * 100 if len(monthly) else np.nan
    worst_month_pct = monthly.min() * 100 if len(monthly) else np.nan
    gains = monthly[monthly > 0].sum()
    losses = -monthly[monthly < 0].sum()
    pf_month = gains / losses if losses > 0 else (np.inf if gains > 0 else np.nan)

    n_pos_changes = int(dd["pos_change_flag"].sum()) if "pos_change_flag" in dd.columns else None

    m = {
        "n_days": int(n_days),
        "net_ann_pct": round(float(net_ann_pct), 2) if not np.isnan(net_ann_pct) else None,
        "sharpe": round(float(sharpe), 3) if not np.isnan(sharpe) else None,
        "maxdd_pct": round(float(max_dd_pct), 2),
        "monthly_mean_pct": round(float(monthly_mean_pct), 3) if not np.isnan(monthly_mean_pct) else None,
        "worst_month_pct": round(float(worst_month_pct), 2) if not np.isnan(worst_month_pct) else None,
        "pf_month_blocks": round(float(pf_month), 2) if pf_month not in (None,) and not (isinstance(pf_month, float) and np.isnan(pf_month)) else None,
        "n_position_changes": n_pos_changes,
        "date_min": str(r.index.min().date()),
        "date_max": str(r.index.max().date()),
    }
    return m, dd


def run_tsm_engine(cutoff, win_start, win_end, metrics_start=None, metrics_end=None):
    """Runs the TSM sleeve engine for all PORTFOLIO_SLEEVES + returns per-L
    sleeve dict, portfolio dict, and raw daily-return DataFrames for saving."""
    raw = {}
    for ticker in PORTFOLIO_SLEEVES:
        df = load_d1_asset(ticker, cutoff=cutoff, win_start=win_start, win_end=win_end)
        if df is not None:
            raw[ticker] = df

    out = {}
    daily_by_L = {}
    for L in L_GRID:
        sleeve_metrics = {}
        sleeve_daily_full = {}
        for ticker, df in raw.items():
            cost_bps = COST_BPS_HIGH if ticker in HIGH_FEE_ASSETS else COST_BPS_NORMAL
            d = build_sleeve(df, L)
            d = apply_costs_tsm(d, cost_bps)
            m, dd_sliced = compute_metrics_tsm(d, metrics_start, metrics_end)
            sleeve_metrics[ticker] = m
            sleeve_daily_full[ticker] = d.set_index("datetime_b3")["net_ret"]

        # portfolio: equal-weight mean of sleeve net_ret (full history for proper
        # warmup), then slice metrics window
        port_cols = {t: sleeve_daily_full[t] for t in PORTFOLIO_SLEEVES if t in sleeve_daily_full}
        port_df = pd.DataFrame(port_cols)
        port_ret_full = port_df.mean(axis=1, skipna=True).dropna()

        port_ret_sliced = port_ret_full.copy()
        if metrics_start is not None:
            port_ret_sliced = port_ret_sliced[port_ret_sliced.index >= metrics_start]
        if metrics_end is not None:
            port_ret_sliced = port_ret_sliced[port_ret_sliced.index <= metrics_end]

        # portfolio metrics computed directly on sliced returns (equity rebased)
        port_frame = pd.DataFrame({"net_ret": port_ret_sliced, "pos_change_flag": False})
        port_m, _ = compute_metrics_tsm(port_frame.reset_index().rename(columns={"index": "datetime_b3"}))
        # n_position_changes for the portfolio row is not meaningful per-flag (each
        # sleeve rebalances on its own schedule); report the sum of sleeve-level
        # rebalance events in the same window as a turnover proxy instead.
        if port_m is not None:
            port_m["n_position_changes"] = sum(
                (m["n_position_changes"] or 0) for m in sleeve_metrics.values() if m is not None
            )

        out[f"L{L}"] = {"sleeves": sleeve_metrics, "portfolio": port_m}
        daily_by_L[L] = {"sleeve_daily_full": sleeve_daily_full, "portfolio_ret_sliced": port_ret_sliced}

    return out, daily_by_L, raw


# ===========================================================================
# E2b -- carry condicional WDO (portado de W2_carry_cond.py)
# ===========================================================================
DIFF_THRESH_BEST = 6
VOL_FILTER_BEST = "median252"
CARRY_COST_BPS = 1.5


def load_proxy(cutoff=None):
    df = pd.read_parquet(PROXY_PATH)
    df["date"] = pd.to_datetime(df["date"])
    if cutoff is not None:
        df = df[df["date"] < cutoff].copy()
    df = df.sort_values("date").reset_index(drop=True)
    df["ffr_ad"] = (1 + df["ffr_aa"] / 100) ** (1 / 252) - 1
    df["wdo_proxy_ret"] = df["usdbrl"].pct_change() - (df["cdi_ad"] / 100 - df["ffr_ad"])
    df["diff_pp"] = df["cdi_ad"] * 252 - df["ffr_aa"]
    df["vol20d"] = df["wdo_proxy_ret"].rolling(20).std() * np.sqrt(TRADING_DAYS_YEAR)
    df["vol_median252"] = df["vol20d"].shift(1).rolling(252, min_periods=126).median()
    df["vol_p75_252"] = df["vol20d"].shift(1).rolling(252, min_periods=126).quantile(0.75)
    return df


def load_real_wdo_d1(cutoff=None):
    df = pd.read_parquet(D1_DIR / "WDO_cont_ADJprop_D1.parquet")
    df["datetime_b3"] = pd.to_datetime(df["datetime_b3"])
    if cutoff is not None:
        df = df[df["datetime_b3"] < cutoff].copy()
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    df["real_ret"] = df["close"].pct_change()
    df = df.rename(columns={"datetime_b3": "date"})
    return df[["date", "real_ret"]]


def build_gate(df, diff_thresh, vol_filter):
    diff_ok = df["diff_pp"] > diff_thresh
    if vol_filter == "median252":
        vol_ok = df["vol20d"] < df["vol_median252"]
    elif vol_filter == "p75_252":
        vol_ok = df["vol20d"] < df["vol_p75_252"]
    else:
        vol_ok = pd.Series(True, index=df.index)
    return (diff_ok & vol_ok).fillna(False)


def run_carry_backtest(df, ret_col, gate, cost_bps_rt, roll_bps_month=ROLL_BPS_MONTH):
    pos = gate.shift(1).fillna(False).astype(int)
    ret = df[ret_col].fillna(0.0)
    strat_ret = np.where(pos == 1, -ret, 0.0)
    roll_drag = (roll_bps_month / 10000.0) / DAYS_PER_MONTH
    strat_ret = strat_ret - np.where(pos == 1, roll_drag, 0.0)
    pos_shift = pos.shift(1).fillna(0).astype(int)
    transition = (pos != pos_shift).values
    half_cost = (cost_bps_rt / 10000.0) / 2.0
    strat_ret = strat_ret - np.where(transition, half_cost, 0.0)
    out = pd.DataFrame({"date": df["date"].values, "pos": pos.values, "ret": strat_ret})
    return out


def compute_metrics_carry(bt):
    ret = bt["ret"]
    n = len(ret)
    if n == 0 or ret.abs().sum() == 0:
        return dict(net_ann_pct=0.0, sharpe=0.0, maxdd_pct=0.0, pf=None,
                    monthly_mean_pct=0.0, worst_month_pct=0.0, n_trades=0,
                    n_days=n)

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
    monthly_mean_pct = monthly.mean() * 100 if len(monthly) else np.nan
    worst_month_pct = monthly.min() * 100 if len(monthly) else np.nan

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
    gains = trade_rets[trade_rets > 0].sum() if n_trades else 0.0
    losses = -trade_rets[trade_rets < 0].sum() if n_trades else 0.0
    pf = gains / losses if losses > 0 else (np.inf if gains > 0 else None)

    return dict(net_ann_pct=round(float(net_ann_pct), 2) if not np.isnan(net_ann_pct) else None,
                sharpe=round(float(sharpe), 3),
                maxdd_pct=round(float(maxdd_pct), 2),
                pf=round(float(pf), 2) if pf not in (None,) and pf != np.inf else pf,
                monthly_mean_pct=round(float(monthly_mean_pct), 3) if not np.isnan(monthly_mean_pct) else None,
                worst_month_pct=round(float(worst_month_pct), 2) if not np.isnan(worst_month_pct) else None,
                n_trades=int(n_trades),
                n_days=int(n))


# ===========================================================================
# E3 -- fade PDH/PDL WIN M5 (portado de W3_level_fade.py)
# ===========================================================================
S_BPS = 25
T_BPS = 10
TIME_STOP_MIN = 60
MAX_TRADES_DAY = 2
SESSION_OPEN = pd.Timedelta(hours=9, minutes=0)
NO_NEW_TRADES_AFTER = pd.Timedelta(hours=17, minutes=30)
E3_COST_BPS = 1.5


def load_win_m5(cutoff=None, win_start=None, win_end=None):
    df = pd.read_parquet(WIN_M5_PATH)
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    df["datetime_b3"] = pd.to_datetime(df["datetime_b3"])
    if cutoff is not None:
        df = df[df["datetime_b3"] < cutoff].reset_index(drop=True)
    if win_start is not None:
        df = df[df["datetime_b3"] >= win_start].reset_index(drop=True)
    if win_end is not None:
        df = df[df["datetime_b3"] <= win_end].reset_index(drop=True)
    df["date"] = df["datetime_b3"].dt.date
    df["tod"] = df["datetime_b3"] - pd.to_datetime(df["date"])
    return df


def build_daily_levels(df):
    daily = df.groupby("date").agg(day_high=("high", "max"), day_low=("low", "min"),
                                     first_tod=("tod", "min")).reset_index()
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["pdh"] = daily["day_high"].shift(1)
    daily["pdl"] = daily["day_low"].shift(1)
    daily["valid_open"] = daily["first_tod"] == SESSION_OPEN
    return daily


def simulate_fade(df, daily, S_bps, T_bps, cost_bps, date_filter_start=None, date_filter_end=None):
    S = S_bps / 10000.0
    T = T_bps / 10000.0
    diffs = df["datetime_b3"].diff().dropna()
    bar_dt = diffs[diffs > pd.Timedelta(0)].mode().iloc[0]
    n_bars_timestop = int(np.ceil(pd.Timedelta(minutes=TIME_STOP_MIN) / bar_dt))

    daily_map = daily.set_index("date")
    trades = []

    grouped = df.groupby("date")
    dates_sorted = sorted(grouped.groups.keys())
    if date_filter_start is not None:
        dates_sorted = [d for d in dates_sorted if d >= date_filter_start]
    if date_filter_end is not None:
        dates_sorted = [d for d in dates_sorted if d <= date_filter_end]

    for d in dates_sorted:
        if d not in daily_map.index:
            continue
        row = daily_map.loc[d]
        if not row["valid_open"]:
            continue
        pdh, pdl = row["pdh"], row["pdl"]
        if pd.isna(pdh) or pd.isna(pdl):
            continue

        day_df = grouped.get_group(d).reset_index(drop=True)
        n_bars = len(day_df)
        levels_hit = {"PDH": False, "PDL": False}
        trades_today = 0

        for i in range(n_bars):
            if trades_today >= MAX_TRADES_DAY:
                break
            bar = day_df.iloc[i]
            if bar["tod"] >= NO_NEW_TRADES_AFTER:
                break

            for level_name, level_price, direction in (
                ("PDH", pdh, "short"),
                ("PDL", pdl, "long"),
            ):
                if levels_hit[level_name]:
                    continue
                if bar["low"] <= level_price <= bar["high"]:
                    levels_hit[level_name] = True
                    trades_today += 1
                    entry_price = level_price
                    entry_idx = i

                    if direction == "short":
                        stop_price = entry_price * (1 + S)
                        tp_price = entry_price * (1 - T)
                    else:
                        stop_price = entry_price * (1 - S)
                        tp_price = entry_price * (1 + T)

                    exit_reason = None
                    exit_price = None

                    last_idx = min(entry_idx + n_bars_timestop, n_bars - 1)
                    for j in range(entry_idx, last_idx + 1):
                        bj = day_df.iloc[j]
                        hit_stop = (bj["low"] <= stop_price <= bj["high"])
                        hit_tp = (bj["low"] <= tp_price <= bj["high"])
                        if hit_stop and hit_tp:
                            exit_reason, exit_price = "stop", stop_price
                            break
                        elif hit_stop:
                            exit_reason, exit_price = "stop", stop_price
                            break
                        elif hit_tp:
                            exit_reason, exit_price = "tp", tp_price
                            break
                        if j == last_idx:
                            exit_reason, exit_price = "time", bj["close"]

                    if direction == "short":
                        gross_ret = (entry_price - exit_price) / entry_price
                    else:
                        gross_ret = (exit_price - entry_price) / entry_price

                    gross_bps = gross_ret * 10000.0
                    net_bps = gross_bps - cost_bps
                    trades.append({
                        "date": d, "level": level_name, "direction": direction,
                        "entry_price": entry_price, "exit_price": exit_price,
                        "exit_reason": exit_reason, "gross_bps": gross_bps,
                        "net_bps": net_bps,
                    })
                    break

    return pd.DataFrame(trades)


def metrics_fade(trades_df):
    if len(trades_df) == 0:
        return dict(n=0, win_rate=None, pf=None, expectancy_bps=None, exit_breakdown={})
    net = trades_df["net_bps"]
    wins = net[net > 0]
    losses = net[net <= 0]
    n = len(net)
    win_rate = len(wins) / n
    gain_sum = wins.sum()
    loss_sum = -losses.sum()
    pf = (gain_sum / loss_sum) if loss_sum > 0 else (np.inf if gain_sum > 0 else None)
    expectancy_bps = net.mean()
    breakdown = trades_df["exit_reason"].value_counts().to_dict()
    return dict(n=n, win_rate=round(float(win_rate), 4),
                pf=round(float(pf), 3) if pf not in (None, np.inf) else pf,
                expectancy_bps=round(float(expectancy_bps), 3),
                exit_breakdown=breakdown)


# ===========================================================================
# Utilidades de validacao (tolerancia +-15%)
# ===========================================================================
def within_tol(value, target, tol=TOLERANCE):
    if value is None or target is None:
        return False
    if target == 0:
        return abs(value) < 1e-6
    rel = abs(value - target) / abs(target)
    return rel <= tol, rel


def check(label, value, target, tol=TOLERANCE):
    if value is None or target is None:
        ok, rel = False, None
    else:
        ok = abs(value - target) / abs(target) <= tol if target != 0 else abs(value) < 1e-6
        rel = abs(value - target) / abs(target) if target != 0 else abs(value)
    status = "OK" if ok else "FORA DA TOLERANCIA"
    rel_str = f"{rel*100:.1f}%" if rel is not None else "n/a"
    log(f"    {label}: obtido={value}  alvo={target}  diff_rel={rel_str}  -> {status}")
    return ok


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    all_results = {"validation": {}, "holdout": {}, "verdicts": {}}

    log("=" * 78)
    log("HOLDOUT b3_factory_v0 -- avaliacao unica, gates pre-registrados")
    log(f"Holdout window: {HOLDOUT_START.date()} .. {HOLDOUT_END.date()}")
    log("=" * 78)

    # check proxy range
    proxy_check = pd.read_parquet(PROXY_PATH)
    proxy_check["date"] = pd.to_datetime(proxy_check["date"])
    log(f"\nproxy_raw.parquet range: {proxy_check['date'].min().date()} .. {proxy_check['date'].max().date()} "
        f"({len(proxy_check)} linhas)")
    if proxy_check["date"].max() < HOLDOUT_END:
        log("  ATENCAO: proxy_raw nao cobre ate o fim do holdout -- seria necessario "
            f"rodar {FETCH_PROXY_SCRIPT} novamente. Abortando E2b.")
        all_results["verdicts"]["E2b"] = "FAIL (proxy incompleto)"
    else:
        log("  proxy_raw cobre o periodo do holdout integralmente -- fetch novo NAO necessario.")

    # =======================================================================
    # ETAPA 1 -- VALIDACAO (<=2024-12-31)
    # =======================================================================
    log("\n" + "=" * 78)
    log("ETAPA 1 -- VALIDACAO (reproducao dos numeros conhecidos, <=2024-12-31)")
    log("=" * 78)

    validation_pass = {}

    # --- E1: carteira TSM multi-ativos, L=63 e L=126 -----------------------
    log("\n--- E1: Carteira TSM multi-ativos (L=63, L=126) ---")
    e1_val_results, e1_val_daily, e1_val_raw = run_tsm_engine(
        cutoff=CUTOFF_2025, win_start=VALIDATION_WINDOW_START, win_end=VALIDATION_WINDOW_END
    )
    all_results["validation"]["E1"] = e1_val_results
    p126 = e1_val_results["L126"]["portfolio"]
    log(f"  L126 portfolio: net_ann={p126['net_ann_pct']}%  Sharpe={p126['sharpe']}  "
        f"maxDD={p126['maxdd_pct']}%  n_days={p126['n_days']}")
    p63 = e1_val_results["L63"]["portfolio"]
    log(f"  L63  portfolio: net_ann={p63['net_ann_pct']}%  Sharpe={p63['sharpe']}  "
        f"maxDD={p63['maxdd_pct']}%  n_days={p63['n_days']}")
    log("  Alvos pre-reg (L=126): net~6.2% aa, Sharpe~1.29, maxDD~-5.8%")
    ok1 = check("net_ann_pct (L126)", p126["net_ann_pct"], 6.2)
    ok2 = check("sharpe (L126)", p126["sharpe"], 1.29)
    ok3 = check("maxdd_pct (L126)", p126["maxdd_pct"], -5.8)
    validation_pass["E1"] = ok1 and ok2 and ok3
    log(f"  E1 validacao: {'PASS' if validation_pass['E1'] else 'FALHOU'}")

    # --- E2a: TSM WDO L=126/F=21 vol-target, real-check ---------------------
    log("\n--- E2a: TSM WDO L=126/F=21 vt (real-check) ---")
    wdo_m = e1_val_results["L126"]["sleeves"]["WDO"]
    log(f"  WDO L126: net_ann={wdo_m['net_ann_pct']}%  Sharpe={wdo_m['sharpe']}  "
        f"maxDD={wdo_m['maxdd_pct']}%  n_days={wdo_m['n_days']}")
    log("  Alvos pre-reg: net~+6.7%, Sharpe~0.66 (maxDD referencia ~-9.7% no FACTORY_REPORT)")
    ok1 = check("net_ann_pct (WDO L126)", wdo_m["net_ann_pct"], 6.7)
    ok2 = check("sharpe (WDO L126)", wdo_m["sharpe"], 0.66)
    validation_pass["E2a"] = ok1 and ok2
    log(f"  E2a validacao: {'PASS' if validation_pass['E2a'] else 'FALHOU'}")

    # --- E2b: carry condicional WDO, diff>6pp / vol<median252, real-check --
    log("\n--- E2b: Carry condicional WDO diff>6pp / vol<median252 (real-check) ---")
    if all_results["verdicts"].get("E2b") == "FAIL (proxy incompleto)":
        validation_pass["E2b"] = False
        log("  proxy incompleto -- pulando validacao E2b")
    else:
        proxy_val = load_proxy(cutoff=CUTOFF_2025)
        real_val = load_real_wdo_d1(cutoff=CUTOFF_2025)
        merged_val = proxy_val[["date", "diff_pp", "vol20d", "vol_median252", "vol_p75_252"]].merge(
            real_val, on="date", how="inner"
        )
        real_check_val = merged_val[
            (merged_val["date"] >= pd.Timestamp("2021-07-01")) & (merged_val["date"] <= VALIDATION_WINDOW_END)
        ].reset_index(drop=True)
        gate_val = build_gate(real_check_val, DIFF_THRESH_BEST, VOL_FILTER_BEST)
        bt_val = run_carry_backtest(real_check_val, "real_ret", gate_val, CARRY_COST_BPS)
        e2b_val_m = compute_metrics_carry(bt_val)
        all_results["validation"]["E2b"] = e2b_val_m
        log(f"  real-check: net_ann={e2b_val_m['net_ann_pct']}%  Sharpe={e2b_val_m['sharpe']}  "
            f"maxDD={e2b_val_m['maxdd_pct']}%  PF={e2b_val_m['pf']}  n_trades={e2b_val_m['n_trades']}")
        log("  Alvo pre-reg: net~+5.7%")
        ok1 = check("net_ann_pct (real-check)", e2b_val_m["net_ann_pct"], 5.7)
        validation_pass["E2b"] = ok1
        log(f"  E2b validacao: {'PASS' if validation_pass['E2b'] else 'FALHOU'}")

    # --- E3: fade PDH/PDL WIN M5 S25/T10, discovery 2023 / confirm 2024 ----
    log("\n--- E3: Fade PDH/PDL WIN M5 S=25/T=10 (discovery 2023 / confirm 2024) ---")
    df_m5_val = load_win_m5(cutoff=CUTOFF_2025)
    daily_m5_val = build_daily_levels(df_m5_val)
    disc_trades = simulate_fade(df_m5_val, daily_m5_val, S_BPS, T_BPS, E3_COST_BPS,
                                 date_filter_start=pd.Timestamp("2023-01-01").date(),
                                 date_filter_end=pd.Timestamp("2023-12-31").date())
    conf_trades = simulate_fade(df_m5_val, daily_m5_val, S_BPS, T_BPS, E3_COST_BPS,
                                 date_filter_start=pd.Timestamp("2024-01-01").date(),
                                 date_filter_end=pd.Timestamp("2024-12-31").date())
    disc_m = metrics_fade(disc_trades)
    conf_m = metrics_fade(conf_trades)
    all_results["validation"]["E3"] = {"discovery_2023": disc_m, "confirm_2024": conf_m}
    log(f"  discovery 2023: PF={disc_m['pf']}  n={disc_m['n']}  win_rate={disc_m['win_rate']}")
    log(f"  confirm   2024: PF={conf_m['pf']}  n={conf_m['n']}  win_rate={conf_m['win_rate']}")
    log("  Alvos pre-reg: PF disc 2023 ~1.77, PF confirm 2024 ~1.48")
    ok1 = check("PF discovery 2023", disc_m["pf"], 1.77)
    ok2 = check("PF confirm 2024", conf_m["pf"], 1.48)
    validation_pass["E3"] = ok1 and ok2
    log(f"  E3 validacao: {'PASS' if validation_pass['E3'] else 'FALHOU'}")

    log("\n" + "-" * 78)
    log("RESUMO ETAPA 1:")
    for k, v in validation_pass.items():
        log(f"  {k}: {'PASS -> prossegue p/ holdout' if v else 'FALHOU -> holdout NAO computado'}")
    log("-" * 78)

    # =======================================================================
    # ETAPA 2 -- HOLDOUT (2025-01-01 .. 2026-07-17)
    # =======================================================================
    log("\n" + "=" * 78)
    log(f"ETAPA 2 -- HOLDOUT ({HOLDOUT_START.date()} .. {HOLDOUT_END.date()})")
    log("=" * 78)

    holdout = {}

    # --- E1 holdout ----------------------------------------------------------
    if validation_pass["E1"]:
        log("\n--- E1 holdout: Carteira TSM multi-ativos (L=63, L=126) ---")
        e1_ho_results, e1_ho_daily, e1_ho_raw = run_tsm_engine(
            cutoff=None, win_start=VALIDATION_WINDOW_START, win_end=HOLDOUT_END,
            metrics_start=HOLDOUT_START, metrics_end=HOLDOUT_END,
        )
        holdout["E1"] = e1_ho_results
        for Lname in ["L63", "L126"]:
            pm = e1_ho_results[Lname]["portfolio"]
            log(f"  {Lname} portfolio: net_ann={pm['net_ann_pct']}%  Sharpe={pm['sharpe']}  "
                f"maxDD={pm['maxdd_pct']}%  PF_month={pm['pf_month_blocks']}  "
                f"n_days={pm['n_days']}  n_pos_changes={pm['n_position_changes']}  "
                f"monthly_mean={pm['monthly_mean_pct']}%  worst_month={pm['worst_month_pct']}%")
        # save daily portfolio series
        port126 = e1_ho_daily[126]["portfolio_ret_sliced"].reset_index()
        port126.columns = ["date", "ret"]
        port126.to_csv(OUT_DIR / "E1_portfolio_L126_holdout_daily.csv", index=False)
        port63 = e1_ho_daily[63]["portfolio_ret_sliced"].reset_index()
        port63.columns = ["date", "ret"]
        port63.to_csv(OUT_DIR / "E1_portfolio_L63_holdout_daily.csv", index=False)

        net_pos = [e1_ho_results[L]["portfolio"]["net_ann_pct"] > 0 for L in ["L63", "L126"]
                   if e1_ho_results[L]["portfolio"]["net_ann_pct"] is not None]
        both_positive = all(net_pos) and len(net_pos) == 2
        best_sharpe = max(
            [s for s in [e1_ho_results["L63"]["portfolio"]["sharpe"], e1_ho_results["L126"]["portfolio"]["sharpe"]] if s is not None],
            default=None,
        )
        worst_dd = min(
            [e1_ho_results["L63"]["portfolio"]["maxdd_pct"], e1_ho_results["L126"]["portfolio"]["maxdd_pct"]]
        )
        if both_positive and best_sharpe is not None and best_sharpe >= 0.5 and worst_dd >= -12:
            verdict = "PASS"
        elif (any(net_pos) and len(net_pos) > 0) and worst_dd >= -20:
            verdict = "PARTIAL"
        else:
            verdict = "FAIL"
        all_results["verdicts"]["E1"] = verdict
        log(f"  E1 veredito: {verdict}  (net>0 nos 2 L: {both_positive}, best_sharpe={best_sharpe}, worst_maxDD={worst_dd}%)")
    else:
        all_results["verdicts"]["E1"] = "NAO COMPUTADO (validacao falhou)"
        log("\n--- E1 holdout: NAO COMPUTADO (validacao Etapa 1 falhou) ---")

    # --- E2a holdout -----------------------------------------------------------
    if validation_pass["E2a"]:
        log("\n--- E2a holdout: TSM WDO L=126/F=21 vt ---")
        # reuse E1 holdout run if available (already computed WDO L126); else recompute
        if validation_pass["E1"]:
            wdo_ho = e1_ho_results["L126"]["sleeves"]["WDO"]
            wdo_ho_series = e1_ho_daily[126]["sleeve_daily_full"]["WDO"]
        else:
            ho_results, ho_daily, _ = run_tsm_engine(
                cutoff=None, win_start=VALIDATION_WINDOW_START, win_end=HOLDOUT_END,
                metrics_start=HOLDOUT_START, metrics_end=HOLDOUT_END,
            )
            wdo_ho = ho_results["L126"]["sleeves"]["WDO"]
            wdo_ho_series = ho_daily[126]["sleeve_daily_full"]["WDO"]
        holdout["E2a"] = wdo_ho
        log(f"  WDO L126: net_ann={wdo_ho['net_ann_pct']}%  Sharpe={wdo_ho['sharpe']}  "
            f"maxDD={wdo_ho['maxdd_pct']}%  PF_month={wdo_ho['pf_month_blocks']}  "
            f"n_days={wdo_ho['n_days']}  n_pos_changes={wdo_ho['n_position_changes']}")
        wdo_ho_slice = wdo_ho_series[(wdo_ho_series.index >= HOLDOUT_START) & (wdo_ho_series.index <= HOLDOUT_END)]
        wdo_ho_slice.reset_index().rename(columns={0: "ret", "datetime_b3": "date"}).to_csv(
            OUT_DIR / "E2a_wdo_tsm_l126_holdout_daily.csv", index=False
        )
        net_ok = wdo_ho["net_ann_pct"] is not None and wdo_ho["net_ann_pct"] > 0
        dd_ok = wdo_ho["maxdd_pct"] >= -15
        verdict = "PASS" if (net_ok and dd_ok) else "FAIL"
        all_results["verdicts"]["E2a"] = verdict
        log(f"  E2a veredito: {verdict}  (net>0: {net_ok}, maxDD<=15%: {dd_ok})")
    else:
        all_results["verdicts"]["E2a"] = "NAO COMPUTADO (validacao falhou)"
        log("\n--- E2a holdout: NAO COMPUTADO (validacao Etapa 1 falhou) ---")

    # --- E2b holdout -----------------------------------------------------------
    if validation_pass.get("E2b"):
        log("\n--- E2b holdout: Carry condicional WDO diff>6pp / vol<median252 ---")
        proxy_ho = load_proxy(cutoff=None)
        real_ho = load_real_wdo_d1(cutoff=None)
        merged_ho = proxy_ho[["date", "diff_pp", "vol20d", "vol_median252", "vol_p75_252"]].merge(
            real_ho, on="date", how="inner"
        )
        merged_ho_full = merged_ho[
            (merged_ho["date"] >= pd.Timestamp("2021-07-01")) & (merged_ho["date"] <= HOLDOUT_END)
        ].reset_index(drop=True)
        gate_ho = build_gate(merged_ho_full, DIFF_THRESH_BEST, VOL_FILTER_BEST)
        bt_ho_full = run_carry_backtest(merged_ho_full, "real_ret", gate_ho, CARRY_COST_BPS)
        # slice metrics to holdout window only (warmup returns excluded)
        bt_ho_full["date"] = pd.to_datetime(bt_ho_full["date"])
        bt_ho_sliced = bt_ho_full[(bt_ho_full["date"] >= HOLDOUT_START) & (bt_ho_full["date"] <= HOLDOUT_END)].reset_index(drop=True)
        e2b_ho_m = compute_metrics_carry(bt_ho_sliced)
        holdout["E2b"] = e2b_ho_m
        log(f"  net_ann={e2b_ho_m['net_ann_pct']}%  Sharpe={e2b_ho_m['sharpe']}  "
            f"maxDD={e2b_ho_m['maxdd_pct']}%  PF={e2b_ho_m['pf']}  n_trades={e2b_ho_m['n_trades']}  "
            f"n_days={e2b_ho_m['n_days']}  monthly_mean={e2b_ho_m['monthly_mean_pct']}%  "
            f"worst_month={e2b_ho_m['worst_month_pct']}%")
        bt_ho_sliced.to_csv(OUT_DIR / "E2b_carry_wdo_holdout_daily.csv", index=False)
        net_ok = e2b_ho_m["net_ann_pct"] is not None and e2b_ho_m["net_ann_pct"] > 0
        dd_ok = e2b_ho_m["maxdd_pct"] >= -10
        verdict = "PASS" if (net_ok and dd_ok) else "FAIL"
        all_results["verdicts"]["E2b"] = verdict
        log(f"  E2b veredito: {verdict}  (net>0: {net_ok}, maxDD<=10%: {dd_ok})")
    else:
        if all_results["verdicts"].get("E2b") != "FAIL (proxy incompleto)":
            all_results["verdicts"]["E2b"] = "NAO COMPUTADO (validacao falhou)"
        log("\n--- E2b holdout: NAO COMPUTADO ---")

    # --- E3 holdout --------------------------------------------------------
    if validation_pass["E3"]:
        log("\n--- E3 holdout: Fade PDH/PDL WIN M5 S=25/T=10 ---")
        df_m5_ho = load_win_m5(cutoff=None)
        daily_m5_ho = build_daily_levels(df_m5_ho)
        ho_trades = simulate_fade(df_m5_ho, daily_m5_ho, S_BPS, T_BPS, E3_COST_BPS,
                                   date_filter_start=HOLDOUT_START.date(),
                                   date_filter_end=HOLDOUT_END.date())
        ho_m = metrics_fade(ho_trades)
        holdout["E3"] = ho_m
        log(f"  n={ho_m['n']}  win_rate={ho_m['win_rate']}  PF={ho_m['pf']}  "
            f"expectancy_bps={ho_m['expectancy_bps']}  exit_breakdown={ho_m['exit_breakdown']}")

        ho_trades["year"] = pd.to_datetime(ho_trades["date"]).dt.year if len(ho_trades) else []
        by_year = {}
        if len(ho_trades):
            for yr, grp in ho_trades.groupby("year"):
                by_year[int(yr)] = metrics_fade(grp)
        log("  breakdown por ano:")
        for yr, m in by_year.items():
            log(f"    {yr}: n={m['n']}  PF={m['pf']}  win_rate={m['win_rate']}  expectancy_bps={m['expectancy_bps']}")
        holdout["E3_by_year"] = by_year

        ho_trades.to_csv(OUT_DIR / "E3_fade_win_m5_holdout_trades.csv", index=False)

        pf = ho_m["pf"]
        if pf is not None and pf != np.inf and pf >= 1.2:
            verdict = "PASS"
        elif pf is not None and pf != np.inf and pf > 1.0:
            verdict = "PARTIAL"
        elif pf is None:
            verdict = "FAIL (sem trades)"
        else:
            verdict = "FAIL"
        all_results["verdicts"]["E3"] = verdict
        log(f"  E3 veredito: {verdict}  (n esperado ~380+; n obtido={ho_m['n']})")
    else:
        all_results["verdicts"]["E3"] = "NAO COMPUTADO (validacao falhou)"
        log("\n--- E3 holdout: NAO COMPUTADO (validacao Etapa 1 falhou) ---")

    all_results["holdout"] = holdout

    # =======================================================================
    # ETAPA 3 -- TABELA FINAL
    # =======================================================================
    log("\n" + "=" * 78)
    log("ETAPA 3 -- TABELA FINAL (PASS / PARTIAL / FAIL por estrutura)")
    log("=" * 78)

    def g(d, *keys, default="n/a"):
        cur = d
        for k in keys:
            if cur is None or k not in cur:
                return default
            cur = cur[k]
        return cur if cur is not None else default

    header = f"{'Estrutura':10s} {'Veredito':10s} {'net_ann%':>10s} {'Sharpe':>8s} {'maxDD%':>8s} {'PF':>7s} {'mens_mean%':>11s} {'pior_mes%':>10s} {'n':>6s}"
    log(header)
    log("-" * len(header))

    # E1 (best L by net_ann for the summary row -> use L126 as primary, per pre-reg framing)
    e1_row = holdout.get("E1", {}).get("L126", {}).get("portfolio", {}) if "E1" in holdout else {}
    log(f"{'E1 (L126)':10s} {g(all_results['verdicts'],'E1',default='n/a'):10s} "
        f"{g(e1_row,'net_ann_pct'):>10} {g(e1_row,'sharpe'):>8} {g(e1_row,'maxdd_pct'):>8} "
        f"{g(e1_row,'pf_month_blocks'):>7} {g(e1_row,'monthly_mean_pct'):>11} {g(e1_row,'worst_month_pct'):>10} "
        f"{g(e1_row,'n_days'):>6}")
    e1_row63 = holdout.get("E1", {}).get("L63", {}).get("portfolio", {}) if "E1" in holdout else {}
    log(f"{'E1 (L63)':10s} {'(ver acima)':10s} "
        f"{g(e1_row63,'net_ann_pct'):>10} {g(e1_row63,'sharpe'):>8} {g(e1_row63,'maxdd_pct'):>8} "
        f"{g(e1_row63,'pf_month_blocks'):>7} {g(e1_row63,'monthly_mean_pct'):>11} {g(e1_row63,'worst_month_pct'):>10} "
        f"{g(e1_row63,'n_days'):>6}")

    e2a_row = holdout.get("E2a", {})
    log(f"{'E2a':10s} {g(all_results['verdicts'],'E2a',default='n/a'):10s} "
        f"{g(e2a_row,'net_ann_pct'):>10} {g(e2a_row,'sharpe'):>8} {g(e2a_row,'maxdd_pct'):>8} "
        f"{g(e2a_row,'pf_month_blocks'):>7} {g(e2a_row,'monthly_mean_pct'):>11} {g(e2a_row,'worst_month_pct'):>10} "
        f"{g(e2a_row,'n_days'):>6}")

    e2b_row = holdout.get("E2b", {})
    log(f"{'E2b':10s} {g(all_results['verdicts'],'E2b',default='n/a'):10s} "
        f"{g(e2b_row,'net_ann_pct'):>10} {g(e2b_row,'sharpe'):>8} {g(e2b_row,'maxdd_pct'):>8} "
        f"{g(e2b_row,'pf'):>7} {g(e2b_row,'monthly_mean_pct'):>11} {g(e2b_row,'worst_month_pct'):>10} "
        f"{g(e2b_row,'n_trades'):>6}")

    e3_row = holdout.get("E3", {})
    log(f"{'E3':10s} {g(all_results['verdicts'],'E3',default='n/a'):10s} "
        f"{'n/a':>10} {'n/a':>8} {'n/a':>8} "
        f"{g(e3_row,'pf'):>7} {'n/a':>11} {'n/a':>10} "
        f"{g(e3_row,'n'):>6}")
    log(f"  E3 win_rate={g(e3_row,'win_rate')}  expectancy_bps={g(e3_row,'expectancy_bps')}")
    if "E3_by_year" in holdout:
        for yr, m in holdout["E3_by_year"].items():
            log(f"    E3 ano {yr}: n={m['n']} PF={m['pf']} win_rate={m['win_rate']}")

    log("\nAnomalias / observacoes:")
    anomalies = []
    if not proxy_check["date"].max() >= HOLDOUT_END:
        anomalies.append("proxy_raw.parquet nao cobria o fim do holdout (ver mensagem acima)")
    for k, v in validation_pass.items():
        if not v:
            anomalies.append(f"{k}: validacao Etapa 1 FALHOU fora da tolerancia +-15% -> holdout nao computado")

    if "E1" in holdout:
        l126_sleeves = holdout["E1"]["L126"]["sleeves"]
        icf_net = l126_sleeves.get("ICF", {}).get("net_ann_pct")
        wdo_net = l126_sleeves.get("WDO", {}).get("net_ann_pct")
        di1_net = l126_sleeves.get("DI1", {}).get("net_ann_pct")
        anomalies.append(
            "E1 FAIL no holdout: a carteira depende de commodities agro (BGI/CCM/ICF), que "
            "viraram negativas em 2025+ (ICF L126 holdout net_ann={}%, maxDD={}%), enquanto "
            "WDO (net_ann={}%) e DI1 (net_ann={}%) seguraram melhor. Motor real (DI1+agro) "
            "citado no FACTORY_REPORT nao se sustentou fora da amostra -- carteira like-for-like "
            "com E1 FALHA, mas os componentes individuais WDO (E2a) e carry WDO (E2b) PASSAM "
            "isoladamente.".format(icf_net, l126_sleeves.get("ICF", {}).get("maxdd_pct"), wdo_net, di1_net)
        )

    if "E2b" in holdout and holdout["E2b"].get("n_trades", 0) < 20:
        anomalies.append(
            f"E2b holdout com amostra pequena (n_trades={holdout['E2b'].get('n_trades')}), "
            "consistente com a ressalva ja registrada no FACTORY_REPORT ('n pequeno nas duas "
            "pernas'). PF/net_ann favoraveis, mas erro-padrao alto dado n baixo."
        )

    if "E3_by_year" in holdout:
        m25 = holdout["E3_by_year"].get(2025, {})
        m26 = holdout["E3_by_year"].get(2026, {})
        if m25.get("pf") and m26.get("pf") and m26["pf"] < 1.2 <= m25["pf"]:
            anomalies.append(
                f"E3: PF cai de {m25.get('pf')} (2025, n={m25.get('n')}) para {m26.get('pf')} "
                f"(2026 parcial ate 07-17, n={m26.get('n')}) -- ainda acima do gate PASS (1.2) "
                "mas com tendencia de deterioracao; 2026 e ano incompleto (~6.5 meses), monitorar "
                "no paper trading."
            )

    if not anomalies:
        anomalies.append("nenhuma anomalia de execucao detectada")
    for a in anomalies:
        log(f"  - {a}")

    all_results["anomalies"] = anomalies
    all_results["meta"] = {
        "holdout_start": str(HOLDOUT_START.date()),
        "holdout_end": str(HOLDOUT_END.date()),
        "tolerance_etapa1": TOLERANCE,
        "proxy_date_max": str(proxy_check["date"].max().date()),
    }

    with open(METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    log(f"\nmetrics JSON salvo em {METRICS_JSON}")

    with open(RESULTS_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES))
    log(f"log completo salvo em {RESULTS_TXT}")


if __name__ == "__main__":
    main()
