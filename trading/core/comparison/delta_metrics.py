"""
Comparison metrics between the base trade and the ghost trade.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def build_trade_comparison(
    signal_row: Dict[str, Any],
    overlay_decision_id: int,
    overlay_decision: Dict[str, Any],
    base_trade: Optional[Dict[str, Any]],
    ghost_trade: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    comparison_label = overlay_decision["comparison_label"]
    entry_delta_bps = _delta_bps(_get(base_trade, "entry_price"), _get(ghost_trade, "entry_price"))
    stop_delta_bps = _delta_bps(_get(base_trade, "stop_price"), _get(ghost_trade, "stop_price"))
    target_delta_bps = _delta_bps(_get(base_trade, "target_price"), _get(ghost_trade, "target_price"))
    pnl_delta_usd = _delta(_get(base_trade, "pnl_usd"), _get(ghost_trade, "pnl_usd"))
    pnl_delta_pct = _delta(_get(base_trade, "pnl_pct"), _get(ghost_trade, "pnl_pct"))

    return {
        "signal_id": int(signal_row["id"]),
        "overlay_decision_id": overlay_decision_id,
        "engine_id": signal_row["engine_id"],
        "symbol": signal_row["symbol"],
        "comparison_label": comparison_label,
        "base_trade_id": _get(base_trade, "id"),
        "ghost_trade_id": _get(ghost_trade, "id"),
        "entry_delta_bps": entry_delta_bps,
        "stop_delta_bps": stop_delta_bps,
        "target_delta_bps": target_delta_bps,
        "pnl_delta_usd": pnl_delta_usd,
        "pnl_delta_pct": pnl_delta_pct,
        "base_close_reason": _get(base_trade, "close_reason"),
        "ghost_close_reason": _get(ghost_trade, "close_reason"),
        "summary": {
            "overlay_action": overlay_decision["action"],
            "overlay_reason": overlay_decision["reason"],
            "base_trade_present": base_trade is not None,
            "ghost_trade_present": ghost_trade is not None,
        },
    }


def _delta_bps(base: Any, overlay: Any) -> Optional[float]:
    base_f = _to_float(base)
    overlay_f = _to_float(overlay)
    if base_f in (None, 0.0) or overlay_f is None:
        return None
    return round((overlay_f - base_f) / base_f * 10_000.0, 4)


def _delta(base: Any, overlay: Any) -> Optional[float]:
    base_f = _to_float(base)
    overlay_f = _to_float(overlay)
    if base_f is None or overlay_f is None:
        return None
    return round(overlay_f - base_f, 6)


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get(row: Optional[Dict[str, Any]], key: str) -> Any:
    if row is None:
        return None
    return row.get(key)
