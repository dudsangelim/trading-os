#!/usr/bin/env python3
"""
Bridge Python <-> MetaTrader 5 — VERSAO VPS (via mt5linux/RPC, container
mt5_b3). Porte fiel do mt5/bridge/mt5_client.py do notebook: a API da
classe MT5Client e o objeto de modulo `mt5` sao os mesmos que os bots
esperam; muda apenas a camada de conexao (RPC 127.0.0.1:8001 em vez do
pacote MetaTrader5 nativo).

Seguranca identica: dry_run default, envio real exige conta demo.
"""
from __future__ import annotations

import logging
import re

import pandas as pd
from mt5linux import MetaTrader5 as _MT5Linux

# objeto de modulo compativel com `import MetaTrader5 as mt5`
mt5 = _MT5Linux(host="127.0.0.1", port=8001)
_conn = mt5._MetaTrader5__conn
_conn._config["sync_request_timeout"] = 600
_conn.execute("import datetime")

TERMINAL_PATH = ""  # irrelevante na VPS (terminal ja roda no container)

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "D1": mt5.TIMEFRAME_D1,
}

REAL_CONTRACT_RE = re.compile(r"^(WIN|WDO)[FGHJKMNQUVXZ]\d{2}$")

log = logging.getLogger("mt5_client")


class MT5Client:
    def __init__(self, terminal_path: str = TERMINAL_PATH, dry_run: bool = True):
        self.terminal_path = terminal_path
        self.dry_run = dry_run
        self._connected = False

    # -- conexao -----------------------------------------------------------

    def connect(self) -> "MT5Client":
        if not mt5.initialize():
            raise ConnectionError(f"mt5.initialize falhou: {mt5.last_error()}")
        self._connected = True
        ai = mt5.account_info()
        if ai is None:
            raise ConnectionError("account_info() retornou None; terminal sem login?")
        log.info("Conectado: conta %s (%s), servidor %s", ai.login, ai.name, ai.server)
        return self

    def close(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False

    def __enter__(self) -> "MT5Client":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def is_demo(self) -> bool:
        ai = mt5.account_info()
        return ai is not None and ai.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO

    # -- dados -------------------------------------------------------------

    def rates(self, symbol: str, tf: str, n: int = 500) -> pd.DataFrame:
        r = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[tf], 0, n)
        if r is None or len(r) == 0:
            raise RuntimeError(f"copy_rates_from_pos({symbol},{tf}) vazio: {mt5.last_error()}")
        df = pd.DataFrame(r)
        df["datetime_b3"] = pd.to_datetime(df["time"], unit="s")
        return df.rename(columns={"time": "epoch"})

    def tick(self, symbol: str):
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise RuntimeError(f"symbol_info_tick({symbol}) falhou: {mt5.last_error()}")
        return t

    def front_contract(self, root: str) -> str:
        best, best_vol = None, -1
        for s in mt5.symbols_get(f"{root}*") or []:
            name = str(s.name)
            if not REAL_CONTRACT_RE.match(name):
                continue
            mt5.symbol_select(name, True)
            info = mt5.symbol_info(name)
            vol = getattr(info, "session_volume", 0) or 0
            if vol > best_vol:
                best, best_vol = name, vol
        if best is None:
            raise RuntimeError(f"nenhum contrato real encontrado para {root}")
        return best

    # -- ordens ------------------------------------------------------------

    def market_order(self, symbol: str, side: str, volume: float,
                     sl: float | None = None, tp: float | None = None,
                     comment: str = "", allow_real: bool = False):
        assert side in ("buy", "sell")
        if self.dry_run:
            log.info("[DRY-RUN] %s %s x%s sl=%s tp=%s (%s)", side, symbol, volume, sl, tp, comment)
            return None
        if not self.is_demo() and not allow_real:
            raise PermissionError("Conta nao-demo e allow_real=False: ordem bloqueada.")
        mt5.symbol_select(symbol, True)
        t = self.tick(symbol)
        price = float(t.ask if side == "buy" else t.bid)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "deviation": 5,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if sl is not None:
            req["sl"] = float(sl)
        if tp is not None:
            req["tp"] = float(tp)
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"order_send falhou: {res}")
        log.info("Executada: %s %s x%s @ %s (deal %s)", side, symbol, volume, res.price, res.deal)
        return res

    def positions(self, symbol: str | None = None):
        return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    def close_position(self, position, allow_real: bool = False):
        side = "sell" if position.type == mt5.POSITION_TYPE_BUY else "buy"
        if self.dry_run:
            log.info("[DRY-RUN] close %s ticket=%s x%s", position.symbol, position.ticket, position.volume)
            return None
        if not self.is_demo() and not allow_real:
            raise PermissionError("Conta nao-demo e allow_real=False: fechamento bloqueado.")
        t = self.tick(position.symbol)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": str(position.symbol),
            "volume": float(position.volume),
            "type": mt5.ORDER_TYPE_SELL if side == "sell" else mt5.ORDER_TYPE_BUY,
            "position": int(position.ticket),
            "price": float(t.bid if side == "sell" else t.ask),
            "deviation": 5,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"close falhou: {res}")
        return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with MT5Client() as c:
        ai = mt5.account_info()
        print(f"conta={ai.login} servidor={ai.server} demo={c.is_demo()}")
        print(f"trade_allowed={mt5.terminal_info().trade_allowed}")
        for root in ("WIN", "WDO"):
            front = c.front_contract(root)
            t = c.tick(front)
            print(f"{root}: front={front} bid={t.bid} ask={t.ask}")
        pos = c.positions()
        for p in (pos or []):
            print(f"posicao: {p.symbol} vol={p.volume} type={p.type} comment={p.comment}")
