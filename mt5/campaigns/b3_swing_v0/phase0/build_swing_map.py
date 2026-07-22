"""
b3_swing_v0 -- Fase 0 (mapa das teses S1-S4, ZERO trades simulados)
Pre-registro: mt5/campaigns/b3_swing_v0/PREREGISTRATION.md

Script auto-suficiente. So pandas/numpy/pyarrow. Roda com:
    python build_swing_map.py

Dados: series AJUSTADAS D1 (*_cont_ADJprop_D1.parquet), 1248 pregoes cada,
2021-07-19 -> 2026-07-17. Retorno diario = close/close_prev - 1 (sempre da
ajustada -- a $N executavel tem gaps de rolagem e NAO e usada aqui).

OOS 2025-01-01+ e removido logo apos o load, antes de qualquer estatistica.
Splits: DISCOVERY = tudo ate 2024-12-31. H1 = ate 2023-03-31.
H2 = 2023-04-01 .. 2024-12-31.

Decisao metodologica (pre-registro nao especifica o mecanismo exato de
particionamento H1/H2 para S2/S3 -- documentado aqui para transparencia,
no mesmo espirito de t3_b3_ny_open_v0/build_t3_map.py::cond_spread_ci):
  A grade nao-sobreposta (a cada F pregoes) de S2/S3 e construida UMA VEZ
  sobre o periodo DISCOVERY inteiro (continuo). O teste principal (gate de
  magnitude + IC) roda sobre essa grade completa. Os subconjuntos H1/H2
  (para o check "mesmo sinal") sao obtidos filtrando as linhas da MESMA
  grade pela data de formacao do estado (nao se reconstroi grade separada
  por metade -- evita efeitos de borda de restart do contador F). G3
  (remocao top 5% |contribuicao|) e aplicado sobre a grade DISCOVERY
  completa, nao replicado por metade (nao pre-registrado por metade).
  Para S3, os quintis usados no teste DISCOVERY sao calculados nos dados do
  proprio DISCOVERY; os quintis usados nos checks H1/H2 sao recalculados
  dentro de cada metade ("thresholds por split", conforme instrucao).

Bootstrap: 1000 reamostragens, seed=42, refeito do zero (mesma seed) a
cada chamada -- convencao ja usada em build_t3_map.py.
"""

import os
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

DATA_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
PATHS = {
    "WIN": os.path.join(DATA_DIR, "WIN_cont_ADJprop_D1.parquet"),
    "WDO": os.path.join(DATA_DIR, "WDO_cont_ADJprop_D1.parquet"),
}
OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v0\phase0"

OOS_CUTOFF = pd.Timestamp("2025-01-01")     # NAO TOCAR em 2025+
H1_END = pd.Timestamp("2023-03-31")
DISCOVERY_END = pd.Timestamp("2024-12-31")

SEED = 42
N_BOOT = 1000

MARKETS = ["WIN", "WDO"]

S2_LS = [21, 63, 126, 252]
S2_FS = [5, 21]
S3_PS = [3, 5]
S3_FS = [1, 3, 5]

WEEKDAY_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

RESULT_ROWS = []   # linhas para swing_map_results.csv


def add_row(thesis, market, split, param_a_name="", param_a="", param_b_name="",
            param_b="", metric="", point=np.nan, lo=np.nan, hi=np.nan, n=np.nan, note=""):
    RESULT_ROWS.append(dict(
        thesis=thesis, market=market, split=split,
        param_a_name=param_a_name, param_a=param_a,
        param_b_name=param_b_name, param_b=param_b,
        metric=metric,
        point_bps=None if pd.isna(point) else round(float(point), 4),
        ci_lo_bps=None if pd.isna(lo) else round(float(lo), 4),
        ci_hi_bps=None if pd.isna(hi) else round(float(hi), 4),
        n=None if pd.isna(n) else int(n),
        note=note,
    ))


# ----------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------

def load_market(path):
    df = pd.read_parquet(path)
    n_raw = len(df)
    df = df[["datetime_b3", "close"]].copy()
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    df = df[df["datetime_b3"] < OOS_CUTOFF].copy().reset_index(drop=True)
    n_oos_dropped = n_raw - len(df)

    wd = df["datetime_b3"].dt.weekday
    n_weekend = int((wd >= 5).sum())
    if n_weekend:
        df = df[wd < 5].reset_index(drop=True)

    df["year"] = df["datetime_b3"].dt.year
    df["month"] = df["datetime_b3"].dt.month
    df["weekday"] = df["datetime_b3"].dt.weekday
    df["ret"] = df["close"] / df["close"].shift(1) - 1.0
    df["ret_bps"] = df["ret"] * 10000.0

    def split_label(ts):
        if ts <= H1_END:
            return "H1"
        elif ts <= DISCOVERY_END:
            return "H2"
        return None

    df["split"] = df["datetime_b3"].apply(split_label)
    return df, n_raw, n_oos_dropped, n_weekend


# ----------------------------------------------------------------------
# Stat helpers
# ----------------------------------------------------------------------

def mean_ci(values, n_boot=N_BOOT, seed=SEED):
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    n = len(v)
    if n == 0:
        return dict(point=np.nan, lo=np.nan, hi=np.nan, n=0)
    point = float(v.mean())
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boots[i] = v[idx].mean()
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return dict(point=point, lo=float(lo), hi=float(hi), n=n)


def bootstrap_group_spread(vals_a, vals_b, n_boot=N_BOOT, seed=SEED):
    """spread = mean(a) - mean(b); bootstrap reamostra cada grupo
    independentemente (grupos ja definidos, ex: estado up vs down)."""
    a = np.asarray(vals_a, dtype=float)
    a = a[~np.isnan(a)]
    b = np.asarray(vals_b, dtype=float)
    b = b[~np.isnan(b)]
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return dict(point=np.nan, lo=np.nan, hi=np.nan, n_a=na, n_b=nb,
                     mean_a=np.nan, mean_b=np.nan)
    mean_a, mean_b = float(a.mean()), float(b.mean())
    point = mean_a - mean_b
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        boots[i] = (a[rng.integers(0, na, na)].mean() -
                    b[rng.integers(0, nb, nb)].mean())
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return dict(point=point, lo=float(lo), hi=float(hi), n_a=na, n_b=nb,
                mean_a=mean_a, mean_b=mean_b)


def trim_top_contrib_spread(vals_a, vals_b, frac=0.05, n_boot=N_BOOT, seed=SEED):
    """G3: remove os `frac` (5%) de maior |contribuicao| do pool a+b, onde
    contribuicao_i = val_i/n_a (grupo a) ou -val_i/n_b (grupo b) -- reflete
    o peso do dia individual no spread = mean(a)-mean(b). Recalcula o
    spread no restante."""
    a = np.asarray(vals_a, dtype=float)
    a = a[~np.isnan(a)]
    b = np.asarray(vals_b, dtype=float)
    b = b[~np.isnan(b)]
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return dict(point=np.nan, lo=np.nan, hi=np.nan, n_a=na, n_b=nb, n_removed=0)
    contrib_a = a / na
    contrib_b = -b / nb
    contrib = np.concatenate([contrib_a, contrib_b])
    tag = np.array(["a"] * na + ["b"] * nb)
    val = np.concatenate([a, b])
    n_pool = len(contrib)
    n_remove = int(round(frac * n_pool))
    order = np.argsort(-np.abs(contrib))
    remove_idx = set(order[:n_remove].tolist())
    keep = np.array([i not in remove_idx for i in range(n_pool)])
    a2 = val[keep & (tag == "a")]
    b2 = val[keep & (tag == "b")]
    r = bootstrap_group_spread(a2, b2, n_boot=n_boot, seed=seed)
    r["n_removed"] = n_remove
    return r


def same_sign(a, b):
    if pd.isna(a) or pd.isna(b):
        return False
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def build_grid_indices(n, lookback, fwd):
    if n - fwd <= lookback:
        return []
    return list(range(lookback, n - fwd, fwd))


def quintile_thresholds(values):
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    return np.percentile(v, [20, 40, 60, 80])


def quintile_assign(values, thr):
    return np.digitize(values, thr, right=True) + 1  # 1..5


# ----------------------------------------------------------------------
# S1 -- Carry
# ----------------------------------------------------------------------

def run_s1(market, df, log):
    log(f"\n{'='*78}\nS1 -- CARRY ESTRUTURAL [{market}]\n{'='*78}")
    disc = df[df["split"].isin(["H1", "H2"])]
    ret_disc = disc["ret_bps"].dropna()

    m_disc = mean_ci(ret_disc)
    log(f"DISCOVERY: media={m_disc['point']:.3f} bps/dia "
        f"IC95=[{m_disc['lo']:.3f},{m_disc['hi']:.3f}] n={m_disc['n']}")
    add_row("S1", market, "DISCOVERY", metric="mean_ret_bps_per_day",
            point=m_disc["point"], lo=m_disc["lo"], hi=m_disc["hi"], n=m_disc["n"])

    by_year = {}
    for yr, g in disc.groupby("year"):
        r = mean_ci(g["ret_bps"].dropna())
        by_year[int(yr)] = r
        log(f"  ano={yr} n={r['n']} media={r['point']:.3f} bps/dia "
            f"IC95=[{r['lo']:.3f},{r['hi']:.3f}]")
        add_row("S1", market, f"year_{yr}", metric="mean_ret_bps_per_day",
                point=r["point"], lo=r["lo"], hi=r["hi"], n=r["n"])

    m_h1 = mean_ci(df[df["split"] == "H1"]["ret_bps"].dropna())
    m_h2 = mean_ci(df[df["split"] == "H2"]["ret_bps"].dropna())
    log(f"H1: media={m_h1['point']:.3f} bps/dia IC95=[{m_h1['lo']:.3f},{m_h1['hi']:.3f}] n={m_h1['n']}")
    log(f"H2: media={m_h2['point']:.3f} bps/dia IC95=[{m_h2['lo']:.3f},{m_h2['hi']:.3f}] n={m_h2['n']}")
    add_row("S1", market, "H1", metric="mean_ret_bps_per_day",
            point=m_h1["point"], lo=m_h1["lo"], hi=m_h1["hi"], n=m_h1["n"])
    add_row("S1", market, "H2", metric="mean_ret_bps_per_day",
            point=m_h2["point"], lo=m_h2["lo"], hi=m_h2["hi"], n=m_h2["n"])

    skew = float(ret_disc.skew())
    log(f"skewness (DISCOVERY, ret bps): {skew:.4f}")

    sorted_ret = disc[["datetime_b3", "ret_bps"]].dropna().sort_values("ret_bps")
    worst5 = sorted_ret.head(5)
    best5 = sorted_ret.tail(5).iloc[::-1]
    log("5 piores dias (ret mais negativo):")
    for _, row in worst5.iterrows():
        log(f"    {row['datetime_b3'].date()}  {row['ret_bps']:.1f} bps")
    log("5 melhores dias (ret mais positivo):")
    for _, row in best5.iterrows():
        log(f"    {row['datetime_b3'].date()}  {row['ret_bps']:.1f} bps")

    gate_mag = m_disc["point"] <= -1.5
    gate_ci = (pd.notna(m_disc["lo"]) and pd.notna(m_disc["hi"]) and
               not (m_disc["lo"] <= 0 <= m_disc["hi"]))
    gate_sign = same_sign(m_h1["point"], m_h2["point"])
    gate_pass = bool(gate_mag and gate_ci and gate_sign)

    log(f"GATE S1 [{market}]: media<=-1.5bps={gate_mag} IC_excl_0={gate_ci} "
        f"mesmo_sinal_H1H2={gate_sign} -> PASS={gate_pass}")

    return dict(mean_discovery=m_disc, mean_h1=m_h1, mean_h2=m_h2, skew=skew,
                gate_mag=bool(gate_mag), gate_ci=bool(gate_ci), gate_sign=bool(gate_sign),
                pass_=gate_pass)


# ----------------------------------------------------------------------
# S2 -- TSM
# ----------------------------------------------------------------------

def run_s2(market, df, log):
    log(f"\n{'='*78}\nS2 -- TIME-SERIES MOMENTUM [{market}]\n{'='*78}")
    disc = df[df["split"].isin(["H1", "H2"])].reset_index(drop=True)
    close = disc["close"].to_numpy()
    split_arr = disc["split"].to_numpy()
    dates = disc["datetime_b3"].to_numpy()
    n = len(disc)

    combo_results = {}
    any_pass = False

    for L in S2_LS:
        for F in S2_FS:
            idxs = build_grid_indices(n, L, F)
            if len(idxs) < 20:
                log(f"L={L} F={F}: grid n={len(idxs)} (<20, pulado)")
                combo_results[(L, F)] = dict(skip=True, n_grid=len(idxs))
                continue

            state_sign = np.empty(len(idxs))
            forward = np.empty(len(idxs))
            row_split = np.empty(len(idxs), dtype=object)
            for k, i in enumerate(idxs):
                state_val = close[i] / close[i - L] - 1.0
                state_sign[k] = np.sign(state_val)
                forward[k] = (close[i + F] / close[i] - 1.0) / F * 10000.0
                row_split[k] = split_arr[i]

            mask_nz = state_sign != 0
            up = forward[mask_nz & (state_sign > 0)]
            down = forward[mask_nz & (state_sign < 0)]

            spread = bootstrap_group_spread(up, down)
            log(f"L={L:>3} F={F:>2} n_grid={len(idxs):>4} n_up={spread['n_a']:>4} "
                f"n_down={spread['n_b']:>4} | up_mean={spread['mean_a']:.3f} "
                f"down_mean={spread['mean_b']:.3f} spread={spread['point']:.3f} bps/dia "
                f"IC95=[{spread['lo']:.3f},{spread['hi']:.3f}]")
            add_row("S2", market, "DISCOVERY", "L", L, "F", F,
                    metric="spread_up_minus_down_bps_per_day",
                    point=spread["point"], lo=spread["lo"], hi=spread["hi"],
                    n=spread["n_a"] + spread["n_b"])

            # H1 / H2 subsets (mesma grade, filtrada por data de formacao)
            sub_h1_mask = mask_nz & (row_split == "H1")
            sub_h2_mask = mask_nz & (row_split == "H2")
            up_h1 = forward[sub_h1_mask & (state_sign > 0)]
            down_h1 = forward[sub_h1_mask & (state_sign < 0)]
            up_h2 = forward[sub_h2_mask & (state_sign > 0)]
            down_h2 = forward[sub_h2_mask & (state_sign < 0)]
            spread_h1 = bootstrap_group_spread(up_h1, down_h1)
            spread_h2 = bootstrap_group_spread(up_h2, down_h2)
            log(f"         H1: n_up={spread_h1['n_a']} n_down={spread_h1['n_b']} "
                f"spread={spread_h1['point']:.3f} bps/dia  |  "
                f"H2: n_up={spread_h2['n_a']} n_down={spread_h2['n_b']} "
                f"spread={spread_h2['point']:.3f} bps/dia")
            add_row("S2", market, "H1", "L", L, "F", F,
                    metric="spread_up_minus_down_bps_per_day",
                    point=spread_h1["point"], lo=spread_h1["lo"], hi=spread_h1["hi"],
                    n=spread_h1["n_a"] + spread_h1["n_b"])
            add_row("S2", market, "H2", "L", L, "F", F,
                    metric="spread_up_minus_down_bps_per_day",
                    point=spread_h2["point"], lo=spread_h2["lo"], hi=spread_h2["hi"],
                    n=spread_h2["n_a"] + spread_h2["n_b"])

            g3 = trim_top_contrib_spread(up, down)
            log(f"         G3 (remove top5% |contrib|, n_removed={g3['n_removed']}): "
                f"spread={g3['point']:.3f} bps/dia IC95=[{g3['lo']:.3f},{g3['hi']:.3f}]")
            add_row("S2", market, "DISCOVERY_G3", "L", L, "F", F,
                    metric="spread_up_minus_down_bps_per_day_g3",
                    point=g3["point"], lo=g3["lo"], hi=g3["hi"],
                    n=g3["n_a"] + g3["n_b"], note=f"n_removed={g3['n_removed']}")

            gate_mag = abs(spread["point"]) >= 2.0
            gate_ci = (pd.notna(spread["lo"]) and pd.notna(spread["hi"]) and
                       not (spread["lo"] <= 0 <= spread["hi"]))
            gate_sign = same_sign(spread_h1["point"], spread_h2["point"])
            gate_g3 = pd.notna(g3["point"]) and abs(g3["point"]) >= 2.0
            combo_pass = bool(gate_mag and gate_ci and gate_sign and gate_g3)
            if combo_pass:
                any_pass = True

            log(f"         GATE: mag>=2bps={gate_mag} IC_excl_0={gate_ci} "
                f"mesmo_sinal_H1H2={gate_sign} G3>=2bps={gate_g3} -> PASS={combo_pass}")

            combo_results[(L, F)] = dict(
                skip=False, spread=spread, spread_h1=spread_h1, spread_h2=spread_h2,
                g3=g3, gate_mag=bool(gate_mag), gate_ci=bool(gate_ci),
                gate_sign=bool(gate_sign), gate_g3=bool(gate_g3), pass_=combo_pass,
            )

    log(f"\nS2 [{market}]: any_combo_pass={any_pass}")
    return dict(combos=combo_results, pass_=any_pass)


# ----------------------------------------------------------------------
# S3 -- Reversao
# ----------------------------------------------------------------------

def run_s3(market, df, log):
    log(f"\n{'='*78}\nS3 -- REVERSAO CURTO PRAZO [{market}]\n{'='*78}")
    disc = df[df["split"].isin(["H1", "H2"])].reset_index(drop=True)
    close = disc["close"].to_numpy()
    split_arr = disc["split"].to_numpy()
    n = len(disc)

    combo_results = {}
    any_pass = False

    for P in S3_PS:
        for F in S3_FS:
            idxs = build_grid_indices(n, P, F)
            if len(idxs) < 20:
                log(f"P={P} F={F}: grid n={len(idxs)} (<20, pulado)")
                combo_results[(P, F)] = dict(skip=True, n_grid=len(idxs))
                continue

            past = np.empty(len(idxs))
            forward = np.empty(len(idxs))
            row_split = np.empty(len(idxs), dtype=object)
            for k, i in enumerate(idxs):
                past[k] = close[i] / close[i - P] - 1.0
                forward[k] = (close[i + F] / close[i] - 1.0) / F * 10000.0
                row_split[k] = split_arr[i]

            thr = quintile_thresholds(past)
            q = quintile_assign(past, thr)
            q1 = forward[q == 1]
            q5 = forward[q == 5]
            spread = bootstrap_group_spread(q1, q5)
            log(f"P={P} F={F} n_grid={len(idxs):>4} n_Q1={spread['n_a']:>4} "
                f"n_Q5={spread['n_b']:>4} | Q1_mean={spread['mean_a']:.3f} "
                f"Q5_mean={spread['mean_b']:.3f} spread(Q1-Q5)={spread['point']:.3f} "
                f"bps/dia IC95=[{spread['lo']:.3f},{spread['hi']:.3f}]")
            add_row("S3", market, "DISCOVERY", "P", P, "F", F,
                    metric="spread_Q1_minus_Q5_bps_per_day",
                    point=spread["point"], lo=spread["lo"], hi=spread["hi"],
                    n=spread["n_a"] + spread["n_b"])

            # H1/H2: thresholds recalculados dentro de cada metade
            spreads_half = {}
            for half in ["H1", "H2"]:
                mask = row_split == half
                past_h = past[mask]
                fwd_h = forward[mask]
                if len(past_h) < 15:
                    spreads_half[half] = dict(point=np.nan, lo=np.nan, hi=np.nan,
                                               n_a=0, n_b=0, mean_a=np.nan, mean_b=np.nan)
                    continue
                thr_h = quintile_thresholds(past_h)
                q_h = quintile_assign(past_h, thr_h)
                sp = bootstrap_group_spread(fwd_h[q_h == 1], fwd_h[q_h == 5])
                spreads_half[half] = sp
                add_row("S3", market, half, "P", P, "F", F,
                        metric="spread_Q1_minus_Q5_bps_per_day",
                        point=sp["point"], lo=sp["lo"], hi=sp["hi"],
                        n=sp["n_a"] + sp["n_b"])

            sh1, sh2 = spreads_half["H1"], spreads_half["H2"]
            log(f"         H1: n_Q1={sh1['n_a']} n_Q5={sh1['n_b']} spread={sh1['point']:.3f} "
                f"bps/dia  |  H2: n_Q1={sh2['n_a']} n_Q5={sh2['n_b']} spread={sh2['point']:.3f} bps/dia")

            g3 = trim_top_contrib_spread(q1, q5)
            log(f"         G3 (remove top5% |contrib|, n_removed={g3['n_removed']}): "
                f"spread={g3['point']:.3f} bps/dia IC95=[{g3['lo']:.3f},{g3['hi']:.3f}]")
            add_row("S3", market, "DISCOVERY_G3", "P", P, "F", F,
                    metric="spread_Q1_minus_Q5_bps_per_day_g3",
                    point=g3["point"], lo=g3["lo"], hi=g3["hi"],
                    n=g3["n_a"] + g3["n_b"], note=f"n_removed={g3['n_removed']}")

            gate_mag = pd.notna(spread["point"]) and abs(spread["point"]) >= 2.0
            gate_ci = (pd.notna(spread["lo"]) and pd.notna(spread["hi"]) and
                       not (spread["lo"] <= 0 <= spread["hi"]))
            gate_sign = same_sign(sh1["point"], sh2["point"])
            gate_g3 = pd.notna(g3["point"]) and abs(g3["point"]) >= 2.0
            combo_pass = bool(gate_mag and gate_ci and gate_sign and gate_g3)
            if combo_pass:
                any_pass = True

            log(f"         GATE: mag>=2bps={gate_mag} IC_excl_0={gate_ci} "
                f"mesmo_sinal_H1H2={gate_sign} G3>=2bps={gate_g3} -> PASS={combo_pass}")

            combo_results[(P, F)] = dict(
                skip=False, spread=spread, spread_h1=sh1, spread_h2=sh2,
                g3=g3, gate_mag=bool(gate_mag), gate_ci=bool(gate_ci),
                gate_sign=bool(gate_sign), gate_g3=bool(gate_g3), pass_=combo_pass,
            )

    log(f"\nS3 [{market}]: any_combo_pass={any_pass}")
    return dict(combos=combo_results, pass_=any_pass)


# ----------------------------------------------------------------------
# S4 -- Calendario (TOM + DOW)
# ----------------------------------------------------------------------

def run_s4_tom(market, df, log):
    log(f"\n{'='*78}\nS4-TOM -- TURN OF MONTH [{market}]\n{'='*78}")
    disc = df[df["split"].isin(["H1", "H2"])].copy()
    disc = disc.dropna(subset=["ret_bps"]).reset_index(drop=True)

    grp = disc.groupby(["year", "month"])
    disc["tdom"] = grp.cumcount() + 1
    disc["month_size"] = grp["ret_bps"].transform("size")
    disc["tom"] = (disc["tdom"] <= 3) | (disc["tdom"] == disc["month_size"])

    n_tom = int(disc["tom"].sum())
    n_nontom = int((~disc["tom"]).sum())
    log(f"dias TOM={n_tom} dias nao-TOM={n_nontom} (total={len(disc)})")

    sp_disc = bootstrap_group_spread(disc.loc[disc["tom"], "ret_bps"],
                                      disc.loc[~disc["tom"], "ret_bps"])
    log(f"DISCOVERY: mean_TOM={sp_disc['mean_a']:.3f} mean_nonTOM={sp_disc['mean_b']:.3f} "
        f"diff={sp_disc['point']:.3f} bps/dia IC95=[{sp_disc['lo']:.3f},{sp_disc['hi']:.3f}]")
    add_row("S4_TOM", market, "DISCOVERY", metric="diff_tom_minus_nontom_bps",
            point=sp_disc["point"], lo=sp_disc["lo"], hi=sp_disc["hi"],
            n=sp_disc["n_a"] + sp_disc["n_b"])

    halves = {}
    for half in ["H1", "H2"]:
        sub = disc[disc["split"] == half]
        sp = bootstrap_group_spread(sub.loc[sub["tom"], "ret_bps"],
                                     sub.loc[~sub["tom"], "ret_bps"])
        halves[half] = sp
        log(f"{half}: mean_TOM={sp['mean_a']:.3f} mean_nonTOM={sp['mean_b']:.3f} "
            f"diff={sp['point']:.3f} bps/dia IC95=[{sp['lo']:.3f},{sp['hi']:.3f}] "
            f"n_tom={sp['n_a']} n_nontom={sp['n_b']}")
        add_row("S4_TOM", market, half, metric="diff_tom_minus_nontom_bps",
                point=sp["point"], lo=sp["lo"], hi=sp["hi"], n=sp["n_a"] + sp["n_b"])

    gate_mag = pd.notna(sp_disc["point"]) and sp_disc["point"] >= 5.0
    gate_ci = (pd.notna(sp_disc["lo"]) and pd.notna(sp_disc["hi"]) and
               not (sp_disc["lo"] <= 0 <= sp_disc["hi"]))
    gate_sign = (same_sign(halves["H1"]["point"], halves["H2"]["point"]) and
                 same_sign(halves["H1"]["point"], sp_disc["point"]))
    gate_pass = bool(gate_mag and gate_ci and gate_sign)
    log(f"GATE S4-TOM [{market}]: diff>=5bps={gate_mag} IC_excl_0={gate_ci} "
        f"mesmo_sinal_H1H2={gate_sign} -> PASS={gate_pass}")

    return dict(discovery=sp_disc, h1=halves["H1"], h2=halves["H2"],
                gate_mag=bool(gate_mag), gate_ci=bool(gate_ci),
                gate_sign=bool(gate_sign), pass_=gate_pass)


def run_s4_dow(market, df, log):
    log(f"\n{'='*78}\nS4-DOW -- DIA DA SEMANA [{market}]\n{'='*78}")
    disc = df[df["split"].isin(["H1", "H2"])].dropna(subset=["ret_bps"])

    by_wd_disc = {}
    for wd, g in disc.groupby("weekday"):
        name = WEEKDAY_NAMES.get(wd, str(wd))
        r = mean_ci(g["ret_bps"])
        by_wd_disc[wd] = r
        log(f"  {name}: n={r['n']} media={r['point']:.3f} bps IC95=[{r['lo']:.3f},{r['hi']:.3f}]")
        add_row("S4_DOW", market, "DISCOVERY", "weekday", name, metric="mean_ret_bps",
                point=r["point"], lo=r["lo"], hi=r["hi"], n=r["n"])

    best_wd = max(by_wd_disc, key=lambda k: by_wd_disc[k]["point"])
    worst_wd = min(by_wd_disc, key=lambda k: by_wd_disc[k]["point"])
    log(f"melhor dia (DISCOVERY) = {WEEKDAY_NAMES[best_wd]} "
        f"({by_wd_disc[best_wd]['point']:.3f} bps); "
        f"pior dia = {WEEKDAY_NAMES[worst_wd]} ({by_wd_disc[worst_wd]['point']:.3f} bps)")

    best_vals = disc.loc[disc["weekday"] == best_wd, "ret_bps"]
    worst_vals = disc.loc[disc["weekday"] == worst_wd, "ret_bps"]
    spread = bootstrap_group_spread(best_vals, worst_vals)
    log(f"spread(melhor-pior) DISCOVERY = {spread['point']:.3f} bps "
        f"IC95=[{spread['lo']:.3f},{spread['hi']:.3f}]")
    add_row("S4_DOW", market, "DISCOVERY", "spread", f"{WEEKDAY_NAMES[best_wd]}-{WEEKDAY_NAMES[worst_wd]}",
            metric="spread_best_minus_worst_bps",
            point=spread["point"], lo=spread["lo"], hi=spread["hi"],
            n=spread["n_a"] + spread["n_b"])

    ci_best = by_wd_disc[best_wd]
    ci_worst = by_wd_disc[worst_wd]
    gate_ci_extremes = (not (ci_best["lo"] <= 0 <= ci_best["hi"]) and
                         not (ci_worst["lo"] <= 0 <= ci_worst["hi"]))
    gate_mag = abs(spread["point"]) >= 8.0

    # ranking por metade
    ranking = {}
    for half in ["H1", "H2"]:
        sub = disc[disc["split"] == half]
        means = {}
        for wd, g in sub.groupby("weekday"):
            means[wd] = float(g["ret_bps"].mean()) if len(g) > 0 else np.nan
        if not means:
            ranking[half] = (None, None)
            continue
        b = max(means, key=lambda k: means[k])
        w = min(means, key=lambda k: means[k])
        ranking[half] = (b, w)
        log(f"  {half}: melhor={WEEKDAY_NAMES.get(b,b)} ({means[b]:.3f} bps) "
            f"pior={WEEKDAY_NAMES.get(w,w)} ({means[w]:.3f} bps)")
        add_row("S4_DOW", market, half, "best_day", WEEKDAY_NAMES.get(b, str(b)),
                metric="mean_ret_bps", point=means[b], n=len(sub[sub["weekday"] == b]))
        add_row("S4_DOW", market, half, "worst_day", WEEKDAY_NAMES.get(w, str(w)),
                metric="mean_ret_bps", point=means[w], n=len(sub[sub["weekday"] == w]))

    gate_ranking = (ranking["H1"][0] is not None and ranking["H2"][0] is not None and
                     ranking["H1"][0] == ranking["H2"][0] and
                     ranking["H1"][1] == ranking["H2"][1])

    gate_pass = bool(gate_mag and gate_ci_extremes and gate_ranking)
    log(f"GATE S4-DOW [{market}]: |spread|>=8bps={gate_mag} "
        f"IC_excl_0_ambos_extremos={gate_ci_extremes} mesmo_ranking_H1H2={gate_ranking} "
        f"-> PASS={gate_pass}")

    return dict(by_weekday=by_wd_disc, best=best_wd, worst=worst_wd, spread=spread,
                ranking=ranking, gate_mag=bool(gate_mag),
                gate_ci_extremes=bool(gate_ci_extremes),
                gate_ranking=bool(gate_ranking), pass_=gate_pass)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    summary_lines = []

    def slog(s=""):
        print(s)
        summary_lines.append(s)

    slog("b3_swing_v0 -- Fase 0 (mapa das teses S1-S4)")
    slog(f"seed={SEED} n_boot={N_BOOT} OOS_CUTOFF={OOS_CUTOFF.date()} "
         f"H1_END={H1_END.date()} DISCOVERY_END={DISCOVERY_END.date()}")
    slog("")

    all_results = {}

    for market in MARKETS:
        slog("#" * 78)
        slog(f"# MERCADO: {market}")
        slog("#" * 78)
        path = PATHS[market]
        df, n_raw, n_oos, n_wknd = load_market(path)
        slog(f"arquivo: {path}")
        slog(f"linhas brutas={n_raw} removidas_OOS(>=2025-01-01)={n_oos} "
             f"removidas_fim_de_semana={n_wknd}")
        slog(f"range apos filtro: {df['datetime_b3'].min().date()} -> {df['datetime_b3'].max().date()}")
        n_disc = int(df["split"].isin(["H1", "H2"]).sum())
        n_h1 = int((df["split"] == "H1").sum())
        n_h2 = int((df["split"] == "H2").sum())
        slog(f"DISCOVERY n={n_disc} (H1 n={n_h1}, H2 n={n_h2})")

        s1 = run_s1(market, df, slog)
        s2 = run_s2(market, df, slog)
        s3 = run_s3(market, df, slog)
        s4tom = run_s4_tom(market, df, slog)
        s4dow = run_s4_dow(market, df, slog)

        all_results[market] = dict(s1=s1, s2=s2, s3=s3, s4tom=s4tom, s4dow=s4dow)
        slog("")

    # ------------------------------------------------------------------
    # Tabela-resumo de gates
    # ------------------------------------------------------------------
    slog("=" * 78)
    slog("TABELA-RESUMO DE GATES (por tese x mercado)")
    slog("=" * 78)

    decisions = {}
    for market in MARKETS:
        r = all_results[market]
        slog(f"\n--- {market} ---")

        s1p = r["s1"]["pass_"]
        slog(f"S1  (carry):        mag={r['s1']['gate_mag']} ci={r['s1']['gate_ci']} "
             f"sign_H1H2={r['s1']['gate_sign']}  -> PASS={s1p}")

        s2p = r["s2"]["pass_"]
        n_s2_combos = len(r["s2"]["combos"])
        n_s2_pass = sum(1 for c in r["s2"]["combos"].values() if not c.get("skip") and c.get("pass_"))
        slog(f"S2  (TSM):          {n_s2_pass}/{n_s2_combos} combos (L,F) passaram -> PASS={s2p}")

        s3p = r["s3"]["pass_"]
        n_s3_combos = len(r["s3"]["combos"])
        n_s3_pass = sum(1 for c in r["s3"]["combos"].values() if not c.get("skip") and c.get("pass_"))
        slog(f"S3  (reversao):     {n_s3_pass}/{n_s3_combos} combos (P,F) passaram -> PASS={s3p}")

        s4tp = r["s4tom"]["pass_"]
        slog(f"S4-TOM:             mag={r['s4tom']['gate_mag']} ci={r['s4tom']['gate_ci']} "
             f"sign_H1H2={r['s4tom']['gate_sign']}  -> PASS={s4tp}")

        s4dp = r["s4dow"]["pass_"]
        slog(f"S4-DOW:             mag={r['s4dow']['gate_mag']} "
             f"ci_extremos={r['s4dow']['gate_ci_extremes']} "
             f"ranking_H1H2={r['s4dow']['gate_ranking']}  -> PASS={s4dp}")

        decisions[market] = dict(S1=bool(s1p), S2=bool(s2p), S3=bool(s3p),
                                  S4_TOM=bool(s4tp), S4_DOW=bool(s4dp))

    # ------------------------------------------------------------------
    # Contagem de testes / falsos positivos esperados
    # ------------------------------------------------------------------
    n_tests_s1 = len(MARKETS)                              # 1 gate/mercado
    n_tests_s2 = sum(len([c for c in all_results[m]["s2"]["combos"].values() if not c.get("skip")])
                      for m in MARKETS)
    n_tests_s3 = sum(len([c for c in all_results[m]["s3"]["combos"].values() if not c.get("skip")])
                      for m in MARKETS)
    n_tests_s4tom = len(MARKETS)
    n_tests_s4dow = len(MARKETS)
    n_tests_total = n_tests_s1 + n_tests_s2 + n_tests_s3 + n_tests_s4tom + n_tests_s4dow
    expected_fp = n_tests_total * 0.05

    slog("\n" + "=" * 78)
    slog("CONTAGEM DE TESTES E FALSOS POSITIVOS ESPERADOS")
    slog("=" * 78)
    slog(f"S1:      {n_tests_s1} testes (1 gate/mercado)")
    slog(f"S2:      {n_tests_s2} testes (combos L x F validos x mercado)")
    slog(f"S3:      {n_tests_s3} testes (combos P x F validos x mercado)")
    slog(f"S4-TOM:  {n_tests_s4tom} testes (1 gate/mercado)")
    slog(f"S4-DOW:  {n_tests_s4dow} testes (1 gate/mercado)")
    slog(f"TOTAL:   {n_tests_total} testes com IC")
    slog(f"Falsos positivos esperados a 5% (sem nenhum efeito real): "
         f"~{expected_fp:.1f} (arredondando, {round(expected_fp)})")
    slog("NOTA: S2 e S3 usam regra 'passa se >=1 combo passar' -- design "
         "vulneravel a multiple comparisons. Qualquer PASS isolado nessas "
         "duas teses deve ser lido com ceticismo adicional proporcional ao "
         "numero de combos testados, nao como confirmacao definitiva.")

    # ------------------------------------------------------------------
    # Decisao mecanica por tese (agregando os 2 mercados)
    # ------------------------------------------------------------------
    slog("\n" + "=" * 78)
    slog("DECISAO MECANICA POR TESE")
    slog("=" * 78)

    thesis_decision = {}
    for thesis in ["S1", "S2", "S3", "S4_TOM", "S4_DOW"]:
        passed_markets = [m for m in MARKETS if decisions[m][thesis]]
        if passed_markets:
            thesis_decision[thesis] = f"ADVANCE_TO_PHASE1 (mercado(s): {', '.join(passed_markets)})"
        else:
            thesis_decision[thesis] = "PREMISE_REFUTED (nenhum mercado passou o gate)"
        slog(f"{thesis}: {thesis_decision[thesis]}")

    any_advance = any("ADVANCE" in v for v in thesis_decision.values())
    overall = ("ADVANCE_TO_PHASE1 (>=1 tese com >=1 mercado passando todos os gates)"
               if any_advance else
               "PREMISE_REFUTED (nenhuma tese passou o gate em nenhum mercado -- fechar campanha como premise_refuted, sem post-hoc)")
    slog(f"\nDECISAO GERAL DA CAMPANHA: {overall}")

    # ------------------------------------------------------------------
    # Salvar outputs
    # ------------------------------------------------------------------
    results_df = pd.DataFrame(RESULT_ROWS)
    results_df.to_csv(os.path.join(OUT_DIR, "swing_map_results.csv"), index=False)
    slog(f"\nswing_map_results.csv: {len(results_df)} linhas salvas em {OUT_DIR}")

    with open(os.path.join(OUT_DIR, "SWING_MAP_SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    print(f"\nOutputs salvos em: {OUT_DIR}")


if __name__ == "__main__":
    main()
