"""Research routes monitor-only por APIs públicas (corretoras locais + Binance + PTAX).

SEM execução, SEM credenciais, SEM rotas descentralizadas — só coleta pública
e cálculo de sinal. Cada rota devolve um `RouteResult` com status:

    OK          — dados coletados, sem sinal aplicável (ex.: recon, 1 fonte só)
    NO_DATA     — insumo obrigatório indisponível (endpoint falhou / não configurado)
    BAD_DATA    — insumo presente mas inválido (book cruzado, preço não finito…)
    WEAK_SIGNAL — |sinal| >= weak_bps
    PROMISING   — |sinal| >= promising_bps
    HOLD        — sinal calculado, abaixo dos thresholds

Falha de endpoint NUNCA vira exceção: os adapters (`PublicRestAdapter`) e os
clients de referência (`references.py`) devolvem erro controlado, e um runner
que estoure exceção inesperada é capturado e vira BAD_DATA.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .adapters import SYNTHETIC_TS, PublicRestAdapter, SyntheticAdapter
from .models import OrderBookSnapshot
from .references import BaseReferenceClient, build_reference_client

STATUS_OK = "OK"
STATUS_NO_DATA = "NO_DATA"
STATUS_BAD_DATA = "BAD_DATA"
STATUS_WEAK_SIGNAL = "WEAK_SIGNAL"
STATUS_PROMISING = "PROMISING"
STATUS_HOLD = "HOLD"

DEFAULT_THRESHOLDS = {"weak_bps": 10.0, "promising_bps": 30.0}


@dataclass
class RouteResult:
    route: str
    kind: str
    ts: float
    status: str
    signal_bps: float | None = None          # sinal principal da rota (bps)
    metrics: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)   # fonte -> "ok" | erro legível
    notes: list[str] = field(default_factory=list)


@dataclass
class ResearchScan:
    ts: float
    mode: str                                # "synthetic" | "rest"
    results: list[RouteResult]
    disabled: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {
            "ts": self.ts,
            "mode": self.mode,
            "monitor_only": True,
            "n_routes": len(self.results),
            "status_counts": counts,
            "routes_disabled": self.disabled,
            "routes": [asdict(r) for r in self.results],
        }


@dataclass
class RouteContext:
    now_ts: float
    synthetic: bool
    refs: BaseReferenceClient
    defaults: dict
    http_get: Callable[[str, float], object] | None = None

    def thresholds(self, route_cfg: dict) -> dict:
        thr = dict(DEFAULT_THRESHOLDS)
        thr.update(self.defaults.get("thresholds") or {})
        thr.update(route_cfg.get("thresholds") or {})
        return thr


# ── helpers ──────────────────────────────────────────────────────────────


def _fetch_book(
    name: str, ex_cfg: dict, symbol: str, ctx: RouteContext,
    api_override: dict | None = None, synthetic_override: dict | None = None,
) -> tuple[OrderBookSnapshot | None, str | None]:
    """Um book local por exchange/símbolo. Retorna (book|None, erro|None).

    Config malformada (synthetic sem bid/ask, api de tipo errado…) vira erro
    controlado DESTA fonte — as demais fontes da rota seguem vivas.
    """
    try:
        if ctx.synthetic:
            scen = synthetic_override if synthetic_override is not None else ex_cfg.get("synthetic")
            if not scen:
                return None, "sem cenário sintético configurado"
            adapter = SyntheticAdapter(
                name, symbol,
                bid=float(scen["bid"]), ask=float(scen["ask"]),
                latency_ms=float(scen.get("latency_ms", 120.0)),
            )
        else:
            api = api_override if api_override is not None else ex_cfg.get("api")
            adapter = PublicRestAdapter(name, symbol, api, http_get=ctx.http_get)
    except Exception as exc:  # noqa: BLE001 — config ruim de UMA fonte não derruba a rota
        return None, f"config inválida: {type(exc).__name__}: {exc}"
    result = adapter.fetch_order_book(now_ts=ctx.now_ts)
    if not result.ok or result.book is None:
        return None, result.error or "falha desconhecida"
    return result.book, None


def _book_mid(book: OrderBookSnapshot) -> tuple[float | None, str | None]:
    """Mid validado. Retorna (mid|None, motivo_invalidez|None)."""
    bid, ask = book.best_bid, book.best_ask
    if bid is None or ask is None:
        return None, "book sem níveis"
    if bid > ask:
        return None, f"book cruzado (bid {bid} > ask {ask})"
    mid = (bid + ask) / 2.0
    if not (mid > 0):
        return None, f"mid inválido: {mid}"
    return mid, None


def _collect_mids(
    exchanges_cfg: dict, symbol: str, ctx: RouteContext, sources: dict, prefix: str = ""
) -> tuple[dict[str, float], int, int]:
    """Coleta mids validados por exchange habilitada.

    Retorna (mids, n_fetched, n_bad): n_fetched conta books que chegaram
    (ok ou inválidos); n_bad conta os que chegaram mas reprovaram validação.
    """
    mids: dict[str, float] = {}
    n_fetched = 0
    n_bad = 0
    for name, ex_cfg in (exchanges_cfg or {}).items():
        if not (ex_cfg or {}).get("enabled", True):
            continue
        key = f"{prefix}{name}"
        book, err = _fetch_book(name, ex_cfg, symbol, ctx)
        if book is None:
            sources[key] = err or "falha desconhecida"
            continue
        n_fetched += 1
        mid, bad = _book_mid(book)
        if mid is None:
            n_bad += 1
            sources[key] = f"dados inválidos: {bad}"
            continue
        sources[key] = "ok"
        mids[name] = mid
    return mids, n_fetched, n_bad


def _grade(abs_signal_bps: float, thr: dict) -> str:
    if abs_signal_bps >= float(thr["promising_bps"]):
        return STATUS_PROMISING
    if abs_signal_bps >= float(thr["weak_bps"]):
        return STATUS_WEAK_SIGNAL
    return STATUS_HOLD


# ── runners por kind ─────────────────────────────────────────────────────


def run_local_pair_monitor(name: str, rcfg: dict, ctx: RouteContext) -> RouteResult:
    """USDC/BRL (ou par local configurado): spread cross-exchange monitor-only."""
    symbol = rcfg.get("symbol", "USDC/BRL")
    thr = ctx.thresholds(rcfg)
    cost_bps = float(rcfg.get("cost_bps", 0.0))
    sources: dict = {}
    books: dict[str, OrderBookSnapshot] = {}
    n_fetched = 0
    n_bad = 0
    for ex_name, ex_cfg in (rcfg.get("exchanges") or {}).items():
        if not (ex_cfg or {}).get("enabled", True):
            continue
        book, err = _fetch_book(ex_name, ex_cfg, symbol, ctx)
        if book is None:
            sources[ex_name] = err or "falha desconhecida"
            continue
        n_fetched += 1
        mid, bad = _book_mid(book)
        if mid is None:
            n_bad += 1
            sources[ex_name] = f"dados inválidos: {bad}"
            continue
        sources[ex_name] = "ok"
        books[ex_name] = book

    result = RouteResult(route=name, kind=rcfg.get("kind", "local_pair_monitor"),
                         ts=ctx.now_ts, status=STATUS_NO_DATA, sources=sources)
    result.metrics = {
        "symbol": symbol,
        "mids": {ex: round((b.best_bid + b.best_ask) / 2.0, 6) for ex, b in books.items()},
        "cost_bps": cost_bps,
    }
    if n_fetched == 0:
        result.notes.append("nenhuma fonte local respondeu")
        return result
    if not books:
        result.status = STATUS_BAD_DATA
        result.notes.append("todas as fontes retornaram dados inválidos")
        return result
    if len(books) < 2:
        result.status = STATUS_OK
        result.notes.append("só 1 fonte saudável — monitor sem sinal cross-exchange")
        return result

    # melhor rota: comprar no menor ask, vender no maior bid (exchanges distintas)
    best = None
    for buy_ex, buy_book in books.items():
        for sell_ex, sell_book in books.items():
            if buy_ex == sell_ex:
                continue
            gross_bps = (sell_book.best_bid / buy_book.best_ask - 1.0) * 10_000.0
            if best is None or gross_bps > best[2]:
                best = (buy_ex, sell_ex, gross_bps)
    buy_ex, sell_ex, gross_bps = best
    net_bps = gross_bps - cost_bps
    result.signal_bps = round(net_bps, 4)
    result.metrics.update({
        "best_route": f"{buy_ex}->{sell_ex}",
        "gross_bps": round(gross_bps, 4),
        "net_bps": round(net_bps, 4),
    })
    result.status = _grade(net_bps, thr)  # sinal direcional: só spread positivo interessa
    return result


def run_stablecoin_premium(name: str, rcfg: dict, ctx: RouteContext) -> RouteResult:
    """Prêmio local de stablecoin BRL vs USD/BRL (PTAX) ajustado por Binance."""
    thr = ctx.thresholds(rcfg)
    sources: dict = {}
    result = RouteResult(route=name, kind=rcfg.get("kind", "stablecoin_premium"),
                         ts=ctx.now_ts, status=STATUS_NO_DATA, sources=sources)

    ptax = ctx.refs.ptax_usd_brl()
    sources["ptax"] = "ok" if ptax.ok else (ptax.error or "falha desconhecida")
    if not ptax.ok:
        result.notes.append("USD/BRL de referência (PTAX) indisponível")
        return result

    premiums: dict[str, float] = {}
    stables_metrics: dict[str, dict] = {}
    n_bad = 0
    for stable, scfg in (rcfg.get("stables") or {}).items():
        symbol = f"{stable}/BRL"
        mids, _n_fetched, bad = _collect_mids(
            scfg.get("exchanges") or {}, symbol, ctx, sources, prefix=f"{stable.lower()}:")
        n_bad += bad
        if not mids:
            stables_metrics[stable] = {"local_mid_brl": None, "premium_bps": None}
            continue
        local_mid = statistics.median(mids.values())

        stable_usd = 1.0
        adjust_ok = True
        adj_symbol = scfg.get("binance_usd_symbol")
        if adj_symbol:
            quote = ctx.refs.binance_price(adj_symbol)
            sources[f"binance:{adj_symbol}"] = "ok" if quote.ok else (quote.error or "falha")
            if quote.ok:
                stable_usd = 1.0 / quote.price if scfg.get("binance_usd_invert") else quote.price
            else:
                adjust_ok = False
                result.notes.append(
                    f"{stable}: ajuste Binance ({adj_symbol}) indisponível — usando 1.0")

        fair_brl = ptax.price * stable_usd
        premium_bps = (local_mid / fair_brl - 1.0) * 10_000.0
        premiums[stable] = premium_bps
        stables_metrics[stable] = {
            "local_mid_brl": round(local_mid, 6),
            "stable_usd": round(stable_usd, 6),
            "adjust_ok": adjust_ok,
            "fair_brl": round(fair_brl, 6),
            "premium_bps": round(premium_bps, 4),
        }

    result.metrics = {"ptax_usd_brl": ptax.price, "stables": stables_metrics}
    if not premiums:
        result.status = STATUS_BAD_DATA if n_bad > 0 else STATUS_NO_DATA
        result.notes.append("nenhum mid local de stablecoin disponível")
        return result

    top_stable = max(premiums, key=lambda s: abs(premiums[s]))
    result.signal_bps = round(premiums[top_stable], 4)
    result.metrics["top_stable"] = top_stable
    result.status = _grade(abs(premiums[top_stable]), thr)
    return result


def run_cross_proxy(name: str, rcfg: dict, ctx: RouteContext) -> RouteResult:
    """BTC/BRL e ETH/BRL locais vs Binance {BTC,ETH}USDT × USDT/BRL local."""
    thr = ctx.thresholds(rcfg)
    sources: dict = {}
    result = RouteResult(route=name, kind=rcfg.get("kind", "cross_proxy"),
                         ts=ctx.now_ts, status=STATUS_NO_DATA, sources=sources)

    usdt_cfg = (rcfg.get("usdt_brl") or {}).get("exchanges") or {}
    usdt_mids, usdt_fetched, usdt_bad = _collect_mids(
        usdt_cfg, "USDT/BRL", ctx, sources, prefix="usdtbrl:")
    if not usdt_mids:
        result.status = STATUS_BAD_DATA if usdt_bad > 0 else STATUS_NO_DATA
        result.notes.append("USDT/BRL local indisponível — proxy sem âncora")
        return result
    usdt_brl = statistics.median(usdt_mids.values())

    deviations: dict[str, float] = {}
    legs_metrics: dict[str, dict] = {}
    n_bad = 0
    for leg, lcfg in (rcfg.get("legs") or {}).items():
        symbol = f"{leg}/BRL"
        mids, _fetched, bad = _collect_mids(
            lcfg.get("exchanges") or {}, symbol, ctx, sources, prefix=f"{leg.lower()}:")
        n_bad += bad
        bin_symbol = lcfg.get("binance_symbol", f"{leg}USDT")
        quote = ctx.refs.binance_price(bin_symbol)
        sources[f"binance:{bin_symbol}"] = "ok" if quote.ok else (quote.error or "falha")
        if not mids or not quote.ok:
            legs_metrics[leg] = {"local_mid_brl": None, "deviation_bps": None}
            continue
        local_mid = statistics.median(mids.values())
        implied_brl = quote.price * usdt_brl
        dev_bps = (local_mid / implied_brl - 1.0) * 10_000.0
        deviations[leg] = dev_bps
        legs_metrics[leg] = {
            "local_mid_brl": round(local_mid, 6),
            "binance_usd": quote.price,
            "implied_brl": round(implied_brl, 6),
            "deviation_bps": round(dev_bps, 4),
        }

    result.metrics = {"usdt_brl_local_mid": round(usdt_brl, 6), "legs": legs_metrics}
    if not deviations:
        result.status = STATUS_BAD_DATA if n_bad > 0 else STATUS_NO_DATA
        result.notes.append("nenhuma perna com book local + referência Binance simultâneos")
        return result

    top_leg = max(deviations, key=lambda k: abs(deviations[k]))
    result.signal_bps = round(deviations[top_leg], 4)
    result.metrics["top_leg"] = top_leg
    result.status = _grade(abs(deviations[top_leg]), thr)
    return result


def run_recon(name: str, rcfg: dict, ctx: RouteContext) -> RouteResult:
    """Recon monitor-only: só verifica se endpoints publicam dados plausíveis."""
    sources: dict = {}
    result = RouteResult(route=name, kind=rcfg.get("kind", "recon"),
                         ts=ctx.now_ts, status=STATUS_NO_DATA, sources=sources)
    found: dict[str, dict] = {}
    checked = 0
    n_bad = 0
    for ex_name, ex_cfg in (rcfg.get("exchanges") or {}).items():
        if not (ex_cfg or {}).get("enabled", True):
            continue
        api_by_symbol = ex_cfg.get("api_by_symbol") or {}
        syn_by_symbol = ex_cfg.get("synthetic_by_symbol") or {}
        for symbol in rcfg.get("symbols") or []:
            key = f"{ex_name}:{symbol}"
            api = api_by_symbol.get(symbol)
            syn = syn_by_symbol.get(symbol)
            if (ctx.synthetic and syn is None) or (not ctx.synthetic and api is None):
                sources[key] = "sem endpoint configurado"
                continue
            checked += 1
            book, err = _fetch_book(ex_name, ex_cfg, symbol, ctx,
                                    api_override=api, synthetic_override=syn)
            if book is None:
                sources[key] = err or "falha desconhecida"
                continue
            mid, bad = _book_mid(book)
            if mid is None:
                n_bad += 1
                sources[key] = f"dados inválidos: {bad}"
                continue
            sources[key] = "ok"
            found[key] = {"mid": round(mid, 6), "levels": len(book.bids) + len(book.asks)}

    result.metrics = {"symbols": list(rcfg.get("symbols") or []), "found": found}
    if checked == 0:
        result.notes.append("nenhum endpoint configurado/confirmado — recon pendente")
        return result
    if not found:
        # dado chegou mas reprovou validação -> BAD_DATA; endpoint mudo -> NO_DATA
        if n_bad > 0:
            result.status = STATUS_BAD_DATA
        result.notes.append("endpoints configurados não retornaram book utilizável")
        return result
    result.status = STATUS_OK
    result.notes.append(f"{len(found)}/{checked} mercado(s) com book público utilizável")
    return result


KIND_RUNNERS: dict[str, Callable[[str, dict, RouteContext], RouteResult]] = {
    "local_pair_monitor": run_local_pair_monitor,
    "stablecoin_premium": run_stablecoin_premium,
    "cross_proxy": run_cross_proxy,
    "recon": run_recon,
}


def run_research_routes(
    cfg: dict,
    synthetic: bool = False,
    now_ts: float | None = None,
    http_get: Callable[[str, float], object] | None = None,
) -> ResearchScan:
    """Executa todas as rotas habilitadas. Nunca levanta por fonte ruim."""
    if now_ts is None:
        now_ts = SYNTHETIC_TS if synthetic else time.time()
    refs = build_reference_client(cfg.get("references"), synthetic=synthetic,
                                  http_get=http_get, now_ts=now_ts)
    ctx = RouteContext(now_ts=now_ts, synthetic=synthetic, refs=refs,
                       defaults=cfg.get("defaults") or {}, http_get=http_get)

    results: list[RouteResult] = []
    disabled: list[str] = []
    for name, rcfg in (cfg.get("routes") or {}).items():
        rcfg = rcfg or {}
        if not rcfg.get("enabled"):
            disabled.append(name)
            continue
        runner = KIND_RUNNERS.get(rcfg.get("kind"))
        if runner is None:
            results.append(RouteResult(
                route=name, kind=str(rcfg.get("kind")), ts=now_ts, status=STATUS_BAD_DATA,
                notes=[f"kind desconhecido: {rcfg.get('kind')!r}"]))
            continue
        try:
            results.append(runner(name, rcfg, ctx))
        except Exception as exc:  # noqa: BLE001 — rota nunca derruba a coleta
            results.append(RouteResult(
                route=name, kind=str(rcfg.get("kind")), ts=now_ts, status=STATUS_BAD_DATA,
                notes=[f"exceção inesperada no runner: {type(exc).__name__}: {exc}"]))

    return ResearchScan(ts=now_ts, mode="synthetic" if synthetic else "rest",
                        results=results, disabled=disabled)
