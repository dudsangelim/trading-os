"""
Replay do parquet 2023+ pelo Dow3LegsSkipLowEngine — para validar que a engine
produz os MESMOS números que o backtest_dow_skiplow_validate.py.

Diferente do run_replay() do paper_trade_dow (que usa gate ATR atual via REST,
inválido pra histórico), este script:
- Carrega parquet 1m do projeto
- Computa ATR(14d) e P20(60d) histórico (mesmo cálculo do backtest)
- Para cada timestamp 23:55 da semana, chama engine.on_tick com gate computado
- Coleta trades resultantes e compara métricas com backtest

Uso:
    python replay_engine_parquet.py [--leverage 1.5] [--no-gate]

Saída: backtests/dow_skiplow_engine_replay/{trades.csv, summary.txt}
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from engine_dow import Dow3LegsSkipLowEngine

PROJ = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças")
PARQUET = PROJ / "BTCUSDT_1m_2021_2026.parquet"
OUT_DIR = PROJ / "backtests" / "dow_skiplow_engine_replay"
OUT_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = pd.Timestamp("2023-01-01", tz="UTC")
ATR_LEN = 14
REGIME_WINDOW = 60
Q_LOW = 0.20
INITIAL_CAPITAL = 100.0


def load_minute():
    df = pd.read_parquet(PARQUET)
    ts_col = None
    for c in ("timestamp", "open_time", "datetime", "time", "date"):
        if c in df.columns:
            ts_col = c; break
    if ts_col is None and isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index().rename(columns={"index": "timestamp"})
        ts_col = "timestamp"
    if pd.api.types.is_integer_dtype(df[ts_col]):
        df["ts"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    else:
        df["ts"] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index("ts").sort_index()
    df = df[df.index >= START_DATE - pd.Timedelta("400D")]
    rename_map = {}
    for src, dst in [("Open", "open"), ("High", "high"), ("Low", "low"),
                     ("Close", "close"), ("Volume", "volume")]:
        if src in df.columns and dst not in df.columns:
            rename_map[src] = dst
    return df.rename(columns=rename_map)


def get_anchor_2355(minute_df):
    """Retorna candle única por dia, usando close de 23:55-23:59."""
    h = minute_df.index.hour
    m = minute_df.index.minute
    mask = (h == 23) & (m >= 55)
    sub = minute_df[mask].copy()
    sub["date"] = sub.index.normalize()
    daily = sub.groupby("date").tail(1)
    return daily[["close"]].copy()


def compute_atr_gate_history(minute_df):
    """Calcula ATR(14d) > P20(60d) para cada dia. Usa daily OHLC do parquet.
    Retorna DataFrame indexado por data (dia) com colunas atr_pct, p20, gate_pass."""
    daily = minute_df.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    daily["tr"] = np.maximum.reduce([
        daily["high"] - daily["low"],
        (daily["high"] - daily["close"].shift(1)).abs(),
        (daily["low"] - daily["close"].shift(1)).abs(),
    ])
    daily["atr"] = daily["tr"].rolling(ATR_LEN).mean()
    daily["atr_pct"] = daily["atr"] / daily["close"] * 100
    daily["p20"] = daily["atr_pct"].rolling(REGIME_WINDOW).quantile(Q_LOW)
    daily["gate_pass"] = daily["atr_pct"] > daily["p20"]
    return daily[["atr_pct", "p20", "gate_pass"]]


def main(leverage: float, no_gate: bool):
    print(f"[load] {PARQUET.name}")
    minute = load_minute()
    print(f"  range: {minute.index.min()} -> {minute.index.max()}")

    anchor = get_anchor_2355(minute)
    anchor = anchor[anchor.index >= START_DATE]
    print(f"  anchors 23:55 (2023+): {len(anchor):,}")

    gate_df = compute_atr_gate_history(minute)
    print(f"  daily ATR gates: {len(gate_df):,}")

    # Engine
    eng = Dow3LegsSkipLowEngine(leverage=leverage)
    print(f"\n[engine] {eng.VERSION}, lev={leverage}x, gate={'OFF' if no_gate else 'ON'}\n")

    # Para cada anchor, determina gate (do dia anterior) e chama engine
    n_skipped = 0
    n_passed = 0
    for ts, row in anchor.iterrows():
        # Engine espera ts SEM tz no on_tick (paper_trade_dow strip-tz)
        ts_naive = ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts
        price = float(row["close"])

        if no_gate:
            gate_pass = True
        else:
            # Gate é avaliado no dia ANTERIOR (sem look-ahead). ts é no dia D 23:55, então
            # buscamos atr_pct e p20 do dia D-1.
            prev_day = ts.normalize() - pd.Timedelta(days=1)
            gate_row = gate_df.loc[gate_df.index <= prev_day]
            if len(gate_row) == 0 or pd.isna(gate_row.iloc[-1]["gate_pass"]):
                gate_pass = False
            else:
                gate_pass = bool(gate_row.iloc[-1]["gate_pass"])
        if gate_pass: n_passed += 1
        else: n_skipped += 1

        eng.on_tick(ts_naive, price, pass_atr_gate=gate_pass)

    # Métricas
    trades = eng.trades
    print(f"[result] {len(trades)} trades, {n_passed} gates pass / {n_skipped} fail")
    if not trades:
        return

    tdf = pd.DataFrame(trades)
    tdf["ret_net_dec"] = tdf["ret_pct_net"] / 100.0
    tdf.to_csv(OUT_DIR / "trades.csv", index=False)

    rets = tdf["ret_net_dec"].values
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    wr = len(wins) / len(rets) * 100
    pf = wins.sum() / abs(losses.sum()) if (len(losses) and losses.sum() != 0) else float("nan")
    eq = INITIAL_CAPITAL
    for r in rets:
        eq *= (1 + r)
    years = (anchor.index.max() - anchor.index.min()).total_seconds() / (365.25 * 24 * 3600)
    annual = ((eq / INITIAL_CAPITAL) ** (1 / max(years, 1e-9)) - 1) * 100
    weekly = ((eq / INITIAL_CAPITAL) ** (1 / max(years * 52.1775, 1e-9)) - 1) * 100

    eq_curve = [INITIAL_CAPITAL]
    e = INITIAL_CAPITAL
    for r in rets:
        e *= (1 + r); eq_curve.append(e)
    rm = np.maximum.accumulate(np.array(eq_curve))
    dd = abs(((np.array(eq_curve) - rm) / rm).min()) * 100

    sharpe = (rets.mean() / rets.std() * np.sqrt(len(rets) / years)) if rets.std() > 0 else float("nan")

    summary = f"""
=== Replay engine — Dow3LegsSkipLowEngine sobre parquet 2023+ ===
  Leverage: {leverage}x
  Gate ATR: {'OFF' if no_gate else 'ON (atr14d > p20 regime 60d)'}

  n_trades:    {len(rets)}
  WR:          {wr:.2f}%
  PF:          {pf:.3f}
  Annual:      {annual:.2f}%
  Weekly:      {weekly:.4f}%
  Max DD:      {dd:.2f}%
  Sharpe:      {sharpe:.3f}
  Final equity: ${eq:.2f} (de $100)

  Per-leg breakdown:
"""
    for leg in tdf["leg"].unique():
        sub = tdf[tdf["leg"] == leg]
        wr_l = (sub["ret_net_dec"] > 0).mean() * 100
        sum_l = sub["ret_net_dec"].sum() * 100
        summary += f"    {leg}: n={len(sub)}, WR={wr_l:.1f}%, sum_pp={sum_l:+.2f}\n"

    print(summary)
    with open(OUT_DIR / "summary.txt", "w", encoding="utf-8") as f:
        f.write(summary)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--no-gate", action="store_true")
    args = ap.parse_args()
    sys.exit(main(args.leverage, args.no_gate) or 0)
