from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading.core.data.candle_reader import Candle
from trading.core.engines.base import Direction
from trading.core.liquidity.overlay_evaluator import LiquidityOverlayEvaluator
from trading.core.liquidity.zone_models import LiquiditySnapshot, build_liquidity_snapshot


def _make_candles(symbol: str = "BTCUSDT", count: int = 64, start: float = 100.0) -> list[Candle]:
    base_ts = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
    candles: list[Candle] = []
    price = start
    for i in range(count):
        candles.append(
            Candle(
                symbol=symbol,
                open_time=base_ts + timedelta(minutes=15 * i),
                open=price,
                high=price + 1.5,
                low=price - 1.0,
                close=price + 0.5,
                volume=1000 + i * 10,
            )
        )
        price += 0.4
    return candles


def test_build_liquidity_snapshot_has_expected_keys():
    snapshot = build_liquidity_snapshot("BTCUSDT", _make_candles())
    assert snapshot.symbol == "BTCUSDT"
    assert "session_high" in snapshot.zones
    assert "session_low" in snapshot.zones
    assert "distance_to_session_high_bps" in snapshot.features
    assert snapshot.features["candles_used"] == 64


def test_overlay_evaluator_skips_long_into_nearby_liquidity():
    snapshot = LiquiditySnapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        zones={"nearest_above": 100.25, "nearest_below": 98.50, "session_high": 102.0, "session_low": 96.0},
        features={
            "distance_to_nearest_above_bps": 25.0,
            "distance_to_nearest_below_bps": -150.0,
            "distance_to_session_high_bps": 200.0,
            "distance_to_session_low_bps": -400.0,
            "candles_used": 64,
        },
    )
    evaluator = LiquidityOverlayEvaluator()

    signal_row = {
        "id": 1,
        "engine_id": "m2_btc",
        "symbol": "BTCUSDT",
        "direction": Direction.LONG.value,
        "bar_timestamp": datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
        "signal_data": {"bar_close": 100.0, "timeframe": "15m"},
    }
    evaluations = evaluator.evaluate(
        signal_row=signal_row,
        base_decision={"action": "ENTER"},
        base_trade=None,
        snapshot=snapshot,
        bankroll=200.0,
    )
    decision = evaluations[0]
    assert decision.action == "SKIP"
    assert decision.comparison_label == "skip"
    assert decision.reason == "LIQUIDITY_REJECTION_ABOVE"


def test_overlay_evaluator_adjusts_long_stop_and_target():
    candles = _make_candles()
    snapshot = build_liquidity_snapshot("BTCUSDT", candles)
    evaluator = LiquidityOverlayEvaluator()
    entry = candles[-1].close

    signal_row = {
        "id": 2,
        "engine_id": "m2_btc",
        "symbol": "BTCUSDT",
        "direction": Direction.LONG.value,
        "bar_timestamp": datetime(2026, 4, 13, 12, 15, tzinfo=timezone.utc),
        "signal_data": {"bar_close": entry, "timeframe": "15m"},
    }
    base_trade = {
        "entry_price": entry,
        "stop_price": entry - 0.2,
        "target_price": entry + 0.5,
        "stake_usd": 5.0,
    }
    evaluations = evaluator.evaluate(
        signal_row=signal_row,
        base_decision={"action": "ENTER"},
        base_trade=base_trade,
        snapshot=snapshot,
        bankroll=200.0,
    )
    decision = evaluations[0]
    assert decision.action == "ADJUST"
    assert decision.stop_price is not None
    assert decision.target_price is not None
    assert decision.stop_price < base_trade["stop_price"] or decision.target_price > base_trade["target_price"]
