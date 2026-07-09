"""Fase 3.3 — inventory_aware: fixture, collect_day, simulate_scenario e CLI."""

import pytest

from trading.local_arb.config import DEFAULT_CONFIG_PATH, load_config
from trading.local_arb.inventory_aware import (
    DayAggregate,
    EligibleRow,
    collect_day,
    generate_inventory_report,
    inventory_cfg,
    rebalance_credit_bps,
    simulate_scenario,
    write_fixture,
)
from trading.local_arb.main import main
from trading.local_arb.observer import iter_spread_rows_for_date

DATE = "2026-07-09"
ROUTE = ("binance", "mercadobitcoin")


@pytest.fixture
def cfg():
    return load_config(DEFAULT_CONFIG_PATH)


def _agg_single_route(n_cycles=3, sizes=(5000.0, 500.0), net_eff=20.0):
    """3 ciclos elegíveis na mesma rota, 2 tamanhos por ciclo (dedup + fallback)."""
    agg = DayAggregate(date_str=DATE)
    for cycle in range(n_cycles):
        ts = 100.0 + cycle * 10.0
        for size in sizes:
            agg.groups.setdefault((ts, *ROUTE), []).append(
                EligibleRow(ts, *ROUTE, size, net_eff - 8.0, net_eff))
    return agg


class TestFixtureAndReport:
    def test_fixture_and_report_in_tmp(self, cfg, tmp_path):
        csv_path = write_fixture(tmp_path, DATE)
        assert csv_path.exists()
        with pytest.raises(FileExistsError):
            write_fixture(tmp_path, DATE)  # nunca sobrescreve dados de observer
        md = generate_inventory_report(tmp_path, DATE, cfg)
        assert md == tmp_path / "reports" / f"inventory_aware_report_{DATE}.md"
        text = md.read_text(encoding="utf-8")
        assert "DECISION:" in text
        # amostra da fixture < min_valid_windows e 1 dia < 7 -> nunca decide
        assert "DECISION: NEED_MORE_DATA" in text
        for scen in ("balanced_all_venues", "top_venues", "low_capital",
                     "brl_local_usdt_global"):
            assert scen in text, f"cenário {scen} ausente do relatório"


class TestCollectDay:
    def test_excludes_synthetic_mixed_and_skew(self, cfg, tmp_path):
        write_fixture(tmp_path, DATE)
        agg = collect_day(iter_spread_rows_for_date(tmp_path, DATE), DATE,
                          rebalance_credit_bps(cfg), 0.0)
        assert agg.n_source_excluded == 2      # synthetic + mixed
        assert agg.n_skew_excluded == 1        # rejected_by_skew
        assert agg.n_valid == agg.n_total - 3
        # as linhas excluídas tinham net 30/40/50; o melhor VÁLIDO é 22
        assert agg.best_net_bps == pytest.approx(22.0)
        # 6 ciclos binance->mb + 2 okx->bitso + 1 brasilbitcoin->bitypreco
        assert agg.n_eligible_groups == 9


class TestSimulateScenario:
    def test_executes_with_sufficient_inventory(self):
        inv = inventory_cfg({})
        balances = {("binance", "BRL"): 20_000.0, ("binance", "USDT"): 0.0,
                    ("mercadobitcoin", "BRL"): 0.0, ("mercadobitcoin", "USDT"): 5_000.0}
        res = simulate_scenario("suficiente", balances, _agg_single_route(), inv)
        assert res.executable_windows == 3     # 1 por ciclo, sempre o maior tamanho
        assert res.blocked_by_inventory == 0
        assert res.executed_notional_brl == pytest.approx(15_000.0)
        assert res.theoretical_pnl_brl == pytest.approx(15_000.0 * 20.0 / 10_000.0)
        # conservação: delta de equity BRL-equiv do cenário == PnL teórico
        ref = float(inv["reference_price_brl"])
        delta = sum((res.final[k] - res.initial[k]) * (ref if k[1] == "USDT" else 1.0)
                    for k in balances)
        assert delta == pytest.approx(res.theoretical_pnl_brl)

    def test_blocks_without_inventory(self):
        inv = inventory_cfg({})
        balances = {("binance", "BRL"): 100.0, ("binance", "USDT"): 0.0,
                    ("mercadobitcoin", "BRL"): 0.0, ("mercadobitcoin", "USDT"): 10.0}
        res = simulate_scenario("insuficiente", balances, _agg_single_route(), inv)
        assert res.executable_windows == 0
        assert res.blocked_by_inventory == 3
        assert res.theoretical_pnl_brl == 0.0
        assert res.blocked_by_route["binance->mercadobitcoin"] == 3

    def test_falls_back_to_smaller_size_then_blocks(self):
        # BRL cobre só um trade de 500: ciclo 1 executa no fallback, 2 e 3 bloqueiam
        inv = inventory_cfg({})
        balances = {("binance", "BRL"): 600.0, ("binance", "USDT"): 0.0,
                    ("mercadobitcoin", "BRL"): 0.0, ("mercadobitcoin", "USDT"): 5_000.0}
        res = simulate_scenario("fallback", balances, _agg_single_route(), inv)
        assert res.executable_windows == 1
        assert res.executed_notional_brl == pytest.approx(500.0)
        assert res.blocked_by_inventory == 2


class TestCli:
    def test_fixture_flag_generates_markdown(self, tmp_path, capsys):
        rc = main(["--inventory-aware-report", "--inventory-fixture",
                   "--data-dir", str(tmp_path), "--inventory-date", DATE])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[inventory-aware] fixture sintética gravada:" in out
        assert "[inventory-aware] gerado:" in out
        md = tmp_path / "reports" / f"inventory_aware_report_{DATE}.md"
        assert md.exists()
        assert "DECISION:" in md.read_text(encoding="utf-8")

    def test_fixture_flag_refused_on_live_like_data_dir(self, tmp_path, capsys):
        live_like = tmp_path / "trading" / "local_arb" / "data"
        rc = main(["--inventory-aware-report", "--inventory-fixture",
                   "--data-dir", str(live_like), "--inventory-date", DATE])
        assert rc == 2
        assert "RECUSADO" in capsys.readouterr().err
        assert not list(live_like.rglob("*.csv")) if live_like.exists() else True
