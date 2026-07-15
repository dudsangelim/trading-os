"""Vol-surface conditioning signal (term-structure slope) for the VRP trader.

Research: /home/agent/research/options_edge2 (2026-07-05). Winner of the
pre-registered F1 family: size the weekly straddle by the expanding tercile of
signed = -z90(slope), slope = ATM IV 30d - ATM IV 7d. Steps 0.5x/1x/1.5x
(mean exposure ~1x — timing, not leverage). Backtest 2022→2026-05:
3.27%/mês, Sharpe 1.93, maxDD -18.4% vs baseline 2.56% / 1.73 / -17.9%;
melhor que o baseline em TODOS os anos; sobrevive custo 2x (2.03%/mês).

Operational fallback logged alongside: signed2 = IV7 - DVOL (z-free, usable
from day one; backtest 3.66%/mês, Sharpe 2.02, maxDD -22.5%).

Default LOG-ONLY (VRP_SLOPE_SIZING=0): the engine keeps trading 1x and the
multiplier is recorded per entry — sizing is linear, so the conditioned track
is exactly mult × weekly return. Flip the env var to apply it for real.

History: daily sample at the 08:05 cycle (mark IVs), persisted to
/data/slope_history.csv, seeded from assets/slope_seed.csv (research series,
trade-based IVs, 2022-01→2026-05-15). The z needs >=45 obs in the trailing 90
calendar days, so after the 05/2026→07/2026 collection gap the slope mult logs
1.0x until ~45 live samples accrue; the IV7-DVOL fallback is valid immediately.
"""
from __future__ import annotations

import csv
import logging
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from trading.vrp_paper import config as C
from trading.vrp_paper import deribit as D

log = logging.getLogger("vrp_paper")

HIST_FILE = C.DATA_DIR / "slope_history.csv"
SEED_FILE = Path(__file__).parent / "assets" / "slope_seed.csv"


# ── live sampling ─────────────────────────────────────────────────────────────
def _atm_iv_bucket(now: datetime, S: float, insts: list[dict],
                   dte_lo: float, dte_hi: float) -> float | None:
    """Mark-IV of the ATM call+put on the expiry nearest the bucket middle."""
    target = (dte_lo + dte_hi) / 2.0
    best = {}
    for i in insts:
        exp_ms = i["expiration_timestamp"]
        dte = (exp_ms / 1000 - now.timestamp()) / 86400
        if not (dte_lo <= dte <= dte_hi):
            continue
        key = exp_ms
        best.setdefault(key, {"dte": dte, "opts": []})
        best[key]["opts"].append(i)
    if not best:
        return None
    exp = min(best.values(), key=lambda v: abs(v["dte"] - target))
    strikes = {}
    for i in exp["opts"]:
        strikes.setdefault(i["strike"], {})[i["option_type"]] = i["instrument_name"]
    for K in sorted((k for k, v in strikes.items() if len(v) == 2),
                    key=lambda k: abs(k - S)):
        ivs = []
        for otype in ("call", "put"):
            tk = D.ticker(strikes[K][otype])
            iv = float(tk.get("mark_iv") or 0.0) / 100.0
            if iv > 0.01:
                ivs.append(iv)
        if len(ivs) == 2:
            return sum(ivs) / 2.0
    return None


def slope_sample(now: datetime) -> dict | None:
    S = D.index_price()
    insts = D.option_instruments()
    iv7 = _atm_iv_bucket(now, S, insts, 5.0, 9.5)
    iv30 = _atm_iv_bucket(now, S, insts, 20.0, 40.0)
    # research kept atm_iv_7d/atm_iv_30d as independent columns (207 of its days
    # had iv7 without iv30 — the ladder leaves [20,40] empty for stretches of
    # each month); slope is simply NaN there. Mirror that: record whatever the
    # surface gives, so the IV7-DVOL fallback and the freshness of the last row
    # do not depend on the 30d bucket being populated.
    if iv7 is None and iv30 is None:
        return None
    try:
        dvol = D.dvol_now()
    except Exception:
        dvol = float("nan")
    slope = None if (iv7 is None or iv30 is None) else round(iv30 - iv7, 6)
    return {"date": now.strftime("%Y-%m-%d"),
            "iv7": None if iv7 is None else round(iv7, 6),
            "iv30": None if iv30 is None else round(iv30, 6),
            "slope": slope, "dvol": round(dvol, 6)}


# ── history ───────────────────────────────────────────────────────────────────
def _ensure_history() -> None:
    if not HIST_FILE.exists():
        HIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SEED_FILE, HIST_FILE)
        log.info("slope history seeded from research (%s)", SEED_FILE.name)


def load_history() -> pd.DataFrame:
    _ensure_history()
    h = pd.read_csv(HIST_FILE, parse_dates=["date"])
    return h.drop_duplicates("date", keep="last").sort_values("date")


def record_daily(now: datetime) -> None:
    """Sample the surface once per day (called on the 08:05 cycle)."""
    h = load_history()
    today = now.strftime("%Y-%m-%d")
    if not h.empty and h["date"].iloc[-1].strftime("%Y-%m-%d") >= today:
        return
    s = slope_sample(now)
    if s is None:
        log.warning("volsurface: no valid slope sample today")
        return
    with open(HIST_FILE, "a", newline="") as f:
        csv.writer(f).writerow(["" if s[k] is None else s[k]
                                for k in ("date", "iv7", "iv30", "slope", "dvol")])

    def _pct(v):
        return "n/a" if v is None else "%.1f%%" % (v * 100)

    log.info("volsurface: iv7=%s iv30=%s slope=%s dvol=%s", _pct(s["iv7"]),
             _pct(s["iv30"]),
             "n/a" if s["slope"] is None else "%+.1f vpts" % (s["slope"] * 100),
             _pct(s["dvol"] or 0))


# ── signal ────────────────────────────────────────────────────────────────────
def _tercile_mult(signed: pd.Series) -> tuple[float, float, float]:
    """(mult, q33, q66) — thresholds from history EXCLUDING today (research
    used expanding_q(...).shift(1)), min 180 obs."""
    hist, today = signed.iloc[:-1].dropna(), signed.iloc[-1]
    if len(hist) < C.SLOPE_TER_MIN or pd.isna(today):
        return 1.0, float("nan"), float("nan")
    q33, q66 = hist.quantile(1 / 3), hist.quantile(2 / 3)
    mult = C.SLOPE_STEPS[0] if today < q33 else (
        C.SLOPE_STEPS[1] if today < q66 else C.SLOPE_STEPS[2])
    return mult, q33, q66


def signal_now() -> dict | None:
    """Multipliers from the recorded history (record_daily must run first)."""
    h = load_history()
    if h.empty:
        return None
    d = h.set_index("date").reindex(
        pd.date_range(h["date"].iloc[0], h["date"].iloc[-1], freq="D"))
    z = ((d["slope"] - d["slope"].rolling(90, min_periods=45).mean())
         / d["slope"].rolling(90, min_periods=45).std())
    m_slope, q33, q66 = _tercile_mult(-z)
    m_dvol, q33b, q66b = _tercile_mult(d["iv7"] - d["dvol"])
    last = h.iloc[-1]
    return {"date": last["date"].strftime("%Y-%m-%d"),
            "iv7": float(last["iv7"]), "iv30": float(last["iv30"]),
            "slope": float(last["slope"]), "dvol": float(last["dvol"]),
            "z_slope": None if pd.isna(z.iloc[-1]) else round(float(z.iloc[-1]), 3),
            "mult_slope": m_slope, "mult_dvol_iv7": m_dvol,
            "applied": bool(C.SLOPE_SIZING)}
