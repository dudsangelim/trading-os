"""Spread bruto entre books e checagem de profundidade por tamanho."""

from __future__ import annotations

from .models import OrderBookLevel, OrderBookSnapshot


def gross_spread_bps(buy_book: OrderBookSnapshot, sell_book: OrderBookSnapshot) -> float | None:
    """Spread bruto em bps: comprar no best ask de buy_book, vender no best bid de sell_book.

    None se algum lado do book estiver vazio.
    """
    ask = buy_book.best_ask
    bid = sell_book.best_bid
    if ask is None or bid is None or ask <= 0:
        return None
    return (bid - ask) / ask * 10_000.0


def cum_notional(levels: list[OrderBookLevel]) -> float:
    return sum(lvl.notional for lvl in levels)


def depth_ok(book: OrderBookSnapshot, side: str, size_quote: float) -> bool:
    """True se o book tem notional (preço×size somado nos níveis) >= size_quote.

    side="buy"  -> consome asks (compramos base gastando quote)
    side="sell" -> consome bids (vendemos base recebendo quote)
    """
    if side == "buy":
        levels = book.asks
    elif side == "sell":
        levels = book.bids
    else:
        raise ValueError(f"side inválido: {side!r}")
    return cum_notional(levels) >= size_quote


def route_depth_ok(buy_book: OrderBookSnapshot, sell_book: OrderBookSnapshot, size_quote: float) -> bool:
    return depth_ok(buy_book, "buy", size_quote) and depth_ok(sell_book, "sell", size_quote)
