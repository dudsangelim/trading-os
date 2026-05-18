from __future__ import annotations

import pandas as pd
import pytest

from trading.bw_jawcross_paper.config import FEE_RT, SLIPPAGE_PCT
from trading.bw_jawcross_paper.engine import BwJawCrossEngine


def _bar(close: float, jaw: float, teeth: float, lips: float, low: float = 99.0, high: float = 104.0):
    return {
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "jaw": jaw,
        "teeth": teeth,
        "lips": lips,
        "atr": 1.0,
    }


def _df(rows: list[dict], start: str = "2026-01-01 00:00:00+00:00") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq="6h", tz="UTC")
    return pd.DataFrame(rows, index=idx)


def test_entry_uses_execution_price_not_signal_close():
    df = _df([
        _bar(close=100.0, jaw=101.0, teeth=102.0, lips=100.0),
        _bar(close=103.0, jaw=101.0, teeth=102.0, lips=103.0),
    ])
    execution_ts = df.index[-1] + pd.Timedelta(hours=6, minutes=2)

    engine = BwJawCrossEngine(initial_equity=1000.0)
    events = engine.replay_bars(df, execution_price=105.0, execution_ts=execution_ts)

    assert events[-1]["action"] == "open_long"
    assert engine.position_entry_ts == execution_ts
    assert engine.position_entry_px == pytest.approx(105.0 * (1 + SLIPPAGE_PCT))
    assert engine.position_entry_px != pytest.approx(103.0 * (1 + SLIPPAGE_PCT))


def test_exit_uses_execution_price_not_lips_touch_price():
    entry_df = _df([
        _bar(close=100.0, jaw=101.0, teeth=102.0, lips=100.0),
        _bar(close=103.0, jaw=101.0, teeth=102.0, lips=103.0),
    ])
    engine = BwJawCrossEngine(initial_equity=1000.0)
    engine.replay_bars(entry_df, execution_price=105.0, execution_ts=entry_df.index[-1] + pd.Timedelta(hours=6, minutes=2))

    exit_df = _df([
        _bar(close=100.0, jaw=101.0, teeth=102.0, lips=101.0, low=99.0, high=107.0),
    ], start="2026-01-01 12:00:00+00:00")
    execution_ts = exit_df.index[-1] + pd.Timedelta(hours=6, minutes=2)
    events = engine.replay_bars(exit_df, execution_price=107.0, execution_ts=execution_ts)

    trade = events[-1]["trade"]
    assert trade["exit_ts"] == execution_ts.isoformat()
    assert trade["exit_px"] == pytest.approx(107.0 * (1 - SLIPPAGE_PCT))
    assert trade["exit_px"] != pytest.approx(101.0 * (1 - SLIPPAGE_PCT))


def test_stale_entry_signal_is_skipped_by_default():
    df = _df([
        _bar(close=100.0, jaw=101.0, teeth=102.0, lips=100.0),
        _bar(close=103.0, jaw=101.0, teeth=102.0, lips=103.0),
        _bar(close=104.0, jaw=101.0, teeth=102.0, lips=103.0),
    ])

    engine = BwJawCrossEngine(initial_equity=1000.0)
    events = engine.replay_bars(df, execution_price=105.0, execution_ts=df.index[-1] + pd.Timedelta(hours=6, minutes=2))

    assert events == []
    assert engine.position_side == 0
    assert engine.last_bar_ts == df.index[-1]


def test_stale_exit_touch_closes_at_current_execution_price():
    engine = BwJawCrossEngine(initial_equity=1000.0)
    engine.position_side = 1
    engine.position_entry_px = 100.0
    engine.position_entry_ts = pd.Timestamp("2026-01-01 00:02:00+00:00")
    engine.position_signal_ts = pd.Timestamp("2025-12-31 18:00:00+00:00")
    engine._prev_bull_order = True

    df = _df([
        _bar(close=98.0, jaw=101.0, teeth=102.0, lips=99.0, low=98.5, high=100.0),
        _bar(close=100.0, jaw=101.0, teeth=102.0, lips=100.0, low=99.0, high=101.0),
    ])
    execution_ts = df.index[-1] + pd.Timedelta(hours=6, minutes=2)

    events = engine.replay_bars(df, execution_price=110.0, execution_ts=execution_ts)

    assert events[-1]["action"] == "close_long"
    trade = events[-1]["trade"]
    assert trade["signal_exit_ts"] == df.index[0].isoformat()
    assert trade["exit_ts"] == execution_ts.isoformat()
    assert trade["exit_px"] == pytest.approx(110.0 * (1 - SLIPPAGE_PCT))
    expected_net = (trade["exit_px"] - 100.0) / 100.0 - FEE_RT
    assert trade["net_ret_pct"] == pytest.approx(round(expected_net * 100, 4))
