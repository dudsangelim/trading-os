"""Continuous Observer v3.2: coleta sem paper, gates separados e rejeições auditáveis."""

import csv
import json

import pytest

from trading.local_arb.adapters import SYNTHETIC_TS, build_adapters
from trading.local_arb.collector import collect_once
from trading.local_arb.config import DEFAULT_CONFIG_PATH, load_config
from trading.local_arb.main import main
from trading.local_arb.observer import (
    generate_observer_report,
    observer_summary,
    persist_observer_scan,
    spread_daily_path,
    utc_date_str,
)
from trading.local_arb.scanner import scan_books


@pytest.fixture
def cfg():
    return load_config(DEFAULT_CONFIG_PATH)


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_ARB_DATABASE_URL", raising=False)


def _scan(cfg, *, paper_enabled=False):
    adapters = build_adapters(cfg, synthetic=True)
    books = collect_once(adapters, cfg, SYNTHETIC_TS)
    return scan_books(books, cfg, SYNTHETIC_TS, paper_enabled=paper_enabled)


def test_observer_gates_and_reason_rejected_are_recorded_on_every_window(cfg):
    scan = _scan(cfg)

    assert scan.spread_windows
    first = scan.spread_windows[0]
    assert isinstance(first.candidate_gate_pass, bool)
    assert isinstance(first.watch_gate_pass, bool)
    assert isinstance(first.execution_gate_theoretical_pass, bool)
    assert first.reason_rejected
    assert isinstance(first.reject_reasons, list)
    assert scan.paper_trades == []


def test_observer_persists_csvs_with_rejection_fields_and_summary(cfg, tmp_path):
    scan = _scan(cfg)
    counts = persist_observer_scan(scan, cfg, tmp_path, config_hash="cfg123")

    assert counts["observer_snapshots"] == len(scan.books)
    assert counts["observer_spread_windows"] == len(scan.spread_windows)

    spread_csv = spread_daily_path(tmp_path, utc_date_str(scan.ts), mode="rest")
    rows = list(csv.DictReader(spread_csv.open(encoding="utf-8")))
    assert rows
    assert "reason_rejected" in rows[0]
    assert "candidate_gate_pass" in rows[0]
    assert "watch_gate_pass" in rows[0]
    assert "capital_required_brl" in rows[0]
    assert rows[0]["source"] == "synthetic"
    assert "skew_ms" in rows[0]

    summary = observer_summary(scan)
    assert summary["paper_enabled"] is False
    assert summary["n_candidate_windows"] >= summary["n_watch_windows"]
    assert summary["reason_rejected_counts"]


def test_observer_once_cli_synthetic_writes_files_without_paper(cfg, tmp_path, capsys):
    rc = main(["--observer-once", "--synthetic", "--data-dir", str(tmp_path)])
    assert rc == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "observer_synthetic"
    assert summary["paper_enabled"] is False
    assert summary["paper_trades"] == 0
    assert summary["persisted"]["observer_spread_windows"] > 0
    assert not (tmp_path / "observer" / "observer_spread_windows.csv").exists()
    assert spread_daily_path(tmp_path, "2025-06-15", mode="synthetic").exists()


def test_observer_loop_respects_max_cycles_and_never_papers(tmp_path, capsys):
    rc = main([
        "--observer-loop", "--synthetic", "--data-dir", str(tmp_path),
        "--max-cycles", "2", "--poll-seconds", "0",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[observer-loop] cycle=1" in out
    assert "[observer-loop] cycle=2" in out
    rows = list(csv.DictReader(spread_daily_path(tmp_path, "2025-06-15", mode="synthetic").open(encoding="utf-8")))
    assert rows
    assert len(rows) % 2 == 0


def test_high_skew_rejects_candidate_windows(cfg):
    scan = _scan(cfg)
    first, second = scan.books[0], scan.books[1]
    first.book.ts = SYNTHETIC_TS
    second.book.ts = SYNTHETIC_TS + 10
    cfg["observer"]["max_skew_ms"] = 1000

    skewed = scan_books([first, second], cfg, SYNTHETIC_TS, paper_enabled=False)
    assert skewed.spread_windows
    assert all(not w.candidate_gate_pass for w in skewed.spread_windows)
    assert all("rejected_by_skew" in w.reject_reasons for w in skewed.spread_windows)


def test_observer_report_contains_hourly_and_rejection_sections(cfg, tmp_path):
    scan = _scan(cfg)
    persist_observer_scan(scan, cfg, tmp_path, config_hash="cfg123")

    report = generate_observer_report(tmp_path, "2026-07-09")
    text = report.read_text(encoding="utf-8")

    assert "# local_arb — Continuous Observer" in text
    assert "## Reason rejected" in text
    assert "## Análise por horário" in text
    assert "## Top janelas líquidas" in text
