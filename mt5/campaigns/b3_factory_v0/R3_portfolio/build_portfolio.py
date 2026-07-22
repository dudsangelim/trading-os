"""
R3 - Portfolio builder + leverage grid
Streams:
  A = TSM WDO   (W0_tsm_wdo/best_cell_daily_net_returns.csv)          2000-2024, daily
  B = Carry cond WDO (W2_carry_cond/best_cell_DISCOVERY_series.csv + best_cell_CONFIRM_series.csv)  2000-2024, daily
  C = Fade intraday WIN M5 S25/T10 (W3_level_fade/trades_M5_discovery_S25_T10.csv + trades_M5_confirm_S25_T10.csv)  2023-2024, trade->daily aggregated
  D = BTC 2C v2 (W7_btc_stream/daily_returns.csv)  2023-2024, trade->daily aggregated (sparse)

All data restricted to <= 2024-12-31. Everything self-contained, absolute paths.
"""
import csv
import json
import math
from collections import defaultdict
from datetime import date

BASE = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_factory_v0"
OUT = BASE + r"\R3_portfolio"
CUTOFF = date(2024, 12, 31)


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_date(s):
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


# ---------------------------------------------------------------
# Stream A: TSM WDO
# ---------------------------------------------------------------
rowsA = read_csv(BASE + r"\W0_tsm_wdo\best_cell_daily_net_returns.csv")
retA = {}
for r in rowsA:
    d = parse_date(r["date"])
    if d <= CUTOFF:
        retA[d] = retA.get(d, 0.0) + float(r["ret"])

# ---------------------------------------------------------------
# Stream B: Carry cond WDO (gate diff best-cell, DISCOVERY + CONFIRM = 2000-2024)
# NOTE: the W2 script auto-selected the "best" internally-consistent cell
# (same-sign, max min(net_ann_DISCOVERY, net_ann_CONFIRM)) across the grid
# of gate_diff in {4,6,8}pp x vol_filter in {none,median252,p75_252}.
# We use the resulting best_cell_*_series.csv files as-is (documented,
# not re-picked here) -- these are the "melhor celula" artifacts from W2.
# ---------------------------------------------------------------
rowsB_disc = read_csv(BASE + r"\W2_carry_cond\best_cell_DISCOVERY_series.csv")
rowsB_conf = read_csv(BASE + r"\W2_carry_cond\best_cell_CONFIRM_series.csv")
retB = {}
for r in rowsB_disc + rowsB_conf:
    d = parse_date(r["date"])
    if d <= CUTOFF:
        retB[d] = retB.get(d, 0.0) + float(r["ret"])

# ---------------------------------------------------------------
# Stream C: Fade intraday WIN, M5 S25/T10, discovery(2023)+confirm(2024)
# trade-level bps -> aggregate to daily simple return (net_bps_1.5 = 1.5bps RT cost, conservative mid)
# ---------------------------------------------------------------
rowsC_disc = read_csv(BASE + r"\W3_level_fade\trades_M5_discovery_S25_T10.csv")
rowsC_conf = read_csv(BASE + r"\W3_level_fade\trades_M5_confirm_S25_T10.csv")
retC = defaultdict(float)
for r in rowsC_disc + rowsC_conf:
    d = parse_date(r["date"])
    if d <= CUTOFF:
        retC[d] += float(r["net_bps_1.5"]) / 10000.0
retC = dict(retC)

# ---------------------------------------------------------------
# Stream D: BTC 2C v2 (already daily-aggregated, sparse trade dates)
# ---------------------------------------------------------------
rowsD = read_csv(BASE + r"\W7_btc_stream\daily_returns.csv")
retD = {}
for r in rowsD:
    d = parse_date(r["date"])
    if d <= CUTOFF:
        retD[d] = retD.get(d, 0.0) + float(r["ret"])

streams = {"A_tsm_wdo": retA, "B_carry_wdo": retB, "C_fade_win": retC, "D_btc_2c": retD}

for name, s in streams.items():
    ds = sorted(s.keys())
    print(f"{name}: n_days={len(ds)}  {ds[0]} -> {ds[-1]}")

# ---------------------------------------------------------------
# Helper stats functions
# ---------------------------------------------------------------

def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs):
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def corr(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return float("nan")
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (sx * sy)


def to_monthly(daily_dict):
    """sum daily simple returns within each calendar month -> monthly return dict {(y,m): ret}"""
    acc = defaultdict(float)
    for d, r in daily_dict.items():
        acc[(d.year, d.month)] += r
    return dict(acc)


def max_drawdown(equity_curve):
    peak = equity_curve[0]
    mdd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = (e - peak) / peak
        if dd < mdd:
            mdd = dd
    return mdd * 100.0


def sharpe_daily_ann(daily_rets, trading_days=252):
    if len(daily_rets) < 2:
        return float("nan")
    m = mean(daily_rets)
    s = stdev(daily_rets)
    if s == 0 or math.isnan(s):
        return float("nan")
    return (m / s) * math.sqrt(trading_days)


def monthly_pf(monthly_rets):
    pos = sum(r for r in monthly_rets if r > 0)
    neg = -sum(r for r in monthly_rets if r < 0)
    if neg == 0:
        return float("inf") if pos > 0 else float("nan")
    return pos / neg


def build_portfolio_series(dates_all, weights, streams_sub, started_dates):
    """
    dates_all: sorted list of dates (union within period)
    weights: dict name->weight
    streams_sub: dict name-> {date: ret}
    started_dates: dict name-> first date active (before this: excluded, i.e. contributes 0 weight redistributed... but
        for simplicity here we only call this on periods where ALL streams have started, so no redistribution needed)
    returns list of portfolio daily returns aligned to dates_all
    """
    port = []
    for d in dates_all:
        r = 0.0
        for name, w in weights.items():
            r += w * streams_sub[name].get(d, 0.0)
        port.append(r)
    return port


def leverage_grid(monthly_rets, daily_rets, maxdd_1x, levs=(1, 2, 3, 4, 5)):
    out = []
    for L in levs:
        mm = mean(monthly_rets) * L * 100.0
        dd = maxdd_1x * L  # linear scaling assumption (leverage multiplies daily P&L, no rebalancing drag modeled)
        sh = sharpe_daily_ann(daily_rets)  # leverage-invariant (scales mean and vol together)
        meets_return = mm >= 3.0
        meets_dd = dd >= -20.0  # dd is negative
        out.append({
            "leverage": L,
            "monthly_mean_pct": round(mm, 3),
            "maxdd_pct": round(dd, 3),
            "sharpe_ann": round(sh, 3) if not math.isnan(sh) else None,
            "meets_3pct_month": meets_return,
            "meets_maxdd_20pct": meets_dd,
            "viable": meets_return and meets_dd,
        })
    return out


def portfolio_report(label, weights, streams_sub, period_dates):
    dates_all = sorted(period_dates)
    daily = build_portfolio_series(dates_all, weights, streams_sub, {})
    equity = [1.0]
    for r in daily:
        equity.append(equity[-1] * (1 + r))
    equity = equity[1:]
    mdd = max_drawdown([1.0] + equity)
    sh = sharpe_daily_ann(daily)
    monthly = to_monthly(dict(zip(dates_all, daily)))
    monthly_vals = list(monthly.values())
    mm = mean(monthly_vals) * 100.0
    ann = (equity[-1] ** (252.0 / len(daily)) - 1.0) * 100.0 if daily else float("nan")
    pf_m = monthly_pf(monthly_vals)
    worst_month = min(monthly_vals) * 100.0 if monthly_vals else float("nan")
    n_months = len(monthly_vals)
    lev = leverage_grid(monthly_vals, daily, mdd)
    return {
        "label": label,
        "weights": weights,
        "period": f"{dates_all[0]} -> {dates_all[-1]}",
        "n_days": len(dates_all),
        "n_months": n_months,
        "monthly_mean_pct": round(mm, 3),
        "ann_pct_geo": round(ann, 3),
        "sharpe_ann": round(sh, 3) if not math.isnan(sh) else None,
        "maxdd_pct": round(mdd, 3),
        "pf_monthly": round(pf_m, 3) if pf_m not in (float("inf"),) else "inf",
        "worst_month_pct": round(worst_month, 3),
        "leverage_grid": lev,
    }


# =================================================================
# PERIOD 1: COMMON PERIOD across ALL 4 STREAMS (2023-01-01 .. 2024-12-31)
# union of trading days where at least one stream has data in this window;
# once a stream has started (all 4 started by 2023 given C/D constraints),
# missing day = 0 return (no trade / no signal that day)
# =================================================================
common_start = date(2023, 1, 1)
common_end = date(2024, 12, 31)

# union of calendar dates on which ANY of A/B (which trade ~daily) has an entry,
# restricted to common window -- use A's calendar as the "trading day" spine since
# WDO trades daily; union with B,C,D dates just in case they have days A doesn't.
spine = set(d for d in retA if common_start <= d <= common_end)
spine |= set(d for d in retB if common_start <= d <= common_end)
spine |= set(d for d in retC if common_start <= d <= common_end)
spine |= set(d for d in retD if common_start <= d <= common_end)
common_dates = sorted(spine)

streams_common = {
    "A_tsm_wdo": {d: retA.get(d, 0.0) for d in common_dates},
    "B_carry_wdo": {d: retB.get(d, 0.0) for d in common_dates},
    "C_fade_win": {d: retC.get(d, 0.0) for d in common_dates},
    "D_btc_2c": {d: retD.get(d, 0.0) for d in common_dates},
}

print(f"\nCommon period (all 4 streams): {common_dates[0]} -> {common_dates[-1]}  n_days={len(common_dates)}")

# --- correlation matrix (daily), common period ---
names = list(streams_common.keys())
corr_daily = {}
for i, n1 in enumerate(names):
    for n2 in names[i:]:
        x = [streams_common[n1][d] for d in common_dates]
        y = [streams_common[n2][d] for d in common_dates]
        corr_daily[f"{n1}__{n2}"] = round(corr(x, y), 4)

# --- correlation matrix (monthly), common period ---
monthly_by_stream = {n: to_monthly(streams_common[n]) for n in names}
all_months = sorted(set().union(*[set(m.keys()) for m in monthly_by_stream.values()]))
corr_monthly = {}
for i, n1 in enumerate(names):
    for n2 in names[i:]:
        x = [monthly_by_stream[n1].get(m, 0.0) for m in all_months]
        y = [monthly_by_stream[n2].get(m, 0.0) for m in all_months]
        corr_monthly[f"{n1}__{n2}"] = round(corr(x, y), 4)

# --- vol full-sample per stream (each stream's OWN available history, not just common window) ---
vol_full = {}
for name, s in streams.items():
    xs = list(s.values())
    vol_full[name] = stdev(xs)
    print(f"{name}: daily vol (full own sample) = {vol_full[name]:.6f}  n={len(xs)}")

inv_vol = {n: 1.0 / vol_full[n] for n in names}
tot_inv = sum(inv_vol.values())
w_rp = {n: inv_vol[n] / tot_inv for n in names}
w_eq = {n: 0.25 for n in names}

print("\nRisk-parity weights (1/vol full-sample, normalized):", {k: round(v, 4) for k, v in w_rp.items()})
print("Equal weights:", w_eq)

rp_common = portfolio_report("4-stream risk-parity (common 2023-2024)", w_rp, streams_common, common_dates)
eq_common = portfolio_report("4-stream equal-weight (common 2023-2024)", w_eq, streams_common, common_dates)

# =================================================================
# PERIOD 1b: same common period EXCLUDING stream D (BTC) -> A,B,C only
# =================================================================
names_noD = ["A_tsm_wdo", "B_carry_wdo", "C_fade_win"]
inv_vol_noD = {n: inv_vol[n] for n in names_noD}
tot_noD = sum(inv_vol_noD.values())
w_rp_noD = {n: inv_vol_noD[n] / tot_noD for n in names_noD}
w_eq_noD = {n: 1.0 / 3 for n in names_noD}

streams_common_noD = {n: streams_common[n] for n in names_noD}
rp_common_noD = portfolio_report("3-stream (no BTC) risk-parity (common 2023-2024)", w_rp_noD, streams_common_noD, common_dates)
eq_common_noD = portfolio_report("3-stream (no BTC) equal-weight (common 2023-2024)", w_eq_noD, streams_common_noD, common_dates)

# =================================================================
# PERIOD 2: FULL period for A+B only (2000-2024) -- C and D have no history before 2023
# =================================================================
spine_full = set(retA.keys()) | set(retB.keys())
full_dates = sorted(spine_full)
streams_full_AB = {
    "A_tsm_wdo": {d: retA.get(d, 0.0) for d in full_dates},
    "B_carry_wdo": {d: retB.get(d, 0.0) for d in full_dates},
}
inv_vol_AB = {n: inv_vol[n] for n in ["A_tsm_wdo", "B_carry_wdo"]}
tot_AB = sum(inv_vol_AB.values())
w_rp_AB = {n: inv_vol_AB[n] / tot_AB for n in inv_vol_AB}
w_eq_AB = {n: 0.5 for n in inv_vol_AB}

rp_full_AB = portfolio_report("2-stream A+B risk-parity (FULL 2000-2024)", w_rp_AB, streams_full_AB, full_dates)
eq_full_AB = portfolio_report("2-stream A+B equal-weight (FULL 2000-2024)", w_eq_AB, streams_full_AB, full_dates)

# correlation A vs B, full period and common period
xA_full = [retA.get(d, 0.0) for d in full_dates]
xB_full = [retB.get(d, 0.0) for d in full_dates]
corr_AB_full_daily = round(corr(xA_full, xB_full), 4)
mA_full = to_monthly({d: retA.get(d, 0.0) for d in full_dates})
mB_full = to_monthly({d: retB.get(d, 0.0) for d in full_dates})
months_full = sorted(set(mA_full) | set(mB_full))
corr_AB_full_monthly = round(corr([mA_full.get(m, 0.0) for m in months_full], [mB_full.get(m, 0.0) for m in months_full]), 4)

print(f"\nA vs B correlation (SAME underlying WDO) -- full sample daily: {corr_AB_full_daily}, monthly: {corr_AB_full_monthly}")
print(f"A vs B correlation (common 2023-2024) -- daily: {corr_daily['A_tsm_wdo__B_carry_wdo']}, monthly: {corr_monthly['A_tsm_wdo__B_carry_wdo']}")

# =================================================================
# Write outputs
# =================================================================
report = {
    "cutoff": "2024-12-31",
    "streams_meta": {
        "A_tsm_wdo": {
            "source": r"W0_tsm_wdo\best_cell_daily_net_returns.csv",
            "own_period": f"{min(retA):%Y-%m-%d} -> {max(retA):%Y-%m-%d}",
            "n_days": len(retA),
            "daily_vol_full_sample": round(vol_full["A_tsm_wdo"], 6),
        },
        "B_carry_wdo": {
            "source": r"W2_carry_cond\best_cell_DISCOVERY_series.csv + best_cell_CONFIRM_series.csv",
            "own_period": f"{min(retB):%Y-%m-%d} -> {max(retB):%Y-%m-%d}",
            "n_days": len(retB),
            "daily_vol_full_sample": round(vol_full["B_carry_wdo"], 6),
            "note": "best-cell auto-selected pelo script w2_carry_cond.py (mesmo sinal DISCOVERY+CONFIRM, "
                    "max min(net_ann)); grid varria gate_diff {4,6,8}pp x vol_filter {none,median252,p75_252}. "
                    "Nao re-selecionado aqui -- usa os artefatos best_cell_*_series.csv do W2 diretamente.",
        },
        "C_fade_win": {
            "source": r"W3_level_fade\trades_M5_discovery_S25_T10.csv + trades_M5_confirm_S25_T10.csv (net_bps_1.5, agregado por dia)",
            "own_period": f"{min(retC):%Y-%m-%d} -> {max(retC):%Y-%m-%d}",
            "n_trade_days": len(retC),
            "daily_vol_full_sample": round(vol_full["C_fade_win"], 6),
            "note": "assume fill otimista em ordem LIMIT no nivel (PDH/PDL); custo RT=1.5bps. "
                    "Dias sem trade = 0 (nao ha turnover diario, so ~230 trades em 2 anos).",
        },
        "D_btc_2c": {
            "source": r"W7_btc_stream\daily_returns.csv",
            "own_period": f"{min(retD):%Y-%m-%d} -> {max(retD):%Y-%m-%d}",
            "n_trade_days": len(retD),
            "daily_vol_full_sample": round(vol_full["D_btc_2c"], 6),
            "note": "serie ja truncada em <=2024-12-31 pelo worker W7; sparse (78 trades em ~2 anos), "
                    "thr=0.55, S3 stop dinamico + F_reduced.",
        },
    },
    "correlation_daily_common_2023_2024": corr_daily,
    "correlation_monthly_common_2023_2024": corr_monthly,
    "correlation_AB_same_underlying_WDO": {
        "warning": "A (TSM) e B (Carry) derivam do MESMO ativo WDO -- risco de dupla-contagem de risco WDO no portfolio.",
        "full_sample_2000_2024_daily": corr_AB_full_daily,
        "full_sample_2000_2024_monthly": corr_AB_full_monthly,
        "common_2023_2024_daily": corr_daily["A_tsm_wdo__B_carry_wdo"],
        "common_2023_2024_monthly": corr_monthly["A_tsm_wdo__B_carry_wdo"],
        "flag_gt_0.5": (abs(corr_AB_full_daily) > 0.5) or (abs(corr_AB_full_monthly) > 0.5)
                       or (abs(corr_daily["A_tsm_wdo__B_carry_wdo"]) > 0.5)
                       or (abs(corr_monthly["A_tsm_wdo__B_carry_wdo"]) > 0.5),
    },
    "weights": {
        "risk_parity_4stream": {k: round(v, 4) for k, v in w_rp.items()},
        "equal_weight_4stream": w_eq,
        "risk_parity_3stream_noBTC": {k: round(v, 4) for k, v in w_rp_noD.items()},
        "equal_weight_3stream_noBTC": w_eq_noD,
        "risk_parity_2stream_AB_full": {k: round(v, 4) for k, v in w_rp_AB.items()},
        "equal_weight_2stream_AB_full": w_eq_AB,
    },
    "portfolio_1x": {
        "4stream_common_2023_2024": {"risk_parity": rp_common, "equal_weight": eq_common},
        "3stream_noBTC_common_2023_2024": {"risk_parity": rp_common_noD, "equal_weight": eq_common_noD},
        "2stream_AB_FULL_2000_2024": {"risk_parity": rp_full_AB, "equal_weight": eq_full_AB},
    },
}

out_json = OUT + r"\portfolio_metrics.json"
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False, default=str)
print(f"\nSalvo: {out_json}")

# also dump the combined daily portfolio series (risk-parity, common period, 4-stream and 3-stream-noBTC)
def dump_series(path, dates_all, weights, streams_sub):
    daily = build_portfolio_series(dates_all, weights, streams_sub, {})
    equity = [1.0]
    for r in daily:
        equity.append(equity[-1] * (1 + r))
    equity = equity[1:]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "ret", "equity"])
        for d, r, e in zip(dates_all, daily, equity):
            w.writerow([d.isoformat(), r, e])

dump_series(OUT + r"\portfolio_4stream_rp_daily.csv", common_dates, w_rp, streams_common)
dump_series(OUT + r"\portfolio_3stream_noBTC_rp_daily.csv", common_dates, w_rp_noD, streams_common_noD)
dump_series(OUT + r"\portfolio_2stream_AB_rp_daily_FULL.csv", full_dates, w_rp_AB, streams_full_AB)

print("Series CSVs salvos em", OUT)
