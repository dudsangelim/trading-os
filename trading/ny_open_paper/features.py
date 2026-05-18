"""
Contextual features computed via Binance REST before each NY session.

Ported faithfully from paper_trade_reference.py (compute_context_live function).

The 7 contextual features (the other 4 are computed at break time in the engine):
  vol_60d_pct           — 60-day hourly return volatility (annualised %)
  vol_20d_pct           — 20-day hourly return volatility (annualised %)
  asian_range_pct       — BTC range 00:00–08:00 UTC as % of open
  london_leadin_return  — BTC return 12:00–13:30 UTC (%)
  btc_vs_sma200_pct     — close vs 200-day SMA (%)
  dow                   — day-of-week (float, Mon=0 … Sun=6)
  funding_last_pct      — most recent funding rate (%)
"""
from __future__ import annotations

import logging
import math
from datetime import date

import pandas as pd

from trading.ny_open_paper.feed import BinanceFeed

log = logging.getLogger("ny_open_paper")


def compute_context_live(session_date: date) -> dict:
    """Compute 7 contextual features for the given session date via Binance REST."""
    day_start = pd.Timestamp(session_date, tz="UTC")
    session_open_ts = day_start + pd.Timedelta(hours=13, minutes=30)
    end_time_ms = int(session_open_ts.timestamp() * 1000)

    # 1h klines: 1500 bars ≈ 62.5 days — enough for vol_60d rolling(1440)
    df1h = BinanceFeed.klines("1h", limit=1500, end_time_ms=end_time_ms)
    df1h = df1h.set_index("open_time").sort_index()
    ret1h = df1h["close"].pct_change().dropna()
    vol_60d_raw = ret1h.rolling(60 * 24).std().iloc[-1]
    vol_20d_raw = ret1h.rolling(20 * 24).std().iloc[-1]
    vol_60d = float(vol_60d_raw * 100) if not (math.isnan(vol_60d_raw) or math.isinf(vol_60d_raw)) else 0.0
    vol_20d = float(vol_20d_raw * 100) if not (math.isnan(vol_20d_raw) or math.isinf(vol_20d_raw)) else 0.0

    # 1m klines: 1500 bars ≈ 25h — covers asian range + london lead-in
    df1m = BinanceFeed.klines("1m", limit=1500, end_time_ms=end_time_ms)
    df1m = df1m.set_index("open_time").sort_index()

    # asian range: 00:00–08:00 UTC
    asian_end = day_start + pd.Timedelta(hours=8)
    asian = df1m[(df1m.index >= day_start) & (df1m.index < asian_end)]
    if len(asian) > 10:
        asian_range_pct = float(
            (asian["high"].max() - asian["low"].min()) / asian["close"].iloc[0] * 100
        )
    else:
        asian_range_pct = 0.0

    # london lead-in: 12:00–13:30 UTC
    leadin_start = day_start + pd.Timedelta(hours=12)
    leadin = df1m[(df1m.index >= leadin_start) & (df1m.index < session_open_ts)]
    if len(leadin) > 10:
        c0 = leadin["close"].iloc[0]
        c1 = leadin["close"].iloc[-1]
        leadin_ret = float((c1 - c0) / c0 * 100) if c0 > 0 else 0.0
    else:
        leadin_ret = 0.0

    # daily klines for SMA200 (300 bars = ~10 months buffer)
    df1d = BinanceFeed.klines("1d", limit=300, end_time_ms=end_time_ms)
    df1d = df1d.set_index("open_time").sort_index()
    sma200 = df1d["close"].rolling(200).mean().iloc[-1]
    close_prev = float(df1d["close"].iloc[-1])
    if sma200 > 0 and not math.isnan(sma200):
        btc_vs_sma200_pct = float((close_prev - sma200) / sma200 * 100)
    else:
        btc_vs_sma200_pct = 0.0

    funding_last_pct = BinanceFeed.last_funding_rate() * 100
    dow = float(pd.Timestamp(session_date).dayofweek)

    ctx = {
        "vol_60d_pct": vol_60d,
        "vol_20d_pct": vol_20d,
        "asian_range_pct": asian_range_pct,
        "london_leadin_return": leadin_ret,
        "btc_vs_sma200_pct": btc_vs_sma200_pct,
        "dow": dow,
        "funding_last_pct": float(funding_last_pct),
    }
    log.info(
        "[ctx %s] vol60d=%.3f vol20d=%.3f asian_range=%.2f leadin=%+.3f "
        "sma200=%+.2f funding=%+.4f dow=%.0f",
        session_date,
        ctx["vol_60d_pct"], ctx["vol_20d_pct"], ctx["asian_range_pct"],
        ctx["london_leadin_return"], ctx["btc_vs_sma200_pct"],
        ctx["funding_last_pct"], ctx["dow"],
    )
    return ctx
