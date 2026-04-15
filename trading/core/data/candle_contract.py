"""
Candle input contract — formal specification of the interface between
candle-collector (el_candles_1m) and Trading Core.

This module is the authoritative source of truth for:
  - the schema expected from el_candles_1m
  - freshness requirements per symbol
  - minimum coverage requirements per symbol
  - the behavior when input is absent, stale, or degraded

Nothing here changes the reader or the circuit breakers.
It formalises what was previously implicit in CandleReader and healthcheck.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

from ..config.settings import ENGINE_CONFIGS, STALE_DATA_THRESHOLD_MINUTES

if TYPE_CHECKING:
    from .candle_reader import CandleReader


# ---------------------------------------------------------------------------
# Contract constants — schema
# ---------------------------------------------------------------------------

# Table owned by candle-collector; Trading Core reads but never writes to it.
CANDLE_SOURCE_TABLE: str = "el_candles_1m"

# Columns expected by Trading Core. Any missing column breaks ingestion.
CANDLE_COLUMNS: tuple = ("symbol", "open_time", "open", "high", "low", "close", "volume")

# All timestamps in the table are UTC (timezone-aware).
CANDLE_TIMESTAMP_TZ: str = "UTC"

# open_time is the bar OPEN timestamp; the bar covers [open_time, open_time + 1 min).
CANDLE_TIMEFRAME_MINUTES: int = 1


# ---------------------------------------------------------------------------
# Contract constants — freshness
# ---------------------------------------------------------------------------

# A symbol is considered fresh if its newest candle is no older than this.
# Must stay in sync with STALE_DATA_THRESHOLD_MINUTES in settings.
FRESHNESS_THRESHOLD_MINUTES: int = STALE_DATA_THRESHOLD_MINUTES

# Seconds equivalent (precomputed for comparison).
FRESHNESS_THRESHOLD_SECONDS: int = FRESHNESS_THRESHOLD_MINUTES * 60


# ---------------------------------------------------------------------------
# Contract constants — coverage
# ---------------------------------------------------------------------------

# Rolling window over which coverage is assessed.
COVERAGE_WINDOW_MINUTES: int = 15

# Minimum number of 1m candles expected within that window.
# Below this, the symbol is "degraded" but not "stale" — trading may still proceed
# with degraded confidence, but should be logged.
MINIMUM_COVERAGE_IN_WINDOW: int = 10  # out of ~15 possible in a 15-minute window


# ---------------------------------------------------------------------------
# Contract constants — required symbols
# ---------------------------------------------------------------------------

# Symbols the trading core currently requires — derived from ENGINE_CONFIGS.
# Any symbol absent here is explicitly not required by the core.
REQUIRED_SYMBOLS: List[str] = sorted({cfg.symbol for cfg in ENGINE_CONFIGS.values()})


# ---------------------------------------------------------------------------
# Degraded-input policy
# ---------------------------------------------------------------------------

class DegradedInputPolicy(str, Enum):
    """
    What Trading Core does when a symbol's input is degraded (fresh but sparse).
    Trading Core currently always CONTINUES on degraded input — this enum
    documents that decision explicitly so it can be changed without hunting code.
    """
    CONTINUE = "continue"       # proceed; log; advisory only
    BLOCK = "block"             # treat as stale; halt the engine


# Current policy for degraded input is advisory-only.
DEGRADED_INPUT_POLICY: DegradedInputPolicy = DegradedInputPolicy.CONTINUE


# ---------------------------------------------------------------------------
# Input quality per symbol
# ---------------------------------------------------------------------------

class InputQuality(str, Enum):
    """
    Observed quality of candle input for one symbol.

    FRESH    — last candle within freshness threshold AND coverage >= minimum.
    DEGRADED — last candle within threshold but coverage below minimum.
               Trading proceeds; alert is advisory.
    STALE    — last candle older than freshness threshold.
               Trading Core must block all engines for this symbol.
    ABSENT   — no candles found for this symbol at all.
               Trading Core must block all engines for this symbol.
    """
    FRESH    = "fresh"
    DEGRADED = "degraded"
    STALE    = "stale"
    ABSENT   = "absent"


def quality_blocks_trading(quality: InputQuality) -> bool:
    """Return True if this quality level should block trading."""
    return quality in (InputQuality.STALE, InputQuality.ABSENT)


# ---------------------------------------------------------------------------
# Observed status per symbol
# ---------------------------------------------------------------------------

@dataclass
class SymbolInputStatus:
    """Observed candle input health for a single required symbol."""
    symbol: str
    last_candle_at: Optional[datetime]
    age_seconds: Optional[float]
    candles_in_window: int
    quality: InputQuality
    freshness_threshold_minutes: int = field(default=FRESHNESS_THRESHOLD_MINUTES)
    coverage_window_minutes: int = field(default=COVERAGE_WINDOW_MINUTES)
    minimum_coverage: int = field(default=MINIMUM_COVERAGE_IN_WINDOW)
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Aggregate validation result
# ---------------------------------------------------------------------------

@dataclass
class CandleInputValidation:
    """
    Aggregate validation result for all required candle inputs.

    ``valid`` is True only when every required symbol is FRESH or DEGRADED
    (i.e. no symbol is STALE or ABSENT).  When ``valid`` is False, trading
    must be suspended until input recovers.

    ``any_degraded`` is advisory — it does not set ``valid = False``.
    """
    valid: bool
    any_stale: bool
    any_absent: bool
    any_degraded: bool
    symbols: List[SymbolInputStatus]
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

async def validate_candle_inputs(
    reader: "CandleReader",
    symbols: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> CandleInputValidation:
    """
    Validate freshness and coverage for all required candle inputs.

    Checks each symbol for:
      1. Freshness — is the newest candle within FRESHNESS_THRESHOLD_MINUTES?
      2. Coverage — are there enough recent candles in the rolling window?

    Args:
        reader:  CandleReader instance backed by el_candles_1m.
        symbols: Symbols to check; defaults to REQUIRED_SYMBOLS.
        now:     Reference time; defaults to UTC now.

    Returns:
        CandleInputValidation with per-symbol quality and aggregate flags.
    """
    check_symbols: List[str] = sorted(symbols) if symbols else REQUIRED_SYMBOLS
    ref_time: datetime = now or datetime.now(timezone.utc)
    freshness_cutoff: datetime = ref_time - timedelta(minutes=FRESHNESS_THRESHOLD_MINUTES)
    coverage_cutoff: datetime = ref_time - timedelta(minutes=COVERAGE_WINDOW_MINUTES)

    symbol_statuses: List[SymbolInputStatus] = []
    any_stale = False
    any_absent = False
    any_degraded = False

    for symbol in check_symbols:
        last_at: Optional[datetime] = await reader.get_last_candle_time(symbol)

        if last_at is None:
            quality = InputQuality.ABSENT
            age: Optional[float] = None
            in_window = 0
            any_absent = True
            detail = "no_candles_in_table"
        else:
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            age = round(max(0.0, (ref_time - last_at).total_seconds()), 3)

            if last_at < freshness_cutoff:
                quality = InputQuality.STALE
                in_window = 0
                any_stale = True
                detail = f"last_candle_age_seconds={age}"
            else:
                # Check rolling coverage — how many candles in the last window?
                recent = await reader.get_latest_candles(
                    symbol,
                    limit=COVERAGE_WINDOW_MINUTES + 5,
                    before=ref_time,
                )
                in_window = sum(
                    1 for c in recent
                    if c.open_time >= coverage_cutoff
                )
                if in_window < MINIMUM_COVERAGE_IN_WINDOW:
                    quality = InputQuality.DEGRADED
                    any_degraded = True
                    detail = f"coverage={in_window}/{MINIMUM_COVERAGE_IN_WINDOW}"
                else:
                    quality = InputQuality.FRESH
                    detail = None

        symbol_statuses.append(SymbolInputStatus(
            symbol=symbol,
            last_candle_at=last_at,
            age_seconds=age,
            candles_in_window=in_window,
            quality=quality,
            detail=detail,
        ))

    # valid = no blocking quality; degraded is advisory only
    valid = not any_stale and not any_absent

    summary_detail: Optional[str] = None
    if any_absent:
        missing = [s.symbol for s in symbol_statuses if s.quality == InputQuality.ABSENT]
        summary_detail = f"absent_symbols={missing}"
    elif any_stale:
        stale = [s.symbol for s in symbol_statuses if s.quality == InputQuality.STALE]
        summary_detail = f"stale_symbols={stale}"
    elif any_degraded:
        degraded = [s.symbol for s in symbol_statuses if s.quality == InputQuality.DEGRADED]
        summary_detail = f"degraded_symbols={degraded}"

    return CandleInputValidation(
        valid=valid,
        any_stale=any_stale,
        any_absent=any_absent,
        any_degraded=any_degraded,
        symbols=symbol_statuses,
        detail=summary_detail,
    )
