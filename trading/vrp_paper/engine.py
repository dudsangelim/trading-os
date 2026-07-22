"""VRP paper engine — weekly short ATM straddle, delta-hedged daily.

Faithful port of research/options_study/run_shortvol2.simulate2 (hedged
straddle), with two upgrades the live feed makes possible:
  * entry at the REAL best bid (research modeled last-trade minus haircut);
  * daily marks and hedge deltas from REAL venue tickers (research modeled
    the IV path from DVOL).
Accounting in USD over INITIAL_CAPITAL, fractional contracts (paper).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from trading.vrp_paper import config as C
from trading.vrp_paper import market as M

log = logging.getLogger("vrp_paper")


def select_straddle(now: datetime) -> Optional[dict]:
    """Pick the ATM straddle expiring next Friday with live bids on both legs."""
    S = M.index_price()
    inst = M.option_instruments()
    cands = {}
    for i in inst:
        exp = datetime.fromtimestamp(i["expiration_timestamp"] / 1000, tz=timezone.utc)
        dte = (exp - now).total_seconds() / 86400
        if not (C.DTE_LO <= dte <= C.DTE_HI and exp.weekday() == C.ENTRY_DOW):
            continue
        if abs(i["strike"] / S - 1) > C.MAX_STRIKE_DIST:
            continue
        cands.setdefault(i["strike"], {})[i["option_type"]] = i
    strikes = sorted((k for k, v in cands.items() if len(v) == 2), key=lambda k: abs(k - S))
    for K in strikes:
        legs = []
        for otype in ("call", "put"):
            ins = cands[K][otype]
            tk = M.ticker(ins["instrument_name"])
            bid = tk.get("best_bid_usd") or 0.0
            if bid <= 0:
                legs = []
                break
            legs.append({
                "instrument": ins["instrument_name"], "strike": float(K),
                "is_call": otype == "call", "bid_usd": float(bid),
                "mark_usd": float(tk.get("mark_usd") or 0.0),
                "iv": float(tk.get("mark_iv") or 0.0),
                "delta": float(tk.get("delta") or 0.0),
                "min_qty": float(ins["min_qty"]), "qty_step": float(ins["qty_step"]),
            })
        if legs:
            return {"S": S, "legs": legs,
                    "expiry": datetime.fromtimestamp(
                        cands[K]["call"]["expiration_timestamp"] / 1000,
                        tz=timezone.utc)}
    return None


@dataclass
class Position:
    entry_ts: str
    expiry_ts: str
    contracts: float
    S0: float
    dvol0: float
    legs: list                     # instrument/strike/is_call/prem_usd/iv0
    hedge_qty: float = 0.0         # BTC per contract basis? -> absolute BTC
    hedge_px: float = 0.0
    hedge_pnl: float = 0.0         # accumulated USD
    eq0: float = 0.0               # equity at entry (USD)

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class Book:
    equity: float = C.INITIAL_CAPITAL   # realized equity (flat basis)
    peak_equity: float = C.INITIAL_CAPITAL
    n_trades: int = 0
    n_wins: int = 0
    consec_losses: int = 0
    last_entry_week: str = ""
    position: Optional[Position] = None

    # ── entry ────────────────────────────────────────────────────────────────
    def open_straddle(self, now: datetime, vol_signal: Optional[dict] = None) -> Optional[dict]:
        sel = select_straddle(now)
        if sel is None:
            log.warning("no tradable ATM straddle found")
            return None
        S = sel["S"]
        try:
            dv = M.dvol_now()
        except Exception:
            dv = C.DVOL_REF
        mult = 1.0
        if vol_signal and C.SLOPE_SIZING:
            mult = float(vol_signal.get("mult_slope") or 1.0)
        raw_contracts = (mult * C.SIZE_MULT * self.equity / S
                         * min(C.DVOL_REF / max(dv, 0.05), C.VOL_SCALE_CAP))
        min_qty = max(lg["min_qty"] for lg in sel["legs"])
        qty_step = max(lg["qty_step"] for lg in sel["legs"])
        contracts = M.quantize_contracts(raw_contracts, min_qty, qty_step)
        if contracts <= 0:
            log.warning("sizing %.6f below venue minimum %.6f", raw_contracts, min_qty)
            return None
        legs = []
        for lg in sel["legs"]:
            prem_usd = lg["bid_usd"] - M.entry_fee_usd(lg["bid_usd"], S)
            legs.append({**lg, "prem_usd": prem_usd})
        self.position = Position(
            entry_ts=now.strftime("%Y-%m-%d %H:%M"),
            expiry_ts=sel["expiry"].strftime("%Y-%m-%d %H:%M"),
            contracts=contracts, S0=S, dvol0=dv, legs=legs, eq0=self.equity,
            hedge_px=S,
        )
        prem_total = sum(l["prem_usd"] for l in legs) * contracts
        return {"type": "open", "S": S, "dvol": dv, "contracts": contracts,
                "legs": legs, "prem_usd": prem_total,
                "prem_pct": prem_total / self.equity * 100,
                "expiry": self.position.expiry_ts, "vol_signal": vol_signal}

    # ── daily hedge + mark (real tickers) ────────────────────────────────────
    def hedge_and_mark(self, do_hedge: bool) -> dict:
        p = self.position
        S = M.index_price()
        mtm = 0.0
        net_delta = 0.0
        marks = []
        for lg in p.legs:
            tk = M.ticker(lg["instrument"])
            mark = float(tk.get("mark_usd") or 0.0)
            dlt = float(tk.get("delta") or 0.0)
            mtm += lg["prem_usd"] - mark                        # short legs
            net_delta -= dlt
            marks.append({"instrument": lg["instrument"], "mark_usd": mark, "delta": dlt})
        mtm *= p.contracts
        # hedge P&L on the move since last hedge/mark
        p.hedge_pnl += p.hedge_qty * (S - p.hedge_px) * p.contracts
        p.hedge_px = S
        if do_hedge:
            target = -net_delta
            adj = target - p.hedge_qty
            cost = abs(adj) * S * p.contracts * C.HEDGE_COST
            p.hedge_pnl -= cost
            p.hedge_qty = target
        mtm += p.hedge_pnl
        eq_now = p.eq0 + mtm
        return {"S": S, "net_delta": net_delta, "hedge_qty": p.hedge_qty,
                "mtm_usd": mtm, "equity_mtm": eq_now, "marks": marks}

    # ── settlement ───────────────────────────────────────────────────────────
    def settle(self, now: datetime) -> Optional[dict]:
        p = self.position
        expiry = datetime.strptime(p.expiry_ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        px = M.delivery_price(expiry)
        source = "delivery"
        if px is None:
            if (now - expiry).total_seconds() < 3 * 3600:
                return None                     # not published yet — retry next cycle
            px = M.index_price()
            source = "index-fallback"
        pnl = 0.0
        for lg in p.legs:
            intr = max(px - lg["strike"], 0) if lg["is_call"] else max(lg["strike"] - px, 0)
            pnl += (lg["prem_usd"] - intr) * p.contracts
            pnl -= M.settlement_fee_usd(px, intr) * p.contracts
        # close hedge at the settlement print
        pnl += p.hedge_qty * (px - p.hedge_px) * p.contracts
        pnl -= abs(p.hedge_qty) * px * p.contracts * C.HEDGE_COST
        pnl += p.hedge_pnl
        self.equity = p.eq0 + pnl
        self.n_trades += 1
        win = pnl > 0
        self.n_wins += int(win)
        self.consec_losses = 0 if win else self.consec_losses + 1
        ev = {"type": "close", "settle": px, "source": source, "pnl": pnl,
              "ret_pct": pnl / p.eq0 * 100, "entry": p.entry_ts,
              "expiry": p.expiry_ts, "equity": self.equity,
              "legs": [lg["instrument"] for lg in p.legs]}
        self.position = None
        return ev

    def to_dict(self):
        d = self.__dict__.copy()
        d["position"] = self.position.to_dict() if self.position else None
        return d
