"""R021-A C1 — Tiered pause gates derived from PAUSE_CRITERIA.md."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


# ── Thresholds (mirror PAUSE_CRITERIA.md) ────────────────────────────────────

# Tier 1 (alert only)
T1_ROLL_N        = 10
T1_WR_MAX        = 0.40
T1_PF_MAX        = 1.0
T1_MEAN_BPS_MAX  = 0.0      # mean_bps < 0 disparado (e >= -20 para não cair em T2)
T1_CUM_N_MIN     = 20
T1_CUM_PF_MAX    = 1.5

# Tier 2 (review required, no halt)
T2_ROLL_N        = 15
T2_WR_MAX        = 0.35
T2_MEAN_BPS_MAX  = -10.0
T2_PF_MAX        = 0.8
T2_CUM_N_MIN     = 30
T2_CUM_PF_MAX    = 1.2
T2_DRAWDOWN_MAX  = -120.0   # USD

# Tier 3 (auto-halt — more aggressive than legacy WR<0.30 @ N=30)
T3_ROLL_N        = 20
T3_WR_MAX        = 0.30
T3_MEAN_BPS_MAX  = -25.0
T3_PF_MAX        = 0.5
T3_CUM_N_MIN     = 30
T3_CUM_PF_MAX    = 0.8
T3_MEAN_CUM_N    = 15
T3_MEAN_CUM_MAX  = -40.0


@dataclass
class TradeRecord:
    won: bool
    net_ret: float   # net of fees, in absolute return (e.g. 0.012 = +1.2%)


@dataclass
class TierResult:
    tier:    int       # 0, 1, 2, or 3 (0 = no breach)
    reason:  str       # which sub-criterion fired
    snapshot: dict     # N, WR, PF, mean_bps, dd_$ for the alert text


def _wr(records: List[TradeRecord]) -> Optional[float]:
    if not records:
        return None
    return sum(1 for r in records if r.won) / len(records)


def _pf(records: List[TradeRecord]) -> Optional[float]:
    if not records:
        return None
    gains  = sum(r.net_ret for r in records if r.net_ret > 0)
    losses = -sum(r.net_ret for r in records if r.net_ret < 0)
    if losses <= 0:
        return float("inf") if gains > 0 else None
    return gains / losses


def _mean_bps(records: List[TradeRecord]) -> Optional[float]:
    if not records:
        return None
    return sum(r.net_ret for r in records) / len(records) * 10000.0


def _running_drawdown_usd(records: List[TradeRecord], notional: float) -> float:
    """Worst running drawdown over the trade sequence (most negative)."""
    peak  = 0.0
    pnl   = 0.0
    worst = 0.0
    for r in records:
        pnl += r.net_ret * notional
        peak = max(peak, pnl)
        worst = min(worst, pnl - peak)
    return worst


def _snapshot(records_all: List[TradeRecord], roll_n: int, notional: float) -> dict:
    roll = records_all[-roll_n:] if len(records_all) >= roll_n else records_all
    return {
        "N_cum":         len(records_all),
        "N_roll":        len(roll),
        "WR_cum":        _wr(records_all),
        "WR_roll":       _wr(roll),
        "PF_cum":        _pf(records_all),
        "PF_roll":       _pf(roll),
        "mean_bps_cum":  _mean_bps(records_all),
        "mean_bps_roll": _mean_bps(roll),
        "dd_usd":        _running_drawdown_usd(records_all, notional),
    }


def evaluate(records: List[TradeRecord], notional: float) -> TierResult:
    """Evaluate tiers given the full sequence of closed trades.

    Returns the highest tier breached, or tier=0 if none.
    Caller must filter duplicate alerts (same tier consecutive trades).
    """
    n = len(records)
    if n == 0:
        return TierResult(0, "no trades yet", _snapshot(records, T1_ROLL_N, notional))

    snap_t1 = _snapshot(records, T1_ROLL_N, notional)
    snap_t2 = _snapshot(records, T2_ROLL_N, notional)
    snap_t3 = _snapshot(records, T3_ROLL_N, notional)

    # ── Tier 3 (most severe — check first to take precedence) ────────────────
    # T3.a: rolling-20 WR<0.30 AND mean_bps<-25
    if n >= T3_ROLL_N:
        wr_r3 = snap_t3["WR_roll"]
        mb_r3 = snap_t3["mean_bps_roll"]
        if wr_r3 is not None and mb_r3 is not None \
                and wr_r3 < T3_WR_MAX and mb_r3 < T3_MEAN_BPS_MAX:
            return TierResult(3, f"T3.a WR_roll20={wr_r3:.3f}<{T3_WR_MAX} & "
                                  f"mean_bps_roll20={mb_r3:.1f}<{T3_MEAN_BPS_MAX}", snap_t3)

        # T3.b: rolling-20 PF<0.5
        pf_r3 = snap_t3["PF_roll"]
        if pf_r3 is not None and pf_r3 < T3_PF_MAX:
            return TierResult(3, f"T3.b PF_roll20={pf_r3:.3f}<{T3_PF_MAX}", snap_t3)

    # T3.c: cumulative PF<0.8 at N>=30
    if n >= T3_CUM_N_MIN:
        pf_c = snap_t3["PF_cum"]
        if pf_c is not None and pf_c < T3_CUM_PF_MAX:
            return TierResult(3, f"T3.c PF_cum(N={n})={pf_c:.3f}<{T3_CUM_PF_MAX}", snap_t3)

    # T3.d: cumulative mean_bps<-40 at N>=15
    if n >= T3_MEAN_CUM_N:
        mb_c = snap_t3["mean_bps_cum"]
        if mb_c is not None and mb_c < T3_MEAN_CUM_MAX:
            return TierResult(3, f"T3.d mean_bps_cum(N={n})={mb_c:.1f}<{T3_MEAN_CUM_MAX}", snap_t3)

    # ── Tier 2 (review required) ──────────────────────────────────────────────
    if n >= T2_ROLL_N:
        wr_r2 = snap_t2["WR_roll"]
        mb_r2 = snap_t2["mean_bps_roll"]
        if wr_r2 is not None and mb_r2 is not None \
                and wr_r2 < T2_WR_MAX and mb_r2 < T2_MEAN_BPS_MAX:
            return TierResult(2, f"T2.a WR_roll15={wr_r2:.3f}<{T2_WR_MAX} & "
                                  f"mean_bps_roll15={mb_r2:.1f}<{T2_MEAN_BPS_MAX}", snap_t2)

        pf_r2 = snap_t2["PF_roll"]
        if pf_r2 is not None and pf_r2 < T2_PF_MAX:
            return TierResult(2, f"T2.b PF_roll15={pf_r2:.3f}<{T2_PF_MAX}", snap_t2)

    # T2.c: cumulative PF<1.2 at N>=30
    if n >= T2_CUM_N_MIN:
        pf_c = snap_t2["PF_cum"]
        if pf_c is not None and pf_c < T2_CUM_PF_MAX:
            return TierResult(2, f"T2.c PF_cum(N={n})={pf_c:.3f}<{T2_CUM_PF_MAX}", snap_t2)

    # T2.d: drawdown < -$120
    dd = snap_t2["dd_usd"]
    if dd < T2_DRAWDOWN_MAX:
        return TierResult(2, f"T2.d dd_usd={dd:.2f}<{T2_DRAWDOWN_MAX}", snap_t2)

    # ── Tier 1 (alert only) ───────────────────────────────────────────────────
    if n >= T1_ROLL_N:
        wr_r1 = snap_t1["WR_roll"]
        if wr_r1 is not None and wr_r1 < T1_WR_MAX:
            return TierResult(1, f"T1.a WR_roll10={wr_r1:.3f}<{T1_WR_MAX}", snap_t1)

        pf_r1 = snap_t1["PF_roll"]
        if pf_r1 is not None and pf_r1 < T1_PF_MAX:
            return TierResult(1, f"T1.b PF_roll10={pf_r1:.3f}<{T1_PF_MAX}", snap_t1)

        mb_r1 = snap_t1["mean_bps_roll"]
        if mb_r1 is not None and mb_r1 < T1_MEAN_BPS_MAX:
            return TierResult(1, f"T1.c mean_bps_roll10={mb_r1:.1f}<{T1_MEAN_BPS_MAX}", snap_t1)

    if n >= T1_CUM_N_MIN:
        pf_c = snap_t1["PF_cum"]
        if pf_c is not None and pf_c < T1_CUM_PF_MAX:
            return TierResult(1, f"T1.d PF_cum(N={n})={pf_c:.3f}<{T1_CUM_PF_MAX}", snap_t1)

    return TierResult(0, "all gates clean", snap_t1)
