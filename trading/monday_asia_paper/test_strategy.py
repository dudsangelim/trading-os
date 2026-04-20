"""
Smoke tests da strategy.py - roda sem rede, sem depender de Hyperliquid.

Simula alguns cenarios sinteticos:
  1. Sunday 22:00 arma o range
  2. Monday 00:05 preco rompe high -> entra long
  3. Preco sobe, bate TP -> exit tp
  4. Scenario de sl, eod, cancelled
"""
from datetime import datetime, timezone, timedelta
from strategy import MondayAsiaORB, StrategyConfig, State


def mk_candles(start: datetime, n: int, o: float, h_offset=5, l_offset=-5) -> list[dict]:
    out = []
    for i in range(n):
        t = start + timedelta(minutes=i)
        out.append({"t": int(t.timestamp() * 1000),
                    "o": o, "h": o + h_offset, "l": o + l_offset, "c": o, "v": 1})
    return out


def test_full_happy_path():
    cfg = StrategyConfig()
    s = MondayAsiaORB("BTC", cfg)

    # Sunday 12:00 UTC ... 22:00 UTC = 600 candles com H=110, L=90 (range=20)
    sunday = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)  # 2026-04-19 eh Sunday
    # (teste: domingo 19 abril 2026)
    cs = mk_candles(sunday, 600, o=100, h_offset=10, l_offset=-10)
    # high max = 110, low min = 90

    now = datetime(2026, 4, 19, 22, 0, tzinfo=timezone.utc)
    events = s.on_tick(now, last_price=100, candle_history=cs)
    assert s.s.state == State.ARMED, f"esperado ARMED, got {s.s.state}"
    assert s.s.range_hi == 110 and s.s.range_lo == 90
    assert len(events) == 1 and events[0].kind == "range_computed"
    print("[OK] teste 1: range armado")

    # Monday 00:05 preco = 112 (rompe high)
    now = datetime(2026, 4, 20, 0, 5, tzinfo=timezone.utc)
    events = s.on_tick(now, last_price=112, candle_history=cs)
    assert s.s.state == State.IN_TRADE
    assert s.s.side == "long"
    assert s.s.entry_price == 110
    assert s.s.tp_price == 110 + 1.5*20  # 140
    assert s.s.sl_price == 90
    assert len(events) == 1 and events[0].kind == "entered"
    print("[OK] teste 2: entry long")

    # Preco sobe pra 141 -> bate TP
    now = datetime(2026, 4, 20, 2, 0, tzinfo=timezone.utc)
    events = s.on_tick(now, last_price=141, candle_history=cs)
    assert s.s.state == State.DONE
    assert s.s.exit_reason == "tp"
    assert s.s.pnl_bps_gross > 0
    assert len(events) == 1 and events[0].kind == "exited"
    print(f"[OK] teste 3: exit TP  pnl_net={s.s.pnl_bps_net:.1f} bps")


def test_entry_filter_cancel():
    cfg = StrategyConfig(entry_filter_minutes=180)
    s = MondayAsiaORB("ETH", cfg)
    sunday = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    cs = mk_candles(sunday, 600, o=1000, h_offset=10, l_offset=-10)

    # arma
    now = datetime(2026, 4, 19, 22, 0, tzinfo=timezone.utc)
    s.on_tick(now, 1000, cs)
    assert s.s.state == State.ARMED

    # Monday 02:00: preco nunca rompeu, esta dentro do range
    now = datetime(2026, 4, 20, 2, 0, tzinfo=timezone.utc)
    events = s.on_tick(now, last_price=1005, candle_history=cs)
    assert s.s.state == State.ARMED
    assert len(events) == 0

    # Monday 03:15: filtro expira
    now = datetime(2026, 4, 20, 3, 15, tzinfo=timezone.utc)
    events = s.on_tick(now, last_price=1005, candle_history=cs)
    assert s.s.state == State.DONE
    assert events[0].kind == "cancelled"
    print("[OK] teste 4: cancelled por filtro 180min")


def test_short_sl():
    cfg = StrategyConfig()
    s = MondayAsiaORB("SOL", cfg)
    sunday = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    cs = mk_candles(sunday, 600, o=50, h_offset=2, l_offset=-2)

    now = datetime(2026, 4, 19, 22, 0, tzinfo=timezone.utc)
    s.on_tick(now, 50, cs)
    assert s.s.range_hi == 52 and s.s.range_lo == 48

    # Monday 00:02 preco cai pra 47 -> short em 48
    now = datetime(2026, 4, 20, 0, 2, tzinfo=timezone.utc)
    events = s.on_tick(now, 47, cs)
    assert s.s.side == "short"
    assert s.s.entry_price == 48
    assert s.s.sl_price == 52
    assert s.s.tp_price == 48 - 1.5*4  # 42

    # Monday 00:30 preco volta pra 52 -> SL
    now = datetime(2026, 4, 20, 0, 30, tzinfo=timezone.utc)
    events = s.on_tick(now, 53, cs)
    assert s.s.state == State.DONE
    assert s.s.exit_reason == "sl"
    assert s.s.pnl_bps_net < 0
    print(f"[OK] teste 5: short->SL  pnl_net={s.s.pnl_bps_net:.1f} bps")


def test_eod_exit():
    cfg = StrategyConfig()
    s = MondayAsiaORB("BTC", cfg)
    sunday = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    cs = mk_candles(sunday, 600, o=100, h_offset=5, l_offset=-5)

    now = datetime(2026, 4, 19, 22, 0, tzinfo=timezone.utc)
    s.on_tick(now, 100, cs)

    # Enter long
    now = datetime(2026, 4, 20, 0, 10, tzinfo=timezone.utc)
    s.on_tick(now, 106, cs)
    assert s.s.state == State.IN_TRADE

    # Preco fica de lado, chega em Monday 12:00 UTC
    now = datetime(2026, 4, 20, 12, 1, tzinfo=timezone.utc)
    events = s.on_tick(now, 107, cs)
    assert s.s.state == State.DONE
    assert s.s.exit_reason == "eod"
    print(f"[OK] teste 6: EOD exit  pnl_net={s.s.pnl_bps_net:.1f} bps")


if __name__ == "__main__":
    test_full_happy_path()
    test_entry_filter_cancel()
    test_short_sl()
    test_eod_exit()
    print("\nTODOS OS TESTES PASSARAM")
