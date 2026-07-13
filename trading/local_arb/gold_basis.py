"""Gold Basis Observer — clone do Basis Observer para PAXG↔XAUT (ouro tokenizado).

Contexto (triagem 13/07/2026, [[project_local_arb_basis]]): PAXG (Paxos) e XAUT
(Tether) são ouro tokenizado de emissores DIFERENTES — não há loop de resgate no
varejo, logo não existe arbitragem pura; o candidato é o BASIS entre eles como
reversão à média (pair-trade). Verificado ao vivo 13/07: Binance lista PAXGUSDT E
XAUTUSDT (books apertados ≤1bp) → existe rota SAME-VENUE sem rails; Bybit lista
XAUTUSDT (spread ~0.3bp) → rota same-token cross-venue. OKX não tem nenhum.

Rotas monitoradas (cada uma em DUAS direções, fwd/rev, para cobrir as duas caudas):
  • xaut_paxg_binance  — XAUT@binance vs PAXG@binance (pair-trade same-venue;
    custo = 4 pernas: abre e fecha os dois tokens)
  • xaut_bybit_binance — XAUT@bybit vs XAUT@binance (mesmo token, cross-venue;
    com inventário nos 2 lados = 2 pernas, análogo ao cross do BRL)

Reusa BasisTracker/generate_basis_report de basis.py; cada rota+direção tem seu
próprio data_dir (data/gold_basis/<rota>_<dir>/...) para não misturar arquivos.
SIGNAL_ONLY: nenhuma ordem, nenhuma credencial.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .basis import BasisPoint, BasisTracker, generate_basis_report
from .observer import utc_date_str

Fetch = Callable[[], tuple[float, float, float]]   # -> (bid, ask, latency_ms)


def _get_json(url: str, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "local-arb-gold/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_binance_l1(symbol: str) -> tuple[float, float, float]:
    t0 = time.monotonic()
    d = _get_json(f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}")
    lat = (time.monotonic() - t0) * 1000.0
    return float(d["bidPrice"]), float(d["askPrice"]), lat


def fetch_bybit_l1(symbol: str) -> tuple[float, float, float]:
    t0 = time.monotonic()
    d = _get_json(
        f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={symbol}&limit=1")
    lat = (time.monotonic() - t0) * 1000.0
    r = d["result"]
    return float(r["b"][0][0]), float(r["a"][0][0]), lat


VENUE_FETCHERS: dict[str, Callable[[str], tuple[float, float, float]]] = {
    "binance": fetch_binance_l1,
    "bybit": fetch_bybit_l1,
}


def gold_cfg(cfg: dict) -> dict:
    return cfg.get("gold_basis", {}) or {}


def gold_enabled(cfg: dict) -> bool:
    return bool(gold_cfg(cfg).get("enabled", False))


@dataclass
class _RouteDir:
    """Uma direção de uma rota: tracker próprio + data_dir próprio."""
    name: str                     # ex. "xaut_paxg_binance_fwd"
    rich_leg: tuple[str, str]     # (venue, symbol) do lado "rico" (vende-se aqui)
    ref_leg: tuple[str, str]
    tracker: BasisTracker
    data_dir: Path
    route_cfg: dict


@dataclass
class GoldBasisObserver:
    cfg: dict
    data_dir: Path
    fetchers: dict[str, Callable[[str], tuple[float, float, float]]] = field(
        default_factory=lambda: dict(VENUE_FETCHERS))
    dirs: list[_RouteDir] = field(default_factory=list)
    current_day: str | None = None

    def __post_init__(self) -> None:
        gc = gold_cfg(self.cfg)
        for route in gc.get("routes", []):
            name = route["name"]
            legs = {
                "fwd": ((route["rich"]["venue"], route["rich"]["symbol"]),
                        (route["ref"]["venue"], route["ref"]["symbol"])),
                "rev": ((route["ref"]["venue"], route["ref"]["symbol"]),
                        (route["rich"]["venue"], route["rich"]["symbol"])),
            }
            for d, (rich, ref) in legs.items():
                rd_dir = self.data_dir / "gold_basis" / f"{name}_{d}"
                route_cfg = {"basis_observer": {
                    **{k: gc[k] for k in
                       ("min_quality_score", "max_skew_ms", "episode_bps",
                        "episode_max_gap_s", "episode_min_duration_s", "paper")
                       if k in gc},
                    "enabled": True,
                    "rich_exchange": f"{rich[0]}:{rich[1]}",
                    "ref_exchange": f"{ref[0]}:{ref[1]}",
                    "fee_scenarios": route.get("fee_scenarios",
                                               gc.get("fee_scenarios", {})),
                }}
                self.dirs.append(_RouteDir(
                    name=f"{name}_{d}", rich_leg=rich, ref_leg=ref,
                    tracker=BasisTracker(cfg=route_cfg, data_dir=rd_dir, mode="rest"),
                    data_dir=rd_dir, route_cfg=route_cfg,
                ))

    def _fetch_legs(self) -> dict[tuple[str, str], tuple[float, float, float] | None]:
        legs = {rd.rich_leg for rd in self.dirs} | {rd.ref_leg for rd in self.dirs}
        out: dict[tuple[str, str], tuple[float, float, float] | None] = {}
        for venue, symbol in legs:
            try:
                out[(venue, symbol)] = self.fetchers[venue](symbol)
            except Exception:
                out[(venue, symbol)] = None    # falha de fonte nunca derruba o loop
        return out

    def step(self, now_ts: float | None = None) -> dict[str, float]:
        """Um ciclo: busca cada perna 1x, atualiza todos os trackers.
        Retorna {rota_dir: sell_premium_bps} das direções fwd (p/ log)."""
        now_ts = now_ts if now_ts is not None else time.time()
        self._rollover(now_ts)
        books = self._fetch_legs()
        summary: dict[str, float] = {}
        for rd in self.dirs:
            rich, ref = books.get(rd.rich_leg), books.get(rd.ref_leg)
            if rich is None or ref is None:
                continue
            rb, ra, rlat = rich
            fb, fa, flat_ = ref
            if not all(x > 0 for x in (rb, ra, fb, fa)):
                continue
            point = BasisPoint(ts=now_ts, rich_bid=rb, rich_ask=ra,
                               ref_bid=fb, ref_ask=fa)
            res = rd.tracker.update(point, 100, 100, abs(rlat - flat_))
            if res is not None and rd.name.endswith("_fwd"):
                summary[rd.name[:-4]] = res["sell_premium_bps"]
        return summary

    def _rollover(self, now_ts: float) -> None:
        today = utc_date_str(now_ts)
        if self.current_day is not None and today != self.current_day:
            for rd in self.dirs:
                rd.tracker.flush()
                try:
                    generate_basis_report(rd.data_dir, self.current_day, rd.route_cfg)
                except Exception:
                    pass    # report nunca derruba o loop
        self.current_day = today
