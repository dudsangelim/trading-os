"""Relatório diário do local_arb: Markdown + CSVs em data/reports/.

Gera daily_report_YYYY-MM-DD.md com a linha obrigatória
`STATUS DA TESE: VIVA|FERIDA|MORTA|HOLD` e CSVs básicos (snapshots, spreads,
oportunidades, paper trades) a partir de um ScanResult em memória (synthetic
ou coleta real do dia).
"""

from __future__ import annotations

import csv
from pathlib import Path

from .report import thesis_status
from .scanner import ScanResult

DEFAULT_DATA_DIR = Path(__file__).parent / "data"


def _write_csv(path: Path, header: list[str], rows: list[list]) -> Path | None:
    if not rows:
        return None
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def generate_daily_report(
    scan: ScanResult,
    cfg: dict,
    date_str: str,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    mode: str = "synthetic",
) -> Path:
    """Escreve daily_report_<date>.md (+ CSVs quando há dados) e retorna o path do MD."""
    reports_dir = Path(data_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    filled = [t for t in scan.paper_trades if t.status == "filled"]
    n = len(filled)
    avg_net = sum(t.realized_net_bps for t in filled) / n if n else 0.0
    avg_decay = sum(t.decay_bps for t in filled) / n if n else 0.0
    rcfg = cfg["report"]
    verdict = thesis_status(
        n_trades=n,
        avg_realized_net_bps=avg_net,
        avg_decay_bps=avg_decay,
        min_trades_for_verdict=int(rcfg["min_trades_for_verdict"]),
        viva_min_net_bps=float(rcfg["viva_min_net_bps"]),
        ferida_max_decay_bps=float(rcfg["ferida_max_decay_bps"]),
    )

    csv_paths = [
        _write_csv(
            reports_dir / f"snapshots_{date_str}.csv",
            ["ts", "exchange", "symbol", "ok", "best_bid", "best_ask", "quality", "flags", "error"],
            [
                [
                    b.book.ts if b.book else scan.ts, b.exchange, b.symbol, b.ok,
                    b.book.best_bid if b.book else "", b.book.best_ask if b.book else "",
                    b.quality, "|".join(b.flags), b.error or "",
                ]
                for b in scan.books
            ],
        ),
        _write_csv(
            reports_dir / f"spread_windows_{date_str}.csv",
            ["ts", "buy_exchange", "sell_exchange", "size_quote", "gross_bps",
             "break_even_bps", "net_bps", "depth_ok", "quality_buy", "quality_sell"],
            [
                [w.ts, w.buy_exchange, w.sell_exchange, w.size_quote,
                 f"{w.gross_spread_bps:.2f}", f"{w.break_even_bps:.2f}", f"{w.net_bps:.2f}",
                 w.depth_ok, w.quality_buy, w.quality_sell]
                for w in scan.spread_windows
            ],
        ),
        _write_csv(
            reports_dir / f"opportunities_{date_str}.csv",
            ["ts", "buy_exchange", "sell_exchange", "size_quote", "gross_bps",
             "break_even_bps", "net_bps"],
            [
                [o.ts, o.buy_exchange, o.sell_exchange, o.size_quote,
                 f"{o.gross_spread_bps:.2f}", f"{o.break_even_bps:.2f}", f"{o.net_bps:.2f}"]
                for o in scan.opportunities
            ],
        ),
        _write_csv(
            reports_dir / f"paper_trades_{date_str}.csv",
            ["trade_id", "ts", "buy_exchange", "sell_exchange", "size_quote", "status",
             "fill_ratio", "expected_net_bps", "realized_net_bps", "decay_bps"],
            [
                [t.trade_id, t.ts, t.buy_exchange, t.sell_exchange, t.size_quote, t.status,
                 f"{t.fill_ratio:.4f}", f"{t.expected_net_bps:.2f}",
                 f"{t.realized_net_bps:.2f}", f"{t.decay_bps:.2f}"]
                for t in scan.paper_trades
            ],
        ),
    ]
    csv_written = [p.name for p in csv_paths if p is not None]

    n_ok = sum(1 for b in scan.books if b.ok)
    lines = [
        f"# local_arb — relatório diário {date_str}",
        "",
        f"STATUS DA TESE: {verdict.status}",
        "",
        f"- Modo: **{mode}** (paper/research only — sem execução real)",
        f"- Par: {scan.symbol}",
        f"- Books coletados: {n_ok}/{len(scan.books)} ok",
        f"- Janelas de spread: {len(scan.spread_windows)}",
        f"- Oportunidades (net ≥ {scan.min_net_bps:.1f} bps): {len(scan.opportunities)}",
        f"- Paper trades: {len(scan.paper_trades)} ({n} filled)",
        "",
        "## Fontes",
        "",
        "| Exchange | OK | Quality | Flags | Erro |",
        "|---|---|---|---|---|",
    ]
    for b in scan.books:
        lines.append(
            f"| {b.exchange} | {'sim' if b.ok else 'NÃO'} | {b.quality} "
            f"| {', '.join(b.flags) or '—'} | {b.error or '—'} |"
        )

    lines += ["", "## Melhores janelas de spread", ""]
    if scan.spread_windows:
        lines += [
            "| Rota | Size (BRL) | Gross (bps) | Break-even (bps) | Net (bps) | Depth |",
            "|---|---|---|---|---|---|",
        ]
        top = sorted(scan.spread_windows, key=lambda w: w.net_bps, reverse=True)[:10]
        for w in top:
            lines.append(
                f"| {w.buy_exchange}→{w.sell_exchange} | {w.size_quote:.0f} "
                f"| {w.gross_spread_bps:.1f} | {w.break_even_bps:.1f} "
                f"| {w.net_bps:.1f} | {'ok' if w.depth_ok else 'raso'} |"
            )
    else:
        lines.append("Sem janelas de spread (menos de 2 books saudáveis).")

    lines += ["", "## Paper trades", ""]
    if n:
        lines.append(
            f"n={n} filled | net médio realizado **{avg_net:.1f} bps** "
            f"| decay médio **{avg_decay:.1f} bps**"
        )
    else:
        lines.append("Nenhum paper trade filled no período.")

    lines += [
        "",
        "## Veredito",
        "",
        f"Status: **{verdict.status}** (linha canônica no topo do relatório)",
        "",
        *[f"- {r}" for r in verdict.reasons],
        "",
        f"CSVs: {', '.join(csv_written) if csv_written else 'nenhum (sem dados)'}",
        "",
    ]

    md_path = reports_dir / f"daily_report_{date_str}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path
