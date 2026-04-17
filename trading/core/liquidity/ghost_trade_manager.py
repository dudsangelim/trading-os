"""
Ghost trade helpers for the liquidity overlay.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..data.candle_reader import CandleReader
from ..engines.base import Direction, Position
from ..execution.position_manager import check_position
from ..storage.repository import TradingRepository


class GhostTradeManager:
    def __init__(self, repo: TradingRepository, reader: CandleReader) -> None:
        self._repo = repo
        self._reader = reader

    async def maybe_open_ghost_trade(
        self,
        overlay_decision_id: int,
        signal_row: Dict[str, Any],
        overlay_decision: Dict[str, Any],
        base_trade: Optional[Dict[str, Any]],
        variant_label: str = "legacy",
        zone_sources_used: Optional[Dict[str, Any]] = None,
        stop_zone_info: Optional[Dict[str, Any]] = None,
        target_zone_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        action = overlay_decision["action"]
        if action not in {"ENTER", "ADJUST"}:
            return None

        signal_id = int(signal_row["id"])
        tf_minutes = _tf_minutes(signal_row)
        latest_completed = _latest_completed_bar_open(datetime.now(timezone.utc), tf_minutes)
        fresh_cutoff = latest_completed - timedelta(minutes=tf_minutes)
        if signal_row["bar_timestamp"] < fresh_cutoff:
            return None

        # Dedup by (signal_id, variant_label) — allows 3 ghost trades per signal
        existing = await self._repo.get_ghost_trade_by_signal_variant(signal_id, variant_label)
        if existing is not None:
            return int(existing["id"])

        entry = _as_float(overlay_decision.get("entry_price"))
        stop = _as_float(overlay_decision.get("stop_price"))
        target = _as_float(overlay_decision.get("target_price"))
        stake = _as_float(overlay_decision.get("stake_usd"))
        if entry is None or stop is None or target is None or stake is None:
            return None

        base_trade_id = int(base_trade["id"]) if base_trade else None
        return await self._repo.open_ghost_trade(
            overlay_decision_id=overlay_decision_id,
            signal_id=signal_id,
            base_trade_id=base_trade_id,
            engine_id=signal_row["engine_id"],
            symbol=signal_row["symbol"],
            direction=Direction(signal_row["direction"]),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            stake_usd=stake,
            opened_at=signal_row["bar_timestamp"],
            variant_label=variant_label,
            zone_sources_used=zone_sources_used,
            stop_zone_info=stop_zone_info,
            target_zone_info=target_zone_info,
        )

    async def process_open_ghost_trades(self) -> int:
        count = 0
        open_trades = await self._repo.get_open_ghost_trades()
        for row in open_trades:
            trade_id = int(row["id"])
            signal_row = await self._repo.get_signal_by_id(int(row["signal_id"]))
            if signal_row is None:
                continue
            candle = await self._reader.aggregate_tf_candle(
                row["symbol"],
                _latest_completed_bar_open(datetime.now(timezone.utc), _tf_minutes(signal_row)),
                _tf_minutes(signal_row),
            )
            if candle is None:
                continue
            position = Position(
                trade_id=trade_id,
                engine_id=row["engine_id"],
                symbol=row["symbol"],
                signal_id=row["signal_id"],
                direction=Direction(row["direction"]),
                entry_price=float(row["entry_price"]),
                stop_price=float(row["stop_price"]),
                target_price=float(row["target_price"]),
                stake_usd=float(row["stake_usd"]),
                opened_at=row["opened_at"],
                bars_held=_bars_held(row["opened_at"], datetime.now(timezone.utc), _tf_minutes(signal_row)),
            )
            close_result = check_position(
                position,
                candle,
                timeout_bars=_timeout_bars(signal_row["engine_id"]),
                slippage_bps=_slippage_bps(signal_row["engine_id"]),
                fee_bps=_fee_bps(signal_row["engine_id"]),
            )
            if close_result is None:
                continue
            await self._repo.close_ghost_trade(
                trade_id,
                close_result.exit_price,
                close_result.pnl_usd,
                close_result.pnl_pct,
                close_result.close_reason,
                close_result.closed_at,
            )
            count += 1
        return count


def _tf_minutes(signal_row: Dict[str, Any]) -> int:
    signal_data = signal_row.get("signal_data") or {}
    if isinstance(signal_data, str):
        try:
            signal_data = json.loads(signal_data)
        except json.JSONDecodeError:
            signal_data = {}
    if not isinstance(signal_data, dict):
        signal_data = {}
    tf = signal_data.get("timeframe", "15m")
    try:
        return int(str(tf).replace("m", ""))
    except ValueError:
        return 15


def _bars_held(opened_at: datetime, now: datetime, tf_minutes: int) -> int:
    elapsed = max((now - opened_at).total_seconds(), 0.0)
    return int(elapsed // (tf_minutes * 60))


def _latest_completed_bar_open(now: datetime, tf_minutes: int) -> datetime:
    minutes_since_epoch = int(now.timestamp() // 60)
    completed_bar_minute = (minutes_since_epoch // tf_minutes) * tf_minutes - tf_minutes
    return datetime.fromtimestamp(completed_bar_minute * 60, tz=timezone.utc)


def _timeout_bars(engine_id: str) -> int:
    from ..config.settings import ENGINE_CONFIGS

    return ENGINE_CONFIGS[engine_id].timeout_bars


def _slippage_bps(engine_id: str) -> int:
    from ..config.settings import ENGINE_CONFIGS

    return ENGINE_CONFIGS[engine_id].slippage_bps


def _fee_bps(engine_id: str) -> int:
    from ..config.settings import ENGINE_CONFIGS

    return ENGINE_CONFIGS[engine_id].fee_bps


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
