from datetime import datetime, timedelta
from mt5linux import MetaTrader5

mt5 = MetaTrader5(host="127.0.0.1", port=8001)
mt5._MetaTrader5__conn._config["sync_request_timeout"] = 240

ok = mt5.initialize()
print("initialize:", ok, "| last_error:", mt5.last_error())
if ok:
    ai = mt5.account_info()
    print("conta:", None if ai is None else (ai.login, ai.server, ai.trade_mode))
    ti = mt5.terminal_info()
    print("connected:", ti.connected, "| trade_allowed:", ti.trade_allowed)

    for sym in ("WIN$N", "WDO$N", "WINQ26", "WDOQ26"):
        sel = mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"{sym}: NAO ENCONTRADO (select={sel})")
        else:
            print(f"{sym}: select={sel} bid={info.bid} last={info.last} time={info.time}")

    r = mt5.copy_rates_from_pos("WIN$N", mt5.TIMEFRAME_M5, 0, 5)
    print("copy_rates WIN$N M5 (5 barras):", "OK n=%d" % len(r) if r is not None else mt5.last_error())
    if r is not None:
        print(r)

    now = datetime.now()
    ticks = mt5.copy_ticks_range("WINQ26", now - timedelta(minutes=10), now, mt5.COPY_TICKS_ALL)
    n = 0 if ticks is None else len(ticks)
    print("copy_ticks_range WINQ26 ult.10min:", n, "ticks", "" if ticks is not None else str(mt5.last_error()))
mt5.shutdown()
