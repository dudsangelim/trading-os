"""
OHLCV-derived liquidity zone models.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..data.candle_reader import Candle


@dataclass
class LiquiditySnapshot:
    symbol: str
    timeframe: str
    zones: Dict[str, float]
    features: Dict[str, float | int | str | bool | None]


def build_liquidity_snapshot(symbol: str, candles: List[Candle], timeframe: str = "15m") -> LiquiditySnapshot:
    if not candles:
        return LiquiditySnapshot(symbol=symbol, timeframe=timeframe, zones={}, features={})

    last = candles[-1]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]

    short_window = candles[-8:] if len(candles) >= 8 else candles
    medium_window = candles[-20:] if len(candles) >= 20 else candles
    long_window = candles[-48:] if len(candles) >= 48 else candles

    nearest_above = max((c.high for c in short_window if c.high > last.close), default=None)
    nearest_below = min((c.low for c in short_window if c.low < last.close), default=None)
    session_high = max(c.high for c in medium_window)
    session_low = min(c.low for c in medium_window)
    range_high = max(c.high for c in long_window)
    range_low = min(c.low for c in long_window)
    volume_median = sorted(volumes)[len(volumes) // 2] if volumes else 0.0

    zones = {
        "nearest_above": round(nearest_above, 8) if nearest_above is not None else None,
        "nearest_below": round(nearest_below, 8) if nearest_below is not None else None,
        "session_high": round(session_high, 8),
        "session_low": round(session_low, 8),
        "range_high": round(range_high, 8),
        "range_low": round(range_low, 8),
    }
    features: Dict[str, float | int | str | bool | None] = {
        "bar_close": round(last.close, 8),
        "bar_high": round(last.high, 8),
        "bar_low": round(last.low, 8),
        "bar_volume": round(last.volume, 8),
        "volume_above_median": last.volume > volume_median,
        "distance_to_nearest_above_bps": _bps(last.close, nearest_above),
        "distance_to_nearest_below_bps": _bps(last.close, nearest_below),
        "distance_to_session_high_bps": _bps(last.close, session_high),
        "distance_to_session_low_bps": _bps(last.close, session_low),
        "short_range_bps": _bps(min(lows[-8:]) if len(lows) >= 8 else min(lows), max(highs[-8:]) if len(highs) >= 8 else max(highs)),
        "trend_lookback_return_bps": _return_bps(closes[0], closes[-1]),
        "candles_used": len(candles),
    }
    return LiquiditySnapshot(symbol=symbol, timeframe=timeframe, zones=zones, features=features)


def _bps(base: float, ref: Optional[float]) -> Optional[float]:
    if ref is None or base == 0:
        return None
    return round((ref - base) / base * 10_000.0, 4)


def _return_bps(first: float, last: float) -> float:
    if first == 0:
        return 0.0
    return round((last - first) / first * 10_000.0, 4)
