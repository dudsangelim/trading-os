"""
W3 - Fade de nivel WIN intraday com custo corrigido (1.5 bps RT).
PDH/PDL fade: toque no nivel -> entrada LIMIT no nivel, fade na direcao do range.
Grid: stop S in {15,25} bps, TP T in {10,20} bps. Time-stop 60min. Max 2 trades/dia.
Sem trades apos 17:30. Custos sensibilidade 1.0/1.5/2.0 bps RT.

Auto-suficiente: paths absolutos, so pandas/numpy/pyarrow.
"""

import pandas as pd
import numpy as np
import json
import os

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_factory_v0\W3_level_fade"
os.makedirs(OUT_DIR, exist_ok=True)

PATHS = {
    "M5": r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history\WIN_cont_N_M5.parquet",
    "M15": r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history\WIN_cont_N_M15.parquet",
}

# discovery / confirm splits per timeframe (per worker spec)
SPLITS = {
    "M5": {
        "discovery": ("2023-01-01", "2023-12-31"),
        "confirm": ("2024-01-01", "2024-12-31"),
    },
    "M15": {
        "discovery": ("2021-07-01", "2023-12-31"),
        "confirm": ("2024-01-01", "2024-12-31"),
    },
}

COST_LEVELS_BPS = [1.0, 1.5, 2.0]
PRIMARY_COST_BPS = 1.5

STOP_GRID = [15, 25]      # bps beyond level
TP_GRID = [10, 20]        # bps favorable
TIME_STOP_MIN = 60
MAX_TRADES_DAY = 2
SESSION_OPEN = pd.Timedelta(hours=9, minutes=0)
NO_NEW_TRADES_AFTER = pd.Timedelta(hours=17, minutes=30)


def load_data(tf):
    df = pd.read_parquet(PATHS[tf])
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    n_before = len(df)
    df = df[df["datetime_b3"] < "2025-01-01"].reset_index(drop=True)
    n_removed = n_before - len(df)
    print(f"[{tf}] linhas removidas por corte 2025-01-01+: {n_removed} (de {n_before} -> {len(df)})")
    df["date"] = df["datetime_b3"].dt.date
    df["tod"] = df["datetime_b3"] - pd.to_datetime(df["date"])
    return df


def build_daily_levels(df):
    """Daily high/low computed from all bars of the day (session data), then
    previous *trading* day's H/L attached to each day via shift on the sorted
    unique-date index."""
    daily = df.groupby("date").agg(day_high=("high", "max"), day_low=("low", "min"),
                                     first_tod=("tod", "min")).reset_index()
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["pdh"] = daily["day_high"].shift(1)
    daily["pdl"] = daily["day_low"].shift(1)
    # discard days that do not open at 09:00 (per project convention)
    daily["valid_open"] = daily["first_tod"] == SESSION_OPEN
    return daily


def simulate_cell(df, daily, S_bps, T_bps, cost_bps_list):
    """Simulate one (S,T) cell. Returns per-trade records (gross ret in bps,
    net ret in bps for each cost level, exit_reason, direction, date)."""
    S = S_bps / 10000.0
    T = T_bps / 10000.0
    bar_dt = None
    # infer bar timedelta from data (mode of diffs within same day)
    diffs = df["datetime_b3"].diff().dropna()
    bar_dt = diffs[diffs > pd.Timedelta(0)].mode().iloc[0]
    n_bars_timestop = int(np.ceil(pd.Timedelta(minutes=TIME_STOP_MIN) / bar_dt))

    daily_map = daily.set_index("date")
    trades = []

    grouped = df.groupby("date")
    dates_sorted = sorted(grouped.groups.keys())

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
                    exit_idx = None

                    last_idx = min(entry_idx + n_bars_timestop, n_bars - 1)
                    for j in range(entry_idx, last_idx + 1):
                        bj = day_df.iloc[j]
                        hit_stop = (bj["low"] <= stop_price <= bj["high"])
                        hit_tp = (bj["low"] <= tp_price <= bj["high"])
                        if hit_stop and hit_tp:
                            # ambiguous same-bar order -> conservative: stop first
                            exit_reason, exit_price, exit_idx = "stop", stop_price, j
                            break
                        elif hit_stop:
                            exit_reason, exit_price, exit_idx = "stop", stop_price, j
                            break
                        elif hit_tp:
                            exit_reason, exit_price, exit_idx = "tp", tp_price, j
                            break
                        if j == last_idx:
                            exit_reason, exit_price, exit_idx = "time", bj["close"], j

                    if direction == "short":
                        gross_ret = (entry_price - exit_price) / entry_price
                    else:
                        gross_ret = (exit_price - entry_price) / entry_price

                    gross_bps = gross_ret * 10000.0
                    rec = {
                        "date": d,
                        "level": level_name,
                        "direction": direction,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "exit_reason": exit_reason,
                        "gross_bps": gross_bps,
                    }
                    for c in cost_bps_list:
                        rec[f"net_bps_{c}"] = gross_bps - c
                    trades.append(rec)
                    break  # only one level per bar iteration (avoid double count same bar)

    return pd.DataFrame(trades), n_bars_timestop


def metrics_for(trades_df, cost_bps, months_in_period):
    if len(trades_df) == 0:
        return dict(n=0, win_rate=None, pf=None, expectancy_bps=None,
                    exit_breakdown={})
    col = f"net_bps_{cost_bps}"
    net = trades_df[col]
    wins = net[net > 0]
    losses = net[net <= 0]
    n = len(net)
    win_rate = len(wins) / n if n > 0 else None
    gain_sum = wins.sum()
    loss_sum = -losses.sum()
    pf = (gain_sum / loss_sum) if loss_sum > 0 else (np.inf if gain_sum > 0 else None)
    expectancy_bps = net.mean()
    breakdown = trades_df["exit_reason"].value_counts().to_dict()
    return dict(n=n, win_rate=win_rate, pf=pf, expectancy_bps=expectancy_bps,
                exit_breakdown=breakdown)


def main():
    all_results = []
    trades_by_key = {}

    for tf in ["M5", "M15"]:
        df = load_data(tf)
        daily = build_daily_levels(df)

        for split_name, (start, end) in SPLITS[tf].items():
            mask = (df["datetime_b3"] >= start) & (df["datetime_b3"] <= end)
            sub_df = df[mask].reset_index(drop=True)
            n_days = sub_df["date"].nunique()
            print(f"\n=== {tf} / {split_name} ({start}..{end}) -- {len(sub_df)} bars, {n_days} dias ===")

            for S in STOP_GRID:
                for T in TP_GRID:
                    trades_df, n_bars_ts = simulate_cell(sub_df, daily, S, T, COST_LEVELS_BPS)
                    cell_label = f"{tf}_{split_name}_S{S}_T{T}"

                    row_out = {"timeframe": tf, "split": split_name, "S_bps": S, "T_bps": T}
                    for c in COST_LEVELS_BPS:
                        m = metrics_for(trades_df, c, None)
                        row_out[f"n_{c}"] = m["n"]
                        row_out[f"win_rate_{c}"] = m["win_rate"]
                        row_out[f"pf_{c}"] = m["pf"]
                        row_out[f"expectancy_bps_{c}"] = m["expectancy_bps"]
                        if c == PRIMARY_COST_BPS:
                            row_out["exit_breakdown"] = m["exit_breakdown"]

                    all_results.append(row_out)

                    m15 = metrics_for(trades_df, PRIMARY_COST_BPS, None)
                    print(f"  S={S} T={T}: n={m15['n']} win_rate={m15['win_rate']} "
                          f"PF(1.5bps)={m15['pf']} exp_bps={m15['expectancy_bps']} "
                          f"breakdown={m15['exit_breakdown']}")

                    trades_by_key[(tf, split_name, S, T)] = trades_df.copy()

    results_df = pd.DataFrame(all_results)
    results_path = os.path.join(OUT_DIR, "grid_results.csv")
    # flatten exit_breakdown to string for csv
    results_df_out = results_df.copy()
    results_df_out["exit_breakdown"] = results_df_out["exit_breakdown"].apply(lambda d: json.dumps(d))
    results_df_out.to_csv(results_path, index=False)
    print(f"\nGrid completo salvo em {results_path}")

    # check discovery+confirm pass together per (tf,S,T)
    print("\n=== Checagem gate PF>=1.2 (1.5bps) discovery E confirm, n>=100 ===")
    pass_cells = []
    for tf in ["M5", "M15"]:
        for S in STOP_GRID:
            for T in TP_GRID:
                disc = results_df[(results_df.timeframe == tf) & (results_df.split == "discovery") &
                                   (results_df.S_bps == S) & (results_df.T_bps == T)]
                conf = results_df[(results_df.timeframe == tf) & (results_df.split == "confirm") &
                                   (results_df.S_bps == S) & (results_df.T_bps == T)]
                if disc.empty or conf.empty:
                    continue
                d = disc.iloc[0]
                c = conf.iloc[0]
                ok = (d[f"n_{PRIMARY_COST_BPS}"] >= 100 and d[f"pf_{PRIMARY_COST_BPS}"] is not None
                      and d[f"pf_{PRIMARY_COST_BPS}"] >= 1.2 and
                      c[f"n_{PRIMARY_COST_BPS}"] >= 100 and c[f"pf_{PRIMARY_COST_BPS}"] is not None
                      and c[f"pf_{PRIMARY_COST_BPS}"] >= 1.2)
                print(f"{tf} S={S} T={T}: discovery PF={d[f'pf_{PRIMARY_COST_BPS}']:.3f} n={d[f'n_{PRIMARY_COST_BPS}']} | "
                      f"confirm PF={c[f'pf_{PRIMARY_COST_BPS}']:.3f} n={c[f'n_{PRIMARY_COST_BPS}']} -> "
                      f"{'PASS' if ok else 'fail'}")
                if ok:
                    pass_cells.append((tf, S, T))

    if pass_cells:
        print(f"\nCelulas PASS: {pass_cells}")
    else:
        print("\nNenhuma celula passou o gate PF>=1.2 (1.5bps) em discovery E confirm com n>=100.")

    for tf, S, T in pass_cells:
        for split_name in SPLITS[tf].keys():
            tdf = trades_by_key[(tf, split_name, S, T)]
            p = os.path.join(OUT_DIR, f"trades_{tf}_{split_name}_S{S}_T{T}.csv")
            tdf.to_csv(p, index=False)
            print(f"Serie de trades salva (PASS cell): {p}")

    print("\n=== FIM ===")


if __name__ == "__main__":
    main()
