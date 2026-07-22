#!/usr/bin/env python3
"""
E1+E2 PAPER BOT (demo XP) — carteira TSM multi-ativos + carry condicional WDO.

Roda 1x/dia util de manha (scheduled task 09:05). Logica fiel ao backtest:
estado calculado nos fechamentos diarios ate D-1 (ADJprop), posicao ajustada
na abertura de D (aprox. do "aplicado em t+1"; divergencia documentada:
backtest usa close-to-close).

- Sleeves TSM: {WDO, DI1, BGI, CCM, ICF, WIN}, L=126, F=21 (grade por sleeve
  persistida em state.json), sinal = sign(ret acumulado 126 pregoes ADJprop).
- Sleeve carry: short WDO quando diff (CDI*252 - FFR) > 6pp E vol20d < mediana
  movel 252d (dados BCB/FRED; fallback: mantem estado anterior se fetch falhar).
- Execucao: 1 contrato por sleeve (front contract real), posicoes NETadas por
  simbolo. Peso teorico de vol-targeting e' LOGADO mas nao executado
  (contrato inteiro; divergencia documentada).
- PAPER: conta demo obrigatoria (bridge bloqueia real). Comment "E1E2P".
"""
from __future__ import annotations

import io
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\bridge")
from mt5_client import MT5Client, mt5  # noqa: E402

BASE = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\paper")
STATE_F = BASE / "eod_state.json"
LOG_F = BASE / "eod_paper_log.jsonl"

L, F = 126, 21
# HOLDOUT 2026-07-22: carteira E1 multi-ativos FALHOU (agro negativo 2025+);
# instrumentados APENAS os componentes aprovados: E2a TSM WDO + E2b carry.
TSM_SLEEVES = ["WDO"]
ADJ_SUFFIX = "$"          # continua ajustada no servidor
COMMENT = "E1E2P"


def log_event(**kw):
    kw["ts"] = datetime.now(timezone.utc).isoformat()
    with LOG_F.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")


def load_state() -> dict:
    if STATE_F.exists():
        return json.loads(STATE_F.read_text(encoding="utf-8"))
    return {"sleeves": {}, "carry": {"on": False}, "created": str(date.today())}


def save_state(st: dict):
    STATE_F.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")


def daily_closes(sym_adj: str, n: int = 400) -> pd.Series:
    r = mt5.copy_rates_from_pos(sym_adj, mt5.TIMEFRAME_D1, 0, n)
    if r is None or len(r) == 0:
        raise RuntimeError(f"sem D1 para {sym_adj}: {mt5.last_error()}")
    df = pd.DataFrame(np.array(r))
    df["d"] = pd.to_datetime(df["time"], unit="s").dt.normalize()
    s = df.set_index("d")["close"].astype(float)
    # exclui a barra de HOJE (parcial) — sinal usa so fechamentos completos
    today = pd.Timestamp(date.today())
    return s[s.index < today]


def tsm_signal(closes: pd.Series) -> int:
    if len(closes) < L + 1:
        return 0
    ret = closes.iloc[-1] / closes.iloc[-1 - L] - 1.0
    return int(np.sign(ret))


def vol_target_weight(closes: pd.Series) -> float:
    r = closes.pct_change().dropna().tail(20)
    if len(r) < 20:
        return 1.0
    vol_ann = float(r.std() * np.sqrt(252))
    return float(min(3.0, 0.10 / vol_ann)) if vol_ann > 0 else 1.0


def carry_gate() -> tuple[bool, dict]:
    """diff = CDI diario*252 (pp) - FFR (pp aa); vol20d WDO < mediana 252d."""
    info = {}
    try:
        r = requests.get(
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados/ultimos/5?formato=json",
            timeout=30)
        cdi_ad = float(str(r.json()[-1]["valor"]).replace(",", "."))
        r2 = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF", timeout=30)
        dff = pd.read_csv(io.StringIO(r2.text))
        ffr = float(pd.to_numeric(dff["DFF"], errors="coerce").dropna().iloc[-1])
        diff_pp = cdi_ad * 252 - ffr
        closes = daily_closes("WDO" + ADJ_SUFFIX, 300)
        rets = closes.pct_change().dropna()
        vol20 = rets.tail(20).std() * np.sqrt(252)
        vol_series = rets.rolling(20).std().dropna() * np.sqrt(252)
        med252 = float(vol_series.tail(252).median())
        on = bool(diff_pp > 6.0 and float(vol20) < med252)
        info = {"diff_pp": round(diff_pp, 2), "vol20": round(float(vol20), 4),
                "med252": round(med252, 4), "gate_on": on}
        return on, info
    except Exception as e:  # noqa: BLE001
        return None, {"error": repr(e)}  # None = manter estado anterior


def current_net_positions(c: MT5Client) -> dict:
    net = {}
    for p in (c.positions() or []):
        if COMMENT not in (p.comment or ""):
            continue
        root = p.symbol[:3]
        sgn = 1 if p.type == mt5.POSITION_TYPE_BUY else -1
        net[root] = net.get(root, 0) + sgn * int(p.volume)
    return net


def main():
    dry = "--dry" in sys.argv
    with MT5Client(dry_run=dry) as c:
        if not c.is_demo():
            raise SystemExit("ABORT: conta nao-demo. Paper so roda em demo.")
        st = load_state()
        today = str(date.today())

        # 1) sinais TSM por sleeve (grade F por sleeve)
        targets = {}   # root -> alvo em contratos (netado)
        for root in TSM_SLEEVES:
            sl = st["sleeves"].setdefault(root, {"state": 0, "last_rebal_date": None})
            try:
                closes = daily_closes(root + ADJ_SUFFIX)
            except Exception as e:  # noqa: BLE001
                log_event(event="data_error", sleeve=root, error=repr(e))
                targets[root] = targets.get(root, 0) + sl["state"]
                continue
            # cadencia F em PREGOES DE CALENDARIO (imune a dias offline):
            # conta barras D1 desde a data do ultimo rebalance
            last_reb = sl.get("last_rebal_date")
            if last_reb is None:
                elapsed = F  # primeiro run: rebalanceia ja
            else:
                elapsed = int((closes.index > pd.Timestamp(last_reb)).sum())
            if elapsed >= F:
                new = tsm_signal(closes)
                w = vol_target_weight(closes)
                log_event(event="rebalance", sleeve=root, old=sl["state"], new=new,
                          vol_weight_teorico=round(w, 3))
                sl["state"] = new
                sl["last_rebal_date"] = str(closes.index[-1].date())
            targets[root] = targets.get(root, 0) + sl["state"]

        # 2) carry condicional (soma ao alvo do WDO)
        on, info = carry_gate()
        if on is None:
            on = st["carry"]["on"]  # fetch falhou: mantem
            log_event(event="carry_fetch_fail", kept=on, **info)
        else:
            if on != st["carry"]["on"]:
                log_event(event="carry_flip", old=st["carry"]["on"], new=on, **info)
            st["carry"]["on"] = on
        if on:
            targets["WDO"] = targets.get("WDO", 0) - 1

        # 3) executa ajustes (front contracts, mercado, 1 contrato por unidade)
        have = current_net_positions(c)
        for root, tgt in targets.items():
            cur = have.get(root, 0)
            delta = tgt - cur
            if delta == 0:
                continue
            try:
                front = c.front_contract(root)
            except Exception as e:  # noqa: BLE001
                log_event(event="front_error", root=root, error=repr(e))
                continue
            side = "buy" if delta > 0 else "sell"
            for _ in range(abs(int(delta))):
                try:
                    res = c.market_order(front, side, 1, comment=COMMENT)
                    log_event(event="order", root=root, symbol=front, side=side,
                              target=tgt, prev=cur,
                              price=getattr(res, "price", None),
                              deal=getattr(res, "deal", None))
                except Exception as e:  # noqa: BLE001
                    log_event(event="order_error", root=root, side=side, error=repr(e))
                    break
                time.sleep(0.5)

        st["last_run"] = today
        save_state(st)
        log_event(event="run_done", targets=targets, had=have, carry=st["carry"]["on"])
        print(f"targets={targets} carry_on={st['carry']['on']}")


if __name__ == "__main__":
    main()
