#!/usr/bin/env python3
"""
win_stock_leadlag_v0 -- Phase 0A -- analise descritiva (premise check).
Sem entry/exit/PnL. Ver PREREGISTRATION.md para a especificacao normativa.

Saidas em C:\\Users\\...\\mt5\\campaigns\\win_stock_leadlag_v0\\phase0a\\:
  t2_leadlag.csv, t3_events.csv, t4_quintis.csv, summary.md
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
OUT = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\win_stock_leadlag_v0\phase0a")
OUT.mkdir(parents=True, exist_ok=True)

STOCKS = ["VALE3", "PETR4", "ITUB4"]
WEIGHTS_RAW = {"VALE3": 0.15, "PETR4": 0.12, "ITUB4": 0.08}
W_SUM = sum(WEIGHTS_RAW.values())
WEIGHTS = {k: v / W_SUM for k, v in WEIGHTS_RAW.items()}

OOS_CUTOFF = pd.Timestamp("2025-01-01")
RNG = np.random.default_rng(20260722)
N_BOOT = 1000

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    log.append(s)


# ---------------------------------------------------------------- load ----
def load_m5(sym_file: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA / sym_file, columns=["datetime_b3", "close", "real_volume"])
    df = df.sort_values("datetime_b3").drop_duplicates("datetime_b3").reset_index(drop=True)
    n_total = len(df)
    df = df[df["datetime_b3"] < OOS_CUTOFF].reset_index(drop=True)  # OOS sagrado
    n_kept = len(df)
    return df, n_total, n_kept


stock_raw = {}
coverage_rows = []
for s in STOCKS:
    df, n_total, n_kept = load_m5(f"{s}_M5.parquet")
    stock_raw[s] = df
    coverage_rows.append((s, "M5", n_total, n_kept,
                           str(df["datetime_b3"].min()), str(df["datetime_b3"].max())))

win_df, win_n_total, win_n_kept = load_m5("WIN_cont_N_M5.parquet")
coverage_rows.append(("WIN_cont_N", "M5", win_n_total, win_n_kept,
                       str(win_df["datetime_b3"].min()), str(win_df["datetime_b3"].max())))

# M1 robustness -- checar se sobra alguma barra pre-2025
m1_rows = []
for s in STOCKS:
    df1, n1_total, n1_kept = load_m5(f"{s}_M1.parquet")
    m1_rows.append((s, "M1", n1_total, n1_kept))
win1_df, win1_n_total, win1_n_kept = load_m5("WIN_cont_N_M1.parquet")
m1_rows.append(("WIN_cont_N", "M1", win1_n_total, win1_n_kept))
m1_all_empty = all(r[3] == 0 for r in m1_rows)

p("=" * 78)
p("COBERTURA DE DADOS (apos filtro OOS 2025+ sagrado)")
p("=" * 78)
for sym, tf, ntot, nkeep, dmin, dmax in coverage_rows:
    p(f"  {sym:12s} {tf:3s}: total={ntot:>7d}  pre-2025={nkeep:>7d}  "
      f"range_pre2025=[{dmin[:10] if nkeep else '-'} .. {dmax[:10] if nkeep else '-'}]")
p("")
p("M1 robustez (k=1..10, sem gate):")
for sym, tf, ntot, nkeep in m1_rows:
    p(f"  {sym:12s} {tf:3s}: total={ntot:>7d}  pre-2025={nkeep:>7d}")
if m1_all_empty:
    p("  ==> TODAS as series M1 (acoes e WIN) tem 0 barras pre-2025.")
    p("  ==> Causa: teto de ~100k barras do terminal MT5 pra M1 alcanca apenas")
    p("      ~9-11 meses pra tras a partir de hoje (2026-07-22), caindo")
    p("      inteiramente dentro do periodo OOS 2025+ embargado.")
    p("  ==> T2 M1 robustez NAO EXECUTAVEL. Reportado como dado ausente, nao")
    p("      simulado nem aproximado.")
p("")

# -------------------------------------------------------- split escolhido -
disc_years = set(pd.to_datetime([r[4] for r in coverage_rows if r[0] in STOCKS]).year) if False else None
# M5 das acoes cobre 2021-12 a 2024-12 (pre-2025) -- verifica se 2022 E 2023 completos
stock_years_present = sorted(set(pd.concat([stock_raw[s]["datetime_b3"] for s in STOCKS]).dt.year))
p(f"Anos presentes no M5 das acoes (pre-2025): {stock_years_present}")
has_2022_2023_full = 2022 in stock_years_present and 2023 in stock_years_present
if has_2022_2023_full:
    DISCOVERY_END = pd.Timestamp("2024-01-01")  # < esta data = discovery (<=2023)
    CONFIRM_START = pd.Timestamp("2024-01-01")
    CONFIRM_END = pd.Timestamp("2025-01-01")
    split_note = ("M5 das acoes cobre 2021-12->2024-12 (2022 e 2023 completos). "
                   "DISCOVERY = tudo <=2023 (inclui a franja de 2021-12). "
                   "CONFIRM = 2024. OOS = 2025+ (ja removido).")
else:
    # fallback: metade/metade do periodo pre-2025 presente
    all_ts = pd.concat([stock_raw[s]["datetime_b3"] for s in STOCKS])
    tmin, tmax = all_ts.min(), all_ts.max()
    mid = tmin + (tmax - tmin) / 2
    DISCOVERY_END = mid
    CONFIRM_START = mid
    CONFIRM_END = OOS_CUTOFF
    split_note = (f"M5 das acoes NAO cobre 2022+2023 completos -- fallback "
                   f"metade/metade do periodo pre-2025: DISCOVERY < {mid}, "
                   f"CONFIRM >= {mid} e < 2025-01-01.")
p(split_note)
p("")


def split_label(ts: pd.Series) -> pd.Series:
    lab = pd.Series("OTHER", index=ts.index)
    lab[ts < DISCOVERY_END] = "DISCOVERY"
    lab[(ts >= CONFIRM_START) & (ts < CONFIRM_END)] = "CONFIRM"
    return lab


# ---------------------------------------------------- retornos por simbolo -
def add_ret5(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    delta_min = df["datetime_b3"].diff().dt.total_seconds() / 60.0
    ret = np.log(df["close"] / df["close"].shift(1))
    ret[delta_min != 5.0] = np.nan
    ret[df["real_volume"] == 0] = np.nan
    df["ret5"] = ret
    return df


stock_r = {s: add_ret5(stock_raw[s]) for s in STOCKS}
win_r = add_ret5(win_df)

# series indexadas por datetime pra lookup (dedupe garantido pelo load)
stock_ret_ser = {s: stock_r[s].set_index("datetime_b3")["ret5"] for s in STOCKS}
stock_close_ser = {s: stock_raw[s].set_index("datetime_b3")["close"] for s in STOCKS}
win_ret_ser = win_r.set_index("datetime_b3")["ret5"]
win_close_ser = win_df.set_index("datetime_b3")["close"]

# intersecao exata de timestamps (interseccao de BARRAS presentes, nao so retorno valido)
stock_ts = {s: set(stock_raw[s].loc[stock_raw[s]["real_volume"] > 0, "datetime_b3"]) |
               set(stock_raw[s].loc[stock_raw[s]["real_volume"] == 0, "datetime_b3"])  # vazio na pratica
            for s in STOCKS}
win_ts = set(win_df["datetime_b3"])

pair_times = {}
zero_vol_dropped = {}
for s in STOCKS:
    sdf = stock_raw[s]
    nz = sdf.loc[sdf["real_volume"] == 0, "datetime_b3"]
    zero_vol_dropped[s] = len(nz)
    valid_stock_ts = set(sdf.loc[sdf["real_volume"] > 0, "datetime_b3"])
    inter = sorted(valid_stock_ts & win_ts)
    pair_times[s] = pd.DatetimeIndex(inter)

p("=" * 78)
p("ALINHAMENTO (interseccao exata de timestamps acao x WIN, M5)")
p("=" * 78)
for s in STOCKS:
    idx = pair_times[s]
    yrs = idx.year
    p(f"  {s}: {len(idx)} barras pareadas | barras c/ real_volume==0 descartadas: "
      f"{zero_vol_dropped[s]}")
    for y in sorted(set(yrs)):
        sub = idx[yrs == y]
        hrs = sub.hour + sub.minute / 60.0
        p(f"      {y}: n={len(sub):>6d}  hora_min={hrs.min():.2f}  hora_max={hrs.max():.2f}")
p("")

# ============================================================== T1 ========
p("=" * 78)
p("T1 -- correlacao contemporanea M5, por acao x ano (sanity, nao e gate)")
p("=" * 78)
t1_rows = []
for s in STOCKS:
    idx = pair_times[s]
    x = stock_ret_ser[s].reindex(idx)
    y = win_ret_ser.reindex(idx)
    d = pd.DataFrame({"x": x, "y": y, "year": idx.year}).dropna()
    for y_ in sorted(d["year"].unique()):
        sub = d[d["year"] == y_]
        c = sub["x"].corr(sub["y"]) if len(sub) > 10 else np.nan
        t1_rows.append((s, int(y_), len(sub), c))
        p(f"  {s} {y_}: n={len(sub):>6d}  corr={c:.4f}" if pd.notna(c) else f"  {s} {y_}: n insuf")
p("")

# ============================================================== T2 ========
p("=" * 78)
p("T2 -- lead-lag linear M5, k=1..6, por acao x split (+ espelho WIN->acao)")
p("=" * 78)
t2_rows = []
for s in STOCKS:
    idx = pair_times[s]
    lab = split_label(pd.Series(idx))
    for split_name in ["DISCOVERY", "CONFIRM"]:
        sel = idx[lab.values == split_name]
        if len(sel) < 30:
            t2_rows.append((s, split_name, None, None, len(sel), None))
            continue
        for k in range(1, 7):
            lag_ts = sel - pd.Timedelta(minutes=5 * k)
            x_lead = stock_ret_ser[s].reindex(lag_ts).values  # ret_acao[t-k]
            y_now = win_ret_ser.reindex(sel).values           # ret_win[t]
            d1 = pd.DataFrame({"x": x_lead, "y": y_now}).dropna()
            corr_a2w = d1["x"].corr(d1["y"]) if len(d1) > 30 else np.nan

            x_lead_w = win_ret_ser.reindex(lag_ts).values      # ret_win[t-k]
            y_now_a = stock_ret_ser[s].reindex(sel).values     # ret_acao[t]
            d2 = pd.DataFrame({"x": x_lead_w, "y": y_now_a}).dropna()
            corr_w2a = d2["x"].corr(d2["y"]) if len(d2) > 30 else np.nan

            t2_rows.append((s, split_name, k, "acao->WIN", len(d1), corr_a2w))
            t2_rows.append((s, split_name, k, "WIN->acao", len(d2), corr_w2a))
            p(f"  {s} {split_name} k={k}: acao->WIN n={len(d1):>6d} corr={corr_a2w: .4f}   "
              f"| WIN->acao n={len(d2):>6d} corr={corr_w2a: .4f}")

t2_df = pd.DataFrame(t2_rows, columns=["stock", "split", "k", "direction", "n", "corr"])
t2_df.to_csv(OUT / "t2_leadlag.csv", index=False)

# T2 M1 robustez
p("")
p("T2 robustez M1 (k=1..10): NAO EXECUTAVEL -- 0 barras pre-2025 em todas as series M1"
  if m1_all_empty else "T2 M1: dados presentes (ver detalhe acima)")
p("")

# ============================================================== T3 ========
p("=" * 78)
p("T3 -- choque idiossincratico (teste central)")
p("=" * 78)


def build_ret15_z(ret5: pd.Series) -> pd.DataFrame:
    """ret15 rolling (janela de 3 barras M5, sem overlap-gap check) + z por
    horario-do-dia com rolling 20 ocorrencias passadas (sem lookahead)."""
    ret5 = ret5.sort_index()
    r1 = ret5
    r2 = ret5.shift(1)
    r3 = ret5.shift(2)
    ret15 = r1 + r2 + r3
    ret15[r1.isna() | r2.isna() | r3.isna()] = np.nan
    d = pd.DataFrame({"ret15": ret15})
    d["tbucket"] = d.index.strftime("%H:%M")
    d = d.sort_index()

    def _roll(s: pd.Series) -> pd.Series:
        return s.rolling(20, min_periods=10).agg(["mean", "std"]).shift(1).apply(tuple, axis=1)

    grp = d.groupby("tbucket")["ret15"]
    mean_ = grp.transform(lambda s: s.rolling(20, min_periods=10).mean().shift(1))
    std_ = grp.transform(lambda s: s.rolling(20, min_periods=10).std().shift(1))
    d["roll_mean"] = mean_
    d["roll_std"] = std_
    d["z"] = (d["ret15"] - d["roll_mean"]) / d["roll_std"]
    return d


z_by_horario_thin = {}
stock_z = {}
for s in STOCKS:
    dz = build_ret15_z(stock_ret_ser[s])
    stock_z[s] = dz
    n_valid = dz["z"].notna().sum()
    n_total_r15 = dz["ret15"].notna().sum()
    z_by_horario_thin[s] = (n_valid, n_total_r15)

win_z = build_ret15_z(win_ret_ser)
n_valid_w = win_z["z"].notna().sum()
n_total_w = win_z["ret15"].notna().sum()

p("Escolha metodologica: z por horario-do-dia (bucket HH:MM), rolling media/desvio "
  "sobre as ultimas 20 ocorrencias PASSADAS do mesmo horario (shift(1), sem lookahead).")
for s in STOCKS:
    nv, nt = z_by_horario_thin[s]
    p(f"  {s}: ret15 validos={nt}, z validos (pos-warmup 20d)={nv} "
      f"({100*nv/max(nt,1):.1f}%) -- {'OK, nao rala' if nv > 0.5*nt else 'RALA'}")
p(f"  WIN: ret15 validos={n_total_w}, z validos={n_valid_w} ({100*n_valid_w/max(n_total_w,1):.1f}%)")
use_horario_bucket = all((nv > 0.3 * nt) for nv, nt in z_by_horario_thin.values()) and n_valid_w > 0.3 * n_total_w
p(f"  Decisao final: {'por horario' if use_horario_bucket else 'SEM distincao de horario (fallback, versao por horario ficou rala)'}")

if not use_horario_bucket:
    # fallback: rolling 20*~32 (=~640) observacoes passadas, sem distincao de horario
    def build_ret15_z_nohour(ret5: pd.Series) -> pd.DataFrame:
        ret5 = ret5.sort_index()
        r1, r2, r3 = ret5, ret5.shift(1), ret5.shift(2)
        ret15 = r1 + r2 + r3
        ret15[r1.isna() | r2.isna() | r3.isna()] = np.nan
        d = pd.DataFrame({"ret15": ret15}).sort_index()
        win_obs = 20 * 32  # ~20 dias uteis * ~32 janelas 15min/dia
        d["roll_mean"] = d["ret15"].rolling(win_obs, min_periods=200).mean().shift(1)
        d["roll_std"] = d["ret15"].rolling(win_obs, min_periods=200).std().shift(1)
        d["z"] = (d["ret15"] - d["roll_mean"]) / d["roll_std"]
        return d
    stock_z = {s: build_ret15_z_nohour(stock_ret_ser[s]) for s in STOCKS}
    win_z = build_ret15_z_nohour(win_ret_ser)
p("")

# eventos
t3_event_rows = []
for s in STOCKS:
    idx = pair_times[s]
    zs = stock_z[s]["z"].reindex(idx)
    zw = win_z["z"].reindex(idx)
    ret15_s = stock_z[s]["ret15"].reindex(idx)
    cand = pd.DataFrame({"z_stock": zs, "z_win": zw, "ret15_stock": ret15_s}, index=idx).dropna()
    mask = (cand["z_stock"].abs() >= 2.5) & (cand["z_win"].abs() < 0.5)
    cand = cand[mask].sort_index()

    # dedupe: min 30min entre eventos da mesma acao
    kept_times = []
    last_t = None
    for t in cand.index:
        if last_t is None or (t - last_t) >= pd.Timedelta(minutes=30):
            kept_times.append(t)
            last_t = t
    events = cand.loc[kept_times]

    for t, row in events.iterrows():
        direction = np.sign(row["ret15_stock"])
        ref_price = win_close_ser.get(t, np.nan)
        fwd = {}
        for h in (5, 15, 30):
            tgt = t + pd.Timedelta(minutes=h)
            px = win_close_ser.get(tgt, np.nan)
            fwd[h] = np.log(px / ref_price) * direction if pd.notna(px) and pd.notna(ref_price) and ref_price > 0 else np.nan
        lab = split_label(pd.Series([t])).iloc[0]
        t3_event_rows.append({
            "stock": s, "event_time": t, "split": lab,
            "z_stock": row["z_stock"], "z_win": row["z_win"],
            "ret15_stock": row["ret15_stock"], "direction": int(direction),
            "fwd_5m": fwd[5], "fwd_15m": fwd[15], "fwd_30m": fwd[30],
        })

t3_df = pd.DataFrame(t3_event_rows)
t3_df.to_csv(OUT / "t3_events.csv", index=False)


def boot_ci(x: np.ndarray, n_boot=N_BOOT):
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return (np.nan, np.nan, np.nan)
    means = np.array([RNG.choice(x, size=len(x), replace=True).mean() for _ in range(n_boot)])
    return (float(np.mean(x)), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


p(f"Total eventos T3 (todas acoes, todos splits, pos-dedupe 30min): {len(t3_df)}")
t3_summary_rows = []
if len(t3_df):
    for s in STOCKS + ["ALL"]:
        sub_s = t3_df if s == "ALL" else t3_df[t3_df["stock"] == s]
        for split_name in ["DISCOVERY", "CONFIRM"]:
            sub = sub_s[sub_s["split"] == split_name]
            for h, col in [(5, "fwd_5m"), (15, "fwd_15m"), (30, "fwd_30m")]:
                x = sub[col].values.astype(float)
                x = x[~np.isnan(x)]
                if len(x) == 0:
                    continue
                mean_, lo, hi = boot_ci(x)
                wr = float((x > 0).mean())
                t3_summary_rows.append({
                    "stock": s, "split": split_name, "horizon_min": h,
                    "n": len(x), "mean_bps": mean_ * 1e4, "median_bps": float(np.median(x)) * 1e4,
                    "ci_lo_bps": lo * 1e4, "ci_hi_bps": hi * 1e4, "win_rate": wr,
                })
                p(f"  {s:6s} {split_name:9s} fwd_{h:>2d}m: n={len(x):>4d}  "
                  f"mean={mean_*1e4:+7.2f}bps  median={np.median(x)*1e4:+7.2f}bps  "
                  f"IC95=[{lo*1e4:+.2f},{hi*1e4:+.2f}]bps  WR={wr*100:.1f}%")
    p("")
    p("Breakdown por ano:")
    t3_df["year"] = pd.to_datetime(t3_df["event_time"]).dt.year
    for (s, y), sub in t3_df.groupby(["stock", "year"]):
        x = sub["fwd_15m"].dropna().values
        if len(x) == 0:
            continue
        p(f"  {s} {y}: n={len(x):>4d}  mean_fwd15={x.mean()*1e4:+7.2f}bps  WR={100*(x>0).mean():.1f}%")
else:
    p("  Nenhum evento T3 encontrado (0 casos |z_acao|>=2.5 & |z_win|<0.5).")
t3_summary_df = pd.DataFrame(t3_summary_rows)
p("")

# ============================================================== T4 ========
p("=" * 78)
p("T4 -- divergencia de cesta ponderada")
p("=" * 78)


def ret30_series(ret5: pd.Series) -> pd.Series:
    ret5 = ret5.sort_index()
    parts = [ret5.shift(i) for i in range(6)]
    r30 = sum(parts)
    valid = parts[0].notna()
    for pt in parts[1:]:
        valid &= pt.notna()
    r30[~valid] = np.nan
    return r30


stock_r30 = {s: ret30_series(stock_ret_ser[s]) for s in STOCKS}
win_r30 = ret30_series(win_ret_ser)

common_idx = None
for s in STOCKS:
    idx = pair_times[s]
    common_idx = set(idx) if common_idx is None else (common_idx & set(idx))
common_idx = pd.DatetimeIndex(sorted(common_idx))

basket = pd.DataFrame(index=common_idx)
for s in STOCKS:
    basket[s] = stock_r30[s].reindex(common_idx)
basket["win"] = win_r30.reindex(common_idx)
basket = basket.dropna()
basket["cesta"] = sum(basket[s] * WEIGHTS[s] for s in STOCKS)
basket["spread"] = basket["cesta"] - basket["win"]
basket["split"] = split_label(pd.Series(basket.index)).values

p(f"Pesos renormalizados: {WEIGHTS}")
p(f"Barras com cesta+WIN validos (ret30, interseccao das 3 acoes): {len(basket)}")

t4_rows = []
disc = basket[basket["split"] == "DISCOVERY"]
conf = basket[basket["split"] == "CONFIRM"]
if len(disc) >= 50:
    try:
        cuts = pd.qcut(disc["spread"], 5, retbins=True, duplicates="drop")[1]
    except Exception:
        cuts = None
    if cuts is not None and len(cuts) == 6:
        labels = ["Q1(mais neg)", "Q2", "Q3", "Q4", "Q5(mais pos)"]

        def assign_q(x):
            return pd.cut(x, bins=cuts, labels=labels, include_lowest=True)

        for split_name, sub in [("DISCOVERY", disc), ("CONFIRM", conf)]:
            if len(sub) == 0:
                continue
            sub = sub.copy()
            sub["q"] = assign_q(sub["spread"])
            ref_px = win_close_ser.reindex(sub.index)
            fwd30 = np.log(win_close_ser.reindex(sub.index + pd.Timedelta(minutes=30)).values / ref_px.values)
            fwd60 = np.log(win_close_ser.reindex(sub.index + pd.Timedelta(minutes=60)).values / ref_px.values)
            sub["fwd30"] = fwd30
            sub["fwd60"] = fwd60
            for q in labels:
                qsub = sub[sub["q"] == q]
                m30 = qsub["fwd30"].dropna()
                m60 = qsub["fwd60"].dropna()
                row = {
                    "split": split_name, "quintile": q, "n": len(qsub),
                    "spread_mean": qsub["spread"].mean(),
                    "fwd30_mean_bps": m30.mean() * 1e4 if len(m30) else np.nan,
                    "fwd60_mean_bps": m60.mean() * 1e4 if len(m60) else np.nan,
                }
                if q in (labels[0], labels[-1]) and len(m30) >= 5:
                    mean_, lo, hi = boot_ci(m30.values)
                    row["fwd30_ci_lo_bps"] = lo * 1e4
                    row["fwd30_ci_hi_bps"] = hi * 1e4
                t4_rows.append(row)
                p(f"  {split_name} {q}: n={len(qsub):>5d}  spread_mean={row['spread_mean']*1e4:+.2f}bps  "
                  f"fwd30={row['fwd30_mean_bps']:+.2f}bps  fwd60={row['fwd60_mean_bps']:+.2f}bps")
    else:
        p("  Nao foi possivel formar 5 quintis (cortes duplicados / dados insuficientes).")
else:
    p(f"  DISCOVERY tem apenas {len(disc)} barras validas de cesta -- insuficiente pra quintis.")
t4_df = pd.DataFrame(t4_rows)
t4_df.to_csv(OUT / "t4_quintis.csv", index=False)
p("")

# ============================================================== GATES =====
p("=" * 78)
p("GATES MECANICOS (reporte passa/falha -- SEM veredito, revisao e do Fable)")
p("=" * 78)

# Gate 1a: T2 k=1 M5, DISCOVERY >=0.05 e CONFIRM >=0.03 mesmo sinal
gate1a_hits = []
for s in STOCKS:
    d = t2_df[(t2_df["stock"] == s) & (t2_df["k"] == 1) & (t2_df["direction"] == "acao->WIN")]
    dd = d[d["split"] == "DISCOVERY"]["corr"]
    cc = d[d["split"] == "CONFIRM"]["corr"]
    if len(dd) and len(cc) and pd.notna(dd.iloc[0]) and pd.notna(cc.iloc[0]):
        vd, vc = dd.iloc[0], cc.iloc[0]
        hit = abs(vd) >= 0.05 and abs(vc) >= 0.03 and np.sign(vd) == np.sign(vc)
        gate1a_hits.append((s, vd, vc, hit))
        p(f"  Gate1a T2 k=1 {s}: DISCOVERY={vd:.4f} CONFIRM={vc:.4f} -> {'PASSA' if hit else 'falha'}")
gate1a = any(h[3] for h in gate1a_hits)

# Gate 1b: T3 fwd15 |mean|>=6bps, n>=100 DISCOVERY, IC exclui zero, mesmo sinal CONFIRM
gate1b_hits = []
if len(t3_summary_df):
    for s in STOCKS:
        dd = t3_summary_df[(t3_summary_df.stock == s) & (t3_summary_df.split == "DISCOVERY") & (t3_summary_df.horizon_min == 15)]
        cc = t3_summary_df[(t3_summary_df.stock == s) & (t3_summary_df.split == "CONFIRM") & (t3_summary_df.horizon_min == 15)]
        if len(dd) and len(cc):
            r = dd.iloc[0]
            rc = cc.iloc[0]
            ci_excl_zero = (r["ci_lo_bps"] > 0) or (r["ci_hi_bps"] < 0)
            hit = (abs(r["mean_bps"]) >= 6 and r["n"] >= 100 and ci_excl_zero and
                   np.sign(r["mean_bps"]) == np.sign(rc["mean_bps"]))
            gate1b_hits.append((s, r["mean_bps"], r["n"], ci_excl_zero, hit))
            p(f"  Gate1b T3 fwd15 {s}: DISCOVERY mean={r['mean_bps']:+.2f}bps n={r['n']:.0f} "
              f"IC_excl_zero={ci_excl_zero} CONFIRM_mean={rc['mean_bps']:+.2f}bps -> {'PASSA' if hit else 'falha'}")
gate1b = any(h[4] for h in gate1b_hits) if gate1b_hits else False

# Gate 1c: T4 quintil extremo >=6bps + monotonicidade, replicando CONFIRM
gate1c = False
if len(t4_df):
    for split_name in ["DISCOVERY"]:
        sub = t4_df[t4_df.split == split_name].set_index("quintile")
        if len(sub) == 5:
            vals = sub.loc[["Q1(mais neg)", "Q2", "Q3", "Q4", "Q5(mais pos)"], "fwd30_mean_bps"]
            monotonic = vals.is_monotonic_increasing or vals.is_monotonic_decreasing
            extreme = max(abs(vals.iloc[0]), abs(vals.iloc[-1]))
            confirm_sub = t4_df[t4_df.split == "CONFIRM"].set_index("quintile")
            replicates = False
            if len(confirm_sub) == 5:
                cvals = confirm_sub.loc[["Q1(mais neg)", "Q2", "Q3", "Q4", "Q5(mais pos)"], "fwd30_mean_bps"]
                replicates = np.sign(vals.iloc[0]) == np.sign(cvals.iloc[0]) and np.sign(vals.iloc[-1]) == np.sign(cvals.iloc[-1])
            gate1c = extreme >= 6 and monotonic and replicates
            p(f"  Gate1c T4 DISCOVERY: extremo={extreme:.2f}bps monotonic={monotonic} "
              f"replica_CONFIRM={replicates} -> {'PASSA' if gate1c else 'falha'}")

gate1 = gate1a or gate1b or gate1c
p(f"\nGATE 1 (T2 OR T3 OR T4): {'PASSA' if gate1 else 'FALHA'}")

# Gate 2: efeito em >=2 de 3 acoes (ou cesta)
n_stocks_gate1a = sum(1 for h in gate1a_hits if h[3])
n_stocks_gate1b = sum(1 for h in gate1b_hits if h[4])
gate2_candidates = max(n_stocks_gate1a, n_stocks_gate1b)
gate2 = gate2_candidates >= 2 or gate1c  # T4 cesta conta como "efeito na cesta"
p(f"GATE 2 (efeito em >=2 acoes ou na cesta): acoes_com_hit(T2)={n_stocks_gate1a} "
  f"acoes_com_hit(T3)={n_stocks_gate1b} cesta_T4={gate1c} -> {'PASSA' if gate2 else 'FALHA'}")

# Gate 3: regua T2-espelho
p("\nGATE 3 (regua T2-espelho, k=1 M5 DISCOVERY):")
gate3_flag_documented = False
for s in STOCKS:
    d = t2_df[(t2_df["stock"] == s) & (t2_df["k"] == 1) & (t2_df["split"] == "DISCOVERY")]
    a2w = d[d["direction"] == "acao->WIN"]["corr"]
    w2a = d[d["direction"] == "WIN->acao"]["corr"]
    if len(a2w) and len(w2a) and pd.notna(a2w.iloc[0]) and pd.notna(w2a.iloc[0]) and abs(a2w.iloc[0]) > 1e-9:
        ratio = abs(w2a.iloc[0]) / abs(a2w.iloc[0])
        confirms_prior = ratio >= 3.0
        if confirms_prior:
            gate3_flag_documented = True
        p(f"  {s}: |WIN->acao|/|acao->WIN| = {ratio:.2f} -> "
          f"{'confirma prior (WIN lidera)' if confirms_prior else 'nao confirma 3x'}")
p(f"GATE 3: {'confirma prior em pelo menos 1 acao -- NAO promover mesmo com gate1 marginal' if gate3_flag_documented else 'nao aplicavel / nao confirmado'}")

p("")
p("RESUMO GATES:")
p(f"  Gate 1 (T2 OR T3 OR T4): {'PASSA' if gate1 else 'FALHA'}")
p(f"  Gate 2 (>=2 acoes/cesta): {'PASSA' if gate2 else 'FALHA'}")
p(f"  Gate 3 (regua espelho, veto): {'ALERTA -- confirma prior' if gate3_flag_documented else 'sem veto'}")
todos_gates = gate1 and gate2 and not gate3_flag_documented
p(f"  TODOS OS GATES MECANICOS: {'PASSA' if todos_gates else 'FALHA'}")
p("  (Nota: decisao de promocao/closeout e da revisao humana/Fable, nao deste script.)")

# ---------------------------------------------------------------- summary -
summary_path = OUT / "summary.md"
with open(summary_path, "w", encoding="utf-8") as f:
    f.write("# win_stock_leadlag_v0 -- Phase 0A -- summary\n\n")
    f.write("Premise check descritivo (sem entry/exit/PnL). Ver PREREGISTRATION.md.\n\n")
    f.write("## Log completo de execucao\n\n```\n")
    f.write("\n".join(log))
    f.write("\n```\n")

p("")
p(f"Arquivos salvos: {OUT}")
p(f"  t2_leadlag.csv, t3_events.csv, t4_quintis.csv, summary.md")
