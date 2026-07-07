"""Dataclasses do local_arb. Sem I/O — só estruturas de dados."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float  # em moeda base (USDT)

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass
class OrderBookSnapshot:
    exchange: str
    symbol: str
    ts: float                       # epoch seconds do snapshot
    bids: list[OrderBookLevel]      # ordenado desc por preço
    asks: list[OrderBookLevel]      # ordenado asc por preço
    latency_ms: float = 0.0         # tempo de coleta reportado pelo fetcher

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    def age_s(self, now_ts: float) -> float:
        return max(0.0, now_ts - self.ts)


@dataclass(frozen=True)
class FeeSchedule:
    exchange: str
    taker_bps: float
    maker_bps: float
    withdrawal_brl_fixed: float = 0.0
    withdrawal_usdt_fixed: float = 0.0


@dataclass(frozen=True)
class RouteBreakEven:
    buy_exchange: str
    sell_exchange: str
    taker_buy_bps: float
    taker_sell_bps: float
    slippage_est_bps: float
    latency_haircut_bps: float
    rebalance_amortized_bps: float

    @property
    def total_bps(self) -> float:
        return (
            self.taker_buy_bps
            + self.taker_sell_bps
            + self.slippage_est_bps
            + self.latency_haircut_bps
            + self.rebalance_amortized_bps
        )


@dataclass
class Opportunity:
    ts: float
    symbol: str
    buy_exchange: str
    sell_exchange: str
    size_quote: float               # tamanho em BRL
    gross_spread_bps: float
    break_even_bps: float
    depth_ok: bool
    quality_buy: int
    quality_sell: int
    status: str = "accepted"        # "accepted" | "rejected"
    reject_reason: str | None = None

    @property
    def net_bps(self) -> float:
        return self.gross_spread_bps - self.break_even_bps


@dataclass
class PaperTrade:
    trade_id: str
    ts: float
    symbol: str
    buy_exchange: str
    sell_exchange: str
    size_quote: float
    expected_net_bps: float
    realized_net_bps: float
    decay_bps: float                # expected - realized (positivo = decaiu)
    fill_ratio: float
    buy_vwap: float
    sell_vwap: float
    base_filled: float
    status: str                     # "filled" | "rejected_min_fill" | "rejected_depth"
    failure_reason: str | None = None  # motivo legível quando status != "filled"


@dataclass
class Balance:
    exchange: str
    asset: str
    free: float = 0.0
