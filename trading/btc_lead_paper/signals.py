"""btc_lead signal builder — exact port of research lib_strategies2.btc_lead.

Signals are computed from the SIGNAL symbol's (BTC) closes aligned to the
traded symbol's (ETH) bar index:
    long_entry  = roc(btc, ROC_N) > 0  AND  btc > ema(btc, EMA_N)
    long_exit   = roc < 0  OR  btc < ema
    short_entry = roc < 0  AND  btc < ema
    short_exit  = roc > 0  OR  btc > ema
Indicators match the research lib_indicators: roc = pct_change(n),
ema = ewm(span=n, adjust=False).
"""
from __future__ import annotations

import pandas as pd

from trading.btc_lead_paper import config as C


def build_signals(trade_df: pd.DataFrame, signal_close: pd.Series) -> pd.DataFrame:
    """trade_df: closed OHLCV bars of the traded symbol (index = bar open time).
    signal_close: closes of the signal symbol, same bar convention."""
    b = signal_close.reindex(trade_df.index).ffill()
    r = b.pct_change(C.ROC_N)
    e = b.ewm(span=C.EMA_N, adjust=False).mean()
    s = pd.DataFrame(index=trade_df.index)
    s["long_entry"] = (r > 0) & (b > e)
    s["long_exit"] = (r < 0) | (b < e)
    s["short_entry"] = (r < 0) & (b < e)
    s["short_exit"] = (r > 0) | (b > e)
    # diagnostics for logging/status
    s["roc"] = r
    s["ema"] = e
    s["btc"] = b
    return s
