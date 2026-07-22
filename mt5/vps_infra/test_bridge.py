from mt5linux import MetaTrader5

mt5 = MetaTrader5(host="127.0.0.1", port=8001)
mt5._MetaTrader5__conn._config["sync_request_timeout"] = 240

path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
ok = mt5.initialize(path=path)
print("initialize(path):", ok, "| last_error:", mt5.last_error())
if not ok:
    ok = mt5.initialize()
    print("initialize():", ok, "| last_error:", mt5.last_error())
if ok:
    ti = mt5.terminal_info()
    print("terminal:", ti.name, "| connected:", ti.connected, "| trade_allowed:", ti.trade_allowed)
    print("version:", mt5.version())
mt5.shutdown()
