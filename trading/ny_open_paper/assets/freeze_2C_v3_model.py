"""
Freeze NY Open 2C v3 after the candle-replay breakeven fix.

v3 changes vs v2:
  - Labels use the corrected rule: after TP1, stop moves to entry in candle replay.
  - Threshold is raised to 0.60.
  - Paper leverage target is reduced to 2x.

Default input is the local BTCUSDT 1m parquet used for research on this host.
Override with --parquet if needed.
"""
from __future__ import annotations

import argparse
import json
from datetime import time as dtime, timezone, datetime
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_PARQUET = Path("/home/agent/data/fimathe/BTCUSDT_1m_2021_2025.parquet")
OUT_DIR = Path(__file__).parent

WINDOW_MIN = 30
BREAK_BUFFER = 0.0005
MAX_WAIT_RETEST_MIN = 15
SESSION_OPEN = dtime(13, 30)
SESSION_CLOSE = dtime(20, 0)

STOP_KIND = "alpha"
STOP_ALPHA = 0.4
STOP_MIN_PCT = 0.003
STOP_MAX_PCT = 0.008

FEE_MAKER = 0.0002
FEE_TAKER = 0.0005

LR = 0.05
ITERS = 5000
L2 = 0.01

RECOMMENDED_THRESHOLD = 0.60
LEVERAGE_TARGET = 2

FEATURE_NAMES = [
    "range_first_30m_pct",
    "overshoot_close_pct",
    "vol_20d_pct",
    "btc_vs_sma200_pct",
    "direction_first_30m",
    "time_to_break_min",
]


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


def fit_logistic(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    n, p = X.shape
    X_ = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(p + 1)
    for _ in range(ITERS):
        p_hat = sigmoid(X_ @ w)
        grad = X_.T @ (p_hat - y) / n
        grad[1:] += L2 * w[1:]
        w -= LR * grad
    return w


def t_after(base: dtime, mins: int) -> dtime:
    total = base.hour * 60 + base.minute + mins
    return dtime(total // 60, total % 60)


def stop_distance_pct(range_first_pct: float) -> float:
    raw = STOP_ALPHA * (range_first_pct / 100.0)
    return max(STOP_MIN_PCT, min(STOP_MAX_PCT, raw))


def load_1m(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(
        path,
        columns=["datetime", "open", "high", "low", "close", "volume_usdt"],
    )
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    return df[~df.index.duplicated(keep="last")]


def resample_5m(df1m: pd.DataFrame) -> pd.DataFrame:
    return (
        df1m.resample("5min", label="left", closed="left")
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume_usdt": "sum",
        })
        .dropna()
    )


def precompute_context(df1m: pd.DataFrame) -> dict:
    df1h = df1m["close"].resample("1h").last().dropna()
    ret1h = df1h.pct_change().dropna()
    vol_20d = ret1h.rolling(20 * 24).std()

    daily = df1m["close"].resample("1D").last().dropna()
    sma200 = daily.rolling(200).mean()

    ctx = {}
    for date in sorted(set(df1m.index.date)):
        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            continue
        day_start = pd.Timestamp(date)
        session_open_ts = day_start + pd.Timedelta(hours=13, minutes=30)

        v20 = vol_20d.asof(session_open_ts - pd.Timedelta(minutes=1))
        vol_20d_pct = 0.0 if pd.isna(v20) else float(v20 * 100)

        c_prev = daily.asof(day_start - pd.Timedelta(minutes=1))
        sma_prev = sma200.asof(day_start - pd.Timedelta(minutes=1))
        if pd.isna(c_prev) or pd.isna(sma_prev) or sma_prev <= 0:
            btc_vs_sma200_pct = 0.0
        else:
            btc_vs_sma200_pct = float((c_prev - sma_prev) / sma_prev * 100)

        ctx[date] = {
            "vol_20d_pct": vol_20d_pct,
            "btc_vs_sma200_pct": btc_vs_sma200_pct,
        }
    return ctx


def iter_sessions(df5m: pd.DataFrame):
    s = df5m[
        (df5m.index.time >= SESSION_OPEN)
        & (df5m.index.time < SESSION_CLOSE)
    ].copy()
    s["date"] = s.index.date
    for date, g in s.groupby("date"):
        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            continue
        if len(g) < 60:
            continue
        yield date, g


def collect_trades(df5m: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    t_end_first = t_after(SESSION_OPEN, WINDOW_MIN)
    max_wait_bars = MAX_WAIT_RETEST_MIN // 5
    rows = []

    for date, g in iter_sessions(df5m):
        first = g[g.index.time < t_end_first]
        rest = g[g.index.time >= t_end_first]
        if len(first) < 3 or len(rest) < 10:
            continue

        sess_open = float(first["open"].iloc[0])
        high_first = float(first["high"].max())
        low_first = float(first["low"].min())
        if high_first - low_first <= 0:
            continue

        mid = (high_first + low_first) / 2
        range_first_pct = (high_first - low_first) / sess_open * 100
        up_move = (high_first - sess_open) / sess_open
        dn_move = (sess_open - low_first) / sess_open

        if up_move >= dn_move:
            extreme = high_first
            opposite = low_first
            break_level = extreme * (1 + BREAK_BUFFER)
            direction = -1
            direction_first = 1.0
        else:
            extreme = low_first
            opposite = high_first
            break_level = extreme * (1 - BREAK_BUFFER)
            direction = 1
            direction_first = -1.0

        highs = rest["high"].values
        lows = rest["low"].values
        closes = rest["close"].values
        times = rest.index

        break_pos = None
        for i in range(len(rest)):
            if direction == -1 and highs[i] >= break_level:
                break_pos = i
                break
            if direction == 1 and lows[i] <= break_level:
                break_pos = i
                break
        if break_pos is None:
            continue

        if direction == -1:
            overshoot_close_pct = (closes[break_pos] - extreme) / extreme * 100
        else:
            overshoot_close_pct = (extreme - closes[break_pos]) / extreme * 100
        time_to_break_min = (times[break_pos] - times[0]).total_seconds() / 60

        retest_pos = None
        end_search = min(break_pos + max_wait_bars + 1, len(rest))
        for i in range(break_pos, end_search):
            if direction == -1 and lows[i] <= extreme:
                retest_pos = i
                break
            if direction == 1 and highs[i] >= extreme:
                retest_pos = i
                break
        if retest_pos is None:
            continue

        entry_price = extreme
        stop_pct = stop_distance_pct(range_first_pct)
        original_stop = (
            entry_price * (1 + stop_pct)
            if direction == -1
            else entry_price * (1 - stop_pct)
        )

        after = rest.iloc[retest_pos:]
        hit_tp1 = False
        partial_fill = None
        exit_price = None
        exit_reason = None

        for _, row in after.iterrows():
            h = float(row["high"])
            l = float(row["low"])
            active_stop = entry_price if hit_tp1 else original_stop

            stop_hit = (
                (direction == -1 and h >= active_stop)
                or (direction == 1 and l <= active_stop)
            )
            if stop_hit:
                if hit_tp1:
                    exit_price = (partial_fill + entry_price) / 2
                    exit_reason = "trail_stop_be"
                else:
                    exit_price = original_stop
                    exit_reason = "stop"
                break

            if not hit_tp1:
                if direction == -1 and l <= mid:
                    hit_tp1 = True
                    partial_fill = mid
                elif direction == 1 and h >= mid:
                    hit_tp1 = True
                    partial_fill = mid
            else:
                if direction == -1 and l <= opposite:
                    exit_price = (partial_fill + opposite) / 2
                    exit_reason = "tp2"
                    break
                if direction == 1 and h >= opposite:
                    exit_price = (partial_fill + opposite) / 2
                    exit_reason = "tp2"
                    break

        if exit_price is None:
            if hit_tp1:
                exit_price = (partial_fill + float(after["close"].iloc[-1])) / 2
                exit_reason = "time_partial"
            else:
                exit_price = float(after["close"].iloc[-1])
                exit_reason = "time"

        pnl_gross = direction * (exit_price - entry_price) / entry_price * 100
        maker_pct = FEE_MAKER * 100
        taker_pct = FEE_TAKER * 100
        if exit_reason == "tp2":
            exit_fee = maker_pct
        elif exit_reason in ("trail_stop_be", "time_partial"):
            exit_fee = 0.5 * maker_pct + 0.5 * taker_pct
        else:
            exit_fee = taker_pct
        pnl_net = pnl_gross - (maker_pct + exit_fee)

        cfeat = ctx.get(date)
        if cfeat is None:
            continue

        rows.append({
            "date": date,
            "direction": direction,
            "pnl_pct_net": pnl_net,
            "y_win": int(pnl_net > 0),
            "stop_pct_used": stop_pct,
            "range_first_30m_pct": range_first_pct,
            "overshoot_close_pct": overshoot_close_pct,
            "vol_20d_pct": cfeat["vol_20d_pct"],
            "btc_vs_sma200_pct": cfeat["btc_vs_sma200_pct"],
            "direction_first_30m": direction_first,
            "time_to_break_min": time_to_break_min,
            "exit_reason": exit_reason,
        })

    out = pd.DataFrame(rows)
    if len(out):
        out["date"] = pd.to_datetime(out["date"])
        out = out.sort_values("date").reset_index(drop=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    if not args.parquet.exists():
        print(f"Missing parquet: {args.parquet}")
        return 1

    df1m = load_1m(args.parquet)
    df5m = resample_5m(df1m)
    ctx = precompute_context(df1m)
    trades = collect_trades(df5m, ctx)

    X = trades[FEATURE_NAMES].values.astype(float)
    y = trades["y_win"].values.astype(float)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std_safe = np.where(std == 0, 1.0, std)
    X_z = (X - mean) / std_safe
    w = fit_logistic(X_z, y)
    p_hat = sigmoid(np.hstack([np.ones((len(trades), 1)), X_z]) @ w)

    trades = trades.copy()
    trades["p_win"] = p_hat

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_path = out_dir / "trades_v3_with_pwin.csv"
    model_path = out_dir / "2C_v3_frozen.json"
    trades.to_csv(trades_path, index=False)

    selected = trades[trades["p_win"] >= RECOMMENDED_THRESHOLD]
    model = {
        "model_version": "2C_v3",
        "model_type": "logistic_regression_l2",
        "train_date_utc": datetime.now(timezone.utc).isoformat(),
        "train_data_range": {
            "start": str(trades["date"].min().date()),
            "end": str(trades["date"].max().date()),
            "n_trades_unfiltered": int(len(trades)),
            "win_rate_unfiltered": float(trades["y_win"].mean()),
        },
        "hyperparams": {"lr": LR, "iters": ITERS, "l2": L2},
        "fee_schedule": {
            "name": "binance_vip0",
            "maker": FEE_MAKER,
            "taker": FEE_TAKER,
        },
        "strategy_params": {
            "window_min": WINDOW_MIN,
            "break_buffer": BREAK_BUFFER,
            "stop_kind": STOP_KIND,
            "stop_alpha": STOP_ALPHA,
            "stop_min_pct": STOP_MIN_PCT,
            "stop_max_pct": STOP_MAX_PCT,
            "max_wait_retest_min": MAX_WAIT_RETEST_MIN,
            "session_open_utc": f"{SESSION_OPEN.hour:02d}:{SESSION_OPEN.minute:02d}",
            "session_close_utc": f"{SESSION_CLOSE.hour:02d}:{SESSION_CLOSE.minute:02d}",
        },
        "feature_names": FEATURE_NAMES,
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "weights": {
            "bias": float(w[0]),
            "coef": [float(x) for x in w[1:]],
        },
        "recommended_threshold": RECOMMENDED_THRESHOLD,
        "estimated_pct_filtered_in": float(len(selected) / len(trades)),
        "execution_notes": {
            "leverage_target": LEVERAGE_TARGET,
            "rationale": (
                "v3 retrained after candle-replay breakeven fix. Threshold 0.60 "
                "keeps walk-forward PF above 1.7 in the local audit while reducing "
                "trade count and drawdown versus 0.55. Leverage target reduced to "
                "2x because edge is execution-sensitive."
            ),
            "max_dd_alert_pct": 10,
            "max_dd_kill_pct": 18,
            "paper_only": True,
        },
    }
    with open(model_path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, ensure_ascii=False)

    print(f"trades={len(trades)} selected={len(selected)} threshold={RECOMMENDED_THRESHOLD}")
    print(f"wrote {model_path}")
    print(f"wrote {trades_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
