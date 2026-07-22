"""
win_flow_v0 -- Fase 0b -- mapa preditivo (mecanico, pre-registrado)

Le flow_bars_1min.parquet (Fase 0a, G4 passou), filtra HOLDOUT (>=2026-04-01)
fora imediatamente, e testa se OFI_k / LARGE_OFI_k (k=5,15,30 min, trailing,
ponderado por volume) preveem retorno forward h (5,15,30 min) do WIN$N,
em grade de amostragem de 5min (09:05-17:30), separando DISCOVERY em
H1 (2025-07->2025-11) e H2 (2025-12->2026-03) para replicacao interna.

Script auto-suficiente. Paths absolutos. Sem env vars especiais.
Dependencias: pandas, numpy, pyarrow (ja instaladas).

Saidas:
  FLOW_MAP_SUMMARY.txt      -- relatorio completo (todas as tabelas)
  flow_map_results.csv      -- uma linha por (signal,k,h,half,quintile)
  flow_map_samples_meta.json -- contagens de amostras/dias por combo/half
"""

import json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
IN_PARQUET = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\win_flow_v0\phase0\flow_bars_1min.parquet"
OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\win_flow_v0\phase0"
OUT_SUMMARY = OUT_DIR + r"\FLOW_MAP_SUMMARY.txt"
OUT_CSV = OUT_DIR + r"\flow_map_results.csv"
OUT_META = OUT_DIR + r"\flow_map_samples_meta.json"

HOLDOUT_START = "2026-04-01"   # NAO TOCAR -- filtrado logo apos load
H2_START = "2025-12-01"        # H1: [inicio, H2_START); H2: [H2_START, HOLDOUT_START)

KS = [5, 15, 30]
HS = [5, 15, 30]
SIGNALS = ["ofi", "large_ofi"]   # nomes das colunas fonte no parquet
SIGNAL_LABEL = {"ofi": "OFI", "large_ofi": "LARGE_OFI"}

GRID_START = "09:05"
GRID_END = "17:30"
GRID_FREQ = "5min"

N_BOOT = 1000
BOOT_SEED = 42
G1_MIN_SPREAD_BPS = 6.0
G1_MIN_H = 15
G2_MIN_SPEARMAN = 0.9
MAKER_LO, MAKER_HI = 2.0, 6.0

lines = []  # buffer do relatorio texto


def log(s=""):
    print(s)
    lines.append(str(s))


# ---------------------------------------------------------------------------
# 1. LOAD + FILTRO HOLDOUT (imediato, inegociavel)
# ---------------------------------------------------------------------------
log("=" * 78)
log("win_flow_v0 -- Fase 0b -- build_flow_map.py")
log("=" * 78)
log(f"IN_PARQUET = {IN_PARQUET}")

df_raw = pd.read_parquet(IN_PARQUET)
n_raw = len(df_raw)
n_days_raw = df_raw["date"].nunique()

df = df_raw[df_raw["date"] < HOLDOUT_START].copy()
n_holdout_dropped = n_raw - len(df)
n_days_holdout = df_raw.loc[df_raw["date"] >= HOLDOUT_START, "date"].nunique()

log(f"\nlinhas totais no parquet: {n_raw} ({n_days_raw} dias)")
log(f"HOLDOUT (>= {HOLDOUT_START}) removido: {n_holdout_dropped} linhas, "
    f"{n_days_holdout} dias -- NAO TOCADO nesta fase")
log(f"linhas DISCOVERY restantes: {len(df)} ({df['date'].nunique()} dias)")

df = df.sort_values(["date", "datetime_b3"]).reset_index(drop=True)

# checagem de gaps internos (informativo -- nao muda metodologia; sinal usa
# "ultimas k BARRAS", nao k minutos de calendario, entao gaps nao invalidam)
gap_days = 0
for d, g in df.groupby("date"):
    diffs = g["datetime_b3"].diff().dropna()
    if (diffs != pd.Timedelta(minutes=1)).any():
        gap_days += 1
log(f"dias com gap interno de minuto (informativo, sinal e' row-based, "
    f"nao afetado): {gap_days} / {df['date'].nunique()}")

# ---------------------------------------------------------------------------
# 2. SPLIT H1 / H2
# ---------------------------------------------------------------------------
df["half"] = np.where(df["date"] < H2_START, "H1", "H2")
n_h1 = df.loc[df["half"] == "H1", "date"].nunique()
n_h2 = df.loc[df["half"] == "H2", "date"].nunique()
log(f"\nH1 (2025-07 -> 2025-11): {n_h1} dias, "
    f"{(df['half']=='H1').sum()} barras")
log(f"H2 (2025-12 -> 2026-03): {n_h2} dias, "
    f"{(df['half']=='H2').sum()} barras")

# ---------------------------------------------------------------------------
# 3. SINAIS TRAILING (OFI_k, LARGE_OFI_k) -- media ponderada por vol_total
#    "ultimas k barras de 1min ATE o minuto da amostra inclusive"
#    LARGE_OFI ponderado por vol_total como APROXIMACAO (nao ha buy_vol_l/
#    sell_vol_l reconstruivel a partir das flow-bars da fase 0a) -- DOCUMENTADO.
# ---------------------------------------------------------------------------
log("\nNOTA METODOLOGICA: LARGE_OFI_k e' ponderado por vol_total (volume "
    "TOTAL do minuto), nao por volume 'large', pois buy_vol_l/sell_vol_l "
    "nao existem nas flow-bars da fase 0a (so large_ofi agregado). "
    "Aproximacao pre-registrada explicitamente.")


# NOTA: pandas 3.0 mudou o default de groupby.apply (include_groups=False e
# nao pode mais ser True) -- a coluna de agrupamento ('date') e' excluida do
# frame passado pra func E some do resultado combinado. Por isso usamos
# iteracao manual (groupby sem .apply) + atribuicao via .loc, que preserva
# 'date' e evita esse comportamento.
for sig in SIGNALS:
    for k in KS:
        df[f"{sig}_{k}"] = np.nan
for h in HS:
    df[f"fwd_ret_{h}"] = np.nan

for date, g in df.groupby("date", sort=False):
    g = g.sort_values("datetime_b3")
    idx = g.index
    vol = g["vol_total"].to_numpy()

    for sig in SIGNALS:
        s = g[sig].to_numpy()
        sv_ser = pd.Series(s * vol)
        v_ser = pd.Series(vol)
        for k in KS:
            roll_sv = sv_ser.rolling(k, min_periods=k).sum()
            roll_v = v_ser.rolling(k, min_periods=k).sum()
            df.loc[idx, f"{sig}_{k}"] = (roll_sv / roll_v).to_numpy()

    close_by_time = pd.Series(g["close_last"].to_numpy(), index=g["datetime_b3"])
    for h in HS:
        target_time = g["datetime_b3"] + pd.Timedelta(minutes=h)
        fwd_close = target_time.map(close_by_time)
        df.loc[idx, f"fwd_ret_{h}"] = (
            fwd_close.to_numpy() / g["close_last"].to_numpy() - 1.0
        ) * 1e4

# ---------------------------------------------------------------------------
# 5. GRADE DE AMOSTRAGEM: a cada 5min, 09:05-17:30, por dia
# ---------------------------------------------------------------------------
grid_times = set(pd.date_range(f"2000-01-01 {GRID_START}",
                                f"2000-01-01 {GRID_END}", freq=GRID_FREQ).time)
df["is_grid"] = df["datetime_b3"].dt.time.isin(grid_times)
df["tod"] = df["datetime_b3"].dt.time
df["session_part"] = np.where(df["tod"] < pd.Timestamp("13:00").time(), "manha", "tarde")

df_grid = df[df["is_grid"]].copy()
log(f"\nbarras na grade de amostragem (5min, {GRID_START}-{GRID_END}), "
    f"antes de exigir historico k / alvo h: {len(df_grid)}")

# ---------------------------------------------------------------------------
# 6. LOOP DE TESTES: (signal, k, h, half)
# ---------------------------------------------------------------------------
results_rows = []       # linhas long-format p/ CSV (uma por quintil)
meta = {}                # contagens por combo/half
combo_stats = {}         # (signal,k,h,half) -> dict com spread/ci/spearman/n/n_days
maker_candidates = []
n_tests = 0

for sig in SIGNALS:
    for k in KS:
        sig_col = f"{sig}_{k}"
        for h in HS:
            fwd_col = f"fwd_ret_{h}"
            for half in ["H1", "H2"]:
                n_tests += 1
                sub = df_grid.loc[
                    (df_grid["half"] == half)
                    & df_grid[sig_col].notna()
                    & df_grid[fwd_col].notna(),
                    ["date", sig_col, fwd_col, "session_part"],
                ].copy()
                sub.columns = ["date", "signal", "fwd_ret", "session_part"]

                n_samples = len(sub)
                n_days_combo = sub["date"].nunique()
                meta_key = f"{SIGNAL_LABEL[sig]}_k{k}_h{h}_{half}"
                meta[meta_key] = {"n_samples": int(n_samples), "n_days": int(n_days_combo)}

                if n_samples < 25 or sub["signal"].nunique() < 5:
                    combo_stats[(sig, k, h, half)] = None
                    continue

                # quintis: thresholds dentro da amostra (signal,k,h,half)
                try:
                    sub["quintile"] = pd.qcut(sub["signal"], 5, labels=[1, 2, 3, 4, 5],
                                               duplicates="drop")
                except ValueError:
                    combo_stats[(sig, k, h, half)] = None
                    continue

                if sub["quintile"].nunique() < 5:
                    # duplicatas colapsaram quintis -- registra o que deu, mas
                    # marca combo como nao-avaliavel p/ gates (precisa 5 quintis)
                    q_stats = sub.groupby("quintile", observed=True)["fwd_ret"].agg(
                        ["mean", "count"])
                    for q, row in q_stats.iterrows():
                        results_rows.append({
                            "signal": SIGNAL_LABEL[sig], "k": k, "h": h, "half": half,
                            "quintile": int(q), "n": int(row["count"]),
                            "mean_fwd_ret_bps": row["mean"],
                            "spread_q5_q1_bps": np.nan, "ci95_lo": np.nan, "ci95_hi": np.nan,
                            "spearman": np.nan, "n_quintiles_valid": int(sub["quintile"].nunique()),
                        })
                    combo_stats[(sig, k, h, half)] = None
                    continue

                q_stats = sub.groupby("quintile", observed=True)["fwd_ret"].agg(
                    ["mean", "count"]).reindex([1, 2, 3, 4, 5])
                mean_q1 = q_stats.loc[1, "mean"]
                mean_q5 = q_stats.loc[5, "mean"]
                spread = mean_q5 - mean_q1
                rho, _ = spearmanr([1, 2, 3, 4, 5], q_stats["mean"].to_numpy())

                # bootstrap de bloco-dia (seed 42, 1000x)
                day_list = sorted(sub["date"].unique())
                day_idx = {d: i for i, d in enumerate(day_list)}
                nd = len(day_list)
                sub["day_i"] = sub["date"].map(day_idx)

                sum_q5 = np.zeros(nd); cnt_q5 = np.zeros(nd)
                sum_q1 = np.zeros(nd); cnt_q1 = np.zeros(nd)
                g5 = sub[sub["quintile"] == 5].groupby("day_i")["fwd_ret"].agg(["sum", "count"])
                g1 = sub[sub["quintile"] == 1].groupby("day_i")["fwd_ret"].agg(["sum", "count"])
                sum_q5[g5.index.to_numpy()] = g5["sum"].to_numpy()
                cnt_q5[g5.index.to_numpy()] = g5["count"].to_numpy()
                sum_q1[g1.index.to_numpy()] = g1["sum"].to_numpy()
                cnt_q1[g1.index.to_numpy()] = g1["count"].to_numpy()

                rng = np.random.default_rng(BOOT_SEED)
                draws = rng.integers(0, nd, size=(N_BOOT, nd))
                b_sum_q5 = sum_q5[draws].sum(axis=1)
                b_cnt_q5 = cnt_q5[draws].sum(axis=1)
                b_sum_q1 = sum_q1[draws].sum(axis=1)
                b_cnt_q1 = cnt_q1[draws].sum(axis=1)
                with np.errstate(invalid="ignore", divide="ignore"):
                    b_spread = (b_sum_q5 / b_cnt_q5) - (b_sum_q1 / b_cnt_q1)
                b_spread = b_spread[np.isfinite(b_spread)]
                ci_lo, ci_hi = (np.percentile(b_spread, [2.5, 97.5])
                                 if len(b_spread) > 0 else (np.nan, np.nan))

                # per-dia (para G3, contribuicao ao spread)
                day_q5 = sub[sub["quintile"] == 5].groupby("date")["fwd_ret"].mean()
                day_q1 = sub[sub["quintile"] == 1].groupby("date")["fwd_ret"].mean()
                day_contrib = (day_q5 - day_q1).dropna()

                combo_stats[(sig, k, h, half)] = {
                    "spread": spread, "ci_lo": ci_lo, "ci_hi": ci_hi,
                    "spearman": rho, "n_samples": n_samples, "n_days": nd,
                    "q_stats": q_stats, "day_contrib": day_contrib,
                    "sum_q5": sum_q5, "cnt_q5": cnt_q5, "sum_q1": sum_q1, "cnt_q1": cnt_q1,
                    "day_list": day_list, "sub": sub,
                }

                for q, row in q_stats.iterrows():
                    results_rows.append({
                        "signal": SIGNAL_LABEL[sig], "k": k, "h": h, "half": half,
                        "quintile": int(q), "n": int(row["count"]),
                        "mean_fwd_ret_bps": row["mean"],
                        "spread_q5_q1_bps": spread, "ci95_lo": ci_lo, "ci95_hi": ci_hi,
                        "spearman": rho, "n_quintiles_valid": 5,
                    })

                if h == 5 and MAKER_LO <= abs(spread) < MAKER_HI and not (ci_lo <= 0 <= ci_hi):
                    maker_candidates.append((sig, k, h, half, spread, ci_lo, ci_hi))

log(f"\ntotal de testes (signal x k x h x half): {n_tests}")

# ---------------------------------------------------------------------------
# 7. IMPRESSAO DAS TABELAS DE QUINTIS POR COMBO x METADE
# ---------------------------------------------------------------------------
log("\n" + "=" * 78)
log("TABELAS DE QUINTIS -- media do ret forward (bps) por quintil de sinal")
log("=" * 78)
for sig in SIGNALS:
    for k in KS:
        for h in HS:
            log(f"\n--- {SIGNAL_LABEL[sig]}_k{k} -> fwd_ret_{h}min ---")
            for half in ["H1", "H2"]:
                cs = combo_stats.get((sig, k, h, half))
                mk = meta[f"{SIGNAL_LABEL[sig]}_k{k}_h{h}_{half}"]
                if cs is None:
                    log(f"  [{half}] n_samples={mk['n_samples']} n_dias={mk['n_days']} "
                        f"-- INSUFICIENTE p/ 5 quintis, pulado")
                    continue
                qs = cs["q_stats"]
                qline = " | ".join(f"Q{int(q)}={qs.loc[q,'mean']:+.2f}bps(n={int(qs.loc[q,'count'])})"
                                    for q in [1, 2, 3, 4, 5])
                log(f"  [{half}] n={cs['n_samples']} dias={cs['n_days']}  {qline}")
                log(f"        spread(Q5-Q1)={cs['spread']:+.2f}bps  "
                    f"IC95=[{cs['ci_lo']:+.2f},{cs['ci_hi']:+.2f}]  "
                    f"spearman={cs['spearman']:+.3f}")

# ---------------------------------------------------------------------------
# 8. GATES
# ---------------------------------------------------------------------------
log("\n" + "=" * 78)
log("AVALIACAO DE GATES")
log("=" * 78)


def ci_excludes_zero(lo, hi):
    return (lo > 0) or (hi < 0)


g1_pass_combos = []
for sig in SIGNALS:
    for k in KS:
        for h in HS:
            if h < G1_MIN_H:
                continue
            cs_h1 = combo_stats.get((sig, k, h, "H1"))
            cs_h2 = combo_stats.get((sig, k, h, "H2"))
            if cs_h1 is None or cs_h2 is None:
                continue
            ok_h1 = (abs(cs_h1["spread"]) >= G1_MIN_SPREAD_BPS
                      and ci_excludes_zero(cs_h1["ci_lo"], cs_h1["ci_hi"]))
            ok_h2 = (abs(cs_h2["spread"]) >= G1_MIN_SPREAD_BPS
                      and ci_excludes_zero(cs_h2["ci_lo"], cs_h2["ci_hi"]))
            same_sign = np.sign(cs_h1["spread"]) == np.sign(cs_h2["spread"]) and np.sign(cs_h1["spread"]) != 0
            if ok_h1 and ok_h2 and same_sign:
                g1_pass_combos.append((sig, k, h))

log(f"\nG1 (h>=15, |spread|>=6bps, IC95 excl. 0, mesmo sinal em H1 e H2): "
    f"{'PASSOU' if g1_pass_combos else 'FALHOU'}")
for sig, k, h in g1_pass_combos:
    cs1 = combo_stats[(sig, k, h, "H1")]
    cs2 = combo_stats[(sig, k, h, "H2")]
    log(f"  -> {SIGNAL_LABEL[sig]}_k{k} h={h}min: "
        f"H1 spread={cs1['spread']:+.2f}bps IC=[{cs1['ci_lo']:+.2f},{cs1['ci_hi']:+.2f}]  "
        f"H2 spread={cs2['spread']:+.2f}bps IC=[{cs2['ci_lo']:+.2f},{cs2['ci_hi']:+.2f}]")

g2_pass_combos = []
for sig, k, h in g1_pass_combos:
    cs1 = combo_stats[(sig, k, h, "H1")]
    cs2 = combo_stats[(sig, k, h, "H2")]
    ok = abs(cs1["spearman"]) >= G2_MIN_SPEARMAN and abs(cs2["spearman"]) >= G2_MIN_SPEARMAN
    if ok:
        g2_pass_combos.append((sig, k, h))

log(f"\nG2 (|spearman|>=0.9 em H1 e H2, na(s) combinacao(oes) de G1): "
    f"{'PASSOU' if g2_pass_combos else 'FALHOU'}")
for sig, k, h in g1_pass_combos:
    cs1 = combo_stats[(sig, k, h, "H1")]
    cs2 = combo_stats[(sig, k, h, "H2")]
    passed = (sig, k, h) in g2_pass_combos
    log(f"  -> {SIGNAL_LABEL[sig]}_k{k} h={h}min: "
        f"spearman H1={cs1['spearman']:+.3f} H2={cs2['spearman']:+.3f} "
        f"-> {'passa' if passed else 'nao passa'}")

# G3: remove top 5% dias por |contribuicao| e recalcula
log(f"\nG3 (sobrevive a remocao dos 5% dias de maior |contribuicao diaria|):")
g3_pass_combos = []
for sig, k, h in g2_pass_combos:
    ok_both = True
    detail = []
    for half in ["H1", "H2"]:
        cs = combo_stats[(sig, k, h, half)]
        contrib = cs["day_contrib"]
        nd_c = len(contrib)
        n_remove = int(np.ceil(0.05 * nd_c))
        days_to_remove = contrib.abs().sort_values(ascending=False).head(n_remove).index
        day_idx_map = {d: i for i, d in enumerate(cs["day_list"])}
        remove_idx = [day_idx_map[d] for d in days_to_remove if d in day_idx_map]
        keep_mask = np.ones(len(cs["day_list"]), dtype=bool)
        keep_mask[remove_idx] = False
        new_sum_q5 = cs["sum_q5"][keep_mask].sum()
        new_cnt_q5 = cs["cnt_q5"][keep_mask].sum()
        new_sum_q1 = cs["sum_q1"][keep_mask].sum()
        new_cnt_q1 = cs["cnt_q1"][keep_mask].sum()
        new_spread = (new_sum_q5 / new_cnt_q5) - (new_sum_q1 / new_cnt_q1) if new_cnt_q5 > 0 and new_cnt_q1 > 0 else np.nan
        ok = np.isfinite(new_spread) and abs(new_spread) >= G1_MIN_SPREAD_BPS
        ok_both = ok_both and ok
        detail.append((half, n_remove, nd_c, new_spread, ok))
    for half, n_remove, nd_c, new_spread, ok in detail:
        log(f"  -> {SIGNAL_LABEL[sig]}_k{k} h={h}min [{half}]: removidos {n_remove}/{nd_c} dias, "
            f"spread pos-remocao={new_spread:+.2f}bps -> {'ok' if ok else 'FALHA'}")
    if ok_both:
        g3_pass_combos.append((sig, k, h))

if not g2_pass_combos:
    log("  (nenhuma combinacao chegou a G3 -- G1 ou G2 ja falharam)")

log(f"\nG3: {'PASSOU' if g3_pass_combos else 'FALHOU'} "
    f"({len(g3_pass_combos)} combo(s) sobrevivem)")

log(f"\nG4 (acuracia classificacao >=70%): PASSOU na Fase 0a (80.85%) -- ja verificado")

# ---------------------------------------------------------------------------
# 9. maker_only_candidate (h=5, spread 2-6bps, IC excl 0)
# ---------------------------------------------------------------------------
log("\n" + "=" * 78)
log("maker_only_candidate (h=5min, |spread| entre 2 e 6 bps, IC95 excl. 0) "
    "-- informativo, NAO passa gate")
log("=" * 78)
# agrupa por (sig,k) exigindo em AMBAS as metades
maker_by_combo = {}
for sig, k, h, half, spread, lo, hi in maker_candidates:
    maker_by_combo.setdefault((sig, k, h), {})[half] = (spread, lo, hi)
any_maker = False
for (sig, k, h), halves in maker_by_combo.items():
    if "H1" in halves and "H2" in halves:
        any_maker = True
        s1, l1, h1_ = halves["H1"]
        s2, l2, h2_ = halves["H2"]
        log(f"  {SIGNAL_LABEL[sig]}_k{k} h={h}min: "
            f"H1 spread={s1:+.2f}bps IC=[{l1:+.2f},{h1_:+.2f}]  "
            f"H2 spread={s2:+.2f}bps IC=[{l2:+.2f},{h2_:+.2f}]")
if not any_maker:
    log("  nenhum combo qualifica em AMBAS as metades")

# ---------------------------------------------------------------------------
# 10. BREAKDOWN MANHA/TARDE -- combo(s) de G1, ou melhor combo se G1 falhou
# ---------------------------------------------------------------------------
log("\n" + "=" * 78)
log("BREAKDOWN MANHA (ate 12:59) vs TARDE (13:00+)")
log("=" * 78)

if g1_pass_combos:
    combos_for_breakdown = g1_pass_combos
    log("(combo(s) que passaram G1)")
else:
    # melhor combo = maior media de |spread| entre H1/H2 (so combos com ambas metades validas), h qualquer
    best = None
    best_score = -1
    for sig in SIGNALS:
        for k in KS:
            for h in HS:
                cs1 = combo_stats.get((sig, k, h, "H1"))
                cs2 = combo_stats.get((sig, k, h, "H2"))
                if cs1 is None or cs2 is None:
                    continue
                score = (abs(cs1["spread"]) + abs(cs2["spread"])) / 2
                if score > best_score:
                    best_score = score
                    best = (sig, k, h)
    combos_for_breakdown = [best] if best else []
    log(f"(nenhum combo passou G1 -- usando 'melhor combo' por maior media "
        f"de |spread| H1/H2: {SIGNAL_LABEL[best[0]]}_k{best[1]} h={best[2]}min, "
        f"score={best_score:.2f}bps)" if best else "(sem dados suficientes)")

for sig, k, h in combos_for_breakdown:
    sig_col = f"{sig}_{k}"
    fwd_col = f"fwd_ret_{h}"
    log(f"\n--- {SIGNAL_LABEL[sig]}_k{k} -> fwd_ret_{h}min ---")
    for half in ["H1", "H2"]:
        for part in ["manha", "tarde"]:
            sub = df_grid.loc[
                (df_grid["half"] == half) & (df_grid["session_part"] == part)
                & df_grid[sig_col].notna() & df_grid[fwd_col].notna(),
                [sig_col, fwd_col],
            ].copy()
            sub.columns = ["signal", "fwd_ret"]
            if len(sub) < 25 or sub["signal"].nunique() < 5:
                log(f"  [{half}/{part}] n={len(sub)} -- insuficiente")
                continue
            sub["quintile"] = pd.qcut(sub["signal"], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
            if sub["quintile"].nunique() < 5:
                log(f"  [{half}/{part}] n={len(sub)} -- quintis colapsaram, pulado")
                continue
            qs = sub.groupby("quintile", observed=True)["fwd_ret"].mean()
            spread_part = qs.loc[5] - qs.loc[1]
            log(f"  [{half}/{part}] n={len(sub)}  spread(Q5-Q1)={spread_part:+.2f}bps")

# ---------------------------------------------------------------------------
# 11. DECISAO MECANICA
# ---------------------------------------------------------------------------
log("\n" + "=" * 78)
log("DECISAO MECANICA")
log("=" * 78)
if g1_pass_combos and g2_pass_combos and g3_pass_combos:
    decision = "advance_to_phase1"
    log(f"G1+G2+G3 PASSARAM -> {decision}")
    log(f"combo(s) vencedor(es): " +
        ", ".join(f"{SIGNAL_LABEL[s]}_k{k}_h{h}" for s, k, h in g3_pass_combos))
else:
    decision = "premise_refuted"
    log(f"G1/G2/G3 NAO passaram completamente (G4 ja passou na 0a) -> {decision}")
    log(f"  G1: {'ok' if g1_pass_combos else 'falhou'} "
        f"({len(g1_pass_combos)} combo(s) candidatos)")
    log(f"  G2: {'ok' if g2_pass_combos else 'falhou'} "
        f"({len(g2_pass_combos)} combo(s))")
    log(f"  G3: {'ok' if g3_pass_combos else 'falhou'} "
        f"({len(g3_pass_combos)} combo(s))")

# ---------------------------------------------------------------------------
# 12. SALVAR SAIDAS
# ---------------------------------------------------------------------------
res_df = pd.DataFrame(results_rows)
res_df.to_csv(OUT_CSV, index=False, encoding="utf-8")

meta_out = {
    "n_tests_total": n_tests,
    "grid": f"{GRID_START}-{GRID_END} every {GRID_FREQ}",
    "holdout_excluded_rows": int(n_holdout_dropped),
    "holdout_excluded_days": int(n_days_holdout),
    "h1_days": int(n_h1),
    "h2_days": int(n_h2),
    "decision": decision,
    "g1_pass_combos": [f"{SIGNAL_LABEL[s]}_k{k}_h{h}" for s, k, h in g1_pass_combos],
    "g2_pass_combos": [f"{SIGNAL_LABEL[s]}_k{k}_h{h}" for s, k, h in g2_pass_combos],
    "g3_pass_combos": [f"{SIGNAL_LABEL[s]}_k{k}_h{h}" for s, k, h in g3_pass_combos],
    "samples_by_combo": meta,
}
with open(OUT_META, "w", encoding="utf-8") as f:
    json.dump(meta_out, f, indent=2, ensure_ascii=False)

with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"\n\nSaidas gravadas:\n  {OUT_SUMMARY}\n  {OUT_CSV}\n  {OUT_META}")
