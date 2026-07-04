"""Causal swing-trade backtest engine + performance metrics.

Conventions
-----------
* Signals are evaluated at the CLOSE of bar i and executed at the OPEN of bar
  i+1 (no look-ahead). The engine handles the shift internally.
* Costs: `fee_bps` + `slip_bps` applied per side (entry and exit).
* Optional ATR stop-loss / take-profit / trailing stop are checked intrabar
  against bar high/low. If both stop and target sit inside one bar, the stop is
  assumed to trigger first (conservative).
* One unit of equity is risked per trade (full-capital, no leverage). Returns
  compound. Leverage simply scales per-trade return linearly and is reported
  separately, never baked into the edge.
"""
from dataclasses import dataclass, field
import numpy as np
import pandas as pd


@dataclass
class Costs:
    fee_bps: float = 4.0     # taker fee per side (0.04% ~ Binance futures taker)
    slip_bps: float = 2.0    # slippage per side


def run_backtest(df: pd.DataFrame, sig: pd.DataFrame, costs: Costs,
                 sl_atr: float = 0.0, tp_atr: float = 0.0, trail_atr: float = 0.0,
                 max_hold: int = 0, allow_long: bool = True,
                 allow_short: bool = True, fund_bar=None) -> dict:
    """df: OHLCV. sig: bool columns long_entry/long_exit/short_entry/short_exit
    and float column `atr`. Returns dict with trades DataFrame + equity Series."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    idx = df.index
    atr = sig["atr"].values if "atr" in sig else np.zeros(len(df))

    le = sig["long_entry"].fillna(False).values
    lx = sig["long_exit"].fillna(False).values
    se = sig["short_entry"].fillna(False).values if "short_entry" in sig else np.zeros(len(df), bool)
    sx = sig["short_exit"].fillna(False).values if "short_exit" in sig else np.zeros(len(df), bool)

    per_side = (costs.fee_bps + costs.slip_bps) / 1e4
    n = len(df)
    fb = fund_bar if fund_bar is not None else np.zeros(n)

    pos = 0            # +1 long, -1 short, 0 flat
    entry_px = 0.0
    entry_i = 0
    stop = 0.0
    target = 0.0
    trail = 0.0
    trades = []
    realized_eq = 1.0          # compounded equity from CLOSED trades only
    equity_bar = np.ones(n)    # true mark-to-market equity per bar

    def close_trade(exit_px, exit_i, reason):
        nonlocal pos, realized_eq
        if pos == 1:
            gross = exit_px / entry_px - 1
        else:
            # USDT-margined short, fixed qty: PnL = qty*(entry-exit), qty = eq/entry
            gross = 1 - exit_px / entry_px
        net = (1 + gross) * (1 - per_side) * (1 - per_side) - 1
        # funding: longs pay positive funding, shorts receive it. Charged at
        # every 8h funding stamp inside the holding window.
        fc = (1.0 if pos == 1 else -1.0) * fb[entry_i:exit_i].sum()
        net = (1 + net) * (1 - fc) - 1
        realized_eq *= (1 + net)
        trades.append({
            "entry_dt": idx[entry_i], "exit_dt": idx[exit_i],
            "side": "L" if pos == 1 else "S",
            "entry_px": entry_px, "exit_px": exit_px,
            "bars": exit_i - entry_i, "ret": net, "reason": reason,
        })
        pos = 0

    for i in range(1, n):
        # ---- manage open position on bar i (intrabar stop/target/trail) ----
        if pos != 0:
            if pos == 1:
                if trail_atr > 0:
                    trail = max(trail, h[i - 1] - trail_atr * atr[i - 1])
                    stop = max(stop, trail) if stop > 0 else trail
                if sl_atr > 0 and l[i] <= stop:
                    close_trade(min(o[i], stop), i, "stop");
                elif tp_atr > 0 and h[i] >= target:
                    close_trade(max(o[i], target), i, "target")
                elif lx[i - 1] or (se[i - 1] and allow_short):
                    close_trade(o[i], i, "signal")
                elif max_hold > 0 and (i - entry_i) >= max_hold:
                    close_trade(o[i], i, "maxhold")
            else:  # short
                if trail_atr > 0:
                    trail = min(trail, l[i - 1] + trail_atr * atr[i - 1]) if trail > 0 else l[i - 1] + trail_atr * atr[i - 1]
                    stop = min(stop, trail) if stop > 0 else trail
                if sl_atr > 0 and h[i] >= stop:
                    close_trade(max(o[i], stop), i, "stop")
                elif tp_atr > 0 and l[i] <= target:
                    close_trade(min(o[i], target), i, "target")
                elif sx[i - 1] or (le[i - 1] and allow_long):
                    close_trade(o[i], i, "signal")
                elif max_hold > 0 and (i - entry_i) >= max_hold:
                    close_trade(o[i], i, "maxhold")

        # ---- new entry on bar i from signal at bar i-1 ----
        if pos == 0:
            if allow_long and le[i - 1]:
                pos = 1; entry_px = o[i]; entry_i = i
                a = atr[i - 1]
                stop = entry_px - sl_atr * a if sl_atr > 0 else 0.0
                target = entry_px + tp_atr * a if tp_atr > 0 else 0.0
                trail = entry_px - trail_atr * a if trail_atr > 0 else 0.0
            elif allow_short and se[i - 1]:
                pos = -1; entry_px = o[i]; entry_i = i
                a = atr[i - 1]
                stop = entry_px + sl_atr * a if sl_atr > 0 else 0.0
                target = entry_px - tp_atr * a if tp_atr > 0 else 0.0
                trail = entry_px + trail_atr * a if trail_atr > 0 else 0.0

        # ---- mark-to-market this bar (open position valued at close[i]) ----
        if pos == 0:
            equity_bar[i] = realized_eq
        else:
            gross_now = (c[i] / entry_px - 1) if pos == 1 else (1 - c[i] / entry_px)
            unreal = (1 + gross_now) * (1 - per_side) * (1 - per_side) - 1
            equity_bar[i] = realized_eq * (1 + unreal)

    if pos != 0:
        close_trade(c[-1], n - 1, "eod")
        equity_bar[n - 1] = realized_eq

    tdf = pd.DataFrame(trades)
    return {"trades": tdf, "index": idx, "equity_bar": pd.Series(equity_bar, index=idx)}


def metrics(res: dict, ann_factor: float = 365.0) -> dict:
    """Compute performance metrics from a backtest result."""
    tdf = res["trades"]
    if tdf is None or len(tdf) == 0:
        return {"n_trades": 0, "pf": 0, "total_ret": 0, "monthly_ret": 0,
                "sharpe": 0, "maxdd": 0, "win_rate": 0, "cagr": 0,
                "pos_months": 0, "avg_bars": 0, "expectancy": 0}

    r = tdf["ret"].values
    wins = r[r > 0].sum()
    losses = -r[r < 0].sum()
    pf = wins / losses if losses > 0 else np.inf

    full_idx = pd.to_datetime(res["index"])
    if "equity_bar" in res:
        # TRUE mark-to-market: open positions valued bar-by-bar
        daily = res["equity_bar"].resample("1D").last().ffill().fillna(1.0)
        total_ret = res["equity_bar"].iloc[-1] - 1
    else:
        # fallback: equity stepped at trade exits (lumpy)
        eq = pd.Series((1 + tdf["ret"]).cumprod().values, index=pd.to_datetime(tdf["exit_dt"]))
        eq = eq[~eq.index.duplicated(keep="last")]
        total_ret = eq.iloc[-1] - 1
        daily = eq.reindex(full_idx.union(eq.index)).ffill().fillna(1.0)
        daily = daily.resample("1D").last().ffill()
    eq = daily
    dd = (daily / daily.cummax() - 1).min()

    monthly = daily.resample("ME").last().ffill()
    m_ret = monthly.pct_change().dropna()
    monthly_mean = m_ret.mean()
    pos_months = (m_ret > 0).mean() if len(m_ret) else 0

    d_ret = daily.pct_change().dropna()
    sharpe = (d_ret.mean() / d_ret.std() * np.sqrt(ann_factor)) if d_ret.std() > 0 else 0

    years = (full_idx[-1] - full_idx[0]).days / 365.25
    cagr = (eq.iloc[-1]) ** (1 / years) - 1 if years > 0 else 0

    return {
        "n_trades": int(len(tdf)),
        "pf": round(float(pf), 3),
        "total_ret": round(float(total_ret), 4),
        "monthly_ret": round(float(monthly_mean), 4),
        "pos_months": round(float(pos_months), 3),
        "sharpe": round(float(sharpe), 2),
        "maxdd": round(float(dd), 4),
        "win_rate": round(float((r > 0).mean()), 3),
        "cagr": round(float(cagr), 4),
        "avg_bars": round(float(tdf["bars"].mean()), 1),
        "expectancy": round(float(r.mean()), 5),
    }
