"""Continuous Observer v3.4: persistência CSV e relatórios sem paper/execução.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Iterable, Iterator

from .report import ThesisVerdict
from .scanner import ScanResult, SpreadWindow

OBSERVER_DIR = "observer"
OBSERVER_SYNTHETIC_DIR = "observer_synthetic"
# monólitos legados (pré-rotação): só leitura, nunca mais escritos
SNAPSHOT_FILE = "observer_snapshots.csv"
SPREAD_FILE = "observer_spread_windows.csv"
LIFECYCLE_PREFIX = "observer_candidate_lifecycle_"

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

LIFECYCLE_FIELDS = [
    "window_id", "route", "pair", "size_brl", "first_seen_ts", "last_seen_ts",
    "duration_seconds", "max_net_bps", "median_net_bps", "max_gross_bps",
    "min_break_even_bps", "n_observations", "status_final",
]

# O PRD exige decisão apenas após 7–14 dias; contagem diária nunca mata a tese.
DEFAULT_MIN_DAYS_FOR_DECISION = 7
DEFAULT_MIN_VALID_OBSERVATIONS_PER_DAY = 500
DEFAULT_LIFECYCLE_MAX_GAP_SECONDS = 30.0
DEFAULT_MIN_DURATION_SECONDS = 3.0
PRD_ADVANCE_GATES = {"positive": 30, "above_10": 10, "above_20": 3}


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


def lifecycle_daily_path(data_dir: Path, date_str: str, mode: str = "rest") -> Path:
    return observer_dir(data_dir, mode) / f"{LIFECYCLE_PREFIX}{date_str}.csv"


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
    """Leitura observacional do DIA — deliberadamente NÃO-LETAL (PRD §18).

    Um único dia nunca mata nem valida a tese: a decisão VIVA/MORTA/ADVANCE só
    acontece na agregação multi-dia (ver `multi_day_thesis_decision`). Aqui só
    reportamos o que o dia mostrou, sem veredito terminal:

    NEED_MORE_DATA — menos janelas válidas que o piso diário (amostra fraca).
    NO_EDGE_TODAY  — amostra suficiente e ZERO janelas acima do gate de execução.
    EDGE_TODAY     — >= 1 janela acima do gate de execução no dia.
    """
    obs = (cfg or {}).get("observer", {}) or {}
    min_windows = int(obs.get("verdict_min_windows",
                              obs.get("min_valid_observations_per_day",
                                      DEFAULT_MIN_VALID_OBSERVATIONS_PER_DAY)))

    if n_windows < min_windows:
        return ThesisVerdict(
            "NEED_MORE_DATA",
            [f"amostra insuficiente: {n_windows}/{min_windows} janelas válidas no dia "
             f"(decisão só na agregação multi-dia)"])
    if n_execution == 0:
        return ThesisVerdict(
            "NO_EDGE_TODAY",
            [f"0 janelas com net ≥ gate de execução em {n_windows} janelas hoje "
             f"(dia isolado não mata a tese)"])
    return ThesisVerdict(
        "EDGE_TODAY",
        [f"{n_execution} janelas acima do gate de execução em {n_windows} janelas hoje"])


def build_candidate_lifecycles(rows: Iterable[dict], cfg: dict | None = None) -> list[dict]:
    """Reconstrói o ciclo de vida de cada janela candidate a partir das linhas do dia.

    Uma "janela" é a tupla (rota buy→sell, par, size_brl). Observações consecutivas
    da mesma janela formam UM ciclo de vida; um intervalo maior que
    lifecycle_max_gap_seconds segmenta em ciclos distintos (a oportunidade sumiu e
    reapareceu). Só entram observações com source=rest e candidate_gate_pass=True.
    Ciclos com duração < min_duration_seconds são descartados (ruído de 1 tick).

    Retorna as linhas prontas para LIFECYCLE_FIELDS, ordenadas por início.
    """
    obs = (cfg or {}).get("observer", {}) or {}
    max_gap = float(obs.get("lifecycle_max_gap_seconds", DEFAULT_LIFECYCLE_MAX_GAP_SECONDS))
    min_duration = float(obs.get("lifecycle_min_duration_seconds", DEFAULT_MIN_DURATION_SECONDS))

    # agrupa observações por janela; segmenta por gap temporal
    by_window: dict[tuple, list[tuple]] = defaultdict(list)
    for row in rows:
        source = (row.get("source") or "rest").strip() or "rest"
        if source != "rest" or row.get("candidate_gate_pass") != "True":
            continue
        key = (
            row.get("route_buy_exchange"), row.get("route_sell_exchange"),
            row.get("pair"), row.get("size_brl"),
        )
        by_window[key].append((
            _float(row, "timestamp_local"),
            _float(row, "net_bps"), _float(row, "gross_bps"),
            _float(row, "break_even_bps"),
            row.get("execution_gate_theoretical_pass") == "True",
        ))

    lifecycles: list[dict] = []
    seq = 0
    for (buy, sell, pair, size), obs_list in by_window.items():
        obs_list.sort(key=lambda t: t[0])
        segment: list[tuple] = []

        def flush(seg: list[tuple]) -> None:
            nonlocal seq
            if not seg:
                return
            first_ts, last_ts = seg[0][0], seg[-1][0]
            duration = last_ts - first_ts
            if duration < min_duration:
                return
            nets = [o[1] for o in seg]
            seq += 1
            lifecycles.append({
                "window_id": f"{pair}:{buy}->{sell}:{size}:{seq}",
                "route": f"{buy}->{sell}",
                "pair": pair,
                "size_brl": size,
                "first_seen_ts": round(first_ts, 3),
                "last_seen_ts": round(last_ts, 3),
                "duration_seconds": round(duration, 3),
                "max_net_bps": round(max(nets), 4),
                "median_net_bps": round(median(nets), 4),
                "max_gross_bps": round(max(o[2] for o in seg), 4),
                "min_break_even_bps": round(min(o[3] for o in seg), 4),
                "n_observations": len(seg),
                # reached_execution: chegou a cruzar o gate de execução em algum tick
                "status_final": "reached_execution" if any(o[4] for o in seg) else "candidate_only",
            })

        prev_ts = None
        for o in obs_list:
            if prev_ts is not None and (o[0] - prev_ts) > max_gap:
                flush(segment)
                segment = []
            segment.append(o)
            prev_ts = o[0]
        flush(segment)

    lifecycles.sort(key=lambda lc: lc["first_seen_ts"])
    return lifecycles


def write_lifecycles(data_dir: Path, date_str: str, lifecycles: list[dict],
                     mode: str = "rest") -> int:
    """Grava (overwrite) o CSV diário de ciclos de vida. Idempotente por dia."""
    path = lifecycle_daily_path(data_dir, date_str, mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LIFECYCLE_FIELDS)
        writer.writeheader()
        for lc in lifecycles:
            writer.writerow({field: lc.get(field) for field in LIFECYCLE_FIELDS})
    return len(lifecycles)


def _observed_daily_dates(data_dir: Path, mode: str = "rest") -> list[str]:
    """Datas UTC com arquivo diário de janelas (ordenadas). Monólito legado ignorado
    aqui de propósito: a decisão multi-dia usa a era rotacionada (v3.3+)."""
    directory = observer_dir(data_dir, mode)
    if not directory.exists():
        return []
    dates = []
    for path in sorted(directory.glob(f"observer_spread_windows_*.csv")):
        stem = path.name[len("observer_spread_windows_"):-len(".csv")]
        if len(stem) == 10 and stem[4] == "-" and stem[7] == "-":
            dates.append(stem)
    return sorted(dates)


def multi_day_thesis_decision(data_dir: Path, cfg: dict | None = None,
                              mode: str = "rest") -> ThesisVerdict:
    """Decisão terminal da tese sobre a janela de dias observados (PRD §18).

    Só esta função pode ADVANCE/KILL. Regras:
    - NEED_MORE_DATA enquanto houver menos de min_days_for_decision dias com
      amostra válida suficiente (>= piso diário de janelas válidas).
    - ADVANCE se, somando os dias válidos, os três gates do PRD forem batidos:
      >= PRD_ADVANCE_GATES['positive'] janelas com net>0,
      >= PRD_ADVANCE_GATES['above_10'] com net>=10 bps,
      >= PRD_ADVANCE_GATES['above_20'] com net>=20 bps.
    - KILL caso a janela mínima de dias esteja cumprida e os gates não fecharem.
    """
    obs = (cfg or {}).get("observer", {}) or {}
    min_days = int(obs.get("min_days_for_decision", DEFAULT_MIN_DAYS_FOR_DECISION))
    min_valid = int(obs.get("verdict_min_windows",
                            obs.get("min_valid_observations_per_day",
                                    DEFAULT_MIN_VALID_OBSERVATIONS_PER_DAY)))
    gates = dict(PRD_ADVANCE_GATES)
    gates.update({k: int(v) for k, v in (obs.get("advance_gates") or {}).items()})

    valid_days = 0
    tot = {"positive": 0, "above_10": 0, "above_20": 0}
    for date_str in _observed_daily_dates(data_dir, mode):
        n_valid = 0
        day = {"positive": 0, "above_10": 0, "above_20": 0}
        for row in iter_spread_rows_for_date(data_dir, date_str, mode):
            if (row.get("source") or "rest").strip() not in ("", "rest"):
                continue
            n_valid += 1
            net = _float(row, "net_bps")
            if net > 0:
                day["positive"] += 1
            if net >= 10:
                day["above_10"] += 1
            if net >= 20:
                day["above_20"] += 1
        if n_valid >= min_valid:
            valid_days += 1
            for k in tot:
                tot[k] += day[k]

    if valid_days < min_days:
        return ThesisVerdict(
            "NEED_MORE_DATA",
            [f"apenas {valid_days}/{min_days} dias com amostra válida "
             f"(>= {min_valid} janelas/dia) — sem decisão terminal ainda"])

    met = {k: tot[k] >= gates[k] for k in gates}
    detail = (f"net>0: {tot['positive']}/{gates['positive']}, "
              f"net>=10bps: {tot['above_10']}/{gates['above_10']}, "
              f"net>=20bps: {tot['above_20']}/{gates['above_20']} "
              f"({valid_days} dias válidos)")
    if all(met.values()):
        return ThesisVerdict("ADVANCE", [f"todos os gates do PRD batidos — {detail}"])
    return ThesisVerdict(
        "KILL", [f"janela de {valid_days} dias fechada e gates não batidos — {detail}"])


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
    # ciclos de vida das janelas candidate do dia (segunda passada pelos CSVs do dia)
    lifecycles = build_candidate_lifecycles(
        iter_spread_rows_for_date(data_dir, date_str, mode), cfg)
    write_lifecycles(data_dir, date_str, lifecycles, mode)
    reached = sum(1 for lc in lifecycles if lc["status_final"] == "reached_execution")
    # decisão terminal só na agregação multi-dia (PRD §18) — um dia nunca decide
    thesis = multi_day_thesis_decision(data_dir, cfg, mode)
    reports_dir = Path(data_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if mode == "rest" else f"_{mode}"
    path = reports_dir / f"observer_report_{date_str}{suffix}.md"

    lines = [
        f"# local_arb — Continuous Observer {date_str}",
        "",
        f"DECISÃO DA TESE (multi-dia, PRD §18): {thesis.status}",
        f"LEITURA DO DIA (não-letal): {verdict.status}",
        "",
        "STATUS: OBSERVER_ONLY — sem capital, sem paper contínuo, sem execução real.",
        "",
        "## Decisão terminal da tese (agregação multi-dia)",
        "",
        f"- Decisão: **{thesis.status}**",
        *[f"- {r}" for r in thesis.reasons],
        "- Regra: um dia isolado nunca mata nem valida a tese; só a janela de "
        "dias observados decide ADVANCE/KILL.",
        "",
        "## Leitura observacional do dia (não-letal)",
        "",
        f"- Status do dia: **{verdict.status}**",
        *[f"- {r}" for r in verdict.reasons],
        f"- Janelas válidas (source=rest): {agg['n_valid']}",
        f"- Candidate: {agg['n_candidate']} | Watch: {agg['n_watch']} "
        f"| Execution-theoretical: {agg['n_execution']}",
        f"- Rejeitadas por skew entre books: {agg['n_skew_rejected']}",
        f"- Ciclos de vida de janelas candidate: {len(lifecycles)} "
        f"(chegaram ao gate de execução: {reached})",
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

    lines += ["", "## Ciclos de vida das janelas candidate (top 20 por duração)", "",
              "| Janela | Rota | Size BRL | Duração s | Obs | Max net bps | Mediana net bps | Status |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for lc in sorted(lifecycles, key=lambda x: x["duration_seconds"], reverse=True)[:20]:
        lines.append(
            f"| {lc['window_id']} | {lc['route']} | {_float(lc, 'size_brl'):.0f} "
            f"| {lc['duration_seconds']:.1f} | {lc['n_observations']} "
            f"| {lc['max_net_bps']:.2f} | {lc['median_net_bps']:.2f} | {lc['status_final']} |"
        )
    if not lifecycles:
        lines.append("| — | — | — | — | — | — | — | nenhum ciclo candidate no dia |")

    lines += [
        "",
        "## Interpretação",
        "",
        "Este relatório não autoriza trade. Ele existe para provar ou matar a tese.",
        "A LEITURA DO DIA é observacional e nunca terminal; a DECISÃO DA TESE só sai da "
        "agregação multi-dia (>= min_days_for_decision dias válidos) contra os gates do PRD.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
