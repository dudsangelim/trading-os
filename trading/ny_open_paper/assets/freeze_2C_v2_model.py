"""
FASE 3 v2 — Congela modelo 2C revisado pra producao.

Mudancas vs v1:
  - Stop dinamico S3: alpha=0.4 x range_first_30m, clamp [0.3%, 0.8%]
    (em vez de stop fixo 0.5%)
  - Features F_reduced (6) em vez de full (11)
  - Threshold 0.55 (em vez de 0.60)

Output: models/2C_v2_frozen.json + models/trades_v2_with_pwin.csv

Em produção, engine_2C.py carrega esse JSON e usa direto. O engine deve
suportar 'stop_kind' = 'alpha' nos strategy_params.

Uso:
    python scripts\\phase3\\freeze_2C_v2_model.py
"""
from pathlib import Path
from datetime import time as dtime, datetime, timezone
import json
import numpy as np
import pandas as pd

PARQUET_PATH = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\BTCUSDT_1m_2021_2026.parquet")
FUNDING_PATH = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\BTCUSDT_funding_2021_2026.parquet")
OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\models")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_JSON = OUT_DIR / "2C_v2_frozen.json"
ORACLE_CSV = OUT_DIR / "trades_v2_with_pwin.csv"

# mecanica
WINDOW_MIN = 30
BREAK_BUFFER = 0.0005
MAX_WAIT_RETEST_MIN = 15
SESSION_OPEN = dtime(13, 30)
SESSION_CLOSE = dtime(20, 0)

# stop dinamico (S3)
STOP_KIND = "alpha"
STOP_ALPHA = 0.4
STOP_MIN_PCT = 0.003
STOP_MAX_PCT = 0.008

# fees
FEE_MAKER = 0.0002
FEE_TAKER = 0.0005

# logistic
LR = 0.05
ITERS = 5000
L2 = 0.01

RECOMMENDED_THRESHOLD = 0.55

FEATURE_NAMES = [
    "range_first_30m_pct", "overshoot_close_pct",
    "vol_20d_pct", "btc_vs_sma200_pct",
    "direction_first_30m", "time_to_break_min",
]


def load_1m():
    df = pd.read_parquet(PARQUET_PATH, columns=["datetime", "open", "high", "low", "close", "volume_usdt"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.set_index("datetime").sort_index()


def resample_5m(df1m):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume_usdt": "sum"}
    return df1m.resample("5min", label="left", closed="left").agg(agg).dropna()


def load_funding():
    if not FUNDING_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(FUNDING_PATH)
    if "datetime" not in df.columns:
        for alt in ["fundingTime", "time", "timestamp"]:
            if alt in df.columns:
                df = df.rename(columns={alt: "datetime"}); break
    if "funding_rate" not in df.columns:
        for alt in ["fundingRate", "rate"]:
            if alt in df.columns:
                df = df.rename(columns={alt: "funding_rate"}); break
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df[["datetime", "funding_rate"]].sort_values("datetime").reset_index(drop=True)


def t_after(base, mins):
    total = base.hour * 60 + base.minute + mins
    return dtime(total // 60, total % 60)


def iter_sessions(df5m):
    s = df5m[(df5m.index.time >= SESSION_OPEN) & (df5m.index.time < SESSION_CLOSE)].copy()
    s["date"] = s.index.date
    for date, g in s.groupby("date"):
        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            continue
        if len(g) < 60:
            continue
        yield date, g


def precompute_context(df1m, funding_df):
    print("[ctx] pre-computando features diarias...")
    df1h = df1m["close"].resample("1h").last().dropna()
    ret1h = df1h.pct_change().dropna()
    vol_60d = ret1h.rolling(60 * 24).std()
    vol_20d = ret1h.rolling(20 * 24).std()
    daily = df1m["close"].resample("1D").last().dropna()
    sma200 = daily.rolling(200).mean()
    ctx = {}
    all_dates = sorted(set(df1m.index.date))
    for date in all_dates:
        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            continue
        day_start = pd.Timestamp(date)
        asian = df1m[(df1m.index >= day_start) & (df1m.index < day_start + pd.Timedelta(hours=8))]
        asian_range_pct = (asian["high"].max() - asian["low"].min()) / asian["close"].iloc[0] * 100 if len(asian) > 10 else np.nan
        leadin_start = day_start + pd.Timedelta(hours=12)
        leadin_end = day_start + pd.Timedelta(hours=13, minutes=30)
        leadin = df1m[(df1m.index >= leadin_start) & (df1m.index < leadin_end)]
        if len(leadin) > 10:
            c0 = leadin["close"].iloc[0]; c1 = leadin["close"].iloc[-1]
            leadin_ret = (c1 - c0) / c0 * 100 if c0 > 0 else 0
        else:
            leadin_ret = np.nan
        ts = day_start + pd.Timedelta(hours=13, minutes=30)
        try:
            v60 = vol_60d.asof(ts - pd.Timedelta(minutes=1)) * 100
            v20 = vol_20d.asof(ts - pd.Timedelta(minutes=1)) * 100
        except Exception:
            v60, v20 = np.nan, np.nan
        try:
            ts_prev = day_start - pd.Timedelta(minutes=1)
            c_prev = daily.asof(ts_prev)
            sma_prev = sma200.asof(ts_prev)
            btc_sma_pct = (c_prev - sma_prev) / sma_prev * 100 if sma_prev and sma_prev > 0 else np.nan
        except Exception:
            btc_sma_pct = np.nan
        funding_last = 0.0
        if len(funding_df) > 0:
            cutoff = day_start + pd.Timedelta(hours=13, minutes=30)
            fmask = funding_df["datetime"] < cutoff
            if fmask.any():
                funding_last = funding_df.loc[fmask, "funding_rate"].iloc[-1] * 100
        dow = pd.Timestamp(date).dayofweek
        ctx[date] = {
            "vol_60d_pct": v60 if not pd.isna(v60) else 0.0,
            "vol_20d_pct": v20 if not pd.isna(v20) else 0.0,
            "asian_range_pct": asian_range_pct if not pd.isna(asian_range_pct) else 0.0,
            "london_leadin_return": leadin_ret if not pd.isna(leadin_ret) else 0.0,
            "btc_vs_sma200_pct": btc_sma_pct if not pd.isna(btc_sma_pct) else 0.0,
            "dow": float(dow),
            "funding_last_pct": funding_last,
        }
    print(f"[ctx] {len(ctx):,} dias processados")
    return ctx


def stop_distance_pct(range_first_pct: float) -> float:
    raw = STOP_ALPHA * (range_first_pct / 100.0)
    return max(STOP_MIN_PCT, min(STOP_MAX_PCT, raw))


def collect_trades(df5m, ctx):
    """Coleta trades fade com stop dinamico S3 (single-trade-per-day, igual v1)."""
    t_end_first = t_after(SESSION_OPEN, WINDOW_MIN)
    rows = []
    for date, g in iter_sessions(df5m):
        first = g[g.index.time < t_end_first]
        rest = g[g.index.time >= t_end_first]
        if len(first) < 3 or len(rest) < 10:
            continue
        sess_open = first["open"].iloc[0]
        high_first = first["high"].max(); low_first = first["low"].min()
        if high_first - low_first <= 0:
            continue
        mid = (high_first + low_first) / 2
        range_first_pct = (high_first - low_first) / sess_open * 100
        up_move = (high_first - sess_open) / sess_open
        dn_move = (sess_open - low_first) / sess_open
        if up_move >= dn_move:
            extreme = high_first; opposite = low_first
            break_level = extreme * (1 + BREAK_BUFFER)
            direction = -1; direction_first = 1.0
        else:
            extreme = low_first; opposite = high_first
            break_level = extreme * (1 - BREAK_BUFFER)
            direction = 1; direction_first = -1.0
        highs = rest["high"].values; lows = rest["low"].values
        closes = rest["close"].values; times = rest.index
        break_pos = None
        for i in range(len(rest)):
            if direction == -1 and highs[i] >= break_level: break_pos = i; break
            if direction == 1 and lows[i] <= break_level: break_pos = i; break
        if break_pos is None: continue
        if direction == -1:
            os_close = (closes[break_pos] - extreme) / extreme * 100
        else:
            os_close = (extreme - closes[break_pos]) / extreme * 100
        time_to_break_min = (times[break_pos] - times[0]).total_seconds() / 60
        retest_pos = None
        max_wait_bars = MAX_WAIT_RETEST_MIN // 5
        end_search = min(break_pos + max_wait_bars + 1, len(rest))
        for i in range(break_pos, end_search):
            if direction == -1 and lows[i] <= extreme: retest_pos = i; break
            if direction == 1 and highs[i] >= extreme: retest_pos = i; break
        if retest_pos is None: continue
        entry_price = extreme
        stop_pct = stop_distance_pct(range_first_pct)
        stop_price = entry_price * (1 + stop_pct) if direction == -1 else entry_price * (1 - stop_pct)
        after = rest.iloc[retest_pos:]
        exit_price = None; exit_reason = None
        hit_tp1 = False; partial_fill = None
        for _, row in after.iterrows():
            if direction == -1 and row["high"] >= stop_price:
                if hit_tp1:
                    exit_price = (partial_fill + entry_price) / 2; exit_reason = "trail_stop_be"
                else:
                    exit_price = stop_price; exit_reason = "stop"
                break
            if direction == 1 and row["low"] <= stop_price:
                if hit_tp1:
                    exit_price = (partial_fill + entry_price) / 2; exit_reason = "trail_stop_be"
                else:
                    exit_price = stop_price; exit_reason = "stop"
                break
            if not hit_tp1:
                if direction == -1 and row["low"] <= mid:
                    hit_tp1 = True; partial_fill = mid
                elif direction == 1 and row["high"] >= mid:
                    hit_tp1 = True; partial_fill = mid
            else:
                if direction == -1 and row["low"] <= opposite:
                    exit_price = (partial_fill + opposite) / 2; exit_reason = "tp2"; break
                if direction == 1 and row["high"] >= opposite:
                    exit_price = (partial_fill + opposite) / 2; exit_reason = "tp2"; break
        if exit_price is None:
            if hit_tp1:
                last_close = after["close"].iloc[-1]
                exit_price = (partial_fill + last_close) / 2
                exit_reason = "time_partial"
            else:
                exit_price = after["close"].iloc[-1]; exit_reason = "time"
        pnl_gross = direction * (exit_price - entry_price) / entry_price * 100
        m = FEE_MAKER * 100; t = FEE_TAKER * 100
        if exit_reason in ("tp", "tp2"): exit_fee = m
        elif exit_reason in ("trail_stop_be", "time_partial"): exit_fee = 0.5 * m + 0.5 * t
        else: exit_fee = t
        fee_total = m + exit_fee
        pnl_net = pnl_gross - fee_total
        cfeat = ctx.get(date)
        if cfeat is None: continue
        rows.append({
            "date": date, "direction": direction,
            "pnl_pct_net": pnl_net,
            "y_win": int(pnl_net > 0),
            "stop_pct_used": stop_pct,
            "range_first_30m_pct": range_first_pct,
            "overshoot_close_pct": os_close,
            "vol_20d_pct": cfeat["vol_20d_pct"],
            "btc_vs_sma200_pct": cfeat["btc_vs_sma200_pct"],
            "direction_first_30m": direction_first,
            "time_to_break_min": time_to_break_min,
        })
    out = pd.DataFrame(rows)
    if len(out):
        out["date"] = pd.to_datetime(out["date"])
        out = out.sort_values("date").reset_index(drop=True)
    return out


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -500, 500)))


def fit_logistic(X, y, lr=LR, iters=ITERS, l2=L2):
    n, p = X.shape
    X_ = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(p + 1)
    for _ in range(iters):
        z = X_ @ w
        p_hat = sigmoid(z)
        grad = X_.T @ (p_hat - y) / n
        grad[1:] += l2 * w[1:]
        w -= lr * grad
    return w


def main():
    if not PARQUET_PATH.exists():
        print(f"[ERRO] {PARQUET_PATH} nao existe."); return 1

    print("[1/4] Carregando dados...")
    df1m = load_1m()
    df5m = resample_5m(df1m)
    funding = load_funding()
    print(f"  1m={len(df1m):,}  5m={len(df5m):,}  funding={len(funding):,}")

    print("\n[2/4] Contexto diario...")
    ctx = precompute_context(df1m, funding)

    print("\n[3/4] Coletando trades S3 (stop dinamico) + features F_reduced...")
    trades = collect_trades(df5m, ctx)
    n = len(trades)
    print(f"  n_trades = {n:,}")
    print(f"  periodo = {trades['date'].min().date()} -> {trades['date'].max().date()}")
    print(f"  win_rate (unfiltered) = {trades['y_win'].mean():.3f}")
    print(f"  stop_pct mean = {trades['stop_pct_used'].mean()*100:.3f}%  "
          f"min={trades['stop_pct_used'].min()*100:.3f}%  "
          f"max={trades['stop_pct_used'].max()*100:.3f}%")

    print("\n[4/4] Treinando logistic em 100% dos dados + exportando modelo v2...")
    X = trades[FEATURE_NAMES].values.astype(float)
    y = trades["y_win"].values.astype(float)

    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std_safe = std.copy()
    std_safe[std_safe == 0] = 1.0
    X_z = (X - mean) / std_safe

    w = fit_logistic(X_z, y, lr=LR, iters=ITERS, l2=L2)

    X_pred = np.hstack([np.ones((n, 1)), X_z])
    p_hat = sigmoid(X_pred @ w)
    pct_above_thr = (p_hat >= RECOMMENDED_THRESHOLD).mean()
    print(f"  Pct trades com p_win >= {RECOMMENDED_THRESHOLD}: {pct_above_thr:.1%}")

    oracle = trades.copy()
    oracle["p_win"] = p_hat
    oracle.to_csv(ORACLE_CSV, index=False)
    print(f"  Oracle (trades + p_win): {ORACLE_CSV}")

    print(f"  Coeficientes (intercepto + {len(FEATURE_NAMES)} features):")
    print(f"    bias = {w[0]:+.4f}")
    for i, fn in enumerate(FEATURE_NAMES):
        print(f"    {fn:30s} = {w[i+1]:+.4f}")

    model = {
        "model_version": "2C_v2",
        "model_type": "logistic_regression_l2",
        "train_date_utc": datetime.now(timezone.utc).isoformat(),
        "train_data_range": {
            "start": str(trades["date"].min().date()),
            "end": str(trades["date"].max().date()),
            "n_trades_unfiltered": int(n),
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
        "estimated_pct_filtered_in": float(pct_above_thr),
        "execution_notes": {
            "leverage_target": 5,
            "rationale": "Lev 5x sobre S3+F_reduced thr=0.55 dá ~+44%/ano com DD ~-11% e P(DD>50)<0.1% no MC 5y. Slippage NAO modelada — em 5x cada 0.05% de slip vira 0.25% extra; monitorar fill quality.",
            "max_dd_alert_pct": 15,
            "max_dd_kill_pct": 25,
        },
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, ensure_ascii=False)
    print(f"\n  Modelo salvo: {OUT_JSON}")
    print("=" * 80)
    print("Pronto. engine_2C.py (v2-aware) carrega este JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
