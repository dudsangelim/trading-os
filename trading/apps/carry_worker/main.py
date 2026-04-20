"""
carry_worker — autonomous loop for the carry_neutral_btc engine.

Loop (every CARRY_WORKER_LOOP_INTERVAL_SECONDS, default 300s):
  1. Skip if CARRY_NEUTRAL_ENABLED is False
  2. Read latest funding rate from derivatives DB (binance_funding_rate table)
  3. Build CarryContext (funding rate, spot/perp prices, open position)
  4. Call engine.evaluate_carry()
  5. Act on decision:
     - OPEN_CARRY → simulate open via CarryFillModel, insert into tr_paper_trades
     - CLOSE_CARRY → simulate close, update tr_paper_trades
     - HOLD with open position → check for new funding event (8h elapsed), accrue funding
     - SKIP → heartbeat + log
  6. Update heartbeat

Graceful shutdown on SIGTERM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from trading.core.config.settings import (
    CARRY_NEUTRAL_ENABLED,
    CARRY_NEUTRAL_MAX_HOLD_HOURS,
    CARRY_NEUTRAL_STAKE_USD,
    CARRY_NEUTRAL_THRESHOLD_LOWER_ANNUALIZED,
    CARRY_NEUTRAL_THRESHOLD_UPPER_ANNUALIZED,
    CARRY_NEUTRAL_VARIANT,
    CARRY_WORKER_LOOP_INTERVAL_SECONDS,
    DATABASE_URL,
    DERIVATIVES_DATABASE_URL,
    DERIVATIVES_ENABLED,
    DERIVATIVES_FEATURE_ADAPTER_ENABLED,
)
from trading.core.engines.carry_neutral_btc import (
    CarryAction,
    CarryContext,
    CarryNeutralBtcEngine,
)
from trading.core.execution.carry_fill_model import CarryFillModel, CarryPosition
from trading.core.monitoring.structured_log import log
from trading.core.monitoring.telegram_alerts import send_alert
from trading.core.storage.migrations import run_migrations
from trading.core.storage.repository import TradingRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")

WORKER_ID = "carry_worker"
ENGINE_ID = "carry_neutral_btc"
SYMBOL = "BTCUSDT"
FUNDING_CYCLE_HOURS = 8

_shutdown = False
_engine = CarryNeutralBtcEngine()
_fill_model = CarryFillModel()
_last_funding_event_at: Optional[datetime] = None


def _on_sigterm(*_: Any) -> None:
    global _shutdown
    _shutdown = True
    log(WORKER_ID, "shutdown_requested", reason="SIGTERM")


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigterm)


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def _get_funding_rate(derivatives_pool: Optional[asyncpg.Pool]) -> Optional[float]:
    """Query latest annualized funding rate for BTCUSDT from derivatives DB."""
    if derivatives_pool is None:
        return None
    try:
        async with derivatives_pool.acquire() as conn:
            # binance_funding_rate stores rate per 8h as a decimal fraction
            row = await conn.fetchrow(
                """
                SELECT funding_rate, funding_time
                FROM binance_funding_rate
                WHERE symbol = 'BTCUSDT'
                ORDER BY funding_time DESC
                LIMIT 1
                """
            )
        if row is None:
            return None
        rate_per_8h = float(row["funding_rate"])
        # Annualize: 3 events/day × 365 days
        return rate_per_8h * 3.0 * 365.0
    except Exception as exc:
        log(WORKER_ID, "funding_rate_query_error", error=str(exc))
        return None


async def _get_funding_history_24h(derivatives_pool: Optional[asyncpg.Pool]) -> list:
    """Return last 3 funding rates (24h history) annualized."""
    if derivatives_pool is None:
        return []
    try:
        async with derivatives_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT funding_rate
                FROM binance_funding_rate
                WHERE symbol = 'BTCUSDT'
                ORDER BY funding_time DESC
                LIMIT 3
                """
            )
        return [float(r["funding_rate"]) * 3.0 * 365.0 for r in rows]
    except Exception:
        return []


async def _get_spot_price(pool: asyncpg.Pool) -> Optional[float]:
    """Get latest BTC spot price from candles."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT close FROM el_candles_1m
                WHERE symbol = 'BTCUSDT'
                ORDER BY open_time DESC
                LIMIT 1
                """
            )
        return float(row["close"]) if row else None
    except Exception as exc:
        log(WORKER_ID, "spot_price_query_error", error=str(exc))
        return None


async def _get_perp_price(derivatives_pool: Optional[asyncpg.Pool], spot_price: float) -> float:
    """Get BTC perp price. Falls back to spot_price if unavailable."""
    if derivatives_pool is None:
        return spot_price
    try:
        async with derivatives_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT mark_price FROM binance_mark_price
                WHERE symbol = 'BTCUSDT'
                ORDER BY timestamp DESC
                LIMIT 1
                """
            )
        if row and row["mark_price"]:
            return float(row["mark_price"])
    except Exception:
        pass
    return spot_price


def _should_process_funding_event(open_position: Optional[Dict[str, Any]]) -> bool:
    """Return True if a new 8h funding cycle has elapsed since last event."""
    global _last_funding_event_at
    if open_position is None:
        return False
    now = datetime.now(timezone.utc)
    if _last_funding_event_at is None:
        # Derive from position open time
        opened_at = open_position.get("opened_at")
        if opened_at is None:
            return False
        events_count = open_position.get("funding_events_count") or 0
        expected_next = opened_at + timedelta(hours=FUNDING_CYCLE_HOURS * (events_count + 1))
        return now >= expected_next
    else:
        return (now - _last_funding_event_at).total_seconds() >= FUNDING_CYCLE_HOURS * 3600


async def _emit_risk_event(
    repo: TradingRepository,
    event_type: str,
    description: str,
    payload: Dict[str, Any],
) -> None:
    try:
        await repo.insert_risk_event(event_type, ENGINE_ID, description, payload)
    except Exception as exc:
        log(WORKER_ID, "risk_event_error", error=str(exc))


async def _run_loop(
    repo: TradingRepository,
    pool: asyncpg.Pool,
    derivatives_pool: Optional[asyncpg.Pool],
) -> None:
    global _last_funding_event_at

    now = datetime.now(timezone.utc)

    if not CARRY_NEUTRAL_ENABLED:
        await repo.upsert_heartbeat(ENGINE_ID, "OK", detail="CARRY_NEUTRAL_DISABLED")
        log(WORKER_ID, "carry_disabled", msg="CARRY_NEUTRAL_ENABLED=False, skipping loop")
        return

    # Gather market data
    funding_rate = await _get_funding_rate(derivatives_pool)
    if funding_rate is None:
        log(WORKER_ID, "carry_skip", reason="NO_FUNDING_DATA")
        await repo.upsert_heartbeat(ENGINE_ID, "OK", detail="NO_FUNDING_DATA")
        await _emit_risk_event(repo, "CARRY_NEUTRAL_SKIP", "No funding rate data", {
            "reason": "NO_FUNDING_DATA", "timestamp": now.isoformat(),
        })
        return

    spot_price = await _get_spot_price(pool)
    if spot_price is None:
        log(WORKER_ID, "carry_skip", reason="NO_SPOT_PRICE")
        await repo.upsert_heartbeat(ENGINE_ID, "OK", detail="NO_SPOT_PRICE")
        return

    perp_price = await _get_perp_price(derivatives_pool, spot_price)
    funding_history = await _get_funding_history_24h(derivatives_pool)
    open_position = await repo.get_open_carry_position(ENGINE_ID)

    bankroll = CARRY_NEUTRAL_STAKE_USD  # carry uses fixed stake, not bankroll-based

    ctx = CarryContext(
        funding_rate_annualized=funding_rate,
        funding_rate_history_24h=funding_history,
        spot_price=spot_price,
        perp_price=perp_price,
        bankroll_usd=bankroll,
        open_position=open_position,
    )

    decision = _engine.evaluate_carry(
        ctx,
        threshold_upper=CARRY_NEUTRAL_THRESHOLD_UPPER_ANNUALIZED,
        threshold_lower=CARRY_NEUTRAL_THRESHOLD_LOWER_ANNUALIZED,
        max_hold_hours=CARRY_NEUTRAL_MAX_HOLD_HOURS,
    )

    log(
        WORKER_ID,
        "carry_evaluated",
        action=decision.action.value,
        reason=decision.reason,
        funding_rate_annualized=round(funding_rate, 4),
        has_position=open_position is not None,
    )

    if decision.action == CarryAction.OPEN_CARRY:
        fill = _fill_model.simulate_open(
            spot_price=spot_price,
            perp_price=perp_price,
            stake_usd=bankroll,
            slippage_bps=5.0,
            fee_bps=10.0,
        )
        trade_id = await repo.open_carry_trade(
            engine_id=ENGINE_ID,
            symbol=SYMBOL,
            stake_usd=bankroll,
            spot_entry_price=fill.spot_entry_price,
            perp_entry_price=fill.perp_entry_price,
            entry_fees_usd=fill.entry_fees_usd,
            variant=CARRY_NEUTRAL_VARIANT,
        )
        log(
            WORKER_ID,
            "carry_opened",
            trade_id=trade_id,
            spot_entry=round(fill.spot_entry_price, 2),
            perp_entry=round(fill.perp_entry_price, 2),
            stake_usd=bankroll,
            entry_fees=round(fill.entry_fees_usd, 4),
            funding_rate_annualized=round(funding_rate, 4),
            variant=CARRY_NEUTRAL_VARIANT,
        )
        await _emit_risk_event(repo, "CARRY_NEUTRAL_OPEN", "Carry position opened", {
            "trade_id": trade_id,
            "spot_entry_price": fill.spot_entry_price,
            "perp_entry_price": fill.perp_entry_price,
            "stake_usd": bankroll,
            "funding_rate_annualized": funding_rate,
            "variant": CARRY_NEUTRAL_VARIANT,
            "timestamp": now.isoformat(),
        })
        try:
            await send_alert(
                f"[carry_neutral_btc] CARRY OPENED | stake=${bankroll:.0f}"
                f" | funding={funding_rate*100:.1f}%/yr"
                f" | spot={spot_price:.0f}"
            )
        except Exception:
            pass
        await repo.upsert_heartbeat(ENGINE_ID, "OK", detail="CARRY_OPEN", last_bar_at=now)

    elif decision.action == CarryAction.CLOSE_CARRY:
        if open_position is not None:
            pos = CarryPosition(
                trade_id=open_position["id"],
                notional_usd=float(open_position["stake_usd"]),
                spot_entry_price=float(open_position.get("spot_entry_price") or spot_price),
                perp_entry_price=float(open_position.get("perp_entry_price") or perp_price),
                funding_accrued_usd=float(open_position.get("funding_accrued_usd") or 0.0),
                funding_events_count=int(open_position.get("funding_events_count") or 0),
                entry_fees_usd=abs(float(open_position.get("funding_accrued_usd") or 0.0))
                    if float(open_position.get("funding_accrued_usd") or 0.0) < 0 else 0.0,
                variant=open_position.get("carry_variant") or CARRY_NEUTRAL_VARIANT,
            )
            # Re-fetch actual entry fees from entry_price column
            pos.entry_fees_usd = float(open_position.get("entry_price") or 0.0)
            pos.funding_accrued_usd = float(open_position.get("funding_accrued_usd") or 0.0)

            close_fill = _fill_model.simulate_close(
                position=pos,
                current_spot=spot_price,
                current_perp=perp_price,
                slippage_bps=5.0,
                fee_bps=10.0,
            )
            await repo.close_carry_trade(
                trade_id=open_position["id"],
                spot_exit_price=close_fill.spot_exit_price,
                perp_exit_price=close_fill.perp_exit_price,
                pnl_usd=close_fill.pnl_usd,
                pnl_pct=close_fill.pnl_pct,
                close_reason=decision.reason,
                closed_at=now,
            )
            log(
                WORKER_ID,
                "carry_closed",
                trade_id=open_position["id"],
                reason=decision.reason,
                pnl_usd=close_fill.pnl_usd,
                pnl_pct=round(close_fill.pnl_pct * 100, 4),
                funding_events=pos.funding_events_count,
            )
            await _emit_risk_event(repo, "CARRY_NEUTRAL_CLOSE", "Carry position closed", {
                "trade_id": open_position["id"],
                "reason": decision.reason,
                "pnl_usd": close_fill.pnl_usd,
                "pnl_pct": close_fill.pnl_pct,
                "funding_events_count": pos.funding_events_count,
                "funding_accrued_usd": pos.funding_accrued_usd,
                "timestamp": now.isoformat(),
            })
            try:
                await send_alert(
                    f"[carry_neutral_btc] CARRY CLOSED | reason={decision.reason}"
                    f" | pnl=${close_fill.pnl_usd:.2f}"
                    f" | events={pos.funding_events_count}"
                )
            except Exception:
                pass
        await repo.upsert_heartbeat(ENGINE_ID, "OK", detail="CARRY_CLOSE", last_bar_at=now)

    elif decision.action == CarryAction.HOLD:
        # Check if a new funding event has elapsed
        if open_position is not None and _should_process_funding_event(open_position):
            pos = CarryPosition(
                trade_id=open_position["id"],
                notional_usd=float(open_position["stake_usd"]),
                spot_entry_price=float(open_position.get("spot_entry_price") or spot_price),
                perp_entry_price=float(open_position.get("perp_entry_price") or perp_price),
                funding_accrued_usd=float(open_position.get("funding_accrued_usd") or 0.0),
                funding_events_count=int(open_position.get("funding_events_count") or 0),
                entry_fees_usd=0.0,
                variant=open_position.get("carry_variant") or CARRY_NEUTRAL_VARIANT,
            )
            funding_fill = _fill_model.simulate_funding_event(
                position=pos,
                funding_rate_annualized=funding_rate,
                variant=pos.variant,
            )
            await repo.update_carry_funding(
                trade_id=open_position["id"],
                funding_accrued_usd=funding_fill.new_total_accrued_usd,
                funding_events_count=funding_fill.new_events_count,
            )
            _last_funding_event_at = now
            log(
                WORKER_ID,
                "carry_funding_accrued",
                trade_id=open_position["id"],
                funding_usd=round(funding_fill.funding_usd, 4),
                total_accrued=round(funding_fill.new_total_accrued_usd, 4),
                events_count=funding_fill.new_events_count,
            )
            await _emit_risk_event(repo, "CARRY_NEUTRAL_FUNDING_EVENT", "Funding event accrued", {
                "trade_id": open_position["id"],
                "funding_usd": funding_fill.funding_usd,
                "total_accrued_usd": funding_fill.new_total_accrued_usd,
                "events_count": funding_fill.new_events_count,
                "funding_rate_per_event": funding_fill.funding_rate_per_event,
                "timestamp": now.isoformat(),
            })
        await repo.upsert_heartbeat(ENGINE_ID, "OK", detail="CARRY_HOLD", last_bar_at=now)

    else:  # SKIP
        await _emit_risk_event(repo, "CARRY_NEUTRAL_SKIP", "Carry evaluation skipped", {
            "reason": decision.reason,
            "funding_rate_annualized": funding_rate,
            "timestamp": now.isoformat(),
        })
        await repo.upsert_heartbeat(ENGINE_ID, "OK", detail=f"SKIP:{decision.reason}")


async def main() -> None:
    log(WORKER_ID, "startup_begin", carry_enabled=CARRY_NEUTRAL_ENABLED)

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, init=_init_conn)

    derivatives_pool: Optional[asyncpg.Pool] = None
    if DERIVATIVES_ENABLED and DERIVATIVES_FEATURE_ADAPTER_ENABLED and DERIVATIVES_DATABASE_URL:
        try:
            derivatives_pool = await asyncpg.create_pool(
                DERIVATIVES_DATABASE_URL,
                min_size=1,
                max_size=3,
                init=_init_conn,
            )
        except Exception as exc:
            log(WORKER_ID, "derivatives_pool_unavailable", error=str(exc))

    await run_migrations(pool)
    repo = TradingRepository(pool, derivatives_pool=derivatives_pool)

    log(WORKER_ID, "startup_complete")

    while not _shutdown:
        loop_start = datetime.now(timezone.utc)
        try:
            await _run_loop(repo, pool, derivatives_pool)
        except Exception as exc:
            log(WORKER_ID, "loop_error", error=str(exc))
            try:
                await repo.insert_risk_event("CARRY_WORKER_ERROR", ENGINE_ID, str(exc), {
                    "timestamp": loop_start.isoformat(),
                })
            except Exception:
                pass
            try:
                await repo.upsert_heartbeat(ENGINE_ID, "DEGRADED", detail="LOOP_ERROR")
            except Exception:
                pass

        elapsed = (datetime.now(timezone.utc) - loop_start).total_seconds()
        sleep_for = max(0.0, CARRY_WORKER_LOOP_INTERVAL_SECONDS - elapsed)
        await asyncio.sleep(sleep_for)

    log(WORKER_ID, "shutdown_begin")
    if derivatives_pool:
        await derivatives_pool.close()
    await pool.close()
    log(WORKER_ID, "shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
