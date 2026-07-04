"""Fidelity check: the live state machine (Sleeve.process_bar) must reproduce
backtest.run_backtest's trade sequence on historical bars. Costs/tick are
neutralized so only the entry/exit/stop LOGIC is compared."""
import sys
import pandas as pd

sys.path.insert(0, "/home/agent/trading-os")
from trading.swing_wf_paper import config as C
C.SLIP_BPS = 0.0; C.FEE_BPS = 0.0; C.FUNDING_ENABLED = False; C.LEVERAGE = 1.0
from trading.swing_wf_paper import backtest as BT
from trading.swing_wf_paper import strategies as S
from trading.swing_wf_paper import engine as E

CACHE = "/home/agent/research/swing_study/cache/{}_4h.parquet"
# signal-exit configs only — the space the live trader actually uses
CASES = [
    ("BTCUSDT", "ema_cross", {"fast": 10, "slow": 100, "adx_min": 0}, {"trail_atr": 0}, True),
    ("ETHUSDT", "supertrend", {"n": 14, "mult": 2.0, "adx_min": 0}, {}, True),
    ("SOLUSDT", "roc_mom", {"roc_n": 40, "ema_n": 200}, {}, True),
    ("ETHUSDT", "ema_cross", {"fast": 10, "slow": 100, "adx_min": 25}, {}, True),
    ("BTCUSDT", "donchian", {"entry_n": 20, "exit_n": 20}, {"trail_atr": 0}, False),
    ("SOLUSDT", "keltner", {"n": 20, "mult": 2.0}, {"trail_atr": 0}, True),
    ("ETHUSDT", "macd_rsi", {"fast": 12, "slow": 21, "signal": 9, "rsi_n": 14}, {}, True),
    ("BTCUSDT", "roc_mom", {"roc_n": 20, "ema_n": 100}, {}, False),
]


def live_trades(df, sname, params, eopt, short):
    sig = S.STRATS[sname][0](df, **params)
    sl = E.Sleeve(symbol="X", tick=1e-9, step=1e-12, min_notional=0.0, equity=1e9,
                  peak_equity=1e9)
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    atr = sig["atr"].values
    out = []
    n = len(df)
    for p in range(1, n):
        j = {"open": o[p], "high": h[p], "low": l[p], "close": c[p], "close_ms": p}
        prev = {"open": o[p - 1], "high": h[p - 1], "low": l[p - 1], "close": c[p - 1]}
        sig_j = {k: bool(sig[k].iloc[p]) for k in ("long_entry", "long_exit", "short_entry", "short_exit")}
        nopen = o[p + 1] if p + 1 < n else c[p]
        nopen_ms = p + 1
        for ev in sl.process_bar(j, prev, sig_j, atr[p], atr[p - 1], nopen, nopen_ms, eopt, short, None):
            out.append(ev)
    # pair into trades
    trades, cur = [], None
    for ev in out:
        if ev["type"] == "open":
            cur = ev
        elif ev["type"] == "close" and cur:
            ret = ev["side"] == "long" and (ev["exit_px"] / cur["px"] - 1) or (cur["px"] / ev["exit_px"] - 1)
            trades.append({"side": cur["side"][0].upper(), "ret": ret, "reason": ev["reason"]})
            cur = None
    return trades


def main():
    print(f"{'case':38s} {'bt_n':>5s} {'live_n':>6s} {'side_match':>10s} {'ret_corr':>9s} {'mean_dret':>10s}")
    for sym, sname, params, eopt, short in CASES:
        df = pd.read_parquet(CACHE.format(sym))
        res = BT.run_backtest(df, S.STRATS[sname][0](df, **params), BT.Costs(0, 0),
                              sl_atr=eopt.get("sl_atr", 0), tp_atr=eopt.get("tp_atr", 0),
                              trail_atr=eopt.get("trail_atr", 0), max_hold=eopt.get("max_hold", 0),
                              allow_long=True, allow_short=short)
        bt = res["trades"]
        bt = bt[bt["reason"] != "eod"]                      # backtest force-closes last bar
        lv = live_trades(df, sname, params, eopt, short)
        m = min(len(bt), len(lv))
        side_ok = sum(1 for i in range(m) if bt["side"].iloc[i] == lv[i]["side"]) / m if m else 0
        bt_ret = bt["ret"].values[:m]; lv_ret = [t["ret"] for t in lv][:m]
        corr = pd.Series(bt_ret).corr(pd.Series(lv_ret)) if m > 1 else float("nan")
        dret = float(pd.Series(bt_ret).sub(pd.Series(lv_ret)).abs().mean()) if m else 0
        tag = f"{sym}/{sname}"
        print(f"{tag:38s} {len(bt):5d} {len(lv):6d} {side_ok*100:9.1f}% {corr:9.4f} {dret:10.2e}")


if __name__ == "__main__":
    main()
