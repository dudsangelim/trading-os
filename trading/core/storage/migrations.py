"""
DDL migrations for the tr_* schema.
All statements are idempotent — safe to run on every startup.
"""
from __future__ import annotations

import asyncpg

DDL_STATEMENTS = [
    # ------------------------------------------------------------------
    # tr_signals
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_signals (
        id              BIGSERIAL PRIMARY KEY,
        engine_id       TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        bar_timestamp   TIMESTAMPTZ NOT NULL,
        direction       TEXT NOT NULL,
        signal_data     JSONB NOT NULL,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (engine_id, bar_timestamp, direction)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_signals_engine ON tr_signals (engine_id, created_at DESC)",

    # ------------------------------------------------------------------
    # tr_signal_decisions
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_signal_decisions (
        id              BIGSERIAL PRIMARY KEY,
        signal_id       BIGINT REFERENCES tr_signals(id),
        action          TEXT NOT NULL,
        reason          TEXT,
        entry_price     NUMERIC(18,8),
        stop_price      NUMERIC(18,8),
        target_price    NUMERIC(18,8),
        stake_usd       NUMERIC(18,2),
        decided_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_decisions_signal ON tr_signal_decisions (signal_id)",

    # ------------------------------------------------------------------
    # tr_paper_trades
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_paper_trades (
        id              BIGSERIAL PRIMARY KEY,
        engine_id       TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        signal_id       BIGINT REFERENCES tr_signals(id),
        direction       TEXT NOT NULL,
        entry_price     NUMERIC(18,8) NOT NULL,
        exit_price      NUMERIC(18,8),
        stop_price      NUMERIC(18,8),
        target_price    NUMERIC(18,8),
        stake_usd       NUMERIC(18,2) NOT NULL,
        pnl_usd         NUMERIC(18,2),
        pnl_pct         NUMERIC(10,6),
        status          TEXT NOT NULL DEFAULT 'OPEN',
        opened_at       TIMESTAMPTZ DEFAULT NOW(),
        closed_at       TIMESTAMPTZ,
        close_reason    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_trades_engine ON tr_paper_trades (engine_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_tr_trades_open ON tr_paper_trades (status) WHERE status = 'OPEN'",

    # ------------------------------------------------------------------
    # tr_daily_equity
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_daily_equity (
        id              BIGSERIAL PRIMARY KEY,
        engine_id       TEXT NOT NULL,
        date_utc        DATE NOT NULL,
        starting_equity NUMERIC(18,2) NOT NULL,
        ending_equity   NUMERIC(18,2),
        pnl_usd         NUMERIC(18,2),
        trades_count    INT DEFAULT 0,
        UNIQUE (engine_id, date_utc)
    )
    """,

    # ------------------------------------------------------------------
    # tr_engine_heartbeat
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_engine_heartbeat (
        engine_id       TEXT PRIMARY KEY,
        last_run_at     TIMESTAMPTZ NOT NULL,
        last_bar_at     TIMESTAMPTZ,
        status          TEXT NOT NULL,
        detail          TEXT,
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ------------------------------------------------------------------
    # tr_engine_runtime_state
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_engine_runtime_state (
        engine_id           TEXT PRIMARY KEY,
        last_bar_seen       TIMESTAMPTZ,
        last_processed_at   TIMESTAMPTZ,
        status              TEXT NOT NULL,
        detail              TEXT,
        runtime_state       JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ------------------------------------------------------------------
    # tr_pending_setups
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_pending_setups (
        engine_id           TEXT PRIMARY KEY,
        setup_type          TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'ACTIVE',
        detail              TEXT,
        reference_bar_ts    TIMESTAMPTZ,
        expires_at          TIMESTAMPTZ,
        state_data          JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_pending_setups_status ON tr_pending_setups (status, updated_at DESC)",

    # ------------------------------------------------------------------
    # tr_runtime_pauses
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_runtime_pauses (
        pause_key           TEXT PRIMARY KEY,
        scope               TEXT NOT NULL,
        engine_id           TEXT,
        pause_type          TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'ACTIVE',
        reason              TEXT,
        started_at          TIMESTAMPTZ NOT NULL,
        observed_at         TIMESTAMPTZ,
        resumed_at          TIMESTAMPTZ,
        payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_runtime_pauses_status ON tr_runtime_pauses (status, updated_at DESC)",

    # ------------------------------------------------------------------
    # tr_worker_runtime_state
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_worker_runtime_state (
        worker_id               TEXT PRIMARY KEY,
        last_loop_started_at    TIMESTAMPTZ,
        last_loop_completed_at  TIMESTAMPTZ,
        last_freshness_check_at TIMESTAMPTZ,
        status                  TEXT NOT NULL,
        detail                  TEXT,
        runtime_state           JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at              TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # ------------------------------------------------------------------
    # tr_risk_events
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_risk_events (
        id              BIGSERIAL PRIMARY KEY,
        engine_id       TEXT,
        event_type      TEXT NOT NULL,
        description     TEXT,
        payload         JSONB,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_risk_events ON tr_risk_events (created_at DESC)",

    # ------------------------------------------------------------------
    # tr_skip_events
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_skip_events (
        id              BIGSERIAL PRIMARY KEY,
        engine_id       TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        bar_timestamp   TIMESTAMPTZ NOT NULL,
        reason          TEXT NOT NULL,
        details         JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_skip_events_engine_created ON tr_skip_events (engine_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tr_skip_events_reason_created ON tr_skip_events (reason, created_at DESC)",

    # ------------------------------------------------------------------
    # tr_candles_4h  — historical 4h OHLCV from Binance backfill
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_candles_4h (
        id          BIGSERIAL PRIMARY KEY,
        symbol      TEXT NOT NULL,
        open_time   TIMESTAMPTZ NOT NULL,
        open        NUMERIC(18,8) NOT NULL,
        high        NUMERIC(18,8) NOT NULL,
        low         NUMERIC(18,8) NOT NULL,
        close       NUMERIC(18,8) NOT NULL,
        volume      NUMERIC(18,8) NOT NULL,
        UNIQUE (symbol, open_time)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_candles_4h ON tr_candles_4h (symbol, open_time DESC)",

    # ------------------------------------------------------------------
    # tr_liquidity_snapshots
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_liquidity_snapshots (
        id              BIGSERIAL PRIMARY KEY,
        symbol          TEXT NOT NULL,
        tf              TEXT NOT NULL,
        snapshot_ts     TIMESTAMPTZ NOT NULL,
        source          TEXT NOT NULL DEFAULT 'binance',
        zones           JSONB NOT NULL,
        features        JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (symbol, tf, snapshot_ts, source)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_liquidity_snapshots_symbol_ts ON tr_liquidity_snapshots (symbol, snapshot_ts DESC)",

    # ------------------------------------------------------------------
    # tr_overlay_decisions
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_overlay_decisions (
        id                  BIGSERIAL PRIMARY KEY,
        signal_id           BIGINT NOT NULL REFERENCES tr_signals(id),
        engine_id           TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        overlay_name        TEXT NOT NULL DEFAULT 'binance_liquidity_v1',
        action              TEXT NOT NULL,
        comparison_label    TEXT NOT NULL,
        reason              TEXT,
        entry_price         NUMERIC(18,8),
        stop_price          NUMERIC(18,8),
        target_price        NUMERIC(18,8),
        stake_usd           NUMERIC(18,2),
        trigger_bar_ts      TIMESTAMPTZ,
        features            JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (signal_id, overlay_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_overlay_decisions_signal ON tr_overlay_decisions (signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_tr_overlay_decisions_engine_created ON tr_overlay_decisions (engine_id, created_at DESC)",

    # ------------------------------------------------------------------
    # tr_ghost_trades
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_ghost_trades (
        id                  BIGSERIAL PRIMARY KEY,
        overlay_decision_id BIGINT NOT NULL REFERENCES tr_overlay_decisions(id),
        signal_id           BIGINT NOT NULL REFERENCES tr_signals(id),
        base_trade_id       BIGINT REFERENCES tr_paper_trades(id),
        engine_id           TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        direction           TEXT NOT NULL,
        entry_price         NUMERIC(18,8) NOT NULL,
        exit_price          NUMERIC(18,8),
        stop_price          NUMERIC(18,8),
        target_price        NUMERIC(18,8),
        stake_usd           NUMERIC(18,2) NOT NULL,
        pnl_usd             NUMERIC(18,2),
        pnl_pct             NUMERIC(10,6),
        status              TEXT NOT NULL DEFAULT 'OPEN',
        opened_at           TIMESTAMPTZ NOT NULL,
        closed_at           TIMESTAMPTZ,
        close_reason        TEXT,
        created_at          TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_ghost_trades_signal ON tr_ghost_trades (signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_tr_ghost_trades_open ON tr_ghost_trades (status) WHERE status = 'OPEN'",
    "CREATE INDEX IF NOT EXISTS idx_tr_ghost_trades_engine_opened ON tr_ghost_trades (engine_id, opened_at DESC)",

    # ------------------------------------------------------------------
    # tr_state_transitions — append-only audit log of status changes
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_state_transitions (
        id              BIGSERIAL PRIMARY KEY,
        entity_type     TEXT NOT NULL,
        entity_id       TEXT NOT NULL,
        from_status     TEXT,
        to_status       TEXT NOT NULL,
        detail          TEXT,
        payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
        recorded_at     TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_state_transitions_entity ON tr_state_transitions (entity_type, entity_id, recorded_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tr_state_transitions_recent ON tr_state_transitions (recorded_at DESC)",

    # ------------------------------------------------------------------
    # tr_trade_comparisons
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tr_trade_comparisons (
        id                  BIGSERIAL PRIMARY KEY,
        signal_id           BIGINT NOT NULL REFERENCES tr_signals(id),
        base_trade_id       BIGINT REFERENCES tr_paper_trades(id),
        ghost_trade_id      BIGINT REFERENCES tr_ghost_trades(id),
        overlay_decision_id BIGINT NOT NULL REFERENCES tr_overlay_decisions(id),
        engine_id           TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        comparison_label    TEXT NOT NULL,
        entry_delta_bps     NUMERIC(10,4),
        stop_delta_bps      NUMERIC(10,4),
        target_delta_bps    NUMERIC(10,4),
        pnl_delta_usd       NUMERIC(18,2),
        pnl_delta_pct       NUMERIC(10,6),
        base_close_reason   TEXT,
        ghost_close_reason  TEXT,
        summary             JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (signal_id, overlay_decision_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tr_trade_comparisons_engine_created ON tr_trade_comparisons (engine_id, created_at DESC)",
]


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Execute all DDL statements inside a single transaction."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            for stmt in DDL_STATEMENTS:
                await conn.execute(stmt.strip())
