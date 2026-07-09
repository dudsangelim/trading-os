"""Scanner: books coletados -> spread_windows, oportunidades e paper trades.

Para cada rota ordenada (compra em A, venda em B) e cada tamanho configurado:
- registra a janela de spread (gross, break-even, net, depth, quality);
- vira oportunidade se net >= min_net_bps E depth ok E os dois books passam
  no min_quality_score;
- cada oportunidade é simulada no paper (fill pessimista multinível).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations

from .break_even import break_even_from_config
from .collector import CollectedBook
from .models import FeeSchedule, Opportunity, PaperTrade
from .paper import simulate_arb
from .spreads import gross_spread_bps, route_depth_ok


@dataclass
class SpreadWindow:
    ts: float
    symbol: str
    buy_exchange: str
    sell_exchange: str
    size_quote: float
    gross_spread_bps: float
    break_even_bps: float
    net_bps: float
    depth_ok: bool
    quality_buy: int
    quality_sell: int
    candidate_gate_pass: bool = False
    watch_gate_pass: bool = False
    execution_gate_theoretical_pass: bool = False
    reason_rejected: str = ""
    reject_reasons: list[str] = field(default_factory=list)
    # skew entre os instantes reais de coleta dos dois books: spread calculado
    # com books afastados no tempo é candidato/watch FALSO por construção
    skew_ms: float = 0.0
    max_skew_ms: float = 0.0
    skew_ok: bool = True
    source: str = "unknown"  # rest | synthetic | mixed — origem dos dois books


@dataclass
class ScanResult:
    ts: float
    symbol: str
    min_net_bps: float
    books: list[CollectedBook] = field(default_factory=list)
    spread_windows: list[SpreadWindow] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    paper_trades: list[PaperTrade] = field(default_factory=list)


def fee_schedule_from_config(cfg: dict, exchange: str) -> FeeSchedule:
    ex = cfg["exchanges"][exchange]
    return FeeSchedule(
        exchange=exchange,
        taker_bps=float(ex["taker_bps"]),
        maker_bps=float(ex["maker_bps"]),
        withdrawal_brl_fixed=float(ex.get("withdrawal_brl_fixed", 0.0)),
        withdrawal_usdt_fixed=float(ex.get("withdrawal_usdt_fixed", 0.0)),
    )


def _observer_thresholds(cfg: dict) -> tuple[float, float, float]:
    obs = cfg.get("observer", {})
    thresholds = cfg.get("thresholds", {})
    return (
        float(obs.get("candidate_min_net_bps", -20.0)),
        float(obs.get("watch_min_net_bps", 0.0)),
        float(obs.get("execution_min_net_bps", thresholds.get("min_net_bps", 20.0))),
    )


DEFAULT_MAX_SKEW_MS = 2000.0


def max_skew_ms_from_config(cfg: dict) -> float:
    return float(cfg.get("observer", {}).get("max_skew_ms", DEFAULT_MAX_SKEW_MS))


def _reject_reasons(window: SpreadWindow, buy: CollectedBook, sell: CollectedBook,
                    min_quality: int, execution_min_net_bps: float) -> list[str]:
    reasons: list[str] = []
    if "crossed" in buy.flags or "crossed" in sell.flags:
        reasons.append("rejected_by_crossed_book")
    if not window.skew_ok:
        reasons.append("rejected_by_skew")
    if buy.quality < min_quality or sell.quality < min_quality:
        reasons.append("rejected_by_quality")
    if not window.depth_ok:
        reasons.append("rejected_by_depth")
    if any(flag in (*buy.flags, *sell.flags) for flag in ("stale", "stale_book")):
        reasons.append("rejected_by_stale_book")
    if any(flag == "high_latency" for flag in (*buy.flags, *sell.flags)):
        reasons.append("rejected_by_latency")
    if window.net_bps < 0:
        reasons.append("rejected_by_negative_net")
    if window.gross_spread_bps > 0 and window.net_bps < 0:
        reasons.append("rejected_by_fee")
    if window.net_bps < execution_min_net_bps:
        reasons.append("rejected_by_execution_threshold")
    return reasons or ["accepted_execution_theoretical"]


def _primary_reason(reasons: list[str], candidate: bool, watch: bool, execution: bool) -> str:
    if execution:
        return "accepted_execution_theoretical"
    if watch:
        return "accepted_watch"
    if candidate:
        return "accepted_candidate"
    priority = [
        "rejected_by_crossed_book",
        "rejected_by_skew",
        "rejected_by_quality",
        "rejected_by_depth",
        "rejected_by_stale_book",
        "rejected_by_latency",
        "rejected_by_negative_net",
        "rejected_by_fee",
        "rejected_by_execution_threshold",
    ]
    for reason in priority:
        if reason in reasons:
            return reason
    return reasons[0] if reasons else "rejected_unknown"


def scan_books(books: list[CollectedBook], cfg: dict, now_ts: float, paper_enabled: bool = True) -> ScanResult:
    thresholds = cfg["thresholds"]
    min_net_bps = float(thresholds["min_net_bps"])
    candidate_min_net_bps, watch_min_net_bps, execution_min_net_bps = _observer_thresholds(cfg)
    min_quality = int(thresholds["min_quality_score"])
    max_skew_ms = max_skew_ms_from_config(cfg)
    sizes = [float(s) for s in cfg["simulation"]["sizes_quote_brl"]]
    sim_cfg = cfg["simulation"]
    result = ScanResult(ts=now_ts, symbol=cfg["pair"]["symbol"], min_net_bps=min_net_bps, books=books)

    healthy = [b for b in books if b.ok and b.book is not None]
    for buy, sell in permutations(healthy, 2):
        gross = gross_spread_bps(buy.book, sell.book)
        if gross is None:
            continue
        fee_buy = fee_schedule_from_config(cfg, buy.exchange)
        fee_sell = fee_schedule_from_config(cfg, sell.exchange)
        break_even = break_even_from_config(fee_buy, fee_sell, cfg)
        # coleta é sequencial: cada book carrega o ts real do próprio fetch
        skew_ms = abs(buy.book.ts - sell.book.ts) * 1000.0
        skew_ok = skew_ms <= max_skew_ms
        source = buy.source if buy.source == sell.source else "mixed"

        for size_quote in sizes:
            depth = route_depth_ok(buy.book, sell.book, size_quote)
            window = SpreadWindow(
                ts=now_ts,
                symbol=result.symbol,
                buy_exchange=buy.exchange,
                sell_exchange=sell.exchange,
                size_quote=size_quote,
                gross_spread_bps=gross,
                break_even_bps=break_even.total_bps,
                net_bps=gross - break_even.total_bps,
                depth_ok=depth,
                quality_buy=buy.quality,
                quality_sell=sell.quality,
                skew_ms=skew_ms,
                max_skew_ms=max_skew_ms,
                skew_ok=skew_ok,
                source=source,
            )
            candidate = (
                window.net_bps >= candidate_min_net_bps
                and depth
                and skew_ok
                and buy.quality >= min_quality
                and sell.quality >= min_quality
                and "crossed" not in buy.flags
                and "crossed" not in sell.flags
            )
            watch = candidate and window.net_bps >= watch_min_net_bps
            execution = watch and window.net_bps >= execution_min_net_bps
            reasons = _reject_reasons(window, buy, sell, min_quality, execution_min_net_bps)
            window.candidate_gate_pass = candidate
            window.watch_gate_pass = watch
            window.execution_gate_theoretical_pass = execution
            window.reject_reasons = reasons
            window.reason_rejected = _primary_reason(reasons, candidate, watch, execution)
            result.spread_windows.append(window)

            is_opportunity = (
                paper_enabled
                and window.net_bps >= min_net_bps
                and depth
                # books coletados a mais de max_skew_ms um do outro: o spread
                # entre eles é fotografia de instantes diferentes, não arb real
                and skew_ok
                and buy.quality >= min_quality
                and sell.quality >= min_quality
                # book cruzado (bid >= ask na MESMA exchange) é dado corrompido:
                # o crossed_penalty (30) deixa o score em exatamente 70 =
                # min_quality_score e o spread fantasma viraria paper trade no DB
                and "crossed" not in buy.flags
                and "crossed" not in sell.flags
            )
            if not is_opportunity:
                continue

            opp = Opportunity(
                ts=now_ts,
                symbol=result.symbol,
                buy_exchange=buy.exchange,
                sell_exchange=sell.exchange,
                size_quote=size_quote,
                gross_spread_bps=gross,
                break_even_bps=break_even.total_bps,
                depth_ok=depth,
                quality_buy=buy.quality,
                quality_sell=sell.quality,
            )
            result.opportunities.append(opp)
            result.paper_trades.append(
                simulate_arb(
                    buy.book,
                    sell.book,
                    size_quote=size_quote,
                    fee_buy_bps=fee_buy.taker_bps,
                    fee_sell_bps=fee_sell.taker_bps,
                    expected_net_bps=opp.net_bps,
                    min_fill_ratio=float(sim_cfg["min_fill_ratio"]),
                    level_participation=float(sim_cfg["level_participation"]),
                    delay_ms=float(cfg["paper"]["delay_ms"]),
                    # custos do break-even que o fill multinível NÃO simula —
                    # sem isto o realized fica ~16 bps otimista e o decay sai negativo
                    extra_costs_bps=(
                        break_even.slippage_est_bps
                        + break_even.latency_haircut_bps
                        + break_even.rebalance_amortized_bps
                    ),
                )
            )
    return result


def scan_summary(scan: ScanResult, mode: str, persisted: dict | None, db_note: str) -> dict:
    """Resumo serializável (JSON) de um ciclo --once."""
    best_net = max((w.net_bps for w in scan.spread_windows), default=None)
    return {
        "engine_role": "local_arb_research",
        "mode": mode,
        "ts": scan.ts,
        "symbol": scan.symbol,
        "min_net_bps": scan.min_net_bps,
        "books": [
            {
                "exchange": b.exchange,
                "ok": b.ok,
                "source": b.source,
                "quality": b.quality,
                "flags": b.flags,
                "best_bid": b.book.best_bid if b.book else None,
                "best_ask": b.book.best_ask if b.book else None,
                "error": b.error,
            }
            for b in scan.books
        ],
        "n_books_ok": sum(1 for b in scan.books if b.ok),
        "n_spread_windows": len(scan.spread_windows),
        "best_net_bps": best_net,
        "opportunities": [
            {
                "buy": o.buy_exchange,
                "sell": o.sell_exchange,
                "size_quote": o.size_quote,
                "gross_bps": round(o.gross_spread_bps, 2),
                "break_even_bps": round(o.break_even_bps, 2),
                "net_bps": round(o.net_bps, 2),
            }
            for o in scan.opportunities
        ],
        "paper_trades": [
            {
                "trade_id": t.trade_id,
                "status": t.status,
                "size_quote": t.size_quote,
                "fill_ratio": round(t.fill_ratio, 4),
                "expected_net_bps": round(t.expected_net_bps, 2),
                "realized_net_bps": round(t.realized_net_bps, 2),
                "decay_bps": round(t.decay_bps, 2),
            }
            for t in scan.paper_trades
        ],
        "persisted": persisted,
        "db": db_note,
    }
