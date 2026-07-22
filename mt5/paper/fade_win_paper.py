#!/usr/bin/env python3
"""
E3 PAPER BOT (demo XP) — fade de PDH/PDL no WIN, M5, S=25bps / T=10bps /
time-stop 60min. Objetivo primario do paper: MEDIR FILL REAL de ordem
limite no nivel (o backtest assume fill no toque — otimista).

Lancado 08:55 por scheduled task; roda ate 17:40. 1 contrato por nivel,
1 trade por nivel por dia. Conta demo obrigatoria. Comment "E3FADE".

Log jsonl registra: niveis, colocacao das ordens, fills (preco/hora),
gestao (stop/tp/time-stop) e distancia nivel->fill (slippage).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\bridge")
from mt5_client import MT5Client, mt5  # noqa: E402

BASE = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\paper")
LOG_F = BASE / "fade_paper_log.jsonl"

STOP_BPS, TP_BPS = 25.0, 10.0
TIME_STOP_MIN = 60
TICK = 5.0            # WIN: 5 pontos
COMMENT = "E3FADE"
END_NEW = (17, 30)    # sem ordens novas apos
END_ALL = (17, 35)    # fecha tudo


def log_event(**kw):
    kw["ts"] = datetime.now(timezone.utc).isoformat()
    with LOG_F.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")


def round_tick(p: float) -> float:
    return round(p / TICK) * TICK


def prev_day_levels(front: str) -> tuple[float, float]:
    r = mt5.copy_rates_from_pos(front, mt5.TIMEFRAME_D1, 0, 10)
    df = pd.DataFrame(np.array(r))
    df["d"] = pd.to_datetime(df["time"], unit="s").dt.normalize()
    today = pd.Timestamp(date.today())
    prev = df[df["d"] < today].iloc[-1]
    return float(prev["high"]), float(prev["low"])


def place_limit(c: MT5Client, front: str, side: str, price: float) -> int | None:
    """Ordem limite com SL/TP anexados. Retorna ticket ou None."""
    price = round_tick(price)
    bps = price / 1e4
    if side == "sell":   # fade da PDH
        sl = round_tick(price + STOP_BPS * bps)
        tp = round_tick(price - TP_BPS * bps)
        otype = mt5.ORDER_TYPE_SELL_LIMIT
    else:                # fade da PDL
        sl = round_tick(price - STOP_BPS * bps)
        tp = round_tick(price + TP_BPS * bps)
        otype = mt5.ORDER_TYPE_BUY_LIMIT
    req = {
        "action": mt5.TRADE_ACTION_PENDING, "symbol": front, "volume": 1.0,
        "type": otype, "price": price, "sl": sl, "tp": tp,
        "type_time": mt5.ORDER_TIME_DAY, "type_filling": mt5.ORDER_FILLING_RETURN,
        "comment": COMMENT,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        log_event(event="limit_reject", side=side, price=price,
                  retcode=getattr(res, "retcode", None),
                  comment=getattr(res, "comment", None))
        return None
    log_event(event="limit_placed", side=side, price=price, sl=sl, tp=tp,
              ticket=res.order)
    return res.order


def main():
    with MT5Client(dry_run=False) as c:
        if not c.is_demo():
            raise SystemExit("ABORT: conta nao-demo.")
        front = c.front_contract("WIN")
        pdh, pdl = prev_day_levels(front)
        log_event(event="session_start", front=front, pdh=pdh, pdl=pdl)

        tickets = {}
        tickets["sell"] = place_limit(c, front, "sell", pdh)
        tickets["buy"] = place_limit(c, front, "buy", pdl)
        fill_time: dict[int, float] = {}     # position_ticket -> epoch do fill
        done_levels = set()

        while True:
            now = datetime.now()
            if (now.hour, now.minute) >= END_ALL:
                break
            # posicoes abertas do bot
            for p in (c.positions(front) or []):
                if COMMENT not in (p.comment or ""):
                    continue
                if p.ticket not in fill_time:
                    fill_time[p.ticket] = time.time()
                    side = "sell" if p.type == mt5.POSITION_TYPE_SELL else "buy"
                    level = pdh if side == "sell" else pdl
                    slip = (p.price_open - level) if side == "sell" else (level - p.price_open)
                    done_levels.add(side)
                    log_event(event="fill", ticket=p.ticket, side=side,
                              price_open=p.price_open, level=level,
                              slippage_pts=slip)
                elif time.time() - fill_time[p.ticket] > TIME_STOP_MIN * 60:
                    try:
                        c.close_position(p)
                        log_event(event="time_stop_close", ticket=p.ticket)
                        fill_time.pop(p.ticket, None)
                    except Exception as e:  # noqa: BLE001
                        log_event(event="close_error", ticket=p.ticket, error=repr(e))
            # cancela pendentes apos horario limite de novas
            if (now.hour, now.minute) >= END_NEW:
                for o in (mt5.orders_get(symbol=front) or []):
                    if COMMENT in (o.comment or ""):
                        mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                        log_event(event="pending_cancelled", ticket=o.ticket)
            time.sleep(10)

        # encerramento: cancela pendentes e zera posicoes do bot
        for o in (mt5.orders_get(symbol=front) or []):
            if COMMENT in (o.comment or ""):
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
        for p in (c.positions(front) or []):
            if COMMENT in (p.comment or ""):
                try:
                    c.close_position(p)
                    log_event(event="eod_close", ticket=p.ticket)
                except Exception as e:  # noqa: BLE001
                    log_event(event="close_error", ticket=p.ticket, error=repr(e))
        log_event(event="session_end", fills=len(fill_time), done=sorted(done_levels))


if __name__ == "__main__":
    main()
