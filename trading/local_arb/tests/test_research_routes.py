"""Research routes: synthetic offline, statuses, cálculo de prêmio/proxy e robustez."""

import json

import pytest

from trading.local_arb.adapters import SYNTHETIC_TS
from trading.local_arb.main import main
from trading.local_arb.references import (
    PublicReferenceClient,
    SyntheticReferenceClient,
    build_reference_client,
)
from trading.local_arb.research_config import (
    DEFAULT_RESEARCH_CONFIG_PATH,
    load_research_config,
    research_config_hash,
)
from trading.local_arb.research_report import generate_research_report
from trading.local_arb.research_routes import (
    STATUS_BAD_DATA,
    STATUS_HOLD,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_PROMISING,
    run_research_routes,
)


@pytest.fixture
def rcfg():
    return load_research_config(DEFAULT_RESEARCH_CONFIG_PATH)


def _result(scan, route):
    return next(r for r in scan.results if r.route == route)


def _no_network(url, timeout_s):
    raise AssertionError(f"modo synthetic tocou a rede: {url}")


class TestConfig:
    def test_load_and_hash(self, rcfg):
        assert set(rcfg["routes"]) >= {
            "usdc_brl", "stablecoin_premium_brl", "btc_eth_brl_proxy", "mbrl_recon"}
        assert len(research_config_hash(rcfg)) == 64

    def test_mbrl_recon_disabled_by_default(self, rcfg):
        assert rcfg["routes"]["mbrl_recon"]["enabled"] is False

    def test_no_dex_anywhere(self, rcfg):
        blob = json.dumps(rcfg, ensure_ascii=False).lower()
        assert "dex" not in blob.replace("index", "")
        assert "uniswap" not in blob and "pancake" not in blob


class TestSyntheticOffline:
    def test_runs_offline_without_network(self, rcfg):
        # http_get que explode se chamado prova que synthetic não toca rede
        scan = run_research_routes(rcfg, synthetic=True, http_get=_no_network)
        assert scan.mode == "synthetic"
        assert scan.ts == SYNTHETIC_TS
        enabled = {n for n, r in rcfg["routes"].items() if r.get("enabled")}
        assert {r.route for r in scan.results} == enabled
        assert "mbrl_recon" in scan.disabled

    def test_usdc_brl_promising_with_default_scenario(self, rcfg):
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "usdc_brl")
        # bybit ask 5.556 -> MB bid 5.640: gross ~151bps, custo 120 -> net ~31bps
        assert r.status == STATUS_PROMISING
        assert r.metrics["best_route"] == "bybit->mercadobitcoin"
        gross = (5.640 / 5.556 - 1.0) * 10_000.0
        assert r.metrics["gross_bps"] == pytest.approx(gross, abs=0.01)
        assert r.signal_bps == pytest.approx(gross - 120.0, abs=0.01)

    def test_usdc_brl_hold_when_spread_narrow(self, rcfg):
        for ex in rcfg["routes"]["usdc_brl"]["exchanges"].values():
            ex["synthetic"] = {"bid": 5.598, "ask": 5.602}
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "usdc_brl")
        assert r.status == STATUS_HOLD
        assert r.signal_bps < 0  # spread cruzado negativo após custos

    def test_usdc_brl_single_source_is_ok_monitor(self, rcfg):
        rcfg["routes"]["usdc_brl"]["exchanges"]["bybit"]["enabled"] = False
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "usdc_brl")
        assert r.status == STATUS_OK
        assert r.signal_bps is None


class TestStablecoinPremium:
    def test_premium_math(self, rcfg):
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "stablecoin_premium_brl")
        usdt = r.metrics["stables"]["USDT"]
        # mids: MB (5.650+5.656)/2=5.653, bybit (5.560+5.565)/2=5.5625 -> mediana 5.60775
        local_mid = (5.653 + 5.5625) / 2.0
        fair = 5.50 * (1.0 / 1.0005)  # PTAX × USD/USDT via USDCUSDT invertido
        expected_bps = (local_mid / fair - 1.0) * 10_000.0
        assert usdt["local_mid_brl"] == pytest.approx(local_mid, abs=1e-6)
        assert usdt["premium_bps"] == pytest.approx(expected_bps, abs=0.01)
        # USDC sem ajuste: fair = PTAX puro
        usdc = r.metrics["stables"]["USDC"]
        usdc_mid = ((5.640 + 5.646) / 2.0 + (5.552 + 5.556) / 2.0) / 2.0
        assert usdc["premium_bps"] == pytest.approx(
            (usdc_mid / 5.50 - 1.0) * 10_000.0, abs=0.01)
        # prêmio sintético ~200bps > promising 150 -> PROMISING
        assert r.status == STATUS_PROMISING
        assert r.signal_bps == pytest.approx(expected_bps, abs=0.01)

    def test_missing_ptax_is_no_data(self, rcfg):
        from trading.local_arb.references import RefQuote
        from trading.local_arb.research_routes import RouteContext, run_stablecoin_premium

        class NoPtax(SyntheticReferenceClient):
            def ptax_usd_brl(self):
                return RefQuote("ptax", "USD/BRL", ok=False, error="fora do ar")

        ctx = RouteContext(now_ts=SYNTHETIC_TS, synthetic=True, refs=NoPtax(), defaults={})
        r = run_stablecoin_premium(
            "stablecoin_premium_brl", rcfg["routes"]["stablecoin_premium_brl"], ctx)
        assert r.status == STATUS_NO_DATA
        assert "fora do ar" in r.sources["ptax"]

    def test_binance_adjust_fallback_to_one(self, rcfg):
        # remove USDCUSDT do cenário sintético: ajuste indisponível -> usa 1.0, sem crash
        rcfg["references"]["synthetic"]["binance"].pop("USDCUSDT")
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "stablecoin_premium_brl")
        usdt = r.metrics["stables"]["USDT"]
        assert usdt["adjust_ok"] is False
        assert usdt["stable_usd"] == 1.0
        local_mid = (5.653 + 5.5625) / 2.0
        assert usdt["premium_bps"] == pytest.approx(
            (local_mid / 5.50 - 1.0) * 10_000.0, abs=0.01)


class TestBtcEthProxy:
    def test_deviation_math(self, rcfg):
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "btc_eth_brl_proxy")
        usdt_brl = (5.653 + 5.5625) / 2.0
        assert r.metrics["usdt_brl_local_mid"] == pytest.approx(usdt_brl, abs=1e-6)
        btc = r.metrics["legs"]["BTC"]
        implied = 100_000.0 * usdt_brl
        local_mid = ((561_000.0 + 561_400.0) / 2.0 + (560_800.0 + 561_500.0) / 2.0) / 2.0
        assert btc["implied_brl"] == pytest.approx(implied, abs=1e-3)
        assert btc["deviation_bps"] == pytest.approx(
            (local_mid / implied - 1.0) * 10_000.0, abs=0.01)
        eth = r.metrics["legs"]["ETH"]
        eth_implied = 3_500.0 * usdt_brl
        assert eth["deviation_bps"] == pytest.approx(
            ((19_660.0 / eth_implied) - 1.0) * 10_000.0, abs=0.01)
        # desvios sintéticos < 30bps -> HOLD
        assert r.status == STATUS_HOLD

    def test_missing_usdt_anchor_is_no_data(self, rcfg):
        for ex in rcfg["routes"]["btc_eth_brl_proxy"]["usdt_brl"]["exchanges"].values():
            ex.pop("synthetic", None)
        scan = run_research_routes(rcfg, synthetic=True)
        assert _result(scan, "btc_eth_brl_proxy").status == STATUS_NO_DATA


class TestFailureModes:
    def test_rest_endpoint_failure_is_no_data_not_exception(self, rcfg):
        def boom(url, timeout_s):
            raise ConnectionError("endpoint fora do ar")

        scan = run_research_routes(rcfg, synthetic=False, http_get=boom,
                                   now_ts=SYNTHETIC_TS)
        assert scan.mode == "rest"
        for route in ("usdc_brl", "stablecoin_premium_brl", "btc_eth_brl_proxy"):
            r = _result(scan, route)
            assert r.status == STATUS_NO_DATA, f"{route}: {r.status}"
            assert any("ConnectionError" in v for v in r.sources.values())

    def test_garbage_payload_is_bad_or_no_data_not_exception(self, rcfg):
        def garbage(url, timeout_s):
            return {"totally": "unexpected"}

        scan = run_research_routes(rcfg, synthetic=False, http_get=garbage,
                                   now_ts=SYNTHETIC_TS)
        for r in scan.results:
            assert r.status in {STATUS_NO_DATA, STATUS_BAD_DATA}

    def test_crossed_book_is_bad_data(self, rcfg):
        for ex in rcfg["routes"]["usdc_brl"]["exchanges"].values():
            ex["synthetic"] = {"bid": 5.70, "ask": 5.60}  # cruzado -> inválido
        scan = run_research_routes(rcfg, synthetic=True)
        assert _result(scan, "usdc_brl").status == STATUS_BAD_DATA

    def test_malformed_synthetic_config_is_per_source_not_route_crash(self, rcfg):
        # synthetic sem "ask" (KeyError na construção do adapter) só derruba
        # ESTA fonte; a outra segue viva e a rota vira OK (1 fonte), não BAD_DATA
        rcfg["routes"]["usdc_brl"]["exchanges"]["bybit"]["synthetic"] = {"bid": 5.55}
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "usdc_brl")
        assert r.status == STATUS_OK
        assert "config inválida" in r.sources["bybit"]
        assert r.sources["mercadobitcoin"] == "ok"
        assert not any("exceção inesperada" in n for n in r.notes)

    def test_malformed_api_config_is_per_source_not_route_crash(self, rcfg):
        # api de tipo errado (string) em modo REST: erro controlado da fonte
        rcfg["routes"]["usdc_brl"]["exchanges"]["bybit"]["api"] = "https://errado"
        scan = run_research_routes(rcfg, synthetic=False, now_ts=SYNTHETIC_TS,
                                   http_get=lambda url, t: {"bids": [["5.64", "10"]],
                                                            "asks": [["5.65", "10"]]})
        r = _result(scan, "usdc_brl")
        assert "config inválida" in r.sources["bybit"]
        assert r.sources["mercadobitcoin"] == "ok"


class TestMbrlRecon:
    def test_enabled_synthetic_recon_finds_market(self, rcfg):
        rcfg["routes"]["mbrl_recon"]["enabled"] = True
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "mbrl_recon")
        assert r.status == STATUS_OK
        assert "mercadobitcoin:MBRL/USDT" in r.metrics["found"]
        assert r.sources["mercadobitcoin:MBRL/USDC"] == "sem endpoint configurado"

    def test_recon_invalid_book_is_bad_data_not_no_data(self, rcfg):
        # dado presente mas inválido (book cruzado) -> BAD_DATA (contrato do
        # módulo); NO_DATA fica reservado p/ endpoint mudo/não configurado
        rcfg["routes"]["mbrl_recon"]["enabled"] = True
        syn = rcfg["routes"]["mbrl_recon"]["exchanges"]["mercadobitcoin"]
        syn["synthetic_by_symbol"]["MBRL/USDT"] = {"bid": 1.01, "ask": 0.99}
        scan = run_research_routes(rcfg, synthetic=True)
        r = _result(scan, "mbrl_recon")
        assert r.status == STATUS_BAD_DATA
        assert "dados inválidos" in r.sources["mercadobitcoin:MBRL/USDT"]


class TestReferences:
    def test_synthetic_client_defaults(self):
        client = build_reference_client(None, synthetic=True)
        assert isinstance(client, SyntheticReferenceClient)
        assert client.ptax_usd_brl().price == 5.50
        assert client.binance_price("BTCUSDT").ok
        missing = client.binance_price("DOGEUSDT")
        assert not missing.ok and "sintético" in missing.error

    def test_public_binance_parse_and_failure(self):
        client = PublicReferenceClient(http_get=lambda u, t: {"price": "123.45"})
        q = client.binance_price("BTCUSDT")
        assert q.ok and q.price == 123.45

        def boom(url, timeout_s):
            raise TimeoutError("timeout")

        q = PublicReferenceClient(http_get=boom).binance_price("BTCUSDT")
        assert not q.ok and "TimeoutError" in q.error

    def test_ptax_olinda_then_sgs_fallback(self):
        def olinda_down_sgs_up(url, timeout_s):
            if "olinda" in url:
                raise ConnectionError("olinda fora")
            return [{"data": "04/07/2026", "valor": "5.4321"}]

        q = PublicReferenceClient(http_get=olinda_down_sgs_up).ptax_usd_brl()
        assert q.ok and q.price == pytest.approx(5.4321)

        def all_down(url, timeout_s):
            raise ConnectionError("tudo fora")

        q = PublicReferenceClient(http_get=all_down).ptax_usd_brl()
        assert not q.ok and "olinda" in q.error and "sgs" in q.error

    def test_ptax_olinda_takes_latest(self):
        def olinda(url, timeout_s):
            assert "olinda" in url
            return {"value": [{"cotacaoVenda": 5.40}, {"cotacaoVenda": 5.55}]}

        q = PublicReferenceClient(http_get=olinda).ptax_usd_brl()
        assert q.ok and q.price == pytest.approx(5.55)


class TestCli:
    def test_research_routes_synthetic_prints_json(self, capsys):
        rc = main(["--research-routes", "--synthetic"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["mode"] == "synthetic"
        assert summary["monitor_only"] is True
        assert summary["research_config_hash"]
        routes = {r["route"]: r for r in summary["routes"]}
        assert routes["usdc_brl"]["status"] == "PROMISING"
        assert "mbrl_recon" in summary["routes_disabled"]

    def test_research_report_synthetic_generates_markdown(self, tmp_path, capsys):
        rc = main(["--research-report", "--synthetic", "--data-dir", str(tmp_path)])
        assert rc == 0
        assert "[research-report] gerado:" in capsys.readouterr().out
        mds = list((tmp_path / "reports").glob("research_report_*.md"))
        assert len(mds) == 1
        text = mds[0].read_text(encoding="utf-8")
        assert "## Research routes" in text
        assert "### usdc_brl" in text
        assert "### stablecoin_premium_brl" in text
        assert "### btc_eth_brl_proxy" in text
        assert "DEX" not in text and "dex" not in text.lower().replace("index", "")
        csvs = list((tmp_path / "reports").glob("research_routes_*.csv"))
        assert len(csvs) == 1


class TestReportModule:
    def test_generate_research_report_paths(self, rcfg, tmp_path):
        scan = run_research_routes(rcfg, synthetic=True)
        md = generate_research_report(scan, rcfg, "2026-07-06", data_dir=tmp_path,
                                      config_hash=research_config_hash(rcfg))
        assert md == tmp_path / "reports" / "research_report_2026-07-06.md"
        text = md.read_text(encoding="utf-8")
        assert "MONITOR-ONLY" in text
        assert "| usdc_brl |" in text
        assert (tmp_path / "reports" / "research_routes_2026-07-06.csv").exists()
