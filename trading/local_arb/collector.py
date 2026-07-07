"""Coleta de snapshots: roda os adapters e aplica o quality score em cada book.

Falha de fonte NÃO derruba a coleta: o book falho entra na lista com ok=False,
quality=0 e flags explicando o motivo (fetch_error/parse_error/no_endpoint/…).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .adapters import BaseAdapter
from .models import OrderBookSnapshot
from .quality import quality_score


@dataclass
class CollectedBook:
    exchange: str
    symbol: str
    ok: bool
    book: OrderBookSnapshot | None
    error: str | None
    quality: int
    flags: list[str] = field(default_factory=list)
    source: str = "unknown"


def collect_once(adapters: list[BaseAdapter], cfg: dict, now_ts: float) -> list[CollectedBook]:
    max_age_s = float(cfg["thresholds"]["max_book_age_s"])
    qcfg = cfg.get("quality") or {}
    collected: list[CollectedBook] = []
    for adapter in adapters:
        result = adapter.fetch_order_book(now_ts=now_ts)
        if result.ok and result.book is not None:
            score, qflags = quality_score(result.book, now_ts, max_age_s, qcfg)
            collected.append(
                CollectedBook(
                    exchange=result.exchange,
                    symbol=result.symbol,
                    ok=True,
                    book=result.book,
                    error=None,
                    quality=score,
                    flags=[*result.flags, *qflags],
                    source=adapter.source,
                )
            )
        else:
            collected.append(
                CollectedBook(
                    exchange=result.exchange,
                    symbol=result.symbol,
                    ok=False,
                    book=None,
                    error=result.error or "falha desconhecida",
                    quality=0,
                    flags=list(result.flags),
                    source=adapter.source,
                )
            )
    return collected
