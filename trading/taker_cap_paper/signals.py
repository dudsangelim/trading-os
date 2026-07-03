"""taker_cap signal builder — exact port of the research build_panel (liqflow
phase 2, run_phase2.py):

    tk1h   = mean of 5m taker buy/sell ratio per hour (Vision end-labels)
    tk24   = rolling(24h, minp 12) mean of log(tk1h), on the price bar index
    z      = (tk24 - mean_7d) / std_7d   (rolling 168h, minp 84, ddof=0)
    z      = z.shift(1)                  (1-bar research lag)
    long_entry = z < Z_TH  (LONG only; exit is a fixed 4-bar max-hold)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from trading.taker_cap_paper import config as C


def build_signals(trade_df: pd.DataFrame, taker_5m: pd.Series) -> pd.DataFrame:
    """trade_df: closed 1h OHLCV bars (index = bar open time, UTC).
    taker_5m: 5m taker ratio with Vision end-labels (see exchange.fetch_taker_5m)."""
    tk1h = taker_5m.resample("1h").mean()
    tk = tk1h.reindex(trade_df.index)
    tk24 = np.log(tk).rolling(C.TK_ROLL, min_periods=C.TK_ROLL_MINP).mean()
    mu = tk24.rolling(C.TK_ZWIN, min_periods=C.TK_ZWIN_MINP).mean()
    sd = tk24.rolling(C.TK_ZWIN, min_periods=C.TK_ZWIN_MINP).std(ddof=0)
    z = ((tk24 - mu) / sd).shift(1)

    s = pd.DataFrame(index=trade_df.index)
    s["long_entry"] = z < C.Z_TH
    s["long_exit"] = False        # exit is max-hold only
    s["short_entry"] = False
    s["short_exit"] = False
    # diagnostics for logging/status
    s["z"] = z
    s["taker"] = tk
    return s
