"""Clients de referência pública para as research routes (Binance + BCB/PTAX).

Contrato (mesmo dos adapters): NENHUMA chamada levanta exceção por falha de
rede/parse — sempre retorna um `RefQuote` com `ok=False` e `error` legível.
PTAX tem fallback controlado (Olinda → série SGS 1); se ambos falharem, o
quote volta ok=False e a rota decide (NO_DATA), sem crash.

Modo synthetic/offline: `SyntheticReferenceClient` serve preços fixos vindos
do bloco `references.synthetic:` do YAML — nenhuma rede é tocada.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

DEFAULT_TIMEOUT_S = 5.0
USER_AGENT = "local-arb-research/0.2 (paper only)"

DEFAULT_BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
DEFAULT_BINANCE_BOOK_URL = "https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
DEFAULT_PTAX_OLINDA_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicialCotacao=@dataInicialCotacao,"
    "dataFinalCotacao=@dataFinalCotacao)?@dataInicialCotacao='{start}'"
    "&@dataFinalCotacao='{end}'&$top=200&$format=json"
)
DEFAULT_PTAX_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados/ultimos/1?formato=json"
DEFAULT_PTAX_LOOKBACK_DAYS = 7

# Preços sintéticos default (sobrescritos pelo YAML references.synthetic)
SYNTHETIC_BINANCE_PRICES = {"BTCUSDT": 100_000.0, "ETHUSDT": 3_500.0, "USDCUSDT": 1.0005}
SYNTHETIC_PTAX_USD_BRL = 5.50


@dataclass
class RefQuote:
    source: str                  # "binance" | "ptax" | "synthetic"
    symbol: str                  # ex.: "BTCUSDT", "USD/BRL"
    ok: bool
    price: float | None = None
    ts: float | None = None
    error: str | None = None


def _valid_price(value: float) -> bool:
    return math.isfinite(value) and value > 0


def _default_http_get(url: str, timeout_s: float):
    import requests

    resp = requests.get(url, timeout=timeout_s, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


class BaseReferenceClient:
    """Contrato mínimo: preço spot Binance + USD/BRL PTAX, sem exceções."""

    def binance_price(self, symbol: str) -> RefQuote:
        raise NotImplementedError

    def ptax_usd_brl(self) -> RefQuote:
        raise NotImplementedError


class PublicReferenceClient(BaseReferenceClient):
    """Referências via REST público. `http_get` injetável p/ testes offline."""

    def __init__(
        self,
        cfg: dict | None = None,
        http_get: Callable[[str, float], object] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        cfg = cfg or {}
        bin_cfg = cfg.get("binance") or {}
        ptax_cfg = cfg.get("ptax") or {}
        self.binance_price_url = bin_cfg.get("price_url", DEFAULT_BINANCE_PRICE_URL)
        self.binance_timeout_s = float(bin_cfg.get("timeout_s", DEFAULT_TIMEOUT_S))
        self.ptax_olinda_url = ptax_cfg.get("olinda_url", DEFAULT_PTAX_OLINDA_URL)
        self.ptax_sgs_url = ptax_cfg.get("sgs_fallback_url", DEFAULT_PTAX_SGS_URL)
        self.ptax_lookback_days = int(ptax_cfg.get("lookback_days", DEFAULT_PTAX_LOOKBACK_DAYS))
        self.ptax_timeout_s = float(ptax_cfg.get("timeout_s", 8.0))
        self._http_get = http_get or _default_http_get
        self._now = now_fn or time.time

    # ── Binance ──────────────────────────────────────────────────────────
    def binance_price(self, symbol: str) -> RefQuote:
        url = self.binance_price_url.format(symbol=symbol)
        try:
            payload = self._http_get(url, self.binance_timeout_s)
            price = float(payload["price"])  # type: ignore[index]
        except Exception as exc:  # noqa: BLE001 — falha de rede/parse vira quote não-ok
            return RefQuote("binance", symbol, ok=False,
                            error=f"{type(exc).__name__}: {exc}")
        if not _valid_price(price):
            return RefQuote("binance", symbol, ok=False, error=f"preço inválido: {price}")
        return RefQuote("binance", symbol, ok=True, price=price, ts=self._now())

    # ── PTAX (BCB) ───────────────────────────────────────────────────────
    def ptax_usd_brl(self) -> RefQuote:
        errors: list[str] = []
        price = self._ptax_olinda(errors)
        if price is None:
            price = self._ptax_sgs(errors)
        if price is None:
            return RefQuote("ptax", "USD/BRL", ok=False, error="; ".join(errors) or "sem dados")
        if not _valid_price(price):
            return RefQuote("ptax", "USD/BRL", ok=False, error=f"cotação inválida: {price}")
        return RefQuote("ptax", "USD/BRL", ok=True, price=price, ts=self._now())

    def _ptax_olinda(self, errors: list[str]) -> float | None:
        end = datetime.fromtimestamp(self._now(), tz=timezone.utc)
        start = end - timedelta(days=self.ptax_lookback_days)
        url = self.ptax_olinda_url.format(
            start=start.strftime("%m-%d-%Y"), end=end.strftime("%m-%d-%Y")
        )
        try:
            payload = self._http_get(url, self.ptax_timeout_s)
            rows = payload["value"]  # type: ignore[index]
            if not rows:
                raise ValueError("resposta Olinda sem cotações no período")
            # última cotação da janela (Olinda retorna em ordem cronológica)
            return float(rows[-1]["cotacaoVenda"])
        except Exception as exc:  # noqa: BLE001 — cai pro fallback SGS
            errors.append(f"olinda: {type(exc).__name__}: {exc}")
            return None

    def _ptax_sgs(self, errors: list[str]) -> float | None:
        try:
            payload = self._http_get(self.ptax_sgs_url, self.ptax_timeout_s)
            valor = payload[0]["valor"]  # type: ignore[index]
            return float(str(valor).replace(",", "."))
        except Exception as exc:  # noqa: BLE001 — falha final vira quote não-ok
            errors.append(f"sgs: {type(exc).__name__}: {exc}")
            return None


class SyntheticReferenceClient(BaseReferenceClient):
    """Preços fixos offline (determinísticos) — nenhuma rede."""

    def __init__(
        self,
        binance_prices: dict[str, float] | None = None,
        ptax_usd_brl: float = SYNTHETIC_PTAX_USD_BRL,
        ts: float = 1_750_000_000.0,
    ) -> None:
        # YAML presente define o universo sintético INTEIRO (permite simular
        # símbolo ausente); sem YAML, usa os defaults do módulo.
        if binance_prices is not None:
            self.prices = {k: float(v) for k, v in binance_prices.items()}
        else:
            self.prices = dict(SYNTHETIC_BINANCE_PRICES)
        self.ptax = float(ptax_usd_brl)
        self.ts = float(ts)

    def binance_price(self, symbol: str) -> RefQuote:
        price = self.prices.get(symbol)
        if price is None:
            return RefQuote("synthetic", symbol, ok=False,
                            error=f"sem preço sintético para {symbol}")
        return RefQuote("synthetic", symbol, ok=True, price=price, ts=self.ts)

    def ptax_usd_brl(self) -> RefQuote:
        return RefQuote("synthetic", "USD/BRL", ok=True, price=self.ptax, ts=self.ts)


def build_reference_client(
    references_cfg: dict | None,
    synthetic: bool = False,
    http_get: Callable[[str, float], object] | None = None,
    now_ts: float | None = None,
) -> BaseReferenceClient:
    cfg = references_cfg or {}
    if synthetic:
        syn = cfg.get("synthetic") or {}
        return SyntheticReferenceClient(
            binance_prices=syn.get("binance"),
            ptax_usd_brl=float(syn.get("ptax_usd_brl", SYNTHETIC_PTAX_USD_BRL)),
            ts=now_ts if now_ts is not None else 1_750_000_000.0,
        )
    return PublicReferenceClient(cfg, http_get=http_get)
