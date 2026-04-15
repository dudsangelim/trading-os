from __future__ import annotations

from trading.core.comparison.delta_metrics import build_trade_comparison


def test_build_trade_comparison_computes_positive_pnl_delta():
    signal_row = {
        "id": 10,
        "engine_id": "m1_eth",
        "symbol": "ETHUSDT",
    }
    overlay_decision = {
        "action": "ADJUST",
        "comparison_label": "tp_higher",
        "reason": "TARGET_AT_NEXT_LIQUIDITY_POOL",
    }
    base_trade = {
        "id": 100,
        "entry_price": 100.0,
        "stop_price": 98.0,
        "target_price": 103.0,
        "pnl_usd": 2.0,
        "pnl_pct": 0.02,
        "close_reason": "CLOSED_TP",
    }
    ghost_trade = {
        "id": 200,
        "entry_price": 100.2,
        "stop_price": 97.5,
        "target_price": 104.0,
        "pnl_usd": 3.5,
        "pnl_pct": 0.035,
        "close_reason": "CLOSED_TP",
    }

    comparison = build_trade_comparison(
        signal_row=signal_row,
        overlay_decision_id=300,
        overlay_decision=overlay_decision,
        base_trade=base_trade,
        ghost_trade=ghost_trade,
    )

    assert comparison["comparison_label"] == "tp_higher"
    assert comparison["pnl_delta_usd"] == 1.5
    assert comparison["pnl_delta_pct"] == 0.015
    assert comparison["entry_delta_bps"] == 20.0


def test_build_trade_comparison_handles_missing_base_trade():
    signal_row = {
        "id": 11,
        "engine_id": "m3_sol",
        "symbol": "SOLUSDT",
    }
    overlay_decision = {
        "action": "ENTER",
        "comparison_label": "earlier",
        "reason": "ENTER_EARLIER_ON_PRE_BREAK_COMPRESSION",
    }
    ghost_trade = {
        "id": 201,
        "entry_price": 150.0,
        "stop_price": 145.0,
        "target_price": 157.0,
        "pnl_usd": -1.0,
        "pnl_pct": -0.01,
        "close_reason": "CLOSED_SL",
    }

    comparison = build_trade_comparison(
        signal_row=signal_row,
        overlay_decision_id=301,
        overlay_decision=overlay_decision,
        base_trade=None,
        ghost_trade=ghost_trade,
    )

    assert comparison["base_trade_id"] is None
    assert comparison["ghost_trade_id"] == 201
    assert comparison["pnl_delta_usd"] is None
