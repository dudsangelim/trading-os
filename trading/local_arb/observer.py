"""Continuous Observer v3.3: persistência CSV e relatórios sem paper/execução.

Este módulo é deliberadamente file-based: serve para pesquisa observacional com
endpoints públicos, sem API key, sem paper contínuo e sem capital.

v3.3 (revisão Fable 5):
- Rotação diária: escreve observer_snapshots_YYYY-MM-DD.csv e
  observer_spread_windows_YYYY-MM-DD.csv (data UTC do ciclo). Os monólitos
  legados (observer_snapshots.csv / observer_spread_windows.csv) NÃO são mais
  escritos, mas continuam sendo lidos pelo relatório via streaming com filtro
  de data — compatibilidade preservada.
- Janelas carregam source (rest|synthetic|mixed) + skew_ms/max_skew_ms; janelas
  com skew acima do limite chegam aqui já rejeitadas pelo scanner
  (rejected_by_skew) e nunca contam como candidate/watch.
- Modo synthetic grava em observer_synthetic/ (nunca no observer/ vivo).
- Knobs persist_all_snapshots / persist_candidate_windows / persist_rejections
  controlam o volume de escrita (ver YAML).
- Relatório agrega o DIA (não um scan único) e emite STATUS DA TESE
  VIVA/FERIDA/MORTA/HOLD por contagem de janelas acima do gate de execução.
"""

from __future__ import annotations

import csv
import heapq
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .report import ThesisVerdict
from .scanner import ScanResult, SpreadWindow

OBSERVER_DIR = "observer"
OBSERVER_SYNTHETIC_DIR = "observer_synthetic"
# monólitos legados (pré-rotação): só leitura, nunca mais escritos
SNAPSHOT_FILE = "observer_snapshots.csv"
SPREAD_FILE = "observer_spread_windows.csv"

SNAPSHOT_FIELDS = [
    "timestamp_local", "exchange", "pair", "best_bid", "best_ask", "mid",
    "depth_500_brl", "depth_1000_brl", "depth_2500_brl", "depth_5000_brl",
    "latency_ms", "quality_score", "flags", "error", "source", "config_hash",
    # v3.3: timestamp_local passou a ser o ts REAL do fetch de cada book;
    # cycle_ts preserva o ts único do ciclo para agrupar coletas
    "cycle_ts",
]

SPREAD_FIELDS = [
    "timestamp_local", "pair", "route_buy_exchange", "route_sell_exchange", "size_brl",
    "gross_bps", "break_even_bps", "net_bps", "depth_ok", "quality_buy", "quality_sell",
    "latency_buy_ms", "latency_sell_ms", "candidate_gate_pass", "watch_gate_pass",
    "execution_gate_theoretical_pass", "reason_rejected", "reject_reasons",
    "capital_required_brl", "capital_efficiency_bps", "config_hash",
    # v3.3: origem dos books e skew real entre os fetches de buy/sell
    "source", "skew_ms", "max_skew_ms",
]

# defaults conservadores do veredito agregado (sobrescritos pelo YAML observer:)
DEFAULT_VERDICT_MIN_WINDOWS = 500
DEFAULT_VERDICT_VIVA_MIN_EXECUTION_WINDOWS = 10


def observer_dir(data_dir: Path, mode: str = "rest") -> Path:
    """Diretório físico por origem: sintético NUNCA divide arquivo com o vivo."""
    sub = OBSERVER_DIR if mode == "rest" else OBSERVER_SYNTHETIC_DIR
    return Path(data_dir) / sub


def utc_date_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def snapshot_daily_path(data_dir: Path, date_str: str, mode: str = "rest") -> Path:
    return observer_dir(data_dir, mode) / f"observer_snapshots_{date_str}.csv"


def spread_daily_path(data_dir: Path, date_str: str, mode: str = "rest") -> Path:
    return observer_dir(data_dir, mode) / f"observer_spread_windows_{date_str}.csv"


def _append_csv(path: Path, fields: list[str], rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})
    return len(rows)


def _depth_quote(levels, max_quote: float) -> float:
    acc = 0.0
    for level in levels:
        acc += min(level.notional, max(0.0, max_quote - acc))
        if acc >= max_quote:
            return max_quote
    return acc


def _snapshot_rows(scan: ScanResult, sizes: list[float], config_hash: str) -> list[dict]:
    rows = []
    for b in scan.books:
        book = b.book
        bid_depths = {size: _depth_quote(book.bids, size) if book else None for size in sizes}
        rows.append({
            # ts real do fetch deste book; sem book (falha), cai no ts do ciclo
            "timestamp_local": book.ts if book else scan.ts,
            "cycle_ts": scan.ts,
            "exchange": b.exchange,
            "pair": b.symbol,
            "best_bid": book.best_bid if book else None,
            "best_ask": book.best_ask if book else None,
            "mid": book.mid if book else None,
            "depth_500_brl": bid_depths.get(500.0),
            "depth_1000_brl": bid_depths.get(1000.0),
            "depth_2500_brl": bid_depths.get(2500.0),
            "depth_5000_brl": bid_depths.get(5000.0),
            "latency_ms": book.latency_ms if book else None,
            "quality_score": b.quality,
            "flags": ",".join(b.flags),
            "error": b.error,
            "source": b.source,
            "config_hash": config_hash,
        })
    return rows


def _book_lookup(scan: ScanResult):
    return {b.exchange: b for b in scan.books}


def _capital_efficiency_bps(w: SpreadWindow) -> float:
    # Observacional e conservador: a janela exige capital nas duas pontas.
    capital_required = max(w.size_quote * 2.0, 1.0)
    theoretical_profit = w.size_quote * (w.net_bps / 10_000.0)
    return (theoretical_profit / capital_required) * 10_000.0


def _spread_rows(scan: ScanResult, config_hash: str) -> list[dict]:
    books = _book_lookup(scan)
    rows = []
    for w in scan.spread_windows:
        buy = books.get(w.buy_exchange)
        sell = books.get(w.sell_exchange)
        rows.append({
            "timestamp_local": w.ts,
            "pair": w.symbol,
            "route_buy_exchange": w.buy_exchange,
            "route_sell_exchange": w.sell_exchange,
            "size_brl": w.size_quote,
            "gross_bps": round(w.gross_spread_bps, 6),
            "break_even_bps": round(w.break_even_bps, 6),
            "net_bps": round(w.net_bps, 6),
            "depth_ok": w.depth_ok,
            "quality_buy": w.quality_buy,
            "quality_sell": w.quality_sell,
            "latency_buy_ms": buy.book.latency_ms if buy and buy.book else None,
            "latency_sell_ms": sell.book.latency_ms if sell and sell.book else None,
            "candidate_gate_pass": w.candidate_gate_pass,
            "watch_gate_pass": w.watch_gate_pass,
            "execution_gate_theoretical_pass": w.execution_gate_theoretical_pass,
            "reason_rejected": w.reason_rejected,
            "reject_reasons": ",".join(w.reject_reasons),
            "capital_required_brl": w.size_quote * 2.0,
            "capital_efficiency_bps": round(_capital_efficiency_bps(w), 6),
            "config_hash": config_hash,
            "source": w.source,
            "skew_ms": round(w.skew_ms, 1),
            "max_skew_ms": w.max_skew_ms,
        })
    return rows


def persist_observer_scan(scan: ScanResult, cfg: dict, data_dir: Path,
                          config_hash: str = "", mode: str = "rest") -> dict[str, int]:
    """Append-only CSV com rotação diária (data UTC do ciclo).

    mode="rest" escreve em <data_dir>/observer/; qualquer outro modo escreve em
    <data_dir>/observer_synthetic/ — dado sintético nunca compartilha arquivo
    com a coleta viva.

    Knobs de volume (cfg observer:):
    - persist_all_snapshots: false = snapshots só em ciclos com >=1 candidate.
    - persist_candidate_windows / persist_rejections: filtram as janelas
      persistidas por candidate_gate_pass True/False respectivamente.
    """
    obs_cfg = cfg.get("observer", {}) or {}
    persist_all_snapshots = bool(obs_cfg.get("persist_all_snapshots", True))
    persist_candidates = bool(obs_cfg.get("persist_candidate_windows", True))
    persist_rejections = bool(obs_cfg.get("persist_rejections", True))

    sizes = [float(s) for s in cfg.get("simulation", {}).get("sizes_quote_brl", [500, 1000, 2500, 5000])]
    date_str = utc_date_str(scan.ts)

    has_candidate = any(w.candidate_gate_pass for w in scan.spread_windows)
    snap_rows = _snapshot_rows(scan, sizes, config_hash) if (persist_all_snapshots or has_candidate) else []
    spread_rows = [
        row for w, row in zip(scan.spread_windows, _spread_rows(scan, config_hash))
        if (persist_candidates if w.candidate_gate_pass else persist_rejections)
    ]

    return {
        "observer_snapshots": _append_csv(
            snapshot_daily_path(data_dir, date_str, mode), SNAPSHOT_FIELDS, snap_rows),
        "observer_spread_windows": _append_csv(
            spread_daily_path(data_dir, date_str, mode), SPREAD_FIELDS, spread_rows),
    }


def observer_summary(scan: ScanResult, persisted: dict | None = None, mode: str = "observer") -> dict:
    reasons = Counter(w.reason_rejected for w in scan.spread_windows)
    best_net = max((w.net_bps for w in scan.spread_windows), default=None)
    return {
        "engine_role": "local_arb_research",
        "mode": mode,
        "paper_enabled": False,
        "ts": scan.ts,
        "symbol": scan.symbol,
        "n_books_ok": sum(1 for b in scan.books if b.ok),
        "n_books": len(scan.books),
        "n_spread_windows": len(scan.spread_windows),
        "n_candidate_windows": sum(1 for w in scan.spread_windows if w.candidate_gate_pass),
        "n_watch_windows": sum(1 for w in scan.spread_windows if w.watch_gate_pass),
        "n_execution_theoretical_windows": sum(
            1 for w in scan.spread_windows if w.execution_gate_theoretical_pass),
        "n_skew_rejected_windows": sum(1 for w in scan.spread_windows if not w.skew_ok),
        "max_skew_ms_observed": round(max((w.skew_ms for w in scan.spread_windows), default=0.0), 1),
        "best_net_bps": best_net,
        "reason_rejected_counts": dict(reasons.most_common()),
        "paper_trades": len(scan.paper_trades),
        "persisted": persisted,
    }


def _float(row: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def _hour_bucket(ts: float) -> str:
    # UTC no armazenamento; relatório operacional usa bucket simples por hora.
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{dt.hour:02d}:00-{dt.hour:02d}:59 UTC"


def iter_spread_rows_for_date(data_dir: Path, date_str: str, mode: str = "rest") -> Iterator[dict]:
    """Streaming das janelas do dia: arquivo diário novo + monólito legado filtrado.

    O monólito legado (pré-rotação, potencialmente centenas de MB) é lido linha a
    linha com filtro de data — nunca materializado inteiro em memória.
    """
    daily = spread_daily_path(data_dir, date_str, mode)
    if daily.exists():
        with daily.open(encoding="utf-8") as fh:
            yield from csv.DictReader(fh)
    legacy = observer_dir(data_dir, mode) / SPREAD_FILE
    if legacy.exists():
        with legacy.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if utc_date_str(_float(row, "timestamp_local")) == date_str:
                    yield row


def observer_verdict(n_windows: int, n_execution: int, cfg: dict | None = None) -> ThesisVerdict:
    """Veredito agregado do dia, por contagem de janelas — conservador:

    HOLD   — menos janelas válidas que verdict_min_windows (amostra insuficiente).
    MORTA  — amostra suficiente e ZERO janelas acima do gate de execução
             (sem evidência de arb executável no dia).
    FERIDA — alguma janela acima do gate, mas menos que o alvo VIVA.
    VIVA   — >= verdict_viva_min_execution_windows janelas acima do gate.
    """
    obs = (cfg or {}).get("observer", {}) or {}
    min_windows = int(obs.get("verdict_min_windows", DEFAULT_VERDICT_MIN_WINDOWS))
    viva_min = int(obs.get("verdict_viva_min_execution_windows",
                           DEFAULT_VERDICT_VIVA_MIN_EXECUTION_WINDOWS))

    if n_windows < min_windows:
        return ThesisVerdict(
            "HOLD", [f"amostra insuficiente: {n_windows}/{min_windows} janelas válidas no dia"])
    if n_execution == 0:
        return ThesisVerdict(
            "MORTA", [f"sem evidência: 0 janelas com net ≥ gate de execução em {n_windows} janelas"])
    if n_execution >= viva_min:
        return ThesisVerdict(
            "VIVA", [f"{n_execution} janelas acima do gate de execução em {n_windows} janelas "
                     f"(alvo ≥ {viva_min})"])
    return ThesisVerdict(
        "FERIDA", [f"evidência fraca: {n_execution} janelas acima do gate de execução "
                   f"(alvo VIVA ≥ {viva_min}) em {n_windows} janelas"])


def _aggregate_rows(rows: Iterator[dict]) -> dict:
    """Uma passada, memória limitada: contadores + heap top-20 + buckets por hora."""
    agg = {
        "n_total": 0,
        "n_synthetic_excluded": 0,   # source != rest (synthetic/mixed) fora do veredito
        "n_valid": 0,                # linhas rest (ou legado sem source)
        "n_candidate": 0,
        "n_watch": 0,
        "n_execution": 0,
        "n_skew_rejected": 0,
        "reasons": Counter(),
        "by_hour": defaultdict(lambda: {"n": 0, "max_net": None, "n_pos": 0}),
    }
    top_heap: list[tuple[float, int, dict]] = []  # (net_bps, seq, row) — seq desempata
    for seq, row in enumerate(rows):
        agg["n_total"] += 1
        source = (row.get("source") or "rest").strip() or "rest"  # legado sem coluna = rest
        if source != "rest":
            agg["n_synthetic_excluded"] += 1
            continue
        agg["n_valid"] += 1
        agg["reasons"][row.get("reason_rejected") or "unknown"] += 1
        if row.get("candidate_gate_pass") == "True":
            agg["n_candidate"] += 1
        if row.get("watch_gate_pass") == "True":
            agg["n_watch"] += 1
        if row.get("execution_gate_theoretical_pass") == "True":
            agg["n_execution"] += 1
        if "rejected_by_skew" in (row.get("reject_reasons") or ""):
            agg["n_skew_rejected"] += 1

        net = _float(row, "net_bps")
        bucket = agg["by_hour"][_hour_bucket(_float(row, "timestamp_local"))]
        bucket["n"] += 1
        bucket["max_net"] = net if bucket["max_net"] is None else max(bucket["max_net"], net)
        if net > 0:
            bucket["n_pos"] += 1

        item = (net, seq, row)
        if len(top_heap) < 20:
            heapq.heappush(top_heap, item)
        elif item > top_heap[0]:
            heapq.heapreplace(top_heap, item)
    agg["top_net"] = [row for _, _, row in sorted(top_heap, key=lambda t: t[0], reverse=True)]
    return agg


def generate_observer_report(data_dir: Path, date_str: str,
                             cfg: dict | None = None, mode: str = "rest") -> Path:
    """Markdown do observer agregando SÓ as janelas do dia (diário + legado filtrado)."""
    agg = _aggregate_rows(iter_spread_rows_for_date(data_dir, date_str, mode))
    verdict = observer_verdict(agg["n_valid"], agg["n_execution"], cfg)
    reports_dir = Path(data_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if mode == "rest" else f"_{mode}"
    path = reports_dir / f"observer_report_{date_str}{suffix}.md"

    lines = [
        f"# local_arb — Continuous Observer {date_str}",
        "",
        f"STATUS DA TESE: {verdict.status}",
        "",
        "STATUS: OBSERVER_ONLY — sem capital, sem paper contínuo, sem execução real.",
        "",
        "## Veredito agregado do dia",
        "",
        f"- Status: **{verdict.status}**",
        *[f"- {r}" for r in verdict.reasons],
        f"- Janelas válidas (source=rest): {agg['n_valid']}",
        f"- Candidate: {agg['n_candidate']} | Watch: {agg['n_watch']} "
        f"| Execution-theoretical: {agg['n_execution']}",
        f"- Rejeitadas por skew entre books: {agg['n_skew_rejected']}",
    ]
    if agg["n_synthetic_excluded"]:
        lines.append(
            f"- Excluídas do veredito (source synthetic/mixed): {agg['n_synthetic_excluded']}")
    lines += [
        "",
        "## Resumo",
        "",
        f"- Spread windows analisadas ({date_str}): {agg['n_valid']}",
        f"- Candidate windows: {agg['n_candidate']}",
        f"- Watch windows: {agg['n_watch']}",
        f"- Execution-theoretical windows: {agg['n_execution']}",
        "",
        "## Reason rejected",
        "",
        "| Motivo | N |",
        "|---|---:|",
    ]
    for reason, count in agg["reasons"].most_common():
        lines.append(f"| {reason} | {count} |")

    lines += ["", "## Análise por horário", "", "| Hora | N | Max net bps | Posição |", "|---|---:|---:|---|"]
    for hour, b in sorted(agg["by_hour"].items()):
        max_net = b["max_net"] if b["max_net"] is not None else 0.0
        lines.append(f"| {hour} | {b['n']} | {max_net:.2f} | {b['n_pos']} positivas |")

    lines += ["", "## Top janelas líquidas", "",
              "| Buy | Sell | Size BRL | Gross bps | Break-even bps | Net bps | Skew ms | Rejeição |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for row in agg["top_net"]:
        skew = row.get("skew_ms")
        lines.append(
            f"| {row.get('route_buy_exchange')} | {row.get('route_sell_exchange')} "
            f"| {_float(row, 'size_brl'):.0f} | {_float(row, 'gross_bps'):.2f} "
            f"| {_float(row, 'break_even_bps'):.2f} | {_float(row, 'net_bps'):.2f} "
            f"| {skew if skew not in (None, '') else '—'} "
            f"| {row.get('reason_rejected')} |"
        )

    lines += [
        "",
        "## Interpretação",
        "",
        "Este relatório não autoriza trade. Ele existe para provar ou matar a tese intraday.",
        "O veredito agrega janelas do dia inteiro; um scan isolado nunca decide nada.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
