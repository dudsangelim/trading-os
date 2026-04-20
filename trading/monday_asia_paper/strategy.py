"""
Monday Asia Open ORB - pure strategy logic (no I/O).
Stateful per-asset. Designed for live paper trading.

State machine:
    WAITING   -> aguardando Sunday 22:00 UTC
    ARMED     -> range computado, monitorando breakout em Monday 00:00-03:00 UTC
    IN_TRADE  -> posicao aberta, monitorando TP/SL/exit time
    DONE      -> trade fechado, aguardando proxima semana
"""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional


class State(str, Enum):
    WAITING = "WAITING"
    ARMED = "ARMED"
    IN_TRADE = "IN_TRADE"
    DONE = "DONE"


@dataclass
class StrategyConfig:
    sunday_window_h: int = 10        # Sunday 12:00-22:00 UTC
    sunday_end_hour_utc: int = 22
    tp_multiplier: float = 1.5       # TP = 1.5 * range
    exit_hour_utc: int = 12          # forcar exit Monday 12:00 UTC
    entry_filter_minutes: int = 180  # ignorar entries apos 03:00 UTC Monday
    cost_bps_per_side: float = 3.0   # Hyperliquid taker ~2.5bps; 3 conservador
    position_size_usd: float = 1000.0  # tamanho fixo por par


@dataclass
class AssetState:
    asset: str
    state: State = State.WAITING
    # Semana corrente
    week_key: Optional[str] = None  # "2026-W16" identificador da semana
    range_hi: Optional[float] = None
    range_lo: Optional[float] = None
    # Trade corrente
    side: Optional[str] = None  # "long" | "short"
    entry_price: Optional[float] = None
    entry_time_utc: Optional[str] = None  # ISO
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    # Resultado (quando DONE)
    exit_price: Optional[float] = None
    exit_time_utc: Optional[str] = None
    exit_reason: Optional[str] = None
    pnl_bps_gross: Optional[float] = None
    pnl_bps_net: Optional[float] = None
    pnl_usd_gross: Optional[float] = None
    pnl_usd_net: Optional[float] = None

    def to_dict(self):
        return {k: (v.value if isinstance(v, Enum) else v) for k, v in asdict(self).items()}

    def reset_for_new_week(self, week_key: str):
        self.state = State.WAITING
        self.week_key = week_key
        self.range_hi = self.range_lo = None
        self.side = self.entry_price = self.entry_time_utc = None
        self.sl_price = self.tp_price = None
        self.exit_price = self.exit_time_utc = self.exit_reason = None
        self.pnl_bps_gross = self.pnl_bps_net = None
        self.pnl_usd_gross = self.pnl_usd_net = None


def week_key_for(dt_utc: datetime) -> str:
    """Identificador Mon-based da semana: o 'dominante' Monday."""
    # Normaliza para a Monday dessa semana
    # Se for Sunday, a "semana" de trade eh a que comeca no Monday seguinte
    # Se for Monday-Saturday, pertence a essa semana
    d = dt_utc.date()
    wd = d.weekday()  # Mon=0, Sun=6
    if wd == 6:  # Sunday
        mon = d + timedelta(days=1)
    else:
        mon = d - timedelta(days=wd)
    return mon.isoformat()


@dataclass
class Event:
    """Evento que o runner deve acionar (notificar, log, executar ordem)."""
    kind: str  # "range_computed", "entered", "exited", "cancelled"
    asset: str
    time_utc: str
    data: dict


class MondayAsiaORB:
    """Logica da estrategia. Stateful. Um AssetState por ativo."""

    def __init__(self, asset: str, config: StrategyConfig):
        self.asset = asset
        self.config = config
        self.s = AssetState(asset=asset)

    # ------------- public API -------------

    def on_tick(self, now_utc: datetime, last_price: float,
                candle_history: list[dict]) -> list[Event]:
        """
        Recebe:
         - now_utc: timestamp atual (tz-aware UTC)
         - last_price: ultimo close 1min
         - candle_history: lista de dicts com {t,o,h,l,c} pra calcular range
        Retorna lista de Events que o runner deve tratar (ordem/notificacao).
        """
        events: list[Event] = []

        # Week rollover: se a semana mudou, reseta.
        current_week = week_key_for(now_utc)
        if self.s.week_key != current_week:
            self.s.reset_for_new_week(current_week)

        # Dispatch por estado
        if self.s.state == State.WAITING:
            events += self._eval_waiting(now_utc, candle_history)
        elif self.s.state == State.ARMED:
            events += self._eval_armed(now_utc, last_price)
        elif self.s.state == State.IN_TRADE:
            events += self._eval_in_trade(now_utc, last_price)
        # DONE: no-op ate a semana rolar

        return events

    # ------------- state transitions -------------

    def _eval_waiting(self, now: datetime, candles: list[dict]) -> list[Event]:
        """Checa se deve computar o range e armar. Acontece em Sunday 22:00+ UTC."""
        events = []
        cfg = self.config
        # Checa Sunday 22:00 UTC pra frente (ate 23:59 UTC pra tolerar dados atrasados)
        if now.weekday() != 6:  # so Sunday
            return events
        if now.hour < cfg.sunday_end_hour_utc:
            return events

        # Range = Sunday 12:00-22:00 UTC (sunday_window_h)
        start_utc = datetime(now.year, now.month, now.day,
                             cfg.sunday_end_hour_utc - cfg.sunday_window_h,
                             0, 0, tzinfo=timezone.utc)
        end_utc = datetime(now.year, now.month, now.day,
                           cfg.sunday_end_hour_utc, 0, 0, tzinfo=timezone.utc)

        # Filtra candles na janela
        in_win = [c for c in candles if start_utc <= _ts(c) < end_utc]
        if len(in_win) < cfg.sunday_window_h * 60 * 0.8:
            # insuficientes dados ainda
            return events

        hi = max(c["h"] for c in in_win)
        lo = min(c["l"] for c in in_win)
        if hi <= lo:
            return events

        self.s.range_hi = hi
        self.s.range_lo = lo
        self.s.state = State.ARMED
        events.append(Event(
            kind="range_computed",
            asset=self.asset,
            time_utc=now.isoformat(),
            data={"range_hi": hi, "range_lo": lo,
                  "range_bps": (hi - lo) / in_win[0]["o"] * 10000,
                  "week": self.s.week_key}))
        return events

    def _eval_armed(self, now: datetime, price: float) -> list[Event]:
        """Monitora breakout entre Monday 00:00 e entry_filter_minutes depois."""
        events = []
        cfg = self.config

        # Calcula tempo desde Monday 00:00 UTC
        mon_00 = datetime.fromisoformat(self.s.week_key).replace(tzinfo=timezone.utc)
        if now < mon_00:
            return events  # ainda Sunday tarde, nao entra ordem antes de Mon 00

        elapsed_min = (now - mon_00).total_seconds() / 60

        # Filtro de 180min: se nao rompeu ainda, cancela e vira DONE
        if elapsed_min > cfg.entry_filter_minutes:
            self.s.state = State.DONE
            events.append(Event(
                kind="cancelled",
                asset=self.asset,
                time_utc=now.isoformat(),
                data={"reason": "no_breakout_within_filter_window",
                      "elapsed_min": elapsed_min,
                      "week": self.s.week_key}))
            return events

        # Checa breakout
        hi, lo = self.s.range_hi, self.s.range_lo
        if price >= hi:
            side = "long"
            entry = price  # preco real de execucao (taker no candle do breakout)
        elif price <= lo:
            side = "short"
            entry = price  # preco real de execucao (taker no candle do breakout)
        else:
            return events  # ainda dentro do range

        # Entra em trade
        rng = hi - lo
        if side == "long":
            sl = lo          # invalidacao: volta dentro do range
            tp = entry + cfg.tp_multiplier * rng
        else:
            sl = hi          # invalidacao: volta dentro do range
            tp = entry - cfg.tp_multiplier * rng

        self.s.state = State.IN_TRADE
        self.s.side = side
        self.s.entry_price = entry
        self.s.entry_time_utc = now.isoformat()
        self.s.sl_price = sl
        self.s.tp_price = tp

        events.append(Event(
            kind="entered",
            asset=self.asset,
            time_utc=now.isoformat(),
            data={"side": side, "entry_price": entry, "sl": sl, "tp": tp,
                  "range_hi": hi, "range_lo": lo,
                  "week": self.s.week_key}))
        return events

    def _eval_in_trade(self, now: datetime, price: float) -> list[Event]:
        """Checa TP/SL/exit time forcado."""
        events = []
        cfg = self.config

        mon_00 = datetime.fromisoformat(self.s.week_key).replace(tzinfo=timezone.utc)
        forced_exit_ts = mon_00 + timedelta(hours=cfg.exit_hour_utc)

        side = self.s.side
        entry = self.s.entry_price
        sl = self.s.sl_price
        tp = self.s.tp_price

        exit_reason = None
        exit_price = None

        if side == "long":
            if price <= sl:
                exit_reason = "sl"; exit_price = sl
            elif price >= tp:
                exit_reason = "tp"; exit_price = tp
        else:  # short
            if price >= sl:
                exit_reason = "sl"; exit_price = sl
            elif price <= tp:
                exit_reason = "tp"; exit_price = tp

        if exit_reason is None and now >= forced_exit_ts:
            exit_reason = "eod"
            exit_price = price  # closeout no mid/ask corrente

        if exit_reason is None:
            return events

        # Calcula PnL
        if side == "long":
            raw_bps = (exit_price / entry - 1) * 10000
        else:
            raw_bps = (entry / exit_price - 1) * 10000
        net_bps = raw_bps - 2 * cfg.cost_bps_per_side
        usd_gross = cfg.position_size_usd * raw_bps / 10000
        usd_net   = cfg.position_size_usd * net_bps / 10000

        self.s.exit_price = exit_price
        self.s.exit_time_utc = now.isoformat()
        self.s.exit_reason = exit_reason
        self.s.pnl_bps_gross = raw_bps
        self.s.pnl_bps_net = net_bps
        self.s.pnl_usd_gross = usd_gross
        self.s.pnl_usd_net = usd_net
        self.s.state = State.DONE

        events.append(Event(
            kind="exited",
            asset=self.asset,
            time_utc=now.isoformat(),
            data={
                "side": side, "entry_price": entry, "exit_price": exit_price,
                "sl": sl, "tp": tp, "exit_reason": exit_reason,
                "pnl_bps_gross": raw_bps, "pnl_bps_net": net_bps,
                "pnl_usd_gross": usd_gross, "pnl_usd_net": usd_net,
                "position_size_usd": cfg.position_size_usd,
                "entry_time_utc": self.s.entry_time_utc,
                "week": self.s.week_key,
            }))
        return events


# ------------- utils -------------

def _ts(candle: dict) -> datetime:
    """Candle t esta em ms desde epoch, UTC."""
    t_ms = candle["t"]
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)


# State persistence helpers
def save_state(state_path: Path, states: dict[str, AssetState]):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({k: v.to_dict() for k, v in states.items()}, indent=2))


def load_state(state_path: Path) -> dict[str, AssetState]:
    if not state_path.exists():
        return {}
    raw = json.loads(state_path.read_text())
    out = {}
    for asset, d in raw.items():
        d = dict(d)
        d["state"] = State(d["state"])
        out[asset] = AssetState(**d)
    return out
