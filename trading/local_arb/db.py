"""Schema Postgres `local_arb` (não destrutivo) + persistência opcional.

Regras:
- `--init-db` só CRIA/GARANTE (CREATE SCHEMA/TABLE IF NOT EXISTS). Nada de
  DROP/TRUNCATE/ALTER destrutivo em lugar nenhum deste módulo.
- Sem DATABASE_URL (ou sem driver psycopg2 instalado) tudo degrada para modo
  no-db: as funções levantam `DbUnavailable` com mensagem legível e o CLI
  segue em dry-run com exit 0 — nunca crash.
"""

from __future__ import annotations

import json
import os

DSN_ENV_VARS = ("LOCAL_ARB_DATABASE_URL", "DATABASE_URL")

SCHEMA_STATEMENTS: list[str] = [
    "CREATE SCHEMA IF NOT EXISTS local_arb",
    """
    CREATE TABLE IF NOT EXISTS local_arb.market_snapshots (
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        best_bid DOUBLE PRECISION,
        best_ask DOUBLE PRECISION,
        mid DOUBLE PRECISION,
        bid_depth_quote DOUBLE PRECISION,
        ask_depth_quote DOUBLE PRECISION,
        n_bid_levels INTEGER,
        n_ask_levels INTEGER,
        latency_ms DOUBLE PRECISION,
        quality_score INTEGER,
        quality_flags TEXT NOT NULL DEFAULT '',
        raw_orderbook_json JSONB,
        config_hash TEXT NOT NULL DEFAULT '',
        git_sha TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'rest',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_arb.data_quality_windows (
        id BIGSERIAL PRIMARY KEY,
        window_start DOUBLE PRECISION NOT NULL,
        window_end DOUBLE PRECISION NOT NULL,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        n_fetches INTEGER NOT NULL,
        n_ok INTEGER NOT NULL,
        avg_quality DOUBLE PRECISION,
        flags_summary TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_arb.spread_windows (
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        symbol TEXT NOT NULL,
        buy_exchange TEXT NOT NULL,
        sell_exchange TEXT NOT NULL,
        size_quote DOUBLE PRECISION NOT NULL,
        gross_spread_bps DOUBLE PRECISION NOT NULL,
        break_even_bps DOUBLE PRECISION NOT NULL,
        net_bps DOUBLE PRECISION NOT NULL,
        depth_ok BOOLEAN NOT NULL,
        quality_buy INTEGER,
        quality_sell INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_arb.opportunities (
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        symbol TEXT NOT NULL,
        buy_exchange TEXT NOT NULL,
        sell_exchange TEXT NOT NULL,
        size_quote DOUBLE PRECISION NOT NULL,
        gross_spread_bps DOUBLE PRECISION NOT NULL,
        break_even_bps DOUBLE PRECISION NOT NULL,
        net_bps DOUBLE PRECISION NOT NULL,
        min_net_bps_threshold DOUBLE PRECISION NOT NULL,
        depth_ok BOOLEAN NOT NULL,
        quality_buy INTEGER,
        quality_sell INTEGER,
        status TEXT NOT NULL DEFAULT 'accepted',
        reject_reason TEXT,
        config_hash TEXT NOT NULL DEFAULT '',
        git_sha TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_arb.paper_trades (
        id BIGSERIAL PRIMARY KEY,
        trade_id TEXT NOT NULL,
        ts DOUBLE PRECISION NOT NULL,
        symbol TEXT NOT NULL,
        buy_exchange TEXT NOT NULL,
        sell_exchange TEXT NOT NULL,
        size_quote DOUBLE PRECISION NOT NULL,
        expected_net_bps DOUBLE PRECISION NOT NULL,
        realized_net_bps DOUBLE PRECISION NOT NULL,
        decay_bps DOUBLE PRECISION NOT NULL,
        fill_ratio DOUBLE PRECISION NOT NULL,
        buy_vwap DOUBLE PRECISION NOT NULL,
        sell_vwap DOUBLE PRECISION NOT NULL,
        base_filled DOUBLE PRECISION NOT NULL,
        status TEXT NOT NULL,
        failure_reason TEXT,
        config_hash TEXT NOT NULL DEFAULT '',
        git_sha TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_arb.simulated_balances (
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        exchange TEXT NOT NULL,
        asset TEXT NOT NULL,
        free DOUBLE PRECISION NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS local_arb.fee_schedule (
        exchange TEXT PRIMARY KEY,
        taker_bps DOUBLE PRECISION NOT NULL,
        maker_bps DOUBLE PRECISION NOT NULL,
        withdrawal_brl_fixed DOUBLE PRECISION NOT NULL DEFAULT 0,
        withdrawal_usdt_fixed DOUBLE PRECISION NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]

# Migrações aditivas p/ instalações que criaram as tabelas antes dos campos PRD.
# ADD COLUMN IF NOT EXISTS é idempotente e não destrói/reescreve dado existente.
MIGRATION_STATEMENTS: list[str] = [
    "ALTER TABLE local_arb.market_snapshots ADD COLUMN IF NOT EXISTS raw_orderbook_json JSONB",
    "ALTER TABLE local_arb.market_snapshots ADD COLUMN IF NOT EXISTS config_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE local_arb.market_snapshots ADD COLUMN IF NOT EXISTS git_sha TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE local_arb.opportunities ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'accepted'",
    "ALTER TABLE local_arb.opportunities ADD COLUMN IF NOT EXISTS reject_reason TEXT",
    "ALTER TABLE local_arb.opportunities ADD COLUMN IF NOT EXISTS config_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE local_arb.opportunities ADD COLUMN IF NOT EXISTS git_sha TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE local_arb.paper_trades ADD COLUMN IF NOT EXISTS failure_reason TEXT",
    "ALTER TABLE local_arb.paper_trades ADD COLUMN IF NOT EXISTS config_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE local_arb.paper_trades ADD COLUMN IF NOT EXISTS git_sha TEXT NOT NULL DEFAULT ''",
]

TABLE_NAMES = [
    "market_snapshots",
    "data_quality_windows",
    "spread_windows",
    "opportunities",
    "paper_trades",
    "simulated_balances",
    "fee_schedule",
]

_INSERT_SNAPSHOT = """
INSERT INTO local_arb.market_snapshots
    (ts, exchange, symbol, best_bid, best_ask, mid, bid_depth_quote, ask_depth_quote,
     n_bid_levels, n_ask_levels, latency_ms, quality_score, quality_flags,
     raw_orderbook_json, config_hash, git_sha, source)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_QUALITY_WINDOW = """
INSERT INTO local_arb.data_quality_windows
    (window_start, window_end, exchange, symbol, n_fetches, n_ok, avg_quality, flags_summary)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_SPREAD_WINDOW = """
INSERT INTO local_arb.spread_windows
    (ts, symbol, buy_exchange, sell_exchange, size_quote, gross_spread_bps,
     break_even_bps, net_bps, depth_ok, quality_buy, quality_sell)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_OPPORTUNITY = """
INSERT INTO local_arb.opportunities
    (ts, symbol, buy_exchange, sell_exchange, size_quote, gross_spread_bps,
     break_even_bps, net_bps, min_net_bps_threshold, depth_ok, quality_buy, quality_sell,
     status, reject_reason, config_hash, git_sha)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_PAPER_TRADE = """
INSERT INTO local_arb.paper_trades
    (trade_id, ts, symbol, buy_exchange, sell_exchange, size_quote, expected_net_bps,
     realized_net_bps, decay_bps, fill_ratio, buy_vwap, sell_vwap, base_filled, status,
     failure_reason, config_hash, git_sha)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_UPSERT_FEE = """
INSERT INTO local_arb.fee_schedule
    (exchange, taker_bps, maker_bps, withdrawal_brl_fixed, withdrawal_usdt_fixed, updated_at)
VALUES (%s, %s, %s, %s, %s, now())
ON CONFLICT (exchange) DO UPDATE SET
    taker_bps = EXCLUDED.taker_bps,
    maker_bps = EXCLUDED.maker_bps,
    withdrawal_brl_fixed = EXCLUDED.withdrawal_brl_fixed,
    withdrawal_usdt_fixed = EXCLUDED.withdrawal_usdt_fixed,
    updated_at = now()
"""


class DbUnavailable(Exception):
    """DB indisponível (sem DSN, sem driver ou conexão falhou) — modo no-db."""


def get_dsn() -> str | None:
    """DSN do ambiente (LOCAL_ARB_DATABASE_URL tem precedência sobre DATABASE_URL)."""
    for var in DSN_ENV_VARS:
        dsn = os.environ.get(var)
        if dsn:
            return dsn
    return None


def connect(dsn: str | None):
    """Conexão psycopg2 (autocommit off). Levanta DbUnavailable em qualquer falha."""
    if not dsn:
        raise DbUnavailable("DATABASE_URL ausente — modo no-db/dry-run")
    try:
        import psycopg2  # import tardio: driver é opcional neste pacote
    except ImportError as exc:
        raise DbUnavailable(f"driver psycopg2 não instalado ({exc}) — modo no-db") from exc
    try:
        return psycopg2.connect(dsn)
    except Exception as exc:  # noqa: BLE001 — conexão ruim vira modo no-db, não crash
        raise DbUnavailable(f"conexão falhou: {type(exc).__name__}: {exc}") from exc


def init_db(dsn: str | None) -> str:
    """Cria/garante schema+tabelas+colunas PRD (idempotente, nunca dropa). Retorna resumo."""
    conn = connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            for stmt in SCHEMA_STATEMENTS:
                cur.execute(stmt)
            for stmt in MIGRATION_STATEMENTS:
                cur.execute(stmt)
        return f"schema local_arb garantido ({len(TABLE_NAMES)} tabelas: {', '.join(TABLE_NAMES)})"
    finally:
        conn.close()


def _raw_book_json(book) -> str | None:
    """Serializa o book cru (níveis price/size) p/ a coluna JSONB de auditoria."""
    if book is None:
        return None
    return json.dumps({
        "ts": book.ts,
        "latency_ms": book.latency_ms,
        "bids": [[lvl.price, lvl.size] for lvl in book.bids],
        "asks": [[lvl.price, lvl.size] for lvl in book.asks],
    })


def persist_scan(
    conn,
    scan,
    fee_rows: list[tuple] | None = None,
    config_hash: str = "",
    git_sha: str = "",
) -> dict[str, int]:
    """Persiste um ScanResult (scanner.ScanResult) numa transação única.

    fee_rows: [(exchange, taker_bps, maker_bps, wd_brl, wd_usdt)] p/ upsert do
    fee_schedule vigente. config_hash/git_sha carimbam snapshots, oportunidades
    e paper trades (proveniência PRD). Retorna contagem de linhas por tabela.
    """
    counts = {"market_snapshots": 0, "data_quality_windows": 0, "spread_windows": 0,
              "opportunities": 0, "paper_trades": 0, "fee_schedule": 0}
    with conn, conn.cursor() as cur:
        for b in scan.books:
            book = b.book
            cur.execute(_INSERT_SNAPSHOT, (
                book.ts if book else scan.ts, b.exchange, b.symbol,
                book.best_bid if book else None,
                book.best_ask if book else None,
                book.mid if book else None,
                sum(lvl.notional for lvl in book.bids) if book else None,
                sum(lvl.notional for lvl in book.asks) if book else None,
                len(book.bids) if book else 0,
                len(book.asks) if book else 0,
                book.latency_ms if book else None,
                b.quality, ",".join(b.flags),
                _raw_book_json(book), config_hash, git_sha, b.source,
            ))
            counts["market_snapshots"] += 1
            cur.execute(_INSERT_QUALITY_WINDOW, (
                scan.ts, scan.ts, b.exchange, b.symbol, 1, 1 if b.ok else 0,
                float(b.quality), ",".join(b.flags),
            ))
            counts["data_quality_windows"] += 1
        for sw in scan.spread_windows:
            cur.execute(_INSERT_SPREAD_WINDOW, (
                sw.ts, sw.symbol, sw.buy_exchange, sw.sell_exchange, sw.size_quote,
                sw.gross_spread_bps, sw.break_even_bps, sw.net_bps, sw.depth_ok,
                sw.quality_buy, sw.quality_sell,
            ))
            counts["spread_windows"] += 1
        for opp in scan.opportunities:
            cur.execute(_INSERT_OPPORTUNITY, (
                opp.ts, opp.symbol, opp.buy_exchange, opp.sell_exchange, opp.size_quote,
                opp.gross_spread_bps, opp.break_even_bps, opp.net_bps, scan.min_net_bps,
                opp.depth_ok, opp.quality_buy, opp.quality_sell,
                opp.status, opp.reject_reason, config_hash, git_sha,
            ))
            counts["opportunities"] += 1
        for tr in scan.paper_trades:
            cur.execute(_INSERT_PAPER_TRADE, (
                tr.trade_id, tr.ts, tr.symbol, tr.buy_exchange, tr.sell_exchange,
                tr.size_quote, tr.expected_net_bps, tr.realized_net_bps, tr.decay_bps,
                tr.fill_ratio, tr.buy_vwap, tr.sell_vwap, tr.base_filled, tr.status,
                tr.failure_reason, config_hash, git_sha,
            ))
            counts["paper_trades"] += 1
        for row in fee_rows or []:
            cur.execute(_UPSERT_FEE, row)
            counts["fee_schedule"] += 1
    return counts
