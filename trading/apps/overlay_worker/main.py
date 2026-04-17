"""
overlay_worker — parallel liquidity layer for ghost-trade comparison.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Any

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from trading.core.config.settings import (
    DATABASE_URL,
    DERIVATIVES_DATABASE_URL,
    DERIVATIVES_ENABLED,
    LIQUIDITY_AGGREGATOR_ENABLED,
    LIQUIDITY_PROVIDER_EQUAL_LEVELS_ENABLED,
    LIQUIDITY_PROVIDER_FVG_ENABLED,
    LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED,
    LIQUIDITY_PROVIDER_PRIOR_LEVELS_ENABLED,
    LIQUIDITY_PROVIDER_SWING_ENABLED,
    WORKER_LOOP_INTERVAL_SECONDS,
)
from trading.core.data.candle_reader import CandleReader
from trading.core.monitoring.structured_log import log
from trading.core.monitoring.telegram_alerts import fmt_overlay_divergence, send_alert
from trading.core.storage.migrations import run_migrations
from trading.core.storage.repository import TradingRepository
from trading.core.liquidity.binance_liquidity_reader import BinanceLiquidityReader
from trading.core.liquidity.overlay_evaluator import LiquidityOverlayEvaluator
from trading.core.liquidity.ghost_trade_manager import GhostTradeManager
from trading.core.comparison.delta_metrics import build_trade_comparison

logging.basicConfig(level=logging.INFO, format="%(message)s")

_shutdown = False


def _on_sigterm(*_: Any) -> None:
    global _shutdown
    _shutdown = True
    log("overlay_worker", "shutdown_requested", reason="SIGTERM")


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigterm)


async def _init_conn(conn: asyncpg.Connection) -> None:
    import json as _json
    await conn.set_type_codec("jsonb", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog")


async def main() -> None:
    log("overlay_worker", "startup_begin")
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, init=_init_conn)
    await run_migrations(pool)

    derivatives_pool = None
    if DERIVATIVES_ENABLED and DERIVATIVES_DATABASE_URL:
        try:
            derivatives_pool = await asyncpg.create_pool(
                DERIVATIVES_DATABASE_URL, min_size=1, max_size=2, init=_init_conn
            )
        except Exception as exc:
            log("overlay_worker", "derivatives_pool_unavailable", error=str(exc))

    repo = TradingRepository(pool, derivatives_pool=derivatives_pool)
    candle_reader = CandleReader(pool)

    aggregator = None
    if LIQUIDITY_AGGREGATOR_ENABLED:
        from trading.core.liquidity.zone_aggregator import LiquidityZoneAggregator
        from trading.core.liquidity.providers import (
            EqualLevelsProvider,
            FairValueGapProvider,
            LiquidationHeatmapProvider,
            PriorLevelsProvider,
            SwingProvider,
        )
        sync_providers = []
        if LIQUIDITY_PROVIDER_EQUAL_LEVELS_ENABLED:
            sync_providers.append(EqualLevelsProvider().detect_zones)
        if LIQUIDITY_PROVIDER_SWING_ENABLED:
            sync_providers.append(SwingProvider().detect_zones)
        if LIQUIDITY_PROVIDER_FVG_ENABLED:
            sync_providers.append(FairValueGapProvider().detect_zones)
        if LIQUIDITY_PROVIDER_PRIOR_LEVELS_ENABLED:
            sync_providers.append(PriorLevelsProvider().detect_zones)
        async_providers = []
        if LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED:
            async_providers.append(LiquidationHeatmapProvider(repo).detect_zones)
        aggregator = LiquidityZoneAggregator(sync_providers, async_providers)

    liquidity_reader = BinanceLiquidityReader(candle_reader, aggregator=aggregator)
    evaluator = LiquidityOverlayEvaluator()
    ghost_manager = GhostTradeManager(repo, candle_reader)

    log("overlay_worker", "startup_complete", overlay=evaluator.overlay_name)

    while not _shutdown:
        loop_start = datetime.now(timezone.utc)
        processed = 0

        pending_signals = await repo.get_pending_overlay_signals(evaluator.overlay_name, limit=100)
        for signal_row in pending_signals:
            signal_row = _normalize_signal_row(signal_row)
            base_decision = await repo.get_signal_decision(int(signal_row["id"]))
            base_trade = await repo.get_trade_by_signal_id(int(signal_row["id"]))
            bankroll = await repo.get_current_bankroll(signal_row["engine_id"])

            snapshot = await liquidity_reader.snapshot_for_signal(
                signal_row["symbol"],
                signal_row["bar_timestamp"],
                tf_minutes=_tf_minutes(signal_row),
            )
            await repo.insert_liquidity_snapshot(
                symbol=signal_row["symbol"],
                tf=snapshot.timeframe,
                snapshot_ts=signal_row["bar_timestamp"],
                zones=snapshot.zones,
                features=snapshot.features,
            )

            evaluations = evaluator.evaluate(signal_row, base_decision, base_trade, snapshot, bankroll)
            primary = evaluations[0]  # baseline — used for overlay_decision record

            overlay_decision_id = await repo.insert_overlay_decision(
                signal_id=int(signal_row["id"]),
                engine_id=signal_row["engine_id"],
                symbol=signal_row["symbol"],
                overlay_name=evaluator.overlay_name,
                action=primary.action,
                comparison_label=primary.comparison_label,
                reason=primary.reason,
                entry_price=primary.entry_price,
                stop_price=primary.stop_price,
                target_price=primary.target_price,
                stake_usd=primary.stake_usd,
                trigger_bar_ts=primary.trigger_bar_ts,
                features=primary.features,
            )

            # Open ghost trades for each variant (1 or 3 depending on flag)
            primary_ghost_id = None
            for eval_decision in evaluations:
                ghost_id = await ghost_manager.maybe_open_ghost_trade(
                    overlay_decision_id=overlay_decision_id,
                    signal_row=signal_row,
                    overlay_decision={
                        "action": eval_decision.action,
                        "entry_price": eval_decision.entry_price,
                        "stop_price": eval_decision.stop_price,
                        "target_price": eval_decision.target_price,
                        "stake_usd": eval_decision.stake_usd,
                    },
                    base_trade=base_trade,
                    variant_label=eval_decision.variant_label,
                )
                if ghost_id is not None and eval_decision.variant_label == "legacy":
                    primary_ghost_id = ghost_id

            # Backward-compatible comparison uses baseline (legacy) ghost trade
            ghost_trade = None
            if primary_ghost_id is not None:
                for row in await repo.get_ghost_trades(engine_id=signal_row["engine_id"], limit=500):
                    if int(row["id"]) == primary_ghost_id:
                        ghost_trade = row
                        break

            comparison = build_trade_comparison(
                signal_row=signal_row,
                overlay_decision_id=overlay_decision_id,
                overlay_decision={
                    "action": primary.action,
                    "comparison_label": primary.comparison_label,
                    "reason": primary.reason,
                },
                base_trade=base_trade,
                ghost_trade=ghost_trade,
            )
            await repo.insert_trade_comparison(**comparison)
            base_action = str((base_decision or {}).get("action") or ("ENTER" if base_trade else "UNKNOWN"))
            if primary.action == "SKIP" or primary.comparison_label != "same":
                await send_alert(
                    fmt_overlay_divergence(
                        signal_row["engine_id"],
                        signal_row["symbol"],
                        base_action=base_action,
                        overlay_action=primary.action,
                        comparison_label=primary.comparison_label,
                        reason=primary.reason,
                    )
                )
            processed += 1
            log(
                "overlay_worker",
                "overlay_evaluated",
                signal_id=signal_row["id"],
                engine_id=signal_row["engine_id"],
                action=primary.action,
                comparison_label=primary.comparison_label,
                variants=len(evaluations),
            )

        closed_ghosts = await ghost_manager.process_open_ghost_trades()
        reconciled = await _reconcile_recent_comparisons(repo, evaluator.overlay_name)

        elapsed = (datetime.now(timezone.utc) - loop_start).total_seconds()
        sleep_for = max(0.0, WORKER_LOOP_INTERVAL_SECONDS - elapsed)
        log(
            "overlay_worker",
            "loop_complete",
            processed=processed,
            closed_ghosts=closed_ghosts,
            reconciled=reconciled,
            elapsed_s=round(elapsed, 2),
            sleep_s=round(sleep_for, 2),
        )
        await asyncio.sleep(sleep_for)

    await pool.close()
    log("overlay_worker", "shutdown_complete")


def _tf_minutes(signal_row: dict[str, Any]) -> int:
    sd = signal_row.get("signal_data") or {}
    if isinstance(sd, str):
        try:
            sd = json.loads(sd)
        except (json.JSONDecodeError, TypeError):
            sd = {}
    if not isinstance(sd, dict):
        sd = {}
    tf = sd.get("timeframe", "15m")
    try:
        return int(str(tf).replace("m", ""))
    except ValueError:
        return 15


async def _reconcile_recent_comparisons(repo: TradingRepository, overlay_name: str) -> int:
    count = 0
    recent = await repo.get_overlay_decisions(limit=200)
    for overlay_decision in recent:
        if overlay_decision.get("overlay_name") != overlay_name:
            continue
        signal_id = int(overlay_decision["signal_id"])
        signal_row = await repo.get_signal_by_id(signal_id)
        if signal_row is None:
            continue
        signal_row = _normalize_signal_row(signal_row)
        base_trade = await repo.get_trade_by_signal_id(signal_id)
        ghost_trade = None
        for row in await repo.get_ghost_trades(engine_id=signal_row["engine_id"], limit=500):
            if int(row["signal_id"]) == signal_id:
                ghost_trade = row
                break
        comparison = build_trade_comparison(
            signal_row=signal_row,
            overlay_decision_id=int(overlay_decision["id"]),
            overlay_decision=overlay_decision,
            base_trade=base_trade,
            ghost_trade=ghost_trade,
        )
        await repo.insert_trade_comparison(**comparison)
        count += 1
    return count


def _normalize_signal_row(signal_row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(signal_row)
    signal_data = normalized.get("signal_data")
    if isinstance(signal_data, str):
        try:
            normalized["signal_data"] = json.loads(signal_data)
        except json.JSONDecodeError:
            normalized["signal_data"] = {}
    elif signal_data is None:
        normalized["signal_data"] = {}
    return normalized


if __name__ == "__main__":
    asyncio.run(main())
