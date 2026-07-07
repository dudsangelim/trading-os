"""Relatório das research routes: seção Markdown + CSVs em <data-dir>/reports/.

Gera `research_report_YYYY-MM-DD.md` (arquivo separado do daily_report, mais
simples de compor) contendo a seção `## Research routes` + detalhe por rota,
e `research_routes_YYYY-MM-DD.csv` com uma linha por rota.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .daily_report import DEFAULT_DATA_DIR
from .research_routes import ResearchScan


def research_routes_section(scan: ResearchScan) -> list[str]:
    """Linhas Markdown da seção `## Research routes` (embutível em outro MD)."""
    lines = [
        "## Research routes",
        "",
        f"- Modo: **{scan.mode}** (monitor-only — apenas APIs públicas de "
        "corretoras locais, Binance e BCB/PTAX; sem execução, sem credenciais)",
        f"- Rotas executadas: {len(scan.results)}"
        + (f" | desabilitadas: {', '.join(scan.disabled)}" if scan.disabled else ""),
        "",
        "| Rota | Kind | Status | Sinal (bps) | Fontes ok | Notas |",
        "|---|---|---|---|---|---|",
    ]
    for r in scan.results:
        ok_sources = sum(1 for v in r.sources.values() if v == "ok")
        signal = f"{r.signal_bps:+.1f}" if r.signal_bps is not None else "—"
        lines.append(
            f"| {r.route} | {r.kind} | **{r.status}** | {signal} "
            f"| {ok_sources}/{len(r.sources) or 0} | {'; '.join(r.notes) or '—'} |"
        )

    for r in scan.results:
        lines += ["", f"### {r.route}", ""]
        lines.append(f"- Status: **{r.status}**"
                     + (f" | sinal: {r.signal_bps:+.2f} bps" if r.signal_bps is not None else ""))
        for note in r.notes:
            lines.append(f"- Nota: {note}")
        if r.metrics:
            lines.append(f"- Métricas: `{json.dumps(r.metrics, ensure_ascii=False, sort_keys=True)}`")
        if r.sources:
            lines.append("- Fontes:")
            for src, state in sorted(r.sources.items()):
                lines.append(f"  - `{src}`: {state}")
    lines.append("")
    return lines


def write_research_csv(scan: ResearchScan, reports_dir: Path, date_str: str) -> Path:
    path = reports_dir / f"research_routes_{date_str}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "route", "kind", "status", "signal_bps",
                         "sources_ok", "sources_total", "notes", "metrics_json"])
        for r in scan.results:
            writer.writerow([
                r.ts, r.route, r.kind, r.status,
                f"{r.signal_bps:.4f}" if r.signal_bps is not None else "",
                sum(1 for v in r.sources.values() if v == "ok"), len(r.sources),
                "; ".join(r.notes),
                json.dumps(r.metrics, ensure_ascii=False, sort_keys=True),
            ])
    return path


def generate_research_report(
    scan: ResearchScan,
    cfg: dict,
    date_str: str,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    config_hash: str | None = None,
) -> Path:
    """Escreve research_report_<date>.md + CSV e retorna o path do MD."""
    reports_dir = Path(data_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    csv_path = write_research_csv(scan, reports_dir, date_str)

    lines = [
        f"# local_arb — research routes {date_str}",
        "",
        "MONITOR-ONLY: nenhuma ordem é enviada; nenhuma credencial é usada.",
        "",
    ]
    if config_hash:
        lines += [f"- research_config_hash: `{config_hash}`", ""]
    lines += research_routes_section(scan)
    lines += [f"CSV: {csv_path.name}", ""]

    md_path = reports_dir / f"research_report_{date_str}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path
