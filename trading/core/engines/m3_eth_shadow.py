"""
Engine M3-ETH-Shadow — signal-only mirror of M3 for ETH.

signal_only=True means the worker will NEVER open a position for this engine.
It records signals and decisions but never creates a tr_paper_trades row
with status=OPEN.

Strategy: Identical to M3 SOL but with tighter parameters:
  - LOOKBACK = 8 (8h window instead of 12h)
  - MAX_WIDTH = 2.0 (tighter zone)
  - Entry is DIRECT (no retest wait) — enters on the FIRST 15m bar after 1h breakout
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    BaseEngine,
    Direction,
    EngineContext,
    OrderPlan,
    Position,
    PositionAction,
    Signal,
    SignalAction,
    SignalDecision,
)
from ..config.settings import ENGINE_CONFIGS
from ..risk.sizing import compute_stake

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOOKBACK = 8        # 8h window instead of 12h
MAX_WIDTH = 2.0     # tighter zone
ATR_PERIOD = 14     # 1h bars


def _ema(values: List[float], span: int) -> List[float]:
    """Exponential moving average, pandas-equivalent (adjust=False)."""
    if not values:
        return []
    alpha = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _bar_open_at(ts: datetime, tf_min: int) -> datetime:
    m = int(ts.timestamp()) // 60
    return datetime.fromtimestamp(
        ((m // tf_min) * tf_min - tf_min) * 60, tz=timezone.utc
    )


class M3EthShadowEngine(BaseEngine):
    ENGINE_ID = "m3_eth_shadow"

    def __init__(self) -> None:
        cfg = ENGINE_CONFIGS[self.ENGINE_ID]
        super().__init__(engine_id=self.ENGINE_ID, symbol=cfg.symbol)
        self._cfg = cfg
        self._trend_ok_long: bool = False
        self._trend_ok_short: bool = False
        self._last_4h_ts: Optional[datetime] = None
        self._last_1h_ts: Optional[datetime] = None
        self._pending_direct: Optional[Dict] = None  # only held for 1 bar

    def export_runtime_state(self) -> Dict[str, Any]:
        return {
            "trend_ok_long": self._trend_ok_long,
            "trend_ok_short": self._trend_ok_short,
            "last_4h_ts": self._serialize_datetime(self._last_4h_ts),
            "last_1h_ts": self._serialize_datetime(self._last_1h_ts),
        }

    def import_runtime_state(self, state: Dict[str, Any]) -> None:
        self._trend_ok_long = bool(state.get("trend_ok_long", False))
        self._trend_ok_short = bool(state.get("trend_ok_short", False))
        self._last_4h_ts = self._deserialize_datetime(state.get("last_4h_ts"))
        self._last_1h_ts = self._deserialize_datetime(state.get("last_1h_ts"))

    def export_pending_state(self) -> Optional[Dict[str, Any]]:
        if self._pending_direct is None:
            return None
        payload = dict(self._pending_direct)
        return {
            "setup_type": "pending_direct",
            "reference_bar_ts": payload.get("reference_bar_ts"),
            "expires_at": payload.get("expires_at"),
            "state_data": payload,
        }

    def import_pending_state(self, setup_type: str, state: Dict[str, Any]) -> None:
        if setup_type != "pending_direct":
            return
        self._pending_direct = dict(state)

    def clear_pending_state(self) -> None:
        self._pending_direct = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, reader: Any) -> None:
        """Fetch last 200 4h candles and compute initial trend_ok."""
        try:
            candles_4h = await reader.get_latest_tf_candles(
                self.symbol, tf_minutes=240, limit=200
            )
            if len(candles_4h) >= 50:
                closes = [c.close for c in candles_4h]
                ema20 = _ema(closes, 20)
                ema50 = _ema(closes, 50)
                if len(ema20) >= 2 and len(ema50) >= 1:
                    slope = ema20[-1] - ema20[-2]
                    self._trend_ok_long = ema20[-1] > ema50[-1] and slope > 0
                    self._trend_ok_short = ema20[-1] < ema50[-1] and slope < 0
                logger.info(
                    "M3EthShadowEngine: initialized trend_ok_long=%s trend_ok_short=%s",
                    self._trend_ok_long, self._trend_ok_short
                )
        except Exception as exc:
            logger.warning("M3EthShadowEngine.initialize error: %s", exc)

    async def enrich_context(self, ctx: EngineContext, reader: Any) -> EngineContext:
        """Update 4h trend filter and 1h breakout detection."""
        bar_ts = ctx.bar_timestamp

        # 4h update
        current_4h_open = _bar_open_at(bar_ts, 240)
        if self._last_4h_ts is None or current_4h_open > self._last_4h_ts:
            try:
                candles_4h = await reader.get_latest_tf_candles(
                    self.symbol, tf_minutes=240, limit=80, before=bar_ts
                )
                if len(candles_4h) >= 50:
                    closes = [c.close for c in candles_4h]
                    ema20 = _ema(closes, 20)
                    ema50 = _ema(closes, 50)
                    if len(ema20) >= 2 and len(ema50) >= 1:
                        slope = ema20[-1] - ema20[-2]
                        self._trend_ok_long = ema20[-1] > ema50[-1] and slope > 0
                        self._trend_ok_short = ema20[-1] < ema50[-1] and slope < 0
                self._last_4h_ts = current_4h_open
            except Exception as exc:
                logger.warning("M3EthShadowEngine.enrich_context 4h error: %s", exc)

        # 1h breakout detection
        current_1h_open = _bar_open_at(bar_ts, 60)
        if self._last_1h_ts is None or current_1h_open > self._last_1h_ts:
            try:
                fetch_count = LOOKBACK + ATR_PERIOD + 5
                candles_1h = await reader.get_latest_tf_candles(
                    self.symbol, tf_minutes=60, limit=fetch_count, before=bar_ts
                )
                if len(candles_1h) >= LOOKBACK + 2:
                    breakout = _detect_breakout(candles_1h)
                    if breakout is not None:
                        bo_dir, comp_high, comp_low = breakout
                        trend_ok = (
                            (bo_dir == "LONG" and self._trend_ok_long)
                            or (bo_dir == "SHORT" and self._trend_ok_short)
                        )
                        if trend_ok:
                            self._pending_direct = {
                                "direction": bo_dir,
                                "comp_high": comp_high,
                                "comp_low": comp_low,
                                "reference_bar_ts": candles_1h[-1].open_time.isoformat(),
                                "expires_at": (bar_ts + timedelta(minutes=15)).isoformat(),
                            }
                self._last_1h_ts = current_1h_open
            except Exception as exc:
                logger.warning("M3EthShadowEngine.enrich_context 1h error: %s", exc)

        ctx.extra["trend_ok_long"] = self._trend_ok_long
        ctx.extra["trend_ok_short"] = self._trend_ok_short
        return ctx

    # ------------------------------------------------------------------
    # BaseEngine interface
    # ------------------------------------------------------------------

    def generate_signal(self, ctx: EngineContext) -> Optional[Signal]:
        self.clear_skip_reason(ctx)
        if self._pending_direct is None:
            self.set_skip_reason(ctx, "NO_PENDING_DIRECT_BREAKOUT")
            return None

        pending = self._pending_direct
        self._pending_direct = None  # direct entry: only valid for 1 bar

        direction = pending["direction"]
        trend_ok = (
            ctx.extra.get("trend_ok_long")
            if direction == "LONG"
            else ctx.extra.get("trend_ok_short")
        )
        if not trend_ok:
            self.set_skip_reason(ctx, "TREND_FILTER_FALSE", direction=direction)
            return None

        comp_high = pending["comp_high"]
        comp_low = pending["comp_low"]

        return Signal(
            engine_id=self.ENGINE_ID,
            symbol=self.symbol,
            bar_timestamp=ctx.bar_timestamp,
            direction=Direction(direction),
            timeframe="15m",
            signal_data={
                "setup_label": "direct_breakout",
                "comp_high": comp_high,
                "comp_low": comp_low,
                "entry_level": comp_high if direction == "LONG" else comp_low,
                "trend_ok": True,
                "bar_close": ctx.bar_close,
                "lookback": LOOKBACK,
                "max_width": MAX_WIDTH,
                "signal_tf": "1h",
            },
        )

    def validate_signal(
        self, signal: Signal, ctx: EngineContext
    ) -> SignalDecision:
        return SignalDecision(
            signal_id=None,
            action=SignalAction.ENTER,
            reason="DIRECT_SHADOW",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        entry_level = signal.signal_data["entry_level"]
        comp_high = signal.signal_data["comp_high"]
        comp_low = signal.signal_data["comp_low"]

        if signal.direction == Direction.LONG:
            stop = comp_low
            R = abs(entry_level - stop)
            target = entry_level + 1.5 * R
        else:
            stop = comp_high
            R = abs(stop - entry_level)
            target = entry_level - 1.5 * R

        stake = compute_stake(self.ENGINE_ID, bankroll)
        return OrderPlan(
            entry_price=entry_level,
            stop_price=stop,
            target_price=target,
            stake_usd=stake,
            direction=signal.direction,
        )

    def manage_open_position(
        self, position: Position, ctx: EngineContext
    ) -> PositionAction:
        return PositionAction.HOLD


# ---------------------------------------------------------------------------
# Zone detection helper (tighter params vs M3 SOL)
# ---------------------------------------------------------------------------

def _detect_breakout(candles_1h: List[Any]) -> Optional[Tuple[str, float, float]]:
    """
    Detect if the last closed 1h bar broke out of the LOOKBACK-bar zone.
    Uses LOOKBACK=8, MAX_WIDTH=2.0 (tighter than M3 SOL).
    """
    if len(candles_1h) < LOOKBACK + 2:
        return None

    curr = candles_1h[-1]
    zone_bars = candles_1h[-(LOOKBACK + 1):-1]

    if len(zone_bars) < LOOKBACK // 2:
        return None

    comp_high = max(c.high for c in zone_bars)
    comp_low = min(c.low for c in zone_bars)
    comp_width = comp_high - comp_low

    atr_bars = candles_1h[-(ATR_PERIOD + 1):-1]
    if not atr_bars:
        return None
    atr14 = sum(c.high - c.low for c in atr_bars) / len(atr_bars)

    if atr14 > 0 and comp_width > MAX_WIDTH * atr14:
        return None

    if curr.close > comp_high:
        return ("LONG", comp_high, comp_low)
    if curr.close < comp_low:
        return ("SHORT", comp_high, comp_low)
    return None
