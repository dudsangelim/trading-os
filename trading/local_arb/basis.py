"""Basis Observer — prêmio USDT/BRL Bybit×Binance como reversão à média.

Motivação (auditoria 2026-07-13 sobre 5 dias / ~963k janelas do Continuous
Observer): a ÚNICA anomalia recorrente do dataset é o book da Bybit cotar
USDT/BRL sistematicamente RICO vs Binance, em episódios de 25-100bps que
duram minutos e revertem (fluxo de onramp varejo BR, mais forte 22h-08h UTC).
Arbitragem taker-taker cross-venue morre no rebalanceamento de BRL; a versão
viável é INTRA-VENUE: vender USDT->BRL na Bybit com prêmio alto e recomprar
na própria Bybit quando o prêmio reverte — nenhum fiat se move. O hedge
opcional na Binance congela o basis durante a janela.

Contabilidade (tudo em bps sobre o mid da Binance como referência):
  sell_premium    = (bybit_bid / binance_mid - 1) * 1e4   # capturável vendendo agora
  buyback_premium = (bybit_ask / binance_mid - 1) * 1e4   # pago recomprando agora
  PnL hedgeado por ciclo = sell_premium(entrada) - buyback_premium(saída) - custos

Custos NÃO são fixados aqui: cada trade paper grava os prêmios crus e o
relatório aplica os `fee_scenarios` da config (taker/maker, com/sem hedge).

SIGNAL_ONLY: nenhuma ordem, nenhuma credencial, nenhum saque.
"""
from __future__ import annotations

import csv
import glob
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .observer import observer_dir, utc_date_str

BASIS_POINT_FIELDS = [
    "ts", "rich_exchange", "ref_exchange", "rich_bid", "rich_ask",
    "ref_bid", "ref_ask", "ref_mid", "sell_premium_bps", "buyback_premium_bps",
    "rich_quality", "ref_quality", "skew_ms", "source",
]
EPISODE_FIELDS = [
    "date", "start_ts", "end_ts", "duration_s", "n_obs",
    "max_sell_premium_bps", "mean_sell_premium_bps", "area_bps_s",
]
PAPER_FIELDS = [
    "entry_ts", "exit_ts", "hold_s", "entry_sell_premium_bps",
    "exit_buyback_premium_bps", "gross_reversion_bps", "exit_reason",
    "entry_ref_mid", "exit_ref_mid", "ref_drift_bps",
]
CROSS_FIELDS = ["ts", "cross_gross_bps", "rich_bid", "ref_ask"]


def basis_cfg(cfg: dict) -> dict:
    return cfg.get("basis_observer", {}) or {}


def basis_enabled(cfg: dict) -> bool:
    return bool(basis_cfg(cfg).get("enabled", False))


def _daily_path(data_dir: Path, kind: str, date_str: str, mode: str = "rest") -> Path:
    return observer_dir(data_dir, mode) / f"basis_{kind}_{date_str}.csv"


def _append(path: Path, fields: list[str], rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


@dataclass
class BasisPoint:
    ts: float
    rich_bid: float
    rich_ask: float
    ref_bid: float
    ref_ask: float

    @property
    def ref_mid(self) -> float:
        return (self.ref_bid + self.ref_ask) / 2.0

    @property
    def sell_premium_bps(self) -> float:
        return (self.rich_bid / self.ref_mid - 1.0) * 1e4

    @property
    def buyback_premium_bps(self) -> float:
        return (self.rich_ask / self.ref_mid - 1.0) * 1e4

    @property
    def cross_gross_bps(self) -> float:
        """Ciclo cross-venue executado no instante: compra no ask da ref, vende no
        bid da rich (inventário pré-posicionado; rebalance via PIX/USDT depois)."""
        return (self.rich_bid / self.ref_ask - 1.0) * 1e4


@dataclass
class _Episode:
    start_ts: float
    last_ts: float
    n_obs: int = 0
    max_p: float = -math.inf
    sum_p: float = 0.0
    area: float = 0.0            # integral do prêmio no tempo (bps·s)

    def as_row(self) -> dict:
        dur = self.last_ts - self.start_ts
        return {
            "date": utc_date_str(self.start_ts),
            "start_ts": self.start_ts, "end_ts": self.last_ts,
            "duration_s": round(dur, 1), "n_obs": self.n_obs,
            "max_sell_premium_bps": round(self.max_p, 2),
            "mean_sell_premium_bps": round(self.sum_p / max(self.n_obs, 1), 2),
            "area_bps_s": round(self.area, 1),
        }


@dataclass
class _OpenTrade:
    entry_ts: float
    entry_sell_premium: float
    entry_ref_mid: float


@dataclass
class BasisTracker:
    """Estado incremental: pontos -> episódios + paper de reversão. Usável tanto
    no loop live (um update por ciclo) quanto em replay de snapshots históricos."""
    cfg: dict
    data_dir: Path | None = None          # None = replay puro (sem CSV incremental)
    mode: str = "rest"
    persist_points: bool = True

    episode: _Episode | None = None
    open_trade: _OpenTrade | None = None
    last_exit_ts: float = 0.0
    last_cross_ts: float = -1e18
    episodes: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    cross_trades: list[dict] = field(default_factory=list)
    n_points: int = 0

    def _c(self, key: str, default):
        return basis_cfg(self.cfg).get(key, default)

    def _p(self, key: str, default):
        return (basis_cfg(self.cfg).get("paper", {}) or {}).get(key, default)

    # ── ponto a ponto ─────────────────────────────────────────────────────
    def update(self, point: BasisPoint, quality_rich: int = 100, quality_ref: int = 100,
               skew_ms: float = 0.0, source: str = "rest") -> dict | None:
        min_q = int(self._c("min_quality_score", 70))
        max_skew = float(self._c("max_skew_ms", 2000.0))
        if quality_rich < min_q or quality_ref < min_q or skew_ms > max_skew:
            return None
        if point.rich_bid <= 0 or point.ref_mid <= 0:
            return None

        self.n_points += 1
        sell_p = point.sell_premium_bps
        buy_p = point.buyback_premium_bps

        if self.data_dir is not None and self.persist_points:
            _append(_daily_path(self.data_dir, "points", utc_date_str(point.ts), self.mode),
                    BASIS_POINT_FIELDS, [{
                        "ts": point.ts,
                        "rich_exchange": self._c("rich_exchange", "bybit"),
                        "ref_exchange": self._c("ref_exchange", "binance"),
                        "rich_bid": point.rich_bid, "rich_ask": point.rich_ask,
                        "ref_bid": point.ref_bid, "ref_ask": point.ref_ask,
                        "ref_mid": round(point.ref_mid, 6),
                        "sell_premium_bps": round(sell_p, 3),
                        "buyback_premium_bps": round(buy_p, 3),
                        "rich_quality": quality_rich, "ref_quality": quality_ref,
                        "skew_ms": round(skew_ms, 1), "source": source,
                    }])

        self._update_episode(point.ts, sell_p)
        trade_row = self._update_paper(point.ts, sell_p, buy_p, point.ref_mid)
        cross_row = self._update_cross(point)
        return {
            "sell_premium_bps": round(sell_p, 2),
            "buyback_premium_bps": round(buy_p, 2),
            "in_episode": self.episode is not None,
            "in_trade": self.open_trade is not None,
            "closed_trade": trade_row,
            "cross_trade": cross_row,
        }

    # ── episódios (descritivo, independente do paper) ─────────────────────
    def _update_episode(self, ts: float, sell_p: float) -> None:
        thr = float(self._c("episode_bps", 20.0))
        gap = float(self._c("episode_max_gap_s", 60.0))
        ep = self.episode
        if ep is not None and (ts - ep.last_ts > gap or sell_p < thr):
            self._close_episode()
            ep = None
        if sell_p >= thr:
            if ep is None:
                self.episode = ep = _Episode(start_ts=ts, last_ts=ts)
            ep.area += sell_p * max(0.0, ts - ep.last_ts)
            ep.last_ts = ts
            ep.n_obs += 1
            ep.max_p = max(ep.max_p, sell_p)
            ep.sum_p += sell_p

    def _close_episode(self) -> None:
        ep = self.episode
        self.episode = None
        if ep is None:
            return
        min_dur = float(self._c("episode_min_duration_s", 3.0))
        if ep.last_ts - ep.start_ts < min_dur or ep.n_obs < 2:
            return
        row = ep.as_row()
        self.episodes.append(row)
        if self.data_dir is not None:
            _append(_daily_path(self.data_dir, "episodes", row["date"], self.mode),
                    EPISODE_FIELDS, [row])

    # ── paper de reversão (um trade por vez) ──────────────────────────────
    def _update_paper(self, ts: float, sell_p: float, buy_p: float,
                      ref_mid: float) -> dict | None:
        entry = float(self._p("entry_bps", 25.0))
        exit_ = float(self._p("exit_bps", 5.0))
        max_hold = float(self._p("max_hold_s", 21600.0))
        cooldown = float(self._p("cooldown_s", 60.0))

        if self.open_trade is None:
            if sell_p >= entry and ts - self.last_exit_ts >= cooldown:
                self.open_trade = _OpenTrade(entry_ts=ts, entry_sell_premium=sell_p,
                                             entry_ref_mid=ref_mid)
            return None

        t = self.open_trade
        hold = ts - t.entry_ts
        reason = None
        if buy_p <= exit_:
            reason = "reverted"
        elif hold >= max_hold:
            reason = "timeout"
        if reason is None:
            return None

        self.open_trade = None
        self.last_exit_ts = ts
        row = {
            "entry_ts": t.entry_ts, "exit_ts": ts, "hold_s": round(hold, 1),
            "entry_sell_premium_bps": round(t.entry_sell_premium, 3),
            "exit_buyback_premium_bps": round(buy_p, 3),
            "gross_reversion_bps": round(t.entry_sell_premium - buy_p, 3),
            "exit_reason": reason,
            "entry_ref_mid": round(t.entry_ref_mid, 6),
            "exit_ref_mid": round(ref_mid, 6),
            "ref_drift_bps": round((ref_mid / t.entry_ref_mid - 1.0) * 1e4, 2),
        }
        self.trades.append(row)
        if self.data_dir is not None:
            _append(_daily_path(self.data_dir, "paper_trades", utc_date_str(ts), self.mode),
                    PAPER_FIELDS, [row])
        return row

    def _update_cross(self, point: BasisPoint) -> dict | None:
        """Paper cross-venue: com inventário nos DOIS lados, o ciclo executa no
        instante (compra ref ask + vende rich bid); PIX/USDT rebalanceiam depois
        (PIX zero-fee confirmado pelo Eduardo em 13/07/2026). Um ciclo por
        cooldown = tempo de rebalanceamento."""
        cc = basis_cfg(self.cfg).get("cross_paper", {}) or {}
        if not cc.get("enabled", False):
            return None
        entry = float(cc.get("entry_bps", 30.0))
        cooldown = float(cc.get("cycle_cooldown_s", 1800.0))
        g = point.cross_gross_bps
        if g < entry or point.ts - self.last_cross_ts < cooldown:
            return None
        self.last_cross_ts = point.ts
        row = {"ts": point.ts, "cross_gross_bps": round(g, 3),
               "rich_bid": point.rich_bid, "ref_ask": point.ref_ask}
        self.cross_trades.append(row)
        if self.data_dir is not None:
            _append(_daily_path(self.data_dir, "cross_trades", utc_date_str(point.ts), self.mode),
                    CROSS_FIELDS, [row])
        return row

    def flush(self) -> None:
        """Fecha episódio aberto (fim de replay/dia)."""
        self._close_episode()


# ── integração com o loop do observer ────────────────────────────────────────
def point_from_scan(scan, cfg: dict) -> tuple[BasisPoint, int, int, float] | None:
    """Extrai o par rich/ref do ScanResult do ciclo. None se algum book faltar."""
    bc = basis_cfg(cfg)
    rich_name = bc.get("rich_exchange", "bybit")
    ref_name = bc.get("ref_exchange", "binance")
    books = {b.exchange: b for b in scan.books}
    rich, ref = books.get(rich_name), books.get(ref_name)
    if not rich or not ref or not rich.ok or not ref.ok:
        return None
    if rich.book is None or ref.book is None:
        return None
    rb, ra = rich.book.best_bid, rich.book.best_ask
    fb, fa = ref.book.best_bid, ref.book.best_ask
    if not all(x and x > 0 for x in (rb, ra, fb, fa)):
        return None
    skew = abs(float(rich.book.latency_ms) - float(ref.book.latency_ms))
    point = BasisPoint(ts=scan.ts, rich_bid=rb, rich_ask=ra, ref_bid=fb, ref_ask=fa)
    return point, int(rich.quality), int(ref.quality), skew


# ── replay/backfill sobre observer_snapshots*.csv ────────────────────────────
def iter_snapshot_points(data_dir: Path, cfg: dict, mode: str = "rest") -> Iterator[tuple]:
    """Reconstrói pontos de basis dos CSVs de snapshots (diários + monólito),
    dedup por cycle_ts, ordenado no tempo."""
    bc = basis_cfg(cfg)
    rich_name = bc.get("rich_exchange", "bybit")
    ref_name = bc.get("ref_exchange", "binance")
    base = observer_dir(data_dir, mode)
    files = sorted(glob.glob(str(base / "observer_snapshots*.csv")))
    cycles: dict[float, dict] = {}
    for fp in files:
        with open(fp, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("source") != "rest" or row.get("error"):
                    continue
                ex = row.get("exchange")
                if ex not in (rich_name, ref_name):
                    continue
                try:
                    cyc = float(row.get("cycle_ts") or row["timestamp_local"])
                except (TypeError, ValueError):
                    continue
                slot = cycles.setdefault(cyc, {})
                slot[ex] = row
    for cyc in sorted(cycles):
        slot = cycles[cyc]
        r, f_ = slot.get(rich_name), slot.get(ref_name)
        if not r or not f_:
            continue
        try:
            point = BasisPoint(
                ts=cyc,
                rich_bid=float(r["best_bid"]), rich_ask=float(r["best_ask"]),
                ref_bid=float(f_["best_bid"]), ref_ask=float(f_["best_ask"]),
            )
            q_r, q_f = int(float(r["quality_score"])), int(float(f_["quality_score"]))
            skew = abs(float(r["latency_ms"]) - float(f_["latency_ms"]))
        except (KeyError, TypeError, ValueError):
            continue
        yield point, q_r, q_f, skew


def run_backfill(data_dir: Path, cfg: dict, mode: str = "rest",
                 grid: bool = True) -> Path:
    """Replay dos snapshots históricos pelo tracker + relatório. Sem grid=False
    roda só a config default."""
    points = list(iter_snapshot_points(data_dir, cfg, mode))
    report_dir = data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    bc = basis_cfg(cfg)
    scenarios = bc.get("fee_scenarios", {}) or {"taker_hedged": 37.0}

    def run_one(entry: float, exit_: float, max_hold: float) -> BasisTracker:
        c = dict(cfg)
        c["basis_observer"] = dict(bc)
        c["basis_observer"]["paper"] = dict(bc.get("paper", {}) or {})
        c["basis_observer"]["paper"].update(
            {"entry_bps": entry, "exit_bps": exit_, "max_hold_s": max_hold})
        tr = BasisTracker(cfg=c, data_dir=None)
        for point, qr, qf, skew in points:
            tr.update(point, qr, qf, skew)
        tr.flush()
        return tr

    # descritivo com a config default
    p = bc.get("paper", {}) or {}
    base_tr = run_one(float(p.get("entry_bps", 25.0)), float(p.get("exit_bps", 5.0)),
                      float(p.get("max_hold_s", 21600.0)))

    days = sorted({utc_date_str(pt.ts) for pt, *_ in points})
    n_days = max(len(days), 1)
    sells = [pt.sell_premium_bps for pt, *_ in points]
    sells_sorted = sorted(sells)

    def pct(x: float) -> float:
        if not sells_sorted:
            return float("nan")
        i = min(int(x / 100 * len(sells_sorted)), len(sells_sorted) - 1)
        return sells_sorted[i]

    L: list[str] = []
    P = L.append
    P(f"# local_arb — Basis Observer BACKFILL ({bc.get('rich_exchange','bybit')}×"
      f"{bc.get('ref_exchange','binance')}) — {datetime.now(timezone.utc).date()}")
    P("")
    P("STATUS: RESEARCH/BACKFILL — replay dos snapshots do Continuous Observer; nenhum número autoriza trade.")
    P("")
    P(f"- Pontos válidos: {base_tr.n_points} | dias: {len(days)} ({days[0] if days else '—'} → {days[-1] if days else '—'})")
    P(f"- sell_premium bps: p50={pct(50):+.1f}  p90={pct(90):+.1f}  p99={pct(99):+.1f}  max={max(sells_sorted):+.1f}" if sells_sorted else "- sem pontos")
    P(f"- % do tempo com prêmio ≥20bps: {100*sum(1 for s in sells if s>=20)/len(sells):.1f}%"
      f" | ≥30bps: {100*sum(1 for s in sells if s>=30)/len(sells):.1f}%"
      f" | ≥50bps: {100*sum(1 for s in sells if s>=50)/len(sells):.1f}%" if sells else "")
    P("")
    P(f"## Episódios (prêmio ≥ {bc.get('episode_bps', 20.0)}bps, config default)")
    P("")
    eps = base_tr.episodes
    if eps:
        durs = sorted(e["duration_s"] for e in eps)
        P(f"- N={len(eps)} ({len(eps)/n_days:.1f}/dia) | duração mediana {durs[len(durs)//2]:.0f}s | "
          f"máxima {durs[-1]:.0f}s | max prêmio {max(e['max_sell_premium_bps'] for e in eps):+.1f}bps")
    else:
        P("- nenhum episódio")
    P("")
    P("## Paper reversão — grid entry/exit (PnL hedgeado bruto por ciclo = entry_sell − exit_buyback)")
    P("")
    P("Custos por cenário (bps/ciclo, aplicados sobre o bruto):")
    for name, c in scenarios.items():
        P(f"- {name}: {c:.1f}")
    P("")
    header = "| entry | exit | max_hold | N | N/dia | reverted% | bruto méd | bruto tot |"
    for name in scenarios:
        header += f" net méd {name} | net tot {name} |"
    P(header)
    P("|" + "---|" * (8 + 2 * len(scenarios)))
    grid_entries = [15.0, 20.0, 25.0, 30.0, 40.0] if grid else [float(p.get("entry_bps", 25.0))]
    grid_exits = [0.0, 5.0, 10.0] if grid else [float(p.get("exit_bps", 5.0))]
    grid_holds = [21600.0, 86400.0, 432000.0] if grid else [float(p.get("max_hold_s", 21600.0))]
    for e_ in grid_entries:
        for x_ in grid_exits:
            if x_ >= e_:
                continue
            for h_ in grid_holds:
                tr = run_one(e_, x_, h_)
                n = len(tr.trades)
                if n == 0:
                    P(f"| {e_:.0f} | {x_:.0f} | {h_/3600:.0f}h | 0 | 0.0 | — | — | — |" +
                      " — | — |" * len(scenarios))
                    continue
                gross = [t["gross_reversion_bps"] for t in tr.trades]
                rev = sum(1 for t in tr.trades if t["exit_reason"] == "reverted")
                line = (f"| {e_:.0f} | {x_:.0f} | {h_/3600:.0f}h | {n} | {n/n_days:.1f} | "
                        f"{100*rev/n:.0f}% | {sum(gross)/n:+.1f} | {sum(gross):+.1f} |")
                for name, c in scenarios.items():
                    nets = [g - c for g in gross]
                    line += f" {sum(nets)/n:+.1f} | {sum(nets):+.1f} |"
                P(line)
    P("")
    P("## Paper CROSS-VENUE — colhe o NÍVEL do prêmio (inventário nos 2 lados, PIX zero-fee)")
    P("")
    cross_scen = bc.get("fee_scenarios_cross", {}) or {"taker": 26.0}
    P("Custos por ciclo (bps): " + ", ".join(f"{k}={v:.0f}" for k, v in cross_scen.items()))
    P("")
    hdr = "| entry | cooldown | N | N/dia |"
    for name in cross_scen:
        hdr += f" net méd {name} | net tot {name} |"
    P(hdr)
    P("|" + "---|" * (4 + 2 * len(cross_scen)))
    for e_ in ([20.0, 25.0, 30.0, 40.0, 50.0] if grid else
               [float((bc.get("cross_paper", {}) or {}).get("entry_bps", 30.0))]):
        for cd_ in ([1800.0, 3600.0] if grid else
                    [float((bc.get("cross_paper", {}) or {}).get("cycle_cooldown_s", 1800.0))]):
            last, gs = -1e18, []
            for pt, *_q in points:
                g = pt.cross_gross_bps
                if g >= e_ and pt.ts - last >= cd_:
                    gs.append(g)
                    last = pt.ts
            n = len(gs)
            if n == 0:
                P(f"| {e_:.0f} | {cd_/60:.0f}min | 0 | 0.0 |" + " — | — |" * len(cross_scen))
                continue
            line = f"| {e_:.0f} | {cd_/60:.0f}min | {n} | {n/n_days:.1f} |"
            for name, c in cross_scen.items():
                nets = [g - float(c) for g in gs]
                line += f" {sum(nets)/n:+.1f} | {sum(nets):+.1f} |"
            P(line)
    P("")
    P("Leitura: bruto em bps/ciclo sobre o notional do clip; net = bruto − cenário de custo.")
    P(f"⚠️ Amostra de {len(days)} dias — grid é exploratório; validar a config escolhida em coleta live antes de qualquer promoção.")
    P("")
    out = report_dir / "basis_backfill_report.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def generate_basis_report(data_dir: Path, date_str: str, cfg: dict,
                          mode: str = "rest") -> Path:
    """Relatório diário do basis observer live (points/episodes/trades do dia)."""
    base = observer_dir(data_dir, mode)
    bc = basis_cfg(cfg)
    scenarios = bc.get("fee_scenarios", {}) or {}

    def read(kind: str) -> list[dict]:
        p = _daily_path(data_dir, kind, date_str, mode)
        if not p.exists():
            return []
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    points = read("points")
    episodes = read("episodes")
    trades = read("paper_trades")
    cross = read("cross_trades")

    L: list[str] = []
    P = L.append
    P(f"# local_arb — Basis Observer {date_str} "
      f"({bc.get('rich_exchange','bybit')}×{bc.get('ref_exchange','binance')})")
    P("")
    P("STATUS: OBSERVER_ONLY — sem capital, sem execução real.")
    P("")
    sells = sorted(float(r["sell_premium_bps"]) for r in points) if points else []
    if sells:
        P(f"- Pontos: {len(sells)} | sell_premium p50 {sells[len(sells)//2]:+.1f}bps | "
          f"max {sells[-1]:+.1f}bps | ≥20bps: {100*sum(1 for s in sells if s>=20)/len(sells):.1f}% do tempo")
    else:
        P("- Sem pontos válidos no dia.")
    P(f"- Episódios (≥{bc.get('episode_bps',20.0)}bps): {len(episodes)}")
    cross_scen = bc.get("fee_scenarios_cross", {}) or {}
    P(f"- Ciclos CROSS-VENUE: {len(cross)}")
    if cross:
        cg = [float(t["cross_gross_bps"]) for t in cross]
        P(f"  - bruto: méd {sum(cg)/len(cg):+.1f}bps | total {sum(cg):+.1f}bps")
        for name, c in cross_scen.items():
            P(f"  - net {name} (custo {float(c):.0f}bps): total {sum(g - float(c) for g in cg):+.1f}bps")
    P(f"- Paper trades reversão fechados: {len(trades)}")
    if trades:
        gross = [float(t["gross_reversion_bps"]) for t in trades]
        P(f"- Bruto: méd {sum(gross)/len(gross):+.1f}bps | total {sum(gross):+.1f}bps")
        for name, c in scenarios.items():
            P(f"- Net {name} (custo {c:.0f}bps): total {sum(g - float(c) for g in gross):+.1f}bps")
        P("")
        P("| entry_ts | hold_s | entry bps | exit bps | bruto bps | saída |")
        P("|---|---:|---:|---:|---:|---|")
        for t in trades[-20:]:
            ets = datetime.fromtimestamp(float(t["entry_ts"]), timezone.utc).strftime("%H:%M:%S")
            P(f"| {ets} | {float(t['hold_s']):.0f} | {float(t['entry_sell_premium_bps']):+.1f} | "
              f"{float(t['exit_buyback_premium_bps']):+.1f} | {float(t['gross_reversion_bps']):+.1f} | "
              f"{t['exit_reason']} |")
    P("")
    out = data_dir / "reports" / f"basis_report_{date_str}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out
