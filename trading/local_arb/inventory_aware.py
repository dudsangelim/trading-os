"""Fase 3.3 — Inventory-aware simulator OFFLINE/read-only (pesquisa, sem execução).

Lê os CSVs do Continuous Observer (arquivo diário observer_spread_windows_YYYY-MM-DD.csv
+ monólito legado via streaming, nunca materializado inteiro) e reexecuta UM dia UTC
contra cenários de inventário pré-posicionado (PRD v3.2 §13-15), escrevendo um
relatório Markdown em <data_dir>/reports/inventory_aware_report_YYYY-MM-DD.md.

O que este módulo NÃO faz, por construção:
- não coleta nada (nem rede, nem DB) — só lê CSVs já persistidos pelo observer;
- não gera ordem, não altera CSVs do observer, não persiste estado próprio;
- não é paper contínuo: é reanálise offline de um dia por vez, sob demanda.

Modelo de simulação (deliberadamente simplificado — ler antes de confiar em número):
- Elegibilidade: source=rest, sem rejected_by_skew, candidate_gate_pass=True e
  net_eff = net_bps + rebalance_credit_bps >= min_net_bps. O crédito existe porque
  o net_bps gravado amortiza rebalanceamento por janela
  (break_even.rebalance_amortized_bps); na tese inventory-aware esse custo não é
  pago por janela — ele vira drain de inventário, que ESTE simulador mede
  explicitamente (creditar e medir o drain evita contar o custo duas vezes).
  Taker fees, slippage, latency haircut e fee_uncertainty seguem DENTRO do net.
- Dedup de tamanho: o observer grava uma janela por (ciclo, rota, tamanho); executar
  os 4 tamanhos somaria 4x o mesmo book. Por (ciclo, rota) tentamos o MAIOR tamanho
  elegível; se o inventário não cobre, caímos para o próximo menor; se nenhum couber,
  a janela conta como blocked_by_inventory.
- Execução de uma janela de S BRL na rota A→B com net efetivo n:
      A.BRL  -= S                          (compra taker na venue barata)
      A.USDT += S / reference_price_brl
      B.USDT -= S / reference_price_brl    (venda taker na venue cara)
      B.BRL  += S * (1 + n/10_000)
  Todos os custos ficam embutidos no crédito BRL da venda (o débito de compra é o
  notional cheio); a quantidade USDT usa preço de referência fixo — isso só afeta
  a conversão de inventário (~fee em bps de erro por giro), não o PnL: o delta de
  equity total do cenário é exatamente a soma dos PnLs teóricos.
- Cada linha do observer é uma foto a cada ~poll_seconds: executable_windows conta
  rota-ciclos executáveis nessa cadência, não janelas únicas de mercado.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .inventory import Ledger
from .observer import (
    SPREAD_FIELDS,
    SPREAD_FILE,
    _float,
    iter_spread_rows_for_date,
    observer_dir,
    observer_verdict,
    spread_daily_path,
    utc_date_str,
)

REPORT_PREFIX = "inventory_aware_report_"

# Gates do PRD v3.2 §18 (referência p/ 7-14 dias de coleta) — exibidos no relatório
PRD_GATES_7_14D = {"net>0": 30, "net>10": 10, "net>20": 3}

DEFAULT_INVENTORY_CFG = {
    "min_net_bps": 0.0,
    # None = usa break_even.rebalance_amortized_bps da config principal
    "rebalance_credit_bps": None,
    "reference_price_brl": 5.60,
    "drain_threshold_pct": 0.10,
    "decision": {
        "min_valid_windows": 500,
        "min_days_for_decision": 7,
        "advance_min_executable": 10,
        "blocked_max_share": 0.5,
    },
    "scenarios": {
        "balanced_all_venues": {"default_brl": 2000.0, "default_usdt": 400.0},
    },
}


def inventory_cfg(cfg: dict) -> dict:
    """Config efetiva: YAML inventory_aware por cima dos defaults do módulo."""
    out = {**DEFAULT_INVENTORY_CFG, **(cfg.get("inventory_aware") or {})}
    out["decision"] = {**DEFAULT_INVENTORY_CFG["decision"], **(out.get("decision") or {})}
    if not out.get("scenarios"):
        out["scenarios"] = dict(DEFAULT_INVENTORY_CFG["scenarios"])
    return out


def rebalance_credit_bps(cfg: dict) -> float:
    credit = (cfg.get("inventory_aware") or {}).get("rebalance_credit_bps")
    if credit is None:
        credit = (cfg.get("break_even") or {}).get("rebalance_amortized_bps", 0.0)
    return float(credit)


@dataclass(frozen=True)
class EligibleRow:
    ts: float
    buy_exchange: str
    sell_exchange: str
    size_brl: float
    net_bps: float       # como gravado pelo observer
    net_eff_bps: float   # net_bps + rebalance_credit_bps


@dataclass
class DayAggregate:
    """Uma passada de streaming sobre as janelas do dia: contadores + elegíveis."""
    date_str: str
    n_total: int = 0
    n_source_excluded: int = 0      # source synthetic/mixed
    n_skew_excluded: int = 0        # rejected_by_skew
    n_parse_error: int = 0
    n_valid: int = 0                # rest, sem skew, parseável
    n_candidate: int = 0
    n_watch: int = 0
    n_execution: int = 0
    n_pos_net: int = 0              # net_bps gravado > 0
    n_net_gt10: int = 0
    n_net_gt20: int = 0
    n_pos_net_eff: int = 0          # net_eff > 0
    best_net_bps: float | None = None
    best_net_eff_bps: float | None = None
    config_hashes: Counter = field(default_factory=Counter)
    # (ts, buy, sell) -> linhas elegíveis (tamanhos diferentes do mesmo ciclo/rota)
    groups: dict[tuple[float, str, str], list[EligibleRow]] = field(default_factory=dict)

    @property
    def n_eligible_groups(self) -> int:
        return len(self.groups)


def collect_day(rows, date_str: str, credit_bps: float, min_net_bps: float) -> DayAggregate:
    """Streaming: filtra source/skew, acumula contadores e agrupa elegíveis por ciclo."""
    agg = DayAggregate(date_str=date_str)
    for row in rows:
        agg.n_total += 1
        source = (row.get("source") or "rest").strip() or "rest"
        if source != "rest":
            agg.n_source_excluded += 1
            continue
        if "rejected_by_skew" in (row.get("reject_reasons") or ""):
            agg.n_skew_excluded += 1
            continue
        try:
            ts = float(row.get("timestamp_local"))
            net = float(row.get("net_bps"))
            size = float(row.get("size_brl"))
        except (TypeError, ValueError):
            agg.n_parse_error += 1
            continue
        agg.n_valid += 1
        agg.config_hashes[(row.get("config_hash") or "unknown")[:12]] += 1
        net_eff = net + credit_bps
        agg.best_net_bps = net if agg.best_net_bps is None else max(agg.best_net_bps, net)
        agg.best_net_eff_bps = (net_eff if agg.best_net_eff_bps is None
                                else max(agg.best_net_eff_bps, net_eff))
        if row.get("candidate_gate_pass") == "True":
            agg.n_candidate += 1
        if row.get("watch_gate_pass") == "True":
            agg.n_watch += 1
        if row.get("execution_gate_theoretical_pass") == "True":
            agg.n_execution += 1
        if net > 0:
            agg.n_pos_net += 1
        if net > 10:
            agg.n_net_gt10 += 1
        if net > 20:
            agg.n_net_gt20 += 1
        if net_eff > 0:
            agg.n_pos_net_eff += 1

        eligible = row.get("candidate_gate_pass") == "True" and net_eff >= min_net_bps
        if eligible:
            buy = row.get("route_buy_exchange") or ""
            sell = row.get("route_sell_exchange") or ""
            agg.groups.setdefault((ts, buy, sell), []).append(
                EligibleRow(ts, buy, sell, size, net, net_eff))
    return agg


def resolve_scenario_balances(scen: dict, cfg: dict) -> dict[tuple[str, str], float]:
    """balances explícito vence; senão default_brl/default_usdt nas venues do cenário
    (ou em todas as exchanges habilitadas na config principal)."""
    if scen.get("balances"):
        return {
            (ex, asset): float(amount)
            for ex, assets in scen["balances"].items()
            for asset, amount in (assets or {}).items()
        }
    venues = scen.get("venues") or [
        name for name, ex in (cfg.get("exchanges") or {}).items() if ex.get("enabled")
    ]
    out: dict[tuple[str, str], float] = {}
    for ex in venues:
        out[(ex, "BRL")] = float(scen.get("default_brl", 0.0))
        out[(ex, "USDT")] = float(scen.get("default_usdt", 0.0))
    return out


@dataclass
class ScenarioResult:
    name: str
    capital_initial_brl_equiv: float = 0.0
    executable_windows: int = 0
    blocked_by_inventory: int = 0
    routes_outside_scenario: int = 0
    theoretical_pnl_brl: float = 0.0
    executed_notional_brl: float = 0.0
    capital_efficiency_bps: float = 0.0
    initial: dict = field(default_factory=dict)
    final: dict = field(default_factory=dict)
    flows: dict = field(default_factory=dict)
    executions_by_route: Counter = field(default_factory=Counter)
    blocked_by_route: Counter = field(default_factory=Counter)
    drained: list = field(default_factory=list)     # (exchange, asset) abaixo do limiar
    idle: list = field(default_factory=list)        # (exchange, asset) com saldo e zero fluxo
    rebalance_pressure_brl: float = 0.0             # BRL-equiv p/ restaurar alocação inicial

    @property
    def blocked_share(self) -> float:
        considered = self.executable_windows + self.blocked_by_inventory
        return (self.blocked_by_inventory / considered) if considered else 0.0


def simulate_scenario(name: str, balances: dict[tuple[str, str], float],
                      agg: DayAggregate, inv_cfg: dict) -> ScenarioResult:
    """Reexecução do dia em ordem cronológica contra um cenário de inventário."""
    ref_price = float(inv_cfg["reference_price_brl"])
    drain_pct = float(inv_cfg["drain_threshold_pct"])
    ledger = Ledger()
    for (ex, asset), amount in balances.items():
        ledger.set_balance(ex, asset, amount)
    members = {ex for (ex, _asset) in balances}
    res = ScenarioResult(name=name, initial=dict(balances))
    res.capital_initial_brl_equiv = sum(
        amount * (ref_price if asset == "USDT" else 1.0)
        for (_ex, asset), amount in balances.items()
    )
    flows: dict[tuple[str, str], float] = {key: 0.0 for key in balances}

    for key in sorted(agg.groups):
        rows = sorted(agg.groups[key], key=lambda r: r.size_brl, reverse=True)
        route = (rows[0].buy_exchange, rows[0].sell_exchange)
        if route[0] not in members or route[1] not in members:
            res.routes_outside_scenario += 1
            continue
        executed = False
        for row in rows:  # maior tamanho primeiro; fallback p/ menores
            usdt_qty = row.size_brl / ref_price
            deltas = {
                (row.buy_exchange, "BRL"): -row.size_brl,
                (row.buy_exchange, "USDT"): usdt_qty,
                (row.sell_exchange, "USDT"): -usdt_qty,
                (row.sell_exchange, "BRL"): row.size_brl * (1 + row.net_eff_bps / 10_000.0),
            }
            if not ledger.can_apply(deltas):
                continue
            ledger.apply_trade(deltas)
            for dkey, delta in deltas.items():
                flows[dkey] = flows.get(dkey, 0.0) + delta
            res.executable_windows += 1
            res.executed_notional_brl += row.size_brl
            res.theoretical_pnl_brl += row.size_brl * (row.net_eff_bps / 10_000.0)
            res.executions_by_route[f"{route[0]}->{route[1]}"] += 1
            executed = True
            break
        if not executed:
            res.blocked_by_inventory += 1
            res.blocked_by_route[f"{route[0]}->{route[1]}"] += 1

    res.final = {key: ledger.get_balance(*key) for key in balances}
    res.flows = flows
    for key, initial in balances.items():
        final = res.final[key]
        if initial > 0 and final < initial * drain_pct:
            res.drained.append(key)
        if initial > 0 and abs(flows.get(key, 0.0)) < 1e-9:
            res.idle.append(key)
        brl_factor = ref_price if key[1] == "USDT" else 1.0
        res.rebalance_pressure_brl += max(0.0, initial - final) * brl_factor
    if res.capital_initial_brl_equiv > 0:
        res.capital_efficiency_bps = (
            res.theoretical_pnl_brl / res.capital_initial_brl_equiv * 10_000.0)
    return res


def _monolith_date_span(legacy: Path) -> tuple[str, str] | None:
    """(primeira, última) data UTC do monólito legado lendo só cabeça e cauda."""
    try:
        with legacy.open(encoding="utf-8") as fh:
            fh.readline()  # header
            first_line = fh.readline()
        if not first_line.strip():
            return None
        first_ts = float(first_line.split(",", 1)[0])
        size = legacy.stat().st_size
        with legacy.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode("utf-8", errors="ignore").splitlines()
        last_ts = None
        for line in reversed(tail):
            head = line.split(",", 1)[0]
            try:
                last_ts = float(head)
                break
            except ValueError:
                continue
        if last_ts is None:
            return None
        # sanidade: cauda corrompida não pode gerar span absurdo
        last_ts = min(max(last_ts, first_ts), first_ts + 60 * 86400)
        return utc_date_str(first_ts), utc_date_str(last_ts)
    except (OSError, ValueError):
        return None


def observed_dates(data_dir: Path, mode: str = "rest") -> set[str]:
    """Datas UTC com dados: arquivos diários + span do monólito legado (estimado)."""
    directory = observer_dir(data_dir, mode)
    dates: set[str] = set()
    if directory.exists():
        for path in directory.glob("observer_spread_windows_*.csv"):
            match = re.match(r"observer_spread_windows_(\d{4}-\d{2}-\d{2})\.csv$", path.name)
            if match:
                dates.add(match.group(1))
    legacy = directory / SPREAD_FILE
    if legacy.exists() and legacy.stat().st_size > 0:
        span = _monolith_date_span(legacy)
        if span:
            day = datetime.strptime(span[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            last = datetime.strptime(span[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            while day <= last:
                dates.add(day.strftime("%Y-%m-%d"))
                day += timedelta(days=1)
    return dates


def decide(agg: DayAggregate, results: list[ScenarioResult],
           inv_cfg: dict, n_observed_days: int) -> tuple[str, list[str]]:
    """Decisão do dia: ADVANCE / HOLD / KILL / NEED_MORE_DATA + razões.

    A amostra manda: com menos de min_days_for_decision dias observados ou menos de
    min_valid_windows janelas válidas no dia, a decisão é SEMPRE NEED_MORE_DATA
    (PRD §18: decidir só após 7-14 dias) — o lean do dia fica registrado nas razões.
    KILL de verdade (matar o redirect inventory-aware) exige dias consecutivos de
    lean KILL com amostra cheia, a critério do dono — este relatório decide UM dia.
    """
    dec = inv_cfg["decision"]
    min_valid = int(dec["min_valid_windows"])
    min_days = int(dec["min_days_for_decision"])
    adv_min = int(dec["advance_min_executable"])
    blocked_max = float(dec["blocked_max_share"])

    best = max(results, key=lambda r: (r.executable_windows, r.theoretical_pnl_brl),
               default=None)
    if best is None or best.executable_windows == 0:
        lean = "KILL"
        lean_reason = ("0 janelas executáveis em todos os cenários, mesmo com crédito "
                       f"de rebalance (melhor net_eff do dia: "
                       f"{agg.best_net_eff_bps if agg.best_net_eff_bps is not None else '—'} bps)")
    elif (best.executable_windows >= adv_min and best.theoretical_pnl_brl > 0
          and best.blocked_share <= blocked_max):
        lean = "ADVANCE"
        lean_reason = (f"cenário '{best.name}': {best.executable_windows} executáveis, "
                       f"PnL teórico R$ {best.theoretical_pnl_brl:.2f}, "
                       f"{best.blocked_share:.0%} bloqueadas por inventário")
    else:
        lean = "HOLD"
        lean_reason = (f"sinal existe mas fraco: melhor cenário '{best.name}' com "
                       f"{best.executable_windows} executáveis (alvo ≥ {adv_min}), "
                       f"PnL R$ {best.theoretical_pnl_brl:.2f}, "
                       f"{best.blocked_share:.0%} bloqueadas")

    reasons = [f"lean do dia: {lean} — {lean_reason}"]
    sample_ok = True
    if agg.n_valid < min_valid:
        sample_ok = False
        reasons.append(f"amostra do dia insuficiente: {agg.n_valid}/{min_valid} janelas válidas")
    if n_observed_days < min_days:
        sample_ok = False
        reasons.append(f"dias observados insuficientes: {n_observed_days}/{min_days} "
                       "(PRD §18 pede 7-14 dias antes de decidir)")
    return (lean if sample_ok else "NEED_MORE_DATA"), reasons


def _fmt_key(key: tuple[str, str]) -> str:
    return f"{key[0]}:{key[1]}"


def generate_inventory_report(data_dir: Path, date_str: str, cfg: dict,
                              mode: str = "rest") -> Path:
    """Streaming dos CSVs do dia -> simulação por cenário -> Markdown em reports/."""
    data_dir = Path(data_dir)
    inv = inventory_cfg(cfg)
    credit = rebalance_credit_bps(cfg)
    min_net = float(inv["min_net_bps"])

    agg = collect_day(iter_spread_rows_for_date(data_dir, date_str, mode),
                      date_str, credit, min_net)
    results = [
        simulate_scenario(name, resolve_scenario_balances(scen or {}, cfg), agg, inv)
        for name, scen in inv["scenarios"].items()
    ]
    n_days = len(observed_dates(data_dir, mode))
    decision, reasons = decide(agg, results, inv, n_days)
    core = observer_verdict(agg.n_valid, agg.n_execution, cfg)

    lines = [
        f"# local_arb — Inventory-aware Simulation (Fase 3.3) — {date_str}",
        "",
        f"DECISION: {decision}",
        "",
        "STATUS: OFFLINE_READ_ONLY — research/SIGNAL_ONLY; lê CSVs do observer, "
        "não coleta, não ordena, não persiste estado. Nenhum número aqui autoriza trade.",
        "",
        "## Amostra do dia",
        "",
        f"- Janelas totais lidas: {agg.n_total} "
        f"(excluídas: {agg.n_source_excluded} synthetic/mixed, "
        f"{agg.n_skew_excluded} skew, {agg.n_parse_error} parse)",
        f"- Janelas válidas (source=rest, sem skew): {agg.n_valid}",
        f"- Candidate: {agg.n_candidate} | Watch: {agg.n_watch} "
        f"| Execution-theoretical: {agg.n_execution}",
        f"- Melhor net_bps gravado: "
        f"{f'{agg.best_net_bps:.2f}' if agg.best_net_bps is not None else '—'} | "
        f"melhor net_eff (crédito +{credit:.1f} bps): "
        f"{f'{agg.best_net_eff_bps:.2f}' if agg.best_net_eff_bps is not None else '—'}",
        f"- Dias observados no data dir (diários + span do monólito): {n_days}",
        f"- config_hashes distintos no dia: {len(agg.config_hashes)} "
        f"({', '.join(sorted(agg.config_hashes))})"
        + (" — ATENÇÃO: mistura de configs muda a composição do break-even entre linhas"
           if len(agg.config_hashes) > 1 else ""),
        "",
        "## CORE_TAKER_TAKER vs INVENTORY_AWARE",
        "",
        f"- CORE_TAKER_TAKER (net gravado, sem crédito): **{core.status}** — "
        + "; ".join(core.reasons),
        f"  - net>0: {agg.n_pos_net} | net>10: {agg.n_net_gt10} | net>20: {agg.n_net_gt20} "
        f"(gates PRD §18 p/ 7-14d: ≥{PRD_GATES_7_14D['net>0']} / "
        f"≥{PRD_GATES_7_14D['net>10']} / ≥{PRD_GATES_7_14D['net>20']})",
        f"- INVENTORY_AWARE (net_eff = net + {credit:.1f} bps de crédito de rebalance): "
        f"{agg.n_pos_net_eff} janelas com net_eff>0; "
        f"{agg.n_eligible_groups} rota-ciclos elegíveis (net_eff ≥ {min_net:.1f} e candidate)",
        "",
        "## Cenários",
        "",
        "| Cenário | Capital (BRL-equiv) | Executáveis | Bloq. inventário | Fora do cenário "
        "| PnL teórico (BRL) | Cap. eff (bps/dia) | Notional exec. | Venues drenadas |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for res in results:
        drained = ", ".join(_fmt_key(k) for k in res.drained) or "—"
        lines.append(
            f"| {res.name} | {res.capital_initial_brl_equiv:.0f} | {res.executable_windows} "
            f"| {res.blocked_by_inventory} | {res.routes_outside_scenario} "
            f"| {res.theoretical_pnl_brl:.2f} | {res.capital_efficiency_bps:.2f} "
            f"| {res.executed_notional_brl:.0f} | {drained} |")

    for res in results:
        lines += [
            "",
            f"### Cenário `{res.name}`",
            "",
            f"- Executáveis: {res.executable_windows} | Bloqueadas por inventário: "
            f"{res.blocked_by_inventory} ({res.blocked_share:.0%} do considerado) "
            f"| Rotas fora do cenário: {res.routes_outside_scenario}",
            f"- PnL teórico: R$ {res.theoretical_pnl_brl:.2f} | Notional executado: "
            f"R$ {res.executed_notional_brl:.0f} | Capital efficiency: "
            f"{res.capital_efficiency_bps:.2f} bps/dia",
            f"- Pressão de rebalance (repor alocação inicial): "
            f"R$ {res.rebalance_pressure_brl:.2f}",
            f"- Capital parado (saldo > 0 e zero fluxo): "
            + (", ".join(_fmt_key(k) for k in res.idle) or "nenhum"),
        ]
        if res.executions_by_route:
            top_exec = ", ".join(f"{route} ×{n}" for route, n
                                 in res.executions_by_route.most_common(5))
            lines.append(f"- Rotas executadas: {top_exec}")
        if res.blocked_by_route:
            top_blocked = ", ".join(f"{route} ×{n}" for route, n
                                    in res.blocked_by_route.most_common(5))
            lines.append(f"- Rotas bloqueadas: {top_blocked}")
        if res.initial:
            lines += ["", "| Venue:Asset | Inicial | Final | Δ |", "|---|---:|---:|---:|"]
            for key in sorted(res.initial):
                initial, final = res.initial[key], res.final.get(key, 0.0)
                lines.append(f"| {_fmt_key(key)} | {initial:.2f} | {final:.2f} "
                             f"| {final - initial:+.2f} |")

    lines += [
        "",
        "## Decisão",
        "",
        f"- **DECISION: {decision}**",
        *[f"- {reason}" for reason in reasons],
        "",
        "## Metodologia e simplificações",
        "",
        f"- Elegibilidade: source=rest, sem rejected_by_skew, candidate_gate_pass=True, "
        f"net_eff = net_bps + {credit:.1f} ≥ {min_net:.1f} bps.",
        "- Crédito de rebalance: o net gravado amortiza rebalanceamento por janela; na tese "
        "inventory-aware esse custo vira drain medido aqui — creditar evita dupla contagem. "
        "Fees taker, slippage, latency haircut e fee_uncertainty permanecem no net.",
        "- Dedup por (ciclo, rota): maior tamanho elegível primeiro, fallback para menores; "
        "nada coube = blocked_by_inventory. Uma linha por ciclo de ~10s: executáveis contam "
        "rota-ciclos nessa cadência, não janelas únicas de mercado.",
        f"- Execução: A.BRL -= S; A.USDT += S/ref; B.USDT -= S/ref; "
        f"B.BRL += S×(1+net_eff/10000) com ref = {float(inv['reference_price_brl']):.2f} "
        "BRL/USDT (afeta só conversão de inventário, não o PnL).",
        "- Nenhuma ordem gerada, nenhum DB tocado; único artefato é este arquivo.",
    ]

    reports_dir = data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if mode == "rest" else f"_{mode}"
    path = reports_dir / f"{REPORT_PREFIX}{date_str}{suffix}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fixture sintética p/ smoke offline (NUNCA no data dir vivo — guard no CLI)
# ---------------------------------------------------------------------------

def write_fixture(data_dir: Path, date_str: str) -> Path:
    """CSV diário sintético p/ smoke do inventory-aware em data dir descartável.

    Recusa sobrescrever arquivo existente. O guard contra o data dir vivo fica no
    CLI (main.py), que conhece o DEFAULT_DATA_DIR.
    """
    path = spread_daily_path(Path(data_dir), date_str, "rest")
    if path.exists():
        raise FileExistsError(f"{path} já existe — não sobrescrevo dados de observer")
    base_ts = datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=12).timestamp()

    def row(ts_off: float, buy: str, sell: str, size: float, net: float, *,
            candidate: bool = True, source: str = "rest", skew: bool = False) -> dict:
        gross = net + 136.0
        reasons = (["rejected_by_skew"] if skew
                   else (["accepted_watch" if net >= 0 else "rejected_by_negative_net"]))
        return {
            "timestamp_local": base_ts + ts_off,
            "pair": "USDT/BRL",
            "route_buy_exchange": buy,
            "route_sell_exchange": sell,
            "size_brl": size,
            "gross_bps": round(gross, 6),
            "break_even_bps": 136.0,
            "net_bps": round(net, 6),
            "depth_ok": True,
            "quality_buy": 100,
            "quality_sell": 100,
            "latency_buy_ms": 150.0,
            "latency_sell_ms": 250.0,
            "candidate_gate_pass": candidate and not skew,
            "watch_gate_pass": candidate and not skew and net >= 0,
            "execution_gate_theoretical_pass": candidate and not skew and net >= 20,
            "reason_rejected": reasons[0],
            "reject_reasons": ",".join(reasons),
            "capital_required_brl": size * 2.0,
            "capital_efficiency_bps": round(net / 2.0, 6),
            "config_hash": "fixture000000",
            "source": source,
            "skew_ms": 3000.0 if skew else 250.0,
            "max_skew_ms": 2000.0,
        }

    rows: list[dict] = []
    # rota principal: binance -> mercadobitcoin, 6 ciclos, 4 tamanhos por ciclo
    for cycle in range(6):
        off = cycle * 10.0
        net = 22.0 - cycle * 3.0  # 22, 19, 16, 13, 10, 7
        for size in (5000.0, 2500.0, 1000.0, 500.0):
            rows.append(row(off, "binance", "mercadobitcoin", size, net))
    # rota secundária global: okx -> bitso
    for cycle in range(2):
        for size in (1000.0, 500.0):
            rows.append(row(200.0 + cycle * 10.0, "okx", "bitso", size, 5.0))
    # rota só-local (fora de top_venues): brasilbitcoin -> bitypreco
    for size in (1000.0, 500.0):
        rows.append(row(300.0, "brasilbitcoin", "bitypreco", size, 12.0))
    # excluídas por construção: skew, synthetic, mixed
    rows.append(row(400.0, "binance", "mercadobitcoin", 1000.0, 30.0, skew=True))
    rows.append(row(410.0, "binance", "mercadobitcoin", 1000.0, 50.0, source="synthetic"))
    rows.append(row(420.0, "binance", "mercadobitcoin", 1000.0, 40.0, source="mixed"))
    # ruído negativo normal
    for cycle in range(8):
        rows.append(row(500.0 + cycle * 10.0, "foxbit", "bybit", 500.0, -80.0 - cycle,
                        candidate=False))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SPREAD_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path
