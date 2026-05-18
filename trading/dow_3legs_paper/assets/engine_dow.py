"""
Engines de calendário para DOW Caporale.

Duas implementações neste arquivo:

1) `DowCaporaleEngine` (LEGACY, 2 pernas):
   - Tue 23:55 UTC: abrir LONG; Wed 23:55: fechar long e abrir SHORT;
     Thu 23:55: fechar short.
   - Mantida pra compat e possível rollback.

2) `Dow3LegsSkipLowEngine` (CORRENTE, 3 pernas + gate ATR):
   - Sun 23:55: abrir LONG (L_Mon); Mon 23:55: fechar.
   - Tue 23:55: abrir LONG (L_Wed); Wed 23:55: fechar.
   - Wed 23:55 (após fechar L_Wed): abrir SHORT (S_Thu); Thu 23:55: fechar.
   - Cada ABERTURA é gated: só abre se `pass_atr_gate=True`.
     Esse gate é calculado externamente: ATR(14d) > P20(60d) do regime — pula
     dias parados onde a edge sazonal não se manifesta.
   - Validado em `backtests/dow_skiplow/`: PF 1.62, Sharpe 1.85, weekly +1.02%,
     walk-forward 3/3 anos positivos.

Em paper trade, o "momento" é definido por timestamps UTC. Acima de 23:55 do
dia da semana correspondente, dispara a transicao.

Cada decisao do engine produz uma entrada em decisions com:
  ts (UTC), action, reason, price, state_after
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict
import pandas as pd

# Limiares de horário (UTC) — disparo aceita janela [HH:MM, +5min] pra resiliência
TRIGGER_HOUR = 23
TRIGGER_MIN = 55
TRIGGER_TOLERANCE_MIN = 10  # aceita ate 5min depois pra evitar perder por timing

FEE_RT_PCT = 0.10  # roundtrip em %, conservador


def _is_trigger_time(ts: pd.Timestamp) -> bool:
    """Retorna True se o timestamp esta na janela de gatilho [23:55..00:09]"""
    t = ts.time()
    if t.hour == TRIGGER_HOUR and t.minute >= TRIGGER_MIN:
        return True
    if t.hour == 0 and t.minute < TRIGGER_TOLERANCE_MIN:
        return True
    return False


def _logical_dow(ts: pd.Timestamp) -> int:
    """Dia da semana logico: se for 23:55-23:59 do dia X, retorna X.
    Se for 00:00-00:09 do dia X+1, retorna X (ainda eh "fim" do dia anterior)."""
    t = ts.time()
    if t.hour == 0 and t.minute < TRIGGER_TOLERANCE_MIN:
        return (ts - pd.Timedelta(hours=1)).dayofweek
    return ts.dayofweek


# ============================================================================
# LEGACY — 2 pernas (Wed long + Thu short)
# ============================================================================
class DowCaporaleEngine:
    """Estado-maquina simples baseada em calendario. 2 pernas (LEGACY)."""

    def __init__(self, leverage: float = 1.0):
        self.state: str = "flat"
        self.leverage = leverage
        self.entry_price: Optional[float] = None
        self.entry_ts: Optional[pd.Timestamp] = None
        self.last_action_ts: Optional[pd.Timestamp] = None
        self.trades: List[Dict] = []
        self.decisions: List[Dict] = []

    def to_state_dict(self) -> dict:
        return dict(
            state=self.state,
            leverage=self.leverage,
            entry_price=self.entry_price,
            entry_ts=self.entry_ts.isoformat() if self.entry_ts is not None else None,
            last_action_ts=self.last_action_ts.isoformat() if self.last_action_ts is not None else None,
            n_trades=len(self.trades),
        )

    def load_state_dict(self, d: dict):
        self.state = d.get("state", "flat")
        self.entry_price = d.get("entry_price")
        ets = d.get("entry_ts")
        self.entry_ts = pd.Timestamp(ets) if ets else None
        lts = d.get("last_action_ts")
        self.last_action_ts = pd.Timestamp(lts) if lts else None

    def on_tick(self, ts: pd.Timestamp, price: float) -> Optional[Dict]:
        if self.last_action_ts is not None:
            if (ts - self.last_action_ts).total_seconds() < 3600:
                return None
        if not _is_trigger_time(ts):
            return None
        ldow = _logical_dow(ts)
        if ldow == 1 and self.state == "flat":
            return self._open_long(ts, price)
        if ldow == 2 and self.state == "long_wed":
            return self._close_long_open_short(ts, price)
        if ldow == 3 and self.state == "short_thu":
            return self._close_short(ts, price)
        return None

    def _open_long(self, ts, price):
        self.state = "long_wed"
        self.entry_price = price
        self.entry_ts = ts
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="open_long", price=price, state_after=self.state)
        self.decisions.append(d)
        return d

    def _close_long_open_short(self, ts, price):
        ret_pct_gross = (price - self.entry_price) / self.entry_price * 100
        ret_pct_net = (ret_pct_gross - FEE_RT_PCT) * self.leverage
        self.trades.append(dict(entry_ts=self.entry_ts.isoformat(), exit_ts=ts.isoformat(),
                                side="long", entry_price=self.entry_price, exit_price=price,
                                ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
                                fee_pct=FEE_RT_PCT, leverage=self.leverage, leg="L_Wed"))
        self.state = "short_thu"
        self.entry_price = price
        self.entry_ts = ts
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="close_long_open_short", price=price,
                 long_ret_net_pct=ret_pct_net, state_after=self.state)
        self.decisions.append(d)
        return d

    def _close_short(self, ts, price):
        ret_pct_gross = (self.entry_price - price) / self.entry_price * 100
        ret_pct_net = (ret_pct_gross - FEE_RT_PCT) * self.leverage
        self.trades.append(dict(entry_ts=self.entry_ts.isoformat(), exit_ts=ts.isoformat(),
                                side="short", entry_price=self.entry_price, exit_price=price,
                                ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
                                fee_pct=FEE_RT_PCT, leverage=self.leverage, leg="S_Thu"))
        self.state = "flat"
        self.entry_price = None
        self.entry_ts = None
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="close_short", price=price,
                 short_ret_net_pct=ret_pct_net, state_after=self.state)
        self.decisions.append(d)
        return d


# ============================================================================
# CORRENTE — 3 pernas (L_Mon + L_Wed + S_Thu) + gate ATR
# ============================================================================
class Dow3LegsSkipLowEngine:
    """
    State machine das 3 pernas DOW + skip_low.

    States:
      - flat
      - long_mon  (aberto Sun 23:55, fecha Mon 23:55)
      - long_wed  (aberto Tue 23:55, fecha Wed 23:55)
      - short_thu (aberto Wed 23:55 após close de long_wed, fecha Thu 23:55)

    on_tick recebe `pass_atr_gate: bool` — só ABRE se True.
    Se já tem posição aberta, fecha SEMPRE no horário previsto, independente do gate
    (não deixa trade pendurado).

    Backtest reference: PF 1.62, Sharpe 1.85, weekly +1.02% (lev=1, 2023+).
    """

    VERSION = "v2_3legs_skiplow"

    def __init__(self, leverage: float = 1.5):
        self.state: str = "flat"
        self.leverage = leverage
        self.entry_price: Optional[float] = None
        self.entry_ts: Optional[pd.Timestamp] = None
        self.last_action_ts: Optional[pd.Timestamp] = None
        self.trades: List[Dict] = []
        self.decisions: List[Dict] = []

    def to_state_dict(self) -> dict:
        return dict(
            version=self.VERSION,
            state=self.state,
            leverage=self.leverage,
            entry_price=self.entry_price,
            entry_ts=self.entry_ts.isoformat() if self.entry_ts is not None else None,
            last_action_ts=self.last_action_ts.isoformat() if self.last_action_ts is not None else None,
            n_trades=len(self.trades),
        )

    def load_state_dict(self, d: dict):
        self.state = d.get("state", "flat")
        self.leverage = d.get("leverage", self.leverage)
        self.entry_price = d.get("entry_price")
        ets = d.get("entry_ts")
        self.entry_ts = pd.Timestamp(ets) if ets else None
        lts = d.get("last_action_ts")
        self.last_action_ts = pd.Timestamp(lts) if lts else None

    def on_tick(self, ts: pd.Timestamp, price: float,
                pass_atr_gate: bool = True) -> Optional[Dict]:
        """
        Args:
            ts: timestamp UTC do tick
            price: preço atual
            pass_atr_gate: True se ATR(14d) > P20(60d). Se False, NÃO abre nova posição.
                           Não afeta fechamento.

        Returns:
            dict da decisão, ou None se nada aconteceu.
        """
        if self.last_action_ts is not None:
            if (ts - self.last_action_ts).total_seconds() < 3600:
                return None
        if not _is_trigger_time(ts):
            return None

        ldow = _logical_dow(ts)

        # ===== Fechamentos primeiro (sempre, independente de gate) =====

        # Mon (0): fechar long_mon
        if ldow == 0 and self.state == "long_mon":
            return self._close_long(ts, price, leg="L_Mon")

        # Wed (2): fechar long_wed e (se gate OK) abrir short_thu
        if ldow == 2 and self.state == "long_wed":
            d_close = self._close_long(ts, price, leg="L_Wed")
            # Após fechar, opcionalmente abrir short_thu
            if pass_atr_gate:
                # encadeia a abertura como segunda action no mesmo tick
                d_open = self._open_short(ts, price, leg="S_Thu")
                # mescla: retornamos uma action conjunta
                merged = dict(
                    ts=ts.isoformat(),
                    action="close_long_open_short",
                    price=price,
                    long_ret_net_pct=d_close["long_ret_net_pct"],
                    leg_closed="L_Wed",
                    leg_opened="S_Thu",
                    state_after=self.state,
                    pass_atr_gate=pass_atr_gate,
                )
                # substitui as 2 últimas decisions adicionadas pela merged (cosmético)
                self.decisions = self.decisions[:-2] + [merged]
                return merged
            else:
                # só fechou; não abriu short
                d_close["pass_atr_gate"] = pass_atr_gate
                d_close["short_skipped_reason"] = "atr_gate_off"
                return d_close

        # Thu (3): fechar short_thu
        if ldow == 3 and self.state == "short_thu":
            return self._close_short(ts, price, leg="S_Thu")

        # ===== Aberturas (com gate) =====

        if not pass_atr_gate:
            # log decision de skip pra rastreabilidade
            if self.state == "flat" and ldow in (6, 1):  # Sun ou Tue: hora normal de abrir
                d = dict(ts=ts.isoformat(), action="skip_open", price=price,
                         reason="atr_gate_off",
                         intended_leg="L_Mon" if ldow == 6 else "L_Wed",
                         state_after=self.state, pass_atr_gate=False)
                self.decisions.append(d)
                self.last_action_ts = ts  # registra pra evitar reprocesso
                return d
            return None

        # Sun (6): abrir long_mon se flat
        if ldow == 6 and self.state == "flat":
            return self._open_long(ts, price, leg="L_Mon")

        # Tue (1): abrir long_wed se flat
        if ldow == 1 and self.state == "flat":
            return self._open_long(ts, price, leg="L_Wed")

        return None

    def _open_long(self, ts, price, leg: str):
        self.state = "long_mon" if leg == "L_Mon" else "long_wed"
        self.entry_price = price
        self.entry_ts = ts
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="open_long", price=price, leg=leg,
                 state_after=self.state)
        self.decisions.append(d)
        return d

    def _open_short(self, ts, price, leg: str):
        self.state = "short_thu"
        self.entry_price = price
        self.entry_ts = ts
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="open_short", price=price, leg=leg,
                 state_after=self.state)
        self.decisions.append(d)
        return d

    def _close_long(self, ts, price, leg: str):
        ret_pct_gross = (price - self.entry_price) / self.entry_price * 100
        ret_pct_net = (ret_pct_gross - FEE_RT_PCT) * self.leverage
        self.trades.append(dict(
            entry_ts=self.entry_ts.isoformat(), exit_ts=ts.isoformat(),
            side="long", leg=leg,
            entry_price=self.entry_price, exit_price=price,
            ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
            fee_pct=FEE_RT_PCT, leverage=self.leverage,
        ))
        self.state = "flat"
        self.entry_price = None
        self.entry_ts = None
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="close_long", price=price, leg=leg,
                 long_ret_net_pct=ret_pct_net, state_after=self.state)
        self.decisions.append(d)
        return d

    def _close_short(self, ts, price, leg: str):
        ret_pct_gross = (self.entry_price - price) / self.entry_price * 100
        ret_pct_net = (ret_pct_gross - FEE_RT_PCT) * self.leverage
        self.trades.append(dict(
            entry_ts=self.entry_ts.isoformat(), exit_ts=ts.isoformat(),
            side="short", leg=leg,
            entry_price=self.entry_price, exit_price=price,
            ret_pct_gross=ret_pct_gross, ret_pct_net=ret_pct_net,
            fee_pct=FEE_RT_PCT, leverage=self.leverage,
        ))
        self.state = "flat"
        self.entry_price = None
        self.entry_ts = None
        self.last_action_ts = ts
        d = dict(ts=ts.isoformat(), action="close_short", price=price, leg=leg,
                 short_ret_net_pct=ret_pct_net, state_after=self.state)
        self.decisions.append(d)
        return d


if __name__ == "__main__":
    # Smoke test 3 pernas
    print("=== Smoke test Dow3LegsSkipLowEngine ===")
    eng = Dow3LegsSkipLowEngine(leverage=1.5)
    samples = [
        # (timestamp, price, atr_gate_ok)
        ("2026-04-19 23:56", 65000.0, True),   # Sun: open L_Mon
        ("2026-04-20 23:55", 66200.0, True),   # Mon: close L_Mon
        ("2026-04-21 23:55", 67000.0, True),   # Tue: open L_Wed
        ("2026-04-22 23:55", 68500.0, True),   # Wed: close L_Wed + open S_Thu
        ("2026-04-23 23:55", 67200.0, True),   # Thu: close S_Thu
        ("2026-04-26 23:56", 69500.0, False),  # Sun: skip (gate off)
        ("2026-04-28 23:56", 70000.0, True),   # Tue: open L_Wed
    ]
    for ts_str, p, gate in samples:
        ts = pd.Timestamp(ts_str, tz="UTC").tz_localize(None)
        d = eng.on_tick(ts, p, pass_atr_gate=gate)
        if d:
            print(f"  {ts_str} gate={gate} -> {d['action']} state={d['state_after']}")
    print(f"\ntrades ({len(eng.trades)}):")
    for t in eng.trades:
        print(f"  {t['side']} {t['leg']}: {t['entry_price']:.0f}->{t['exit_price']:.0f} "
              f"ret_net={t['ret_pct_net']:+.3f}% lev={t['leverage']}x")
