"""CLI do local_arb (research/paper only — sem credenciais, sem execução real).

Uso:
    python -m trading.local_arb.main --status
    python -m trading.local_arb.main --self-test
    python -m trading.local_arb.main --init-db                 # garante schema local_arb (se houver DATABASE_URL)
    python -m trading.local_arb.main --once [--synthetic]      # 1 ciclo coleta+scan; JSON no stdout
    python -m trading.local_arb.main --report [--synthetic]    # relatório diário em data/reports/
    python -m trading.local_arb.main --once --data-dir /tmp/x  # diretório de dados alternativo
    python -m trading.local_arb.main --research-routes [--synthetic]   # research routes; JSON no stdout
    python -m trading.local_arb.main --research-report [--synthetic]   # research_report_<data>.md + CSV

Sem DATABASE_URL (ou sem driver) tudo roda em modo no-db/dry-run e sai com 0.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .adapters import SYNTHETIC_TS, build_adapters
from .break_even import break_even_from_config
from .collector import collect_once
from .config import DEFAULT_CONFIG_PATH, config_hash, git_sha, load_config
from .daily_report import DEFAULT_DATA_DIR, generate_daily_report
from .db import DbUnavailable, connect, get_dsn, init_db, persist_scan
from .inventory import Ledger
from .models import OrderBookLevel, OrderBookSnapshot, Opportunity
from .observer import generate_observer_report, observer_summary, persist_observer_scan
from .paper import simulate_arb
from .quality import quality_score
from .report import thesis_status
from .research_config import (
    DEFAULT_RESEARCH_CONFIG_PATH,
    load_research_config,
    research_config_hash,
)
from .research_report import generate_research_report
from .research_routes import run_research_routes
from .scanner import ScanResult, fee_schedule_from_config, scan_books, scan_summary
from .spreads import gross_spread_bps, route_depth_ok

ENGINE_ROLE = "local_arb_research"

# alias mantido p/ compat com o self-test original
fee_schedule = fee_schedule_from_config


def cmd_status(cfg: dict) -> int:
    enabled = [k for k, v in cfg["exchanges"].items() if v.get("enabled")]
    print(f"ENGINE_ROLE={ENGINE_ROLE}")
    print(f"config_hash={config_hash(cfg)}")
    print(f"git_sha={git_sha()}")
    print(f"config_path={DEFAULT_CONFIG_PATH}")
    print(f"pair={cfg['pair']['symbol']}")
    print(f"exchanges_enabled={','.join(enabled)}")
    print(f"min_net_bps={cfg['thresholds']['min_net_bps']}")
    print(f"sizes_quote_brl={cfg['simulation']['sizes_quote_brl']}")
    print(f"paper_delay_ms={cfg['paper']['delay_ms']}")
    print(f"database_url={'set' if get_dsn() else 'ausente (modo no-db)'}")
    return 0


def _synthetic_books(now_ts: float) -> tuple[OrderBookSnapshot, OrderBookSnapshot]:
    """Cenário sintético: NovaDAX barata (asks), Mercado Bitcoin cara (bids). ~5.60 BRL/USDT."""
    buy_book = OrderBookSnapshot(
        exchange="novadax",
        symbol="USDT/BRL",
        ts=now_ts - 1.0,
        bids=[OrderBookLevel(5.560 - i * 0.002, 400.0) for i in range(6)],
        asks=[OrderBookLevel(5.565 + i * 0.002, 400.0) for i in range(6)],
        latency_ms=120.0,
    )
    sell_book = OrderBookSnapshot(
        exchange="mercadobitcoin",
        symbol="USDT/BRL",
        ts=now_ts - 1.5,
        bids=[OrderBookLevel(5.650 - i * 0.002, 400.0) for i in range(6)],
        asks=[OrderBookLevel(5.655 + i * 0.002, 400.0) for i in range(6)],
        latency_ms=200.0,
    )
    return buy_book, sell_book


def cmd_self_test(cfg: dict) -> int:
    now_ts = 1_750_000_000.0  # ts fixo: self-test é determinístico, sem relógio/rede/DB
    print(f"[self-test] ENGINE_ROLE={ENGINE_ROLE} config_hash={config_hash(cfg)[:16]}…")

    buy_book, sell_book = _synthetic_books(now_ts)
    qcfg = cfg["quality"]
    max_age = float(cfg["thresholds"]["max_book_age_s"])
    q_buy, flags_buy = quality_score(buy_book, now_ts, max_age, qcfg)
    q_sell, flags_sell = quality_score(sell_book, now_ts, max_age, qcfg)
    print(f"[self-test] quality buy={q_buy} {flags_buy} sell={q_sell} {flags_sell}")
    assert q_buy >= cfg["thresholds"]["min_quality_score"], "book de compra reprovado"
    assert q_sell >= cfg["thresholds"]["min_quality_score"], "book de venda reprovado"

    gross = gross_spread_bps(buy_book, sell_book)
    assert gross is not None and gross > 0, "spread bruto deveria ser positivo no cenário"
    fee_buy = fee_schedule(cfg, buy_book.exchange)
    fee_sell = fee_schedule(cfg, sell_book.exchange)
    be = break_even_from_config(fee_buy, fee_sell, cfg)
    print(f"[self-test] gross={gross:.1f} bps  break_even={be.total_bps:.1f} bps  "
          f"net={gross - be.total_bps:.1f} bps")

    size_quote = float(cfg["simulation"]["sizes_quote_brl"][0])
    opp = Opportunity(
        ts=now_ts,
        symbol=buy_book.symbol,
        buy_exchange=buy_book.exchange,
        sell_exchange=sell_book.exchange,
        size_quote=size_quote,
        gross_spread_bps=gross,
        break_even_bps=be.total_bps,
        depth_ok=route_depth_ok(buy_book, sell_book, size_quote),
        quality_buy=q_buy,
        quality_sell=q_sell,
    )
    assert opp.depth_ok, "profundidade insuficiente no cenário sintético"

    trade = simulate_arb(
        buy_book,
        sell_book,
        size_quote=size_quote,
        fee_buy_bps=fee_buy.taker_bps,
        fee_sell_bps=fee_sell.taker_bps,
        expected_net_bps=opp.net_bps,
        min_fill_ratio=float(cfg["simulation"]["min_fill_ratio"]),
        level_participation=float(cfg["simulation"]["level_participation"]),
        delay_ms=float(cfg["paper"]["delay_ms"]),
        extra_costs_bps=be.slippage_est_bps + be.latency_haircut_bps
        + be.rebalance_amortized_bps,
    )
    print(f"[self-test] paper {trade.status}: fill={trade.fill_ratio:.3f} "
          f"expected={trade.expected_net_bps:.1f} realized={trade.realized_net_bps:.1f} "
          f"decay={trade.decay_bps:.1f} bps")
    assert trade.status == "filled", f"paper trade não preencheu: {trade.status}"

    ledger = Ledger()
    init = cfg["paper"]["initial_balances"]
    for ex in (buy_book.exchange, sell_book.exchange):
        ledger.set_balance(ex, "BRL", float(init["brl_per_exchange"]))
        ledger.set_balance(ex, "USDT", float(init["usdt_per_exchange"]))
    deltas = {
        (buy_book.exchange, "BRL"): -trade.base_filled * trade.buy_vwap
        * (1 + fee_buy.taker_bps / 10_000.0),
        (buy_book.exchange, "USDT"): trade.base_filled,
        (sell_book.exchange, "USDT"): -trade.base_filled,
        (sell_book.exchange, "BRL"): trade.base_filled * trade.sell_vwap
        * (1 - fee_sell.taker_bps / 10_000.0),
    }
    ledger.apply_trade(deltas)
    print(f"[self-test] ledger pós-trade: "
          + " | ".join(f"{b.exchange}:{b.asset}={b.free:.2f}" for b in ledger.balances()))
    ledger.revert_trade(deltas)
    assert abs(ledger.get_balance(buy_book.exchange, "BRL") - init["brl_per_exchange"]) < 1e-6

    rcfg = cfg["report"]
    verdict = thesis_status(
        n_trades=25,
        avg_realized_net_bps=trade.realized_net_bps,
        avg_decay_bps=trade.decay_bps,
        min_trades_for_verdict=int(rcfg["min_trades_for_verdict"]),
        viva_min_net_bps=float(rcfg["viva_min_net_bps"]),
        ferida_max_decay_bps=float(rcfg["ferida_max_decay_bps"]),
    )
    print(f"[self-test] veredito sintético: {verdict.status} — {'; '.join(verdict.reasons)}")

    print("[self-test] OK — todos os passos sintéticos passaram (sem rede, sem DB).")
    return 0


def cmd_init_db() -> int:
    """Garante schema+tabelas. Modo no-db imprime aviso e sai 0 (dry-run)."""
    try:
        note = init_db(get_dsn())
        print(f"[init-db] {note}")
    except DbUnavailable as exc:
        print(f"[init-db] no-db: {exc} — nada a fazer (dry-run, exit 0)")
    return 0


def _run_scan(cfg: dict, synthetic: bool, paper_enabled: bool = True) -> tuple[ScanResult, str]:
    mode = "synthetic" if synthetic else "rest"
    now_ts = SYNTHETIC_TS if synthetic else time.time()
    adapters = build_adapters(cfg, synthetic=synthetic)
    books = collect_once(adapters, cfg, now_ts)
    return scan_books(books, cfg, now_ts, paper_enabled=paper_enabled), mode


def _maybe_persist(scan: ScanResult, cfg: dict, synthetic: bool = False) -> tuple[dict | None, str]:
    """Persiste no Postgres se houver DSN + driver + schema; senão nota de no-db.

    Modo synthetic NUNCA persiste: dados fake não podem poluir o schema de pesquisa.
    """
    if synthetic:
        return None, "synthetic: persistência pulada (dados sintéticos não vão pro DB)"
    dsn = get_dsn()
    if not dsn:
        return None, "no-db: DATABASE_URL ausente (dry-run)"
    try:
        conn = connect(dsn)
    except DbUnavailable as exc:
        return None, f"no-db: {exc}"
    try:
        fee_rows = [
            (name, float(ex["taker_bps"]), float(ex["maker_bps"]),
             float(ex.get("withdrawal_brl_fixed", 0.0)), float(ex.get("withdrawal_usdt_fixed", 0.0)))
            for name, ex in cfg["exchanges"].items()
            if ex.get("enabled")
        ]
        counts = persist_scan(conn, scan, fee_rows,
                              config_hash=config_hash(cfg), git_sha=git_sha())
        return counts, "persistido no schema local_arb"
    except Exception as exc:  # noqa: BLE001 — persistência é best-effort, coleta não pode crashar
        return None, f"persistência falhou ({type(exc).__name__}: {exc}) — rode --init-db antes"
    finally:
        conn.close()


def cmd_once(cfg: dict, synthetic: bool) -> int:
    scan, mode = _run_scan(cfg, synthetic)
    persisted, db_note = _maybe_persist(scan, cfg, synthetic)
    summary = scan_summary(scan, mode, persisted, db_note)
    summary["config_hash"] = config_hash(cfg)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_report(cfg: dict, synthetic: bool, data_dir: Path) -> int:
    scan, mode = _run_scan(cfg, synthetic)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = generate_daily_report(scan, cfg, date_str, data_dir=data_dir, mode=mode)
    print(f"[report] gerado: {md_path}")
    return 0


def cmd_research_routes(rcfg: dict, synthetic: bool) -> int:
    scan = run_research_routes(rcfg, synthetic=synthetic)
    summary = scan.summary()
    summary["research_config_hash"] = research_config_hash(rcfg)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_research_report(rcfg: dict, synthetic: bool, data_dir: Path) -> int:
    scan = run_research_routes(rcfg, synthetic=synthetic)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = generate_research_report(scan, rcfg, date_str, data_dir=data_dir,
                                       config_hash=research_config_hash(rcfg))
    print(f"[research-report] gerado: {md_path}")
    return 0


def cmd_observer_once(cfg: dict, synthetic: bool, data_dir: Path) -> int:
    """Um ciclo do Continuous Observer: persiste CSV, sem paper e sem execução."""
    scan, mode = _run_scan(cfg, synthetic, paper_enabled=False)
    persisted = persist_observer_scan(scan, cfg, data_dir, config_hash=config_hash(cfg), mode=mode)
    summary = observer_summary(scan, persisted, mode=f"observer_{mode}")
    summary["config_hash"] = config_hash(cfg)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_observer_report(data_dir: Path) -> int:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = generate_observer_report(data_dir, date_str)
    print(f"[observer-report] gerado: {md_path}")
    return 0


def cmd_observer_loop(cfg: dict, synthetic: bool, data_dir: Path,
                      poll_seconds: float | None, max_cycles: int | None) -> int:
    """Loop observacional contínuo. Sem paper e sem execução real."""
    interval = float(poll_seconds if poll_seconds is not None
                     else cfg.get("observer", {}).get("poll_seconds", 10))
    cycle = 0
    while True:
        cycle += 1
        scan, mode = _run_scan(cfg, synthetic, paper_enabled=False)
        persisted = persist_observer_scan(scan, cfg, data_dir, config_hash=config_hash(cfg), mode=mode)
        summary = observer_summary(scan, persisted, mode=f"observer_{mode}")
        print(
            f"[observer-loop] cycle={cycle} books_ok={summary['n_books_ok']}/{summary['n_books']} "
            f"windows={summary['n_spread_windows']} candidates={summary['n_candidate_windows']} "
            f"watch={summary['n_watch_windows']} best_net={summary['best_net_bps']}",
            flush=True,
        )
        if max_cycles is not None and cycle >= max_cycles:
            return 0
        jitter = float(cfg.get("observer", {}).get("jitter_seconds", 0) or 0)
        sleep_s = interval + (random.uniform(0, jitter) if jitter > 0 else 0.0)
        time.sleep(max(0.0, sleep_s))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local_arb", description=__doc__)
    parser.add_argument("--status", action="store_true", help="imprime config_hash/git_sha/role")
    parser.add_argument("--self-test", action="store_true", help="roda cenário sintético offline")
    parser.add_argument("--init-db", action="store_true",
                        help="garante schema local_arb no Postgres (não destrutivo; no-db = dry-run)")
    parser.add_argument("--once", action="store_true",
                        help="1 ciclo coleta+scan; JSON no stdout (e persiste no DB se disponível)")
    parser.add_argument("--synthetic", action="store_true",
                        help="usa adapters sintéticos offline (sem rede)")
    parser.add_argument("--report", action="store_true",
                        help="gera relatório diário (md+csv) em <data-dir>/reports/")
    parser.add_argument("--research-routes", action="store_true",
                        help="1 coleta monitor-only das research routes; JSON no stdout")
    parser.add_argument("--research-report", action="store_true",
                        help="gera research_report_<data>.md (+csv) em <data-dir>/reports/")
    parser.add_argument("--observer-once", action="store_true",
                        help="1 ciclo Continuous Observer; persiste CSV sem paper/execução")
    parser.add_argument("--observer-report", action="store_true",
                        help="gera observer_report_<data>.md a partir dos CSVs do observer")
    parser.add_argument("--observer-loop", action="store_true",
                        help="loop Continuous Observer; persiste CSV sem paper/execução")
    parser.add_argument("--poll-seconds", type=float, default=None,
                        help="intervalo do --observer-loop; default vem do YAML")
    parser.add_argument("--max-cycles", type=int, default=None,
                        help="limite opcional de ciclos do --observer-loop para smoke/teste")
    parser.add_argument("--research-config", default=None,
                        help="caminho alternativo do research_routes.yaml")
    parser.add_argument("--data-dir", default=None,
                        help="diretório de dados (default: env LOCAL_ARB_DATA_DIR, "
                             f"senão {DEFAULT_DATA_DIR})")
    parser.add_argument("--config", default=None, help="caminho alternativo do YAML")
    args = parser.parse_args(argv)

    cfg = load_config(args.config or DEFAULT_CONFIG_PATH)
    data_dir_arg = args.data_dir or os.environ.get("LOCAL_ARB_DATA_DIR")
    data_dir = Path(data_dir_arg) if data_dir_arg else DEFAULT_DATA_DIR

    ran = False
    rc = 0
    if args.init_db:
        ran = True
        rc = max(rc, cmd_init_db())
    if args.once:
        ran = True
        rc = max(rc, cmd_once(cfg, args.synthetic))
    if args.report:
        ran = True
        rc = max(rc, cmd_report(cfg, args.synthetic, data_dir))
    if args.observer_once:
        ran = True
        rc = max(rc, cmd_observer_once(cfg, args.synthetic, data_dir))
    if args.observer_report:
        ran = True
        rc = max(rc, cmd_observer_report(data_dir))
    if args.observer_loop:
        ran = True
        rc = max(rc, cmd_observer_loop(cfg, args.synthetic, data_dir,
                                      args.poll_seconds, args.max_cycles))
    if args.research_routes or args.research_report:
        rcfg = load_research_config(args.research_config or DEFAULT_RESEARCH_CONFIG_PATH)
        if args.research_routes:
            ran = True
            rc = max(rc, cmd_research_routes(rcfg, args.synthetic))
        if args.research_report:
            ran = True
            rc = max(rc, cmd_research_report(rcfg, args.synthetic, data_dir))
    if args.status:
        ran = True
        rc = max(rc, cmd_status(cfg))
    if args.self_test:
        ran = True
        rc = max(rc, cmd_self_test(cfg))

    if not ran:
        parser.print_help()
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
