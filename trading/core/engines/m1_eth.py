"""
Engine M1 — ETH/USDT

Strategy: Compression Breakout in 15m bars, filtered by 4h HMM regime "Trend / drift".

Entry: price breaks out of a 4-bar compression zone, confirmed by regime filter
       and hour-of-day window (13–17 UTC).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

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
from ..config.settings import ENGINE_CONFIGS, SLIPPAGE_BPS
from ..risk.sizing import compute_stake

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REGIME_REQUIRED = "Trend / drift"
COMPRESSION_BARS = 4
ATR_LOOKBACK = 48       # 15m bars = 12h
ATR_PERCENTILE = 0.35
WIDTH_MULTIPLIER = 2.5
MAX_BODY_PCT = 0.003
ENTRY_HOURS = {13, 14, 15, 16, 17}  # UTC
RR_RATIO = 2.0          # R:R target — take profit at 2R


class M1EthEngine(BaseEngine):
    ENGINE_ID = "m1_eth"

    def __init__(self) -> None:
        cfg = ENGINE_CONFIGS[self.ENGINE_ID]
        super().__init__(engine_id=self.ENGINE_ID, symbol=cfg.symbol)
        self._cfg = cfg
        self._hmm = RegimeClassifier(cfg.symbol)
        self._current_regime: Optional[str] = None
        self._last_4h_ts: Optional[datetime] = None
        self._ema_trend_dir: Optional[str] = None  # "LONG" | "SHORT" | None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, reader: Any) -> None:
        """Fetch last 500 4h candles and fit HMM."""
        try:
            candles_4h = await reader.get_latest_4h_candles(self.symbol, limit=500)
            fitted = self._hmm.fit(candles_4h)
            if fitted:
                logger.info("M1EthEngine: HMM fitted on %d 4h bars", len(candles_4h))
            else:
                logger.warning("M1EthEngine: HMM fit failed or skipped — will return None")
        except Exception as exc:
            logger.warning("M1EthEngine.initialize error: %s", exc)

    async def enrich_context(self, ctx: EngineContext, reader: Any) -> EngineContext:
        """Update 4h regime if a new 4h bar closed; fetch 60 15m candles."""
        bar_ts = ctx.bar_timestamp

        # Check if a new 4h bar closed since last update
        current_4h_open = _bar_open_at(bar_ts, 240)
        if self._last_4h_ts is None or current_4h_open > self._last_4h_ts:
            try:
                candles_4h = await reader.get_latest_4h_candles(
                    self.symbol, limit=150, before=bar_ts
                )
                if candles_4h:
                    regime = self._hmm.current_regime(candles_4h)
                    self._current_regime = regime
                    logger.debug("M1EthEngine: 4h regime updated → %s", regime)
                    # EMA20/50 fallback trend direction (used when HMM != REGIME_REQUIRED)
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
                logger.warning("M1EthEngine.enrich_context 4h update error: %s", exc)

        # Fetch 15m candles
        candles_15m: List[Any] = []
        try:
            candles_15m = await reader.get_latest_tf_candles(
                self.symbol, tf_minutes=15, limit=60, before=bar_ts
            )
        except Exception as exc:
            logger.warning("M1EthEngine.enrich_context 15m fetch error: %s", exc)

        ctx.extra["regime_4h"] = self._current_regime
        ctx.extra["ema_trend_dir"] = self._ema_trend_dir
        ctx.extra["candles_15m"] = candles_15m
        return ctx

    # ------------------------------------------------------------------
    # BaseEngine interface
    # ------------------------------------------------------------------

    def generate_signal(self, ctx: EngineContext) -> Optional[Signal]:
        self.clear_skip_reason(ctx)
        # 1. Regime filter — allow entry when HMM says "Trend / drift" OR when EMA20/50
        #    trend direction confirms the breakout direction (fallback for ambiguous HMM states).
        regime = ctx.extra.get("regime_4h")
        ema_trend = ctx.extra.get("ema_trend_dir")
        regime_ok = regime == REGIME_REQUIRED
        # EMA fallback is evaluated lazily — only if regime doesn't match
        if not regime_ok and ema_trend is None:
            self.set_skip_reason(ctx, "REGIME_MISMATCH", required=REGIME_REQUIRED, actual=regime, ema_trend=ema_trend)
            return None
        # If using EMA fallback, direction filtering happens at step 9 (breakout direction check)
        logger.debug("M1EthEngine: regime=%s regime_ok=%s ema_trend=%s", regime, regime_ok, ema_trend)

        # 2. Entry hour filter
        if ctx.bar_timestamp.hour not in ENTRY_HOURS:
            self.set_skip_reason(ctx, "OUTSIDE_ENTRY_WINDOW", hour=ctx.bar_timestamp.hour)
            return None

        # 3. Need enough 15m candles
        candles_15m = ctx.extra.get("candles_15m", [])
        if len(candles_15m) < ATR_LOOKBACK + COMPRESSION_BARS:
            self.set_skip_reason(
                ctx,
                "INSUFFICIENT_15M_HISTORY",
                have=len(candles_15m),
                need=ATR_LOOKBACK + COMPRESSION_BARS,
            )
            return None

        # 4. Last 4 bars before current bar = compression zone
        comp_bars = candles_15m[-COMPRESSION_BARS:]

        # 5. ATRp35: 35th percentile of (H-L)/C over last ATR_LOOKBACK bars
        atr_bars = candles_15m[-ATR_LOOKBACK:]
        hl_ranges = sorted(
            (c.high - c.low) / c.close for c in atr_bars if c.close > 0
        )
        atr_idx = int(len(hl_ranges) * ATR_PERCENTILE)
        atrp35 = hl_ranges[atr_idx] if hl_ranges else 0.0

        # 6. Zone bounds
        ch = max(c.high for c in comp_bars)
        cl = min(c.low for c in comp_bars)
        cr = (ch - cl) / comp_bars[-1].close if comp_bars[-1].close > 0 else 0.0

        # 7. Compression width check
        if cr > atrp35 * WIDTH_MULTIPLIER:
            self.set_skip_reason(
                ctx,
                "COMPRESSION_TOO_WIDE",
                compression_width_pct=round(cr, 6),
                max_allowed_pct=round(atrp35 * WIDTH_MULTIPLIER, 6),
            )
            return None

        # 8. Body ratio check — no bar should have a large body
        for c in comp_bars:
            if c.close > 0 and abs(c.close - c.open) / c.close > MAX_BODY_PCT:
                self.set_skip_reason(ctx, "LARGE_BODY_IN_COMPRESSION")
                return None

        # 9. Breakout direction
        if ctx.bar_close > ch:
            direction = Direction.LONG
        elif ctx.bar_close < cl:
            direction = Direction.SHORT
        else:
            self.set_skip_reason(
                ctx,
                "NO_BREAKOUT",
                bar_close=round(ctx.bar_close, 6),
                compression_high=round(ch, 6),
                compression_low=round(cl, 6),
            )
            return None

        # When using EMA fallback (HMM not "Trend / drift"), only trade in the EMA trend direction
        if not regime_ok:
            if ema_trend != direction.value:
                self.set_skip_reason(
                    ctx,
                    "EMA_TREND_MISMATCH",
                    breakout=direction.value,
                    ema_trend=ema_trend,
                )
                return None

        return Signal(
            engine_id=self.ENGINE_ID,
            symbol=self.symbol,
            bar_timestamp=ctx.bar_timestamp,
            direction=direction,
            timeframe="15m",
            signal_data={
                "setup_label": "compression_breakout",
                "regime_label": regime if regime_ok else f"EMA_FALLBACK:{ema_trend}",
                "compression_high": ch,
                "compression_low": cl,
                "atrp35": atrp35,
                "compression_width_pct": cr,
                "bar_close": ctx.bar_close,
            },
        )

    def validate_signal(
        self, signal: Signal, ctx: EngineContext
    ) -> SignalDecision:
        return SignalDecision(
            signal_id=None,
            action=SignalAction.ENTER,
            reason="BREAKOUT_CONFIRMED",
        )

    def build_order_plan(self, signal: Signal, bankroll: float) -> OrderPlan:
        entry_ref = signal.signal_data["bar_close"]
        ch = signal.signal_data["compression_high"]
        cl = signal.signal_data["compression_low"]

        if signal.direction == Direction.LONG:
            stop = cl
            R = entry_ref - stop
            target = entry_ref + RR_RATIO * R
        else:
            stop = ch
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
