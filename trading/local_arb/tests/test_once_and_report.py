"""CLI --once --synthetic (JSON, exit 0, oportunidade no cenário) e --report."""

import json

import pytest

from trading.local_arb.adapters import SYNTHETIC_TS, build_adapters
from trading.local_arb.collector import collect_once
from trading.local_arb.config import DEFAULT_CONFIG_PATH, load_config
from trading.local_arb.daily_report import generate_daily_report
from trading.local_arb.main import main
from trading.local_arb.scanner import scan_books


@pytest.fixture
def cfg():
    return load_config(DEFAULT_CONFIG_PATH)


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_ARB_DATABASE_URL", raising=False)


def _synthetic_scan(cfg):
    adapters = build_adapters(cfg, synthetic=True)
    books = collect_once(adapters, cfg, SYNTHETIC_TS)
    return scan_books(books, cfg, SYNTHETIC_TS)


class TestScanSynthetic:
    def test_scan_finds_binance_to_mb_opportunity(self, cfg):
        scan = _synthetic_scan(cfg)
        assert all(b.ok for b in scan.books)
        assert scan.spread_windows, "com books saudáveis tem de haver janelas"
        routes = {(o.buy_exchange, o.sell_exchange) for o in scan.opportunities}
        assert ("binance", "mercadobitcoin") in routes
        for opp in scan.opportunities:
            assert opp.net_bps >= scan.min_net_bps
        assert scan.paper_trades and any(t.status == "filled" for t in scan.paper_trades)

    def test_crossed_book_never_becomes_opportunity(self, cfg):
        # book cruzado (bid >= ask na mesma exchange) score 100-30=70, passa o
        # min_quality_score=70 no limite — mas é dado corrompido: o bid cruzado
        # inflaria o spread e viraria paper trade fantasma no DB
        cfg["exchanges"]["mercadobitcoin"]["synthetic"] = {"bid": 5.70, "ask": 5.60}
        scan = _synthetic_scan(cfg)
        crossed = next(b for b in scan.books if b.exchange == "mercadobitcoin")
        assert "crossed" in crossed.flags
        assert crossed.quality >= float(cfg["thresholds"]["min_quality_score"])
        for opp in scan.opportunities:
            assert "mercadobitcoin" not in (opp.buy_exchange, opp.sell_exchange)
        for trade in scan.paper_trades:
            assert "mercadobitcoin" not in (trade.buy_exchange, trade.sell_exchange)
        # a janela de spread continua registrada p/ diagnóstico
        assert any("mercadobitcoin" in (w.buy_exchange, w.sell_exchange)
                   for w in scan.spread_windows)

    def test_failed_source_does_not_crash_scan(self, cfg):
        from trading.local_arb.adapters import PublicRestAdapter

        adapters = build_adapters(cfg, synthetic=True)
        # troca a 1a fonte por um adapter REST sem endpoint (falha controlada)
        adapters[0] = PublicRestAdapter(adapters[0].exchange, "USDT/BRL", None)
        books = collect_once(adapters, cfg, SYNTHETIC_TS)
        assert sum(1 for b in books if not b.ok) == 1
        scan = scan_books(books, cfg, SYNTHETIC_TS)  # não levanta
        healthy = [b.exchange for b in books if b.ok]
        for w in scan.spread_windows:
            assert w.buy_exchange in healthy and w.sell_exchange in healthy


class TestOnceCli:
    def test_once_synthetic_prints_json_and_exits_zero(self, capsys):
        rc = main(["--once", "--synthetic"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["mode"] == "synthetic"
        assert summary["n_books_ok"] == len(summary["books"])
        assert summary["opportunities"], "cenário sintético tem arb acima do break-even"
        assert summary["persisted"] is None
        assert ("no-db" in summary["db"]) or ("synthetic" in summary["db"])
        assert summary["paper_trades"][0]["status"] == "filled"


class TestReport:
    def test_generate_daily_report_files(self, cfg, tmp_path):
        scan = _synthetic_scan(cfg)
        md_path = generate_daily_report(scan, cfg, "2026-07-06", data_dir=tmp_path,
                                        mode="synthetic")
        assert md_path == tmp_path / "reports" / "daily_report_2026-07-06.md"
        text = md_path.read_text(encoding="utf-8")
        status_lines = [l for l in text.splitlines() if l.startswith("STATUS DA TESE: ")]
        assert len(status_lines) == 1
        assert status_lines[0].split(": ")[1].split()[0] in {"VIVA", "FERIDA", "MORTA", "HOLD"}
        for name in ("snapshots", "spread_windows", "opportunities", "paper_trades"):
            assert (tmp_path / "reports" / f"{name}_2026-07-06.csv").exists()

    def test_report_cli_synthetic(self, tmp_path, capsys):
        rc = main(["--report", "--synthetic", "--data-dir", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[report] gerado:" in out
        reports = list((tmp_path / "reports").glob("daily_report_*.md"))
        assert len(reports) == 1
        assert "STATUS DA TESE: " in reports[0].read_text(encoding="utf-8")

    def test_report_without_trades_is_hold(self, cfg, tmp_path):
        scan = _synthetic_scan(cfg)
        scan.paper_trades = []
        md_path = generate_daily_report(scan, cfg, "2026-07-06", data_dir=tmp_path)
        text = md_path.read_text(encoding="utf-8")
        assert "STATUS DA TESE: HOLD" in text
