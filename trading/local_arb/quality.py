"""Quality score 0-100 de um snapshot de book, com flags explicando descontos."""

from __future__ import annotations

from .models import OrderBookSnapshot

DEFAULTS = {
    "min_levels": 5,
    "stale_penalty": 40,
    "thin_book_penalty": 20,
    "crossed_penalty": 30,
    "high_latency_penalty": 10,
    "max_latency_ms": 1500,
}


def quality_score(
    book: OrderBookSnapshot,
    now_ts: float,
    max_age_s: float = 10.0,
    params: dict | None = None,
) -> tuple[int, list[str]]:
    """Retorna (score 0-100, flags). Book vazio = 0 direto."""
    p = {**DEFAULTS, **(params or {})}
    flags: list[str] = []

    if not book.bids or not book.asks:
        return 0, ["empty_book"]

    score = 100

    if book.age_s(now_ts) > max_age_s:
        score -= p["stale_penalty"]
        flags.append("stale")

    if len(book.bids) < p["min_levels"] or len(book.asks) < p["min_levels"]:
        score -= p["thin_book_penalty"]
        flags.append("thin_book")

    if book.best_bid >= book.best_ask:
        score -= p["crossed_penalty"]
        flags.append("crossed")

    if book.latency_ms > p["max_latency_ms"]:
        score -= p["high_latency_penalty"]
        flags.append("high_latency")

    return max(0, min(100, score)), flags
