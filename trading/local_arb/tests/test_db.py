"""db.py: SQL não destrutivo, modo no-db controlado (sem DB real nos testes)."""

import re

import pytest

from trading.local_arb.db import (
    DbUnavailable,
    MIGRATION_STATEMENTS,
    SCHEMA_STATEMENTS,
    TABLE_NAMES,
    connect,
    get_dsn,
    init_db,
    persist_scan,
)

EXPECTED_TABLES = {
    "market_snapshots",
    "data_quality_windows",
    "spread_windows",
    "opportunities",
    "paper_trades",
    "simulated_balances",
    "fee_schedule",
}


class TestSchemaSql:
    def test_creates_schema_if_not_exists(self):
        assert SCHEMA_STATEMENTS[0].strip() == "CREATE SCHEMA IF NOT EXISTS local_arb"

    def test_all_seven_tables_present(self):
        assert set(TABLE_NAMES) == EXPECTED_TABLES
        joined = "\n".join(SCHEMA_STATEMENTS)
        for table in EXPECTED_TABLES:
            assert f"CREATE TABLE IF NOT EXISTS local_arb.{table}" in joined

    def test_every_create_is_idempotent(self):
        for stmt in SCHEMA_STATEMENTS:
            assert "IF NOT EXISTS" in stmt

    def test_nothing_destructive(self):
        joined = "\n".join(SCHEMA_STATEMENTS).upper()
        for verb in ("DROP ", "TRUNCATE", "DELETE FROM", "ALTER TABLE"):
            assert verb not in joined

    def test_migrations_only_additive(self):
        # Única forma de ALTER permitida: ADD COLUMN IF NOT EXISTS (idempotente, aditivo).
        for stmt in MIGRATION_STATEMENTS:
            assert re.match(
                r"^ALTER TABLE local_arb\.\w+ ADD COLUMN IF NOT EXISTS ", stmt
            ), stmt
        joined = "\n".join(MIGRATION_STATEMENTS).upper()
        for verb in ("DROP ", "TRUNCATE", "DELETE FROM"):
            assert verb not in joined

    def test_prd_fields_present_in_schema(self):
        by_table = {
            name: next(s for s in SCHEMA_STATEMENTS if f"local_arb.{name}" in s)
            for name in TABLE_NAMES
        }
        for col in ("raw_orderbook_json", "latency_ms", "quality_flags",
                    "config_hash", "git_sha"):
            assert col in by_table["market_snapshots"], col
        for col in ("status", "reject_reason", "config_hash", "git_sha"):
            assert col in by_table["opportunities"], col
        for col in ("failure_reason", "config_hash", "git_sha"):
            assert col in by_table["paper_trades"], col


class TestNoDbMode:
    def test_get_dsn_none_without_env(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("LOCAL_ARB_DATABASE_URL", raising=False)
        assert get_dsn() is None

    def test_local_arb_dsn_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://generic")
        monkeypatch.setenv("LOCAL_ARB_DATABASE_URL", "postgres://specific")
        assert get_dsn() == "postgres://specific"

    def test_connect_without_dsn_raises_controlled(self):
        with pytest.raises(DbUnavailable, match="no-db"):
            connect(None)

    def test_init_db_without_dsn_raises_controlled(self):
        with pytest.raises(DbUnavailable):
            init_db(None)

    def test_connect_with_dsn_but_no_driver_or_server_is_controlled(self):
        # Sem psycopg2 instalado -> DbUnavailable(driver); com driver mas DSN
        # inválido -> DbUnavailable(conexão). Nunca exceção crua.
        with pytest.raises(DbUnavailable):
            connect("postgresql://nouser:nopass@127.0.0.1:1/void")


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        assert (params is None) or sql.count("%s") == len(params), \
            f"placeholders != params em: {sql}"
        self.executed.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestPersistScanProvenance:
    """persist_scan carimba config_hash/git_sha e grava o book cru (sem DB real)."""

    def _scan(self):
        from trading.local_arb.adapters import SYNTHETIC_TS, build_adapters
        from trading.local_arb.collector import collect_once
        from trading.local_arb.config import DEFAULT_CONFIG_PATH, load_config
        from trading.local_arb.scanner import scan_books

        cfg = load_config(DEFAULT_CONFIG_PATH)
        books = collect_once(build_adapters(cfg, synthetic=True), cfg, SYNTHETIC_TS)
        return scan_books(books, cfg, SYNTHETIC_TS)

    def test_stamps_and_raw_book(self):
        import json

        scan = self._scan()
        conn = _FakeConn()
        counts = persist_scan(conn, scan, config_hash="cfg-hash-x", git_sha="sha-y")
        assert counts["market_snapshots"] == len(scan.books)
        assert counts["opportunities"] == len(scan.opportunities) > 0
        assert counts["paper_trades"] == len(scan.paper_trades) > 0

        snap_calls = [(s, p) for s, p in conn.cur.executed if "market_snapshots" in s]
        for _, params in snap_calls:
            assert "cfg-hash-x" in params and "sha-y" in params
            raw = next(p for p in params if isinstance(p, str) and p.startswith("{"))
            book = json.loads(raw)
            assert book["bids"] and book["asks"]

        opp_calls = [(s, p) for s, p in conn.cur.executed if "local_arb.opportunities" in s]
        for _, params in opp_calls:
            assert "accepted" in params
            assert "cfg-hash-x" in params and "sha-y" in params

        trade_calls = [(s, p) for s, p in conn.cur.executed if "paper_trades" in s]
        for _, params in trade_calls:
            assert "cfg-hash-x" in params and "sha-y" in params
