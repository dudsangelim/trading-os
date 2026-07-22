#!/usr/bin/env python3
"""
Bridge Python <-> MetaTrader 5 para bots B3 (WIN/WDO), conta XP demo.

Camada fina sobre o pacote MetaTrader5: conexao, dados e ordens.
Toda a logica de estrategia fica FORA daqui (nos bots/campanhas).

Seguranca:
  - dry_run=True por default: order_send apenas loga, nao envia.
  - Envio real exige dry_run=False E conta demo (trade_mode DEMO),
    a menos que allow_real=True seja passado explicitamente.

Uso tipico:
    from mt5_client import MT5Client
    with MT5Client() as c:
        df = c.rates("WIN$N", "M5", n=500)
        sym = c.front_contract("WIN")     # ex.: WINQ26
        c.market_order(sym, "buy", volume=1)   # dry-run: so loga
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import MetaTrader5 as mt5

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "D1": mt5.TIMEFRAME_D1,
}

# WINQ26, WDOH27 etc. (letra de mes + 2 digitos de ano)
REAL_CONTRACT_RE = re.compile(r"^(WIN|WDO)[FGHJKMNQUVXZ]\d{2}$")

log = logging.getLogger("mt5_client")


class MT5Client:
    def __init__(self, terminal_path: str = TERMINAL_PATH, dry_run: bool = True):
        self.terminal_path = terminal_path
        self.dry_run = dry_run
        self._connected = False

    # -- conexao -----------------------------------------------------------

    def connect(self) -> "MT5Client":
        if not (mt5.initialize(path=self.terminal_path) or mt5.initialize()):
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
        """Ultimas n barras de (symbol, tf). Timestamps em horario B3 (UTC-3)."""
        r = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[tf], 0, n)
        if r is None or len(r) == 0:
            raise RuntimeError(f"copy_rates_from_pos({symbol},{tf}) vazio: {mt5.last_error()}")
        df = pd.DataFrame(r)
        df["datetime_b3"] = pd.to_datetime(df["time"], unit="s")
        return df.rename(columns={"time": "epoch"})

    def rates_range(self, symbol: str, tf: str, start: datetime, end: datetime) -> pd.DataFrame:
        r = mt5.copy_rates_range(symbol, TIMEFRAMES[tf], start, end)
        if r is None or len(r) == 0:
            raise RuntimeError(f"copy_rates_range({symbol},{tf}) vazio: {mt5.last_error()}")
        df = pd.DataFrame(r)
        df["datetime_b3"] = pd.to_datetime(df["time"], unit="s")
        return df.rename(columns={"time": "epoch"})

    def tick(self, symbol: str):
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise RuntimeError(f"symbol_info_tick({symbol}) falhou: {mt5.last_error()}")
        return t

    def front_contract(self, root: str) -> str:
        """Contrato real com maior volume de sessao para root WIN ou WDO."""
        best, best_vol = None, -1
        for s in mt5.symbols_get(f"{root}*") or []:
            if not REAL_CONTRACT_RE.match(s.name):
                continue
            mt5.symbol_select(s.name, True)
            info = mt5.symbol_info(s.name)
            vol = getattr(info, "session_volume", 0) or 0
            if vol > best_vol:
                best, best_vol = s.name, vol
        if best is None:
            raise RuntimeError(f"nenhum contrato real encontrado para {root}")
        return best

    # -- ordens ------------------------------------------------------------

    def market_order(self, symbol: str, side: str, volume: float,
                     sl: float | None = None, tp: float | None = None,
                     comment: str = "", allow_real: bool = False):
        """Ordem a mercado. Em dry_run (default) apenas loga e retorna None."""
        assert side in ("buy", "sell")
        if self.dry_run:
            log.info("[DRY-RUN] %s %s x%s sl=%s tp=%s (%s)", side, symbol, volume, sl, tp, comment)
            return None
        if not self.is_demo() and not allow_real:
            raise PermissionError("Conta nao-demo e allow_real=False: ordem bloqueada.")
        mt5.symbol_select(symbol, True)
        t = self.tick(symbol)
        price = t.ask if side == "buy" else t.bid
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
        """Fecha uma posicao aberta (objeto de positions())."""
        side = "sell" if position.type == mt5.POSITION_TYPE_BUY else "buy"
        if self.dry_run:
            log.info("[DRY-RUN] close %s ticket=%s x%s", position.symbol, position.ticket, position.volume)
            return None
        if not self.is_demo() and not allow_real:
            raise PermissionError("Conta nao-demo e allow_real=False: fechamento bloqueado.")
        t = self.tick(position.symbol)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if side == "sell" else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": t.bid if side == "sell" else t.ask,
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
        for root in ("WIN", "WDO"):
            front = c.front_contract(root)
            t = c.tick(front)
            print(f"{root}: front={front} bid={t.bid} ask={t.ask}")
        df = c.rates("WIN$N", "M5", n=5)
        print(df[["datetime_b3", "open", "high", "low", "close", "real_volume"]].to_string(index=False))
