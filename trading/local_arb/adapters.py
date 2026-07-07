"""Adapters de order book: REST público (sem credenciais) + sintético offline.

Contrato central: `fetch_order_book()` NUNCA levanta exceção por falha de
rede/parse/config — retorna sempre um `FetchResult` com `ok=False`, `error`
legível e `flags` de qualidade. O processo de coleta não pode crashar por
fonte ruim.

O adapter REST é genérico e dirigido pelo bloco `api:` do YAML — nenhuma
exchange é hardcoded aqui. Exemplo de bloco:

    api:
      url: "https://api.novadax.com/v1/market/depth?symbol=USDT_BRL&limit=20"
      data_path: [data]        # desce chaves aninhadas até o objeto do book
      bids_key: bids
      asks_key: asks
      level_format: pair       # pair = [preço, tamanho] | obj = {price:, amount:}
      price_key: price         # só p/ level_format=obj
      size_key: amount         # só p/ level_format=obj
      timeout_s: 5
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

from .models import OrderBookLevel, OrderBookSnapshot

DEFAULT_TIMEOUT_S = 5.0
MAX_LEVELS = 20
USER_AGENT = "local-arb-research/0.2 (paper only)"

# Cenário sintético padrão (mesma assimetria do --self-test: novadax barata,
# mercadobitcoin cara — spread largo o bastante p/ net > min_net_bps com as
# taxas default). Exchanges fora do dict recebem um mid neutro deslocado pelo
# índice, determinístico.
SYNTHETIC_SCENARIO: dict[str, dict[str, float]] = {
    "novadax": {"bid": 5.560, "ask": 5.565, "latency_ms": 120.0},
    "mercadobitcoin": {"bid": 5.655, "ask": 5.660, "latency_ms": 200.0},
    "bitypreco": {"bid": 5.598, "ask": 5.604, "latency_ms": 150.0},
}
SYNTHETIC_TS = 1_750_000_000.0  # ts fixo -> coleta sintética determinística


@dataclass
class FetchResult:
    exchange: str
    symbol: str
    ok: bool
    book: OrderBookSnapshot | None = None
    error: str | None = None
    flags: list[str] = field(default_factory=list)


class BaseAdapter:
    """Contrato mínimo de adapter. Subclasses implementam fetch_order_book."""

    exchange: str = "?"
    symbol: str = "?"
    source: str = "unknown"

    def fetch_order_book(self, now_ts: float | None = None) -> FetchResult:
        raise NotImplementedError

    def _fail(self, error: str, flag: str = "fetch_error") -> FetchResult:
        return FetchResult(self.exchange, self.symbol, ok=False, error=error, flags=[flag])


def _default_http_get(url: str, timeout_s: float):
    import requests

    resp = requests.get(url, timeout=timeout_s, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _to_level(raw, level_format: str, price_key: str, size_key: str) -> OrderBookLevel:
    if level_format == "obj":
        price, size = float(raw[price_key]), float(raw[size_key])
    else:  # "pair": [preço, tamanho]
        price, size = float(raw[0]), float(raw[1])
    # isfinite primeiro: NaN passa em `price <= 0` (comparação com NaN é False)
    if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
        raise ValueError(f"nível inválido price={price} size={size}")
    return OrderBookLevel(price, size)


class PublicRestAdapter(BaseAdapter):
    """Adapter REST público genérico, configurado pelo bloco `api:` do YAML.

    `http_get` é injetável para testes offline (recebe (url, timeout_s) e
    retorna o JSON decodificado).
    """

    source = "rest"

    def __init__(
        self,
        exchange: str,
        symbol: str,
        api_cfg: dict | None,
        http_get: Callable[[str, float], object] | None = None,
    ) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.api = dict(api_cfg or {})
        self._http_get = http_get or _default_http_get

    def fetch_order_book(self, now_ts: float | None = None) -> FetchResult:
        url = self.api.get("url")
        if not url:
            return self._fail("api.url ausente na config (endpoint não configurado)", "no_endpoint")

        t0 = time.monotonic()
        try:
            payload = self._http_get(url, float(self.api.get("timeout_s", DEFAULT_TIMEOUT_S)))
        except Exception as exc:  # noqa: BLE001 — qualquer falha de rede vira erro controlado
            return self._fail(f"{type(exc).__name__}: {exc}", "fetch_error")
        latency_ms = (time.monotonic() - t0) * 1000.0

        try:
            bids, asks = self._parse(payload)
        except Exception as exc:  # noqa: BLE001 — payload inesperado vira erro controlado
            return self._fail(f"parse: {type(exc).__name__}: {exc}", "parse_error")

        if not bids or not asks:
            return self._fail("book vazio na resposta", "empty_book")

        book = OrderBookSnapshot(
            exchange=self.exchange,
            symbol=self.symbol,
            ts=now_ts if now_ts is not None else time.time(),
            bids=bids,
            asks=asks,
            latency_ms=latency_ms,
        )
        return FetchResult(self.exchange, self.symbol, ok=True, book=book)

    def _parse(self, payload) -> tuple[list[OrderBookLevel], list[OrderBookLevel]]:
        node = payload
        for key in self.api.get("data_path", []) or []:
            node = node[key]
        level_format = self.api.get("level_format", "pair")
        price_key = self.api.get("price_key", "price")
        size_key = self.api.get("size_key", "amount")
        bids = [
            _to_level(raw, level_format, price_key, size_key)
            for raw in node[self.api.get("bids_key", "bids")]
        ]
        asks = [
            _to_level(raw, level_format, price_key, size_key)
            for raw in node[self.api.get("asks_key", "asks")]
        ]
        # ordenar ANTES de truncar: payload não ordenado truncado primeiro
        # descartaria os melhores níveis e corromperia best bid/ask
        bids.sort(key=lambda lvl: lvl.price, reverse=True)
        asks.sort(key=lambda lvl: lvl.price)
        return bids[:MAX_LEVELS], asks[:MAX_LEVELS]


class SyntheticAdapter(BaseAdapter):
    """Books determinísticos offline, para testes/smoke sem rede."""

    source = "synthetic"

    def __init__(
        self,
        exchange: str,
        symbol: str,
        bid: float,
        ask: float,
        n_levels: int = 6,
        size: float = 400.0,
        latency_ms: float = 120.0,
        age_s: float = 1.0,
    ) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.bid = float(bid)
        self.ask = float(ask)
        self.n_levels = int(n_levels)
        self.size = float(size)
        self.latency_ms = float(latency_ms)
        self.age_s = float(age_s)

    def fetch_order_book(self, now_ts: float | None = None) -> FetchResult:
        ts = (now_ts if now_ts is not None else SYNTHETIC_TS) - self.age_s
        book = OrderBookSnapshot(
            exchange=self.exchange,
            symbol=self.symbol,
            ts=ts,
            bids=[OrderBookLevel(self.bid - i * 0.002, self.size) for i in range(self.n_levels)],
            asks=[OrderBookLevel(self.ask + i * 0.002, self.size) for i in range(self.n_levels)],
            latency_ms=self.latency_ms,
        )
        return FetchResult(self.exchange, self.symbol, ok=True, book=book)


def build_adapters(
    cfg: dict,
    synthetic: bool = False,
    http_get: Callable[[str, float], object] | None = None,
) -> list[BaseAdapter]:
    """Um adapter por exchange habilitada na config.

    synthetic=True -> SyntheticAdapter (cenário do YAML `synthetic:` da exchange,
    senão SYNTHETIC_SCENARIO, senão mid neutro deslocado pelo índice).
    synthetic=False -> PublicRestAdapter dirigido pelo bloco `api:` (sem bloco,
    o adapter existe mas falha controlado com flag no_endpoint).
    """
    symbol = cfg["pair"]["symbol"]
    adapters: list[BaseAdapter] = []
    for idx, (name, ex_cfg) in enumerate(cfg["exchanges"].items()):
        if not ex_cfg.get("enabled"):
            continue
        if synthetic:
            scen = ex_cfg.get("synthetic") or SYNTHETIC_SCENARIO.get(name) or {
                "bid": 5.600 + idx * 0.003,
                "ask": 5.606 + idx * 0.003,
            }
            adapters.append(
                SyntheticAdapter(
                    name,
                    symbol,
                    bid=float(scen["bid"]),
                    ask=float(scen["ask"]),
                    latency_ms=float(scen.get("latency_ms", 120.0)),
                )
            )
        else:
            adapters.append(PublicRestAdapter(name, symbol, ex_cfg.get("api"), http_get=http_get))
    return adapters
