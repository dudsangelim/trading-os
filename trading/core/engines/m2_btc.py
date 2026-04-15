"""
Engine M2 — BTC/USDT

Strategy: Sweep & Reclaim in 15m bars, filtered by 4h HMM regime "Compressed balance".

Entry is a two-bar pattern:
  Bar 1 (sweep bar): price sweeps below a 32-bar low (LONG) or above a 32-bar high (SHORT)
                     then closes back inside the range.
  Bar 2 (confirmation bar): next bar confirms the reclaim.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
from .regime_hmm import RegimeClassifier
from ..config.settings import ENGINE_CONFIGS
from ..risk.sizing import compute_stake

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REGIME_REQUIRED = "Compressed balance"
LOOKBACK = 32           # 8h of 15m bars
VOL_LOOKBACK = 20
STOP_BUFFER_BPS = 1     # 0.01%
RR_RATIO = 1.5          # R:R target — take profit at 1.5R

# Entry window: 07:00-17:00 UTC (widened from 13:00-14:30)
def _in_entry_window(dt: datetime) -> bool:
    return 7 <= dt.hour < 17


class M2BtcEngine(BaseEngine):
    ENGINE_ID = "m2_btc"

    def __init__(self) -> None:
        cfg = ENGINE_CONFIGS[self.ENGINE_ID]
        super().__init__(engine_id=self.ENGINE_ID, symbol=cfg.symbol)
        self._cfg = cfg
        self._hmm = RegimeClassifier(cfg.symbol)
        self._current_regime: Optional[str] = None
        self._last_4h_ts: Optional[datetime] = None
        self._pending_sweep: Optional[Dict] = None
        self._ema_trend_dir: Optional[str] = None  # "LONG" | "SHORT" | None

    def export_runtime_state(self) -> Dict[str, Any]:
        return {
            "current_regime": self._current_regime,
            "last_4h_ts": self._serialize_datetime(self._last_4h_ts),
            "ema_trend_dir": self._ema_trend_dir,
        }

    def import_runtime_state(self, state: Dict[str, Any]) -> None:
        self._current_regime = state.get("current_regime")
        self._last_4h_ts = self._deserialize_datetime(state.get("last_4h_ts"))
        self._ema_trend_dir = state.get("ema_trend_dir")

    def export_pending_state(self) -> Optional[Dict[str, Any]]:
        if self._pending_sweep is None:
            return None
        payload = dict(self._pending_sweep)
        return {
            "setup_type": "pending_sweep",
            "reference_bar_ts": payload.get("reference_bar_ts"),
            "expires_at": payload.get("expires_at"),
            "state_data": payload,
        }

    def import_pending_state(self, setup_type: str, state: Dict[str, Any]) -> None:
        if setup_type != "pending_sweep":
            return
        self._pending_sweep = dict(state)

    def clear_pending_state(self) -> None:
        self._pending_sweep = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, reader: Any) -> None:
        """Fetch last 500 4h candles and fit HMM."""
        try:
            candles_4h = await reader.get_latest_4h_candles(self.symbol, limit=500)
            fitted = self._hmm.fit(candles_4h)
            if fitted:
                logger.info("M2BtcEngine: HMM fitted on %d 4h bars", len(candles_4h))
            else:
                logger.warning("M2BtcEngine: HMM fit failed or skipped — will return None")
        except Exception as exc:
            logger.warning("M2BtcEngine.initialize error: %s", exc)

    async def enrich_context(self, ctx: EngineContext, reader: Any) -> EngineContext:
        """Update 4h regime if a new 4h bar closed; fetch 40 15m candles."""
        bar_ts = ctx.bar_timestamp

        current_4h_open = _bar_open_at(bar_ts, 240)
        if self._last_4h_ts is None or current_4h_open > self._last_4h_ts:
            try:
                candles_4h = await reader.get_latest_4h_candles(
                    self.symbol, limit=150, before=bar_ts
                )
                if candles_4h:
                    regime = self._hmm.current_regime(candles_4h)
                    self._current_regime = regime
                    logger.debug("M2BtcEngine: 4h regime updated → %s", regime)
                    # EMA20/50 directional bias — only take sweeps aligned with the trend
                    closes = [float(c.close) for c in candles_4h]
                    if len(closes) >= 50:
                        alpha20 = 2.0 / 21
                        alpha50 = 2.0 / 51
                        ema20 = closes[0]
                        ema50 = closes[0]
                        for v in closes[1:]:
                            ema20 = alpha20 * v + (1 - alpha20) * ema20
                            ema50 = alpha50 * v + (1 - alpha50) * ema50
                        if ema20 > ema50:
                            self._ema_trend_dir = "LONG"
                        elif ema20 < ema50:
                            self._ema_trend_dir = "SHORT"
                        else:
                            self._ema_trend_dir = None
                self._last_4h_ts = current_4h_open
            except Exception as exc:
                logger.warning("M2BtcEngine.enrich_context 4h update error: %s", exc)

        candles_15m: List[Any] = []
        try:
            candles_15m = await reader.get_latest_tf_candles(
                self.symbol, tf_minutes=15, limit=40, before=bar_ts
            )
        except Exception as exc:
            logger.warning("M2BtcEngine.enrich_context 15m fetch error: %s", exc)

        ctx.extra["regime_4h"] = self._current_regime
        ctx.extra["ema_trend_dir"] = self._ema_trend_dir
        ctx.extra["candles_15m"] = candles_15m
        return ctx

    # ------------------------------------------------------------------
    # BaseEngine interface
    # ------------------------------------------------------------------

    def generate_signal(self, ctx: EngineContext) -> Optional[Signal]:
        self.clear_skip_reason(ctx)
        # --- Check for pending sweep confirmation first ---
        if self._pending_sweep is not None:
            pending = self._pending_sweep
            self._pending_sweep = None  # consume immediately

            direction = pending["direction"]
            low_ref = pending["low_ref"]
            high_ref = pending["high_ref"]

            confirmed = (
                (direction == "LONG" and ctx.bar_close > low_ref)
                or (direction == "SHORT" and ctx.bar_close < high_ref)
            )

            if confirmed:
                return Signal(
                    engine_id=self.ENGINE_ID,
                    symbol=self.symbol,
                    bar_timestamp=ctx.bar_timestamp,
                    direction=Direction(direction),
                    timeframe="15m",
                    signal_data={
                        "setup_label": "sweep_reclaim",
                        "bar_close": ctx.bar_close,
                        **pending,
                    },
                )
            self.set_skip_reason(ctx, "SWEEP_CONFIRMATION_FAILED", direction=direction)
            return None

        # --- Check for new sweep setup ---
        # 1. Regime filter
        if ctx.extra.get("regime_4h") != REGIME_REQUIRED:
            self.set_skip_reason(
                ctx,
                "REGIME_MISMATCH",
                required=REGIME_REQUIRED,
                actual=ctx.extra.get("regime_4h"),
            )
            return None

        # 2. Entry window
        if not _in_entry_window(ctx.bar_timestamp):
            self.set_skip_reason(
                ctx,
                "OUTSIDE_ENTRY_WINDOW",
                hour=ctx.bar_timestamp.hour,
                minute=ctx.bar_timestamp.minute,
            )
            return None

        # 3. Need enough 15m candles
        candles_15m = ctx.extra.get("candles_15m", [])
        if len(candles_15m) < LOOKBACK:
            self.set_skip_reason(ctx, "INSUFFICIENT_15M_HISTORY", have=len(candles_15m), need=LOOKBACK)
            return None

        # 4. Range from last LOOKBACK bars
        low_ref = min(c.low for c in candles_15m[-LOOKBACK:])
        high_ref = max(c.high for c in candles_15m[-LOOKBACK:])

        # 5. Volume filter
        vol_window = sorted(c.volume for c in candles_15m[-VOL_LOOKBACK:])
        volmed20 = vol_window[len(vol_window) // 2] if vol_window else 0.0
        if ctx.bar_volume <= volmed20:
            self.set_skip_reason(
                ctx,
                "VOLUME_BELOW_MEDIAN",
                bar_volume=round(ctx.bar_volume, 6),
                median_volume=round(volmed20, 6),
            )
            return None

        # 6. Detect sweep — only in direction aligned with EMA20/50 trend
        ema_trend = ctx.extra.get("ema_trend_dir")
        if ctx.bar_low < low_ref and ctx.bar_close > low_ref:
            # LONG sweep: broke below then reclaimed
            if ema_trend == "LONG" or ema_trend is None:
                self._pending_sweep = {
                    "direction": "LONG",
                    "low_ref": low_ref,
                    "high_ref": high_ref,
                    "sweep_extreme": ctx.bar_low,
                    "volmed20": volmed20,
                    "reference_bar_ts": ctx.bar_timestamp.isoformat(),
                    "expires_at": ctx.bar_timestamp.isoformat(),
                }
            else:
                self.set_skip_reason(ctx, "EMA_TREND_MISMATCH", sweep="LONG", ema_trend=ema_trend)
                return None
        elif ctx.bar_high > high_ref and ctx.bar_close < high_ref:
            # SHORT sweep: broke above then fell back
            if ema_trend == "SHORT" or ema_trend is None:
                self._pending_sweep = {
                    "direction": "SHORT",
                    "low_ref": low_ref,
                    "high_ref": high_ref,
                    "sweep_extreme": ctx.bar_high,
                    "volmed20": volmed20,
                    "reference_bar_ts": ctx.bar_timestamp.isoformat(),
                    "expires_at": ctx.bar_timestamp.isoformat(),
                }
            else:
                self.set_skip_reason(ctx, "EMA_TREND_MISMATCH", sweep="SHORT", ema_trend=ema_trend)
                return None

        # Signal comes on next bar (after confirmation)
        if self._pending_sweep is not None:
            self.set_skip_reason(ctx, "SWEEP_DETECTED_WAITING_CONFIRMATION", direction=self._pending_sweep["direction"])
        else:
            self.set_skip_reason(ctx, "NO_SWEEP_SETUP")
        return None

    def validate_signal(
        self, signal: Signal, ctx: EngineContext
    ) -> SignalDecision:
        return SignalDecision(
            signal_id=None,
            action=SignalAction.ENTER,
            reason="SWEEP_RECLAIM_CONFIRMED",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        entry_ref = signal.signal_data["bar_close"]
        sweep_extreme = signal.signal_data["sweep_extreme"]

        if signal.direction == Direction.LONG:
            stop = sweep_extreme * (1 - 0.0001)
            R = entry_ref - stop
            target = entry_ref + RR_RATIO * R
        else:
            stop = sweep_extreme * (1 + 0.0001)
            R = stop - entry_ref
            target = entry_ref - RR_RATIO * R

        stake = compute_stake(self.ENGINE_ID, bankroll)
        return OrderPlan(
            entry_price=entry_ref,
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
# Helpers
# ---------------------------------------------------------------------------

def _bar_open_at(ts: datetime, tf_min: int) -> datetime:
    m = int(ts.timestamp()) // 60
    return datetime.fromtimestamp(
        ((m // tf_min) * tf_min - tf_min) * 60, tz=timezone.utc
    )
