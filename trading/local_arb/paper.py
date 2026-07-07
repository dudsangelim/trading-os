"""Execução paper pessimista: fill progressivo multinível, taker fee sempre, fill mínimo 95%."""

from __future__ import annotations

import itertools

from .models import OrderBookLevel, OrderBookSnapshot, PaperTrade

_trade_counter = itertools.count(1)

MIN_FILL_RATIO_DEFAULT = 0.95


def fill_buy(
    asks: list[OrderBookLevel], size_quote: float, level_participation: float = 1.0
) -> tuple[float, float, float]:
    """Compra base gastando até size_quote nos asks, nível a nível (pior preço conforme sobe).

    Pessimismo: só level_participation (ex.: 50%) do size exibido em cada nível é capturável.
    Retorna (base_filled, quote_spent, fill_ratio).
    """
    remaining = size_quote
    base_filled = 0.0
    for lvl in asks:
        if remaining <= 0:
            break
        avail_quote = lvl.price * lvl.size * level_participation
        take_quote = min(remaining, avail_quote)
        base_filled += take_quote / lvl.price
        remaining -= take_quote
    quote_spent = size_quote - remaining
    fill_ratio = quote_spent / size_quote if size_quote > 0 else 0.0
    return base_filled, quote_spent, fill_ratio


def fill_sell(
    bids: list[OrderBookLevel], size_base: float, level_participation: float = 1.0
) -> tuple[float, float, float]:
    """Vende até size_base nos bids, nível a nível. Retorna (base_sold, quote_received, fill_ratio)."""
    remaining = size_base
    quote_received = 0.0
    for lvl in bids:
        if remaining <= 0:
            break
        avail_base = lvl.size * level_participation
        take_base = min(remaining, avail_base)
        quote_received += take_base * lvl.price
        remaining -= take_base
    base_sold = size_base - remaining
    fill_ratio = base_sold / size_base if size_base > 0 else 0.0
    return base_sold, quote_received, fill_ratio


def simulate_arb(
    buy_book: OrderBookSnapshot,
    sell_book: OrderBookSnapshot,
    size_quote: float,
    fee_buy_bps: float,
    fee_sell_bps: float,
    expected_net_bps: float,
    min_fill_ratio: float = MIN_FILL_RATIO_DEFAULT,
    level_participation: float = 1.0,
    delay_ms: float = 0.0,
    extra_costs_bps: float = 0.0,
) -> PaperTrade:
    """Round-trip paper: compra em buy_book.asks, vende em sell_book.bids.

    Sempre taker dos dois lados. Rejeita se o fill efetivo (base casada nas duas pontas,
    valorada ao custo de compra) fica abaixo de min_fill_ratio do tamanho pedido.
    extra_costs_bps: componentes de custo NÃO simulados pelo fill multinível
    (slippage além do book visível, latency haircut, rebalance amortizado) — cobrados
    do realized para ficar na mesma base do expected (que já os desconta via break-even).
    decay_bps = expected_net_bps - realized_net_bps (isola o efeito multinível vs topo).
    delay_ms é só registrado (o pessimismo do atraso entra via extra_costs_bps).
    """
    base_bought, quote_spent, _ = fill_buy(buy_book.asks, size_quote, level_participation)
    base_sold, gross_proceeds, _ = fill_sell(sell_book.bids, base_bought, level_participation)

    buy_vwap = quote_spent / base_bought if base_bought > 0 else 0.0
    sell_vwap = gross_proceeds / base_sold if base_sold > 0 else 0.0

    # Só a base casada nas duas pontas conta como fill do round-trip.
    matched_quote = base_sold * buy_vwap
    fill_ratio = matched_quote / size_quote if size_quote > 0 else 0.0

    ts_exec = max(buy_book.ts, sell_book.ts) + delay_ms / 1000.0
    # id único entre processos/ciclos: cada --once é um processo novo e o contador
    # sozinho colidiria no DB (pt-000001 a cada ciclo).
    trade_id = f"pt-{int(ts_exec * 1000)}-{next(_trade_counter):04d}"

    if fill_ratio < min_fill_ratio or base_sold <= 0:
        failure_reason = (
            "nenhuma base casada nas duas pontas (book raso demais)"
            if base_sold <= 0
            else f"fill {fill_ratio:.3f} abaixo do mínimo {min_fill_ratio:.2f}"
        )
        return PaperTrade(
            trade_id=trade_id,
            ts=ts_exec,
            symbol=buy_book.symbol,
            buy_exchange=buy_book.exchange,
            sell_exchange=sell_book.exchange,
            size_quote=size_quote,
            expected_net_bps=expected_net_bps,
            realized_net_bps=0.0,
            decay_bps=0.0,
            fill_ratio=fill_ratio,
            buy_vwap=buy_vwap,
            sell_vwap=sell_vwap,
            base_filled=0.0,
            status="rejected_min_fill",
            failure_reason=failure_reason,
        )

    cost = matched_quote * (1.0 + fee_buy_bps / 10_000.0)
    proceeds = base_sold * sell_vwap * (1.0 - fee_sell_bps / 10_000.0)
    realized_net_bps = (proceeds / cost - 1.0) * 10_000.0 - extra_costs_bps
    decay_bps = expected_net_bps - realized_net_bps

    return PaperTrade(
        trade_id=trade_id,
        ts=ts_exec,
        symbol=buy_book.symbol,
        buy_exchange=buy_book.exchange,
        sell_exchange=sell_book.exchange,
        size_quote=size_quote,
        expected_net_bps=expected_net_bps,
        realized_net_bps=realized_net_bps,
        decay_bps=decay_bps,
        fill_ratio=fill_ratio,
        buy_vwap=buy_vwap,
        sell_vwap=sell_vwap,
        base_filled=base_sold,
        status="filled",
    )
