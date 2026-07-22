"""
b3_swing_v1_proxy -- Fase 0 (mapa das teses S1-S4 sobre proxies publicas, 26 anos)
Pre-registro: mt5/campaigns/b3_swing_v1_proxy/PREREGISTRATION.md
Referencia de implementacao (mesma logica, splits diferentes):
    mt5/campaigns/b3_swing_v0/phase0/build_swing_map.py

Script auto-suficiente. So pandas/numpy/pyarrow. Roda com:
    python build_proxy_map.py

Dados:
  1. proxy_raw.parquet (date, ibov, usdbrl, cdi_ad %ao dia, ffr_aa %ao ano),
     2000-01-03 -> hoje. Cortado em 2024-12-31 IMEDIATAMENTE apos o load --
     nada além dessa data entra em qualquer estatistica (nem discovery, nem
     confirm, nem qualquer serie derivada).
       win_proxy_ret = ibov.pct_change() - cdi_ad/100
       ffr_ad        = (1+ffr_aa/100)^(1/252) - 1
       wdo_proxy_ret = usdbrl.pct_change() - (cdi_ad/100 - ffr_ad)
     Preco sintetico p/ S2/S3: P_t = cumprod(1+ret) por mercado, P_0 = 1.0.

  2. Futuros reais ADJprop D1 (WIN, WDO) -- REAL-CHECK apenas, filtrado
     IMEDIATAMENTE apos o load para 2021-07-19 -> 2024-12-31. 2025+ NUNCA
     e lido para dentro de nenhuma variavel usada em calculo.

Splits (proxy):
  DISCOVERY = 2000-01-01 -> 2018-12-31 (H1 2000-2009, H2 2010-2018)
  CONFIRM   = 2019-01-01 -> 2024-12-31
  REAL-CHECK (futuros reais) = 2021-07-19 -> 2024-12-31 (dado independente,
  nao e "novo OOS": e a mesma janela ja usada na v0. So sinal, sem exigencia
  de IC (n pequeno)).
  OOS 2025+ dos futuros reais: INTOCADO -- nem carregado neste script.

Sequencia de avaliacao por combo (cascata, conforme instrucao da tarefa):
  (1) DISCOVERY passa magnitude + IC excl-0 + mesmo sinal H1/H2?
  (2) se sim -> CONFIRM: mesmo sinal (+ magnitude minima quando
      pre-registrada: S2/S3 exigem >=1 bps/dia; S1/S4 exigem so sinal)?
  (3) se sim -> G3 (remove 5% |contribuicao| maior) no DISCOVERY, mesmo
      threshold de magnitude do gate de discovery?
  (4) se sim -> REAL-CHECK: mesmo sinal no futuro real 2021-07->2024-12
      (grade nao-sobreposta quando aplicavel; so sinal, sem exigencia de IC).
  Combo so avanca (ADVANCE_TO_PHASE1 candidato) se passar as 4 etapas.
  Se falha em qualquer etapa, as etapas seguintes NAO sao avaliadas
  (marcadas NOT_EVALUATED) -- decisao mecanica, sem burlar a cascata.

Decisoes metodologicas documentadas aqui (pre-registro nao especifica o
mecanismo exato -- mesmo espirito de transparencia do build_swing_map.py):
  - Grade nao-sobreposta de S2/S3 e construida UMA VEZ por periodo
    (DISCOVERY inteiro, CONFIRM inteiro, REAL-CHECK inteiro), cada uma
    com lookback contido dentro do proprio periodo (sem vazar barras de
    um periodo pro outro). H1/H2 sao SUBCONJUNTOS da grade DISCOVERY
    (filtrados pela data de formacao do estado), nao grades separadas.
  - G3 e aplicado sempre sobre a grade/pool DISCOVERY completa (nao
    replicado por metade, nao pre-registrado por metade).
  - Quintis (S3): thresholds calculados dentro de cada split usado no
    teste daquele split (DISCOVERY inteiro p/ teste DISCOVERY, cada
    metade p/ H1/H2, CONFIRM inteiro p/ CONFIRM, REAL-CHECK inteiro p/
    REAL-CHECK) -- "thresholds por split" conforme instrucao da tarefa.
  - S4-DOW: o dia "melhor" e "pior" sao fixados no DISCOVERY. CONFIRM e
    REAL-CHECK reusam ESSES MESMOS dois dias da semana (nao re-elegem
    campeao) -- e assim que "confirmar o sinal" faz sentido estatistico
    (senao seria garantido achar *algum* par com sinal igual por acaso).
  - G3 para S1 e S4 (grupos unicos, nao spread a-vs-b): contribuicao_i =
    valor_i / n; remove-se os 5% de |contribuicao| mais alta do pool
    relevante (todas as obs DISCOVERY p/ S1; pool TOM+nonTOM ou
    melhor+pior-dia p/ S4) e recalcula-se a estatistica.

Bootstrap: 1000 reamostragens, seed=42, refeito do zero (mesma seed) a
cada chamada -- convencao ja usada em build_swing_map.py / build_t3_map.py.
"""

import os
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

BASE_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy"
PROXY_PATH = os.path.join(BASE_DIR, "proxy_raw.parquet")
REAL_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history"
REAL_PATHS = {
    "WIN": os.path.join(REAL_DIR, "WIN_cont_ADJprop_D1.parquet"),
    "WDO": os.path.join(REAL_DIR, "WDO_cont_ADJprop_D1.parquet"),
}
OUT_DIR = os.path.join(BASE_DIR, "phase0")

DISCOVERY_START = pd.Timestamp("2000-01-01")
H1_END = pd.Timestamp("2009-12-31")
DISCOVERY_END = pd.Timestamp("2018-12-31")
CONFIRM_START = pd.Timestamp("2019-01-01")
CONFIRM_END = pd.Timestamp("2024-12-31")
HARD_CUTOFF = pd.Timestamp("2024-12-31")           # nada alem disso, em nenhuma serie

REALCHECK_START = pd.Timestamp("2021-07-19")
REALCHECK_END = pd.Timestamp("2024-12-31")

SEED = 42
N_BOOT = 1000

MARKETS = ["WIN", "WDO"]
WEEKDAY_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

S2_LS = [21, 63, 126, 252]
S2_FS = [5, 21]
S3_PS = [3, 5]
S3_FS = [1, 3, 5]

# thresholds pre-registrados
S1_MAG_DISC = -1.5      # bps/dia, <=
S2_MAG_DISC = 2.0        # bps/dia, >=
S2_MAG_CONFIRM = 1.0     # bps/dia, >=
S3_MAG_DISC = 2.0
S3_MAG_CONFIRM = 1.0
S4TOM_MAG_DISC = 5.0
S4DOW_MAG_DISC = 8.0

RESULT_ROWS = []
N_TESTS_DISCOVERY = 0     # contador de testes independentes na etapa DISCOVERY


def add_row(thesis, market, stage, param_a_name="", param_a="", param_b_name="",
            param_b="", metric="", point=np.nan, lo=np.nan, hi=np.nan, n=np.nan,
            note="", gate_pass=None):
    RESULT_ROWS.append(dict(
        thesis=thesis, market=market, stage=stage,
        param_a_name=param_a_name, param_a=param_a,
        param_b_name=param_b_name, param_b=param_b,
        metric=metric,
        point_bps=None if pd.isna(point) else round(float(point), 4),
        ci_lo_bps=None if pd.isna(lo) else round(float(lo), 4),
        ci_hi_bps=None if pd.isna(hi) else round(float(hi), 4),
        n=None if pd.isna(n) else int(n),
        gate_pass=gate_pass,
        note=note,
    ))


# ----------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------

def load_proxy():
    df = pd.read_parquet(PROXY_PATH)
    df = df.sort_values("date").reset_index(drop=True)
    n_raw = len(df)
    df = df[df["date"] <= HARD_CUTOFF].copy().reset_index(drop=True)
    n_cut = n_raw - len(df)

    wd = df["date"].dt.weekday
    n_weekend = int((wd >= 5).sum())
    if n_weekend:
        df = df[wd < 5].reset_index(drop=True)

    df["ret_ibov"] = df["ibov"].pct_change()
    df["win_proxy_ret"] = df["ret_ibov"] - df["cdi_ad"] / 100.0
    df["ffr_ad"] = (1.0 + df["ffr_aa"] / 100.0) ** (1.0 / 252.0) - 1.0
    df["ret_usdbrl"] = df["usdbrl"].pct_change()
    df["wdo_proxy_ret"] = df["ret_usdbrl"] - (df["cdi_ad"] / 100.0 - df["ffr_ad"])

    for mkt, col in [("WIN", "win_proxy_ret"), ("WDO", "wdo_proxy_ret")]:
        df[f"{mkt}_ret_bps"] = df[col] * 10000.0
        # preco sintetico P_t = cumprod(1+ret), P_0 = 1.0
        factor = (1.0 + df[col]).fillna(1.0)
        df[f"{mkt}_close"] = factor.cumprod()

    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["weekday"] = df["date"].dt.weekday

    def split_label(ts):
        if ts < DISCOVERY_START:
            return None
        if ts <= H1_END:
            return "H1"
        if ts <= DISCOVERY_END:
            return "H2"
        if CONFIRM_START <= ts <= CONFIRM_END:
            return "CONFIRM"
        return None

    df["split"] = df["date"].apply(split_label)
    return df, n_raw, n_cut, n_weekend


def load_real(market):
    df = pd.read_parquet(REAL_PATHS[market])
    df = df[["datetime_b3", "close"]].copy()
    df["datetime_b3"] = pd.to_datetime(df["datetime_b3"])
    df = df.sort_values("datetime_b3").reset_index(drop=True)
    n_raw = len(df)
    # 2025+ jamais carregado para calculo -- corta IMEDIATAMENTE
    df = df[(df["datetime_b3"] >= REALCHECK_START) &
            (df["datetime_b3"] <= REALCHECK_END)].copy().reset_index(drop=True)
    n_dropped = n_raw - len(df)
    df["ret_bps"] = (df["close"] / df["close"].shift(1) - 1.0) * 10000.0
    df["weekday"] = df["datetime_b3"].dt.weekday
    df["year"] = df["datetime_b3"].dt.year
    df["month"] = df["datetime_b3"].dt.month
    return df, n_raw, n_dropped


# ----------------------------------------------------------------------
# Stat helpers (identicos a build_swing_map.py)
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


def trim_top_contrib_mean(values, frac=0.05, n_boot=N_BOOT, seed=SEED):
    """G3 p/ grupo unico (S1): contribuicao_i = valor_i/n == remove por |valor|."""
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    n = len(v)
    if n == 0:
        return dict(point=np.nan, lo=np.nan, hi=np.nan, n=0, n_removed=0)
    n_remove = int(round(frac * n))
    order = np.argsort(-np.abs(v))
    keep_idx = np.ones(n, dtype=bool)
    keep_idx[order[:n_remove]] = False
    v2 = v[keep_idx]
    r = mean_ci(v2, n_boot=n_boot, seed=seed)
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


def cascade_str(disc, confirm, g3, real):
    def tag(x):
        if x is None:
            return "NOT_EVALUATED"
        return "PASS" if x else "FAIL"
    return (f"DISCOVERY={tag(disc)} CONFIRM={tag(confirm)} "
            f"G3={tag(g3)} REAL_CHECK={tag(real)}")


# ----------------------------------------------------------------------
# S1 -- Carry (WDO apenas, per pre-registro)
# ----------------------------------------------------------------------

def run_s1(df, real_wdo, log):
    global N_TESTS_DISCOVERY
    market = "WDO"
    log(f"\n{'='*78}\nS1 -- CARRY ESTRUTURAL [{market}] (tese e FX-especifica; "
        f"WIN nao avaliado, per pre-registro)\n{'='*78}")
    col = f"{market}_ret_bps"
    disc = df[df["split"].isin(["H1", "H2"])]
    ret_disc = disc[col].dropna()

    N_TESTS_DISCOVERY += 1
    m_disc = mean_ci(ret_disc)
    log(f"DISCOVERY: media={m_disc['point']:.3f} bps/dia "
        f"IC95=[{m_disc['lo']:.3f},{m_disc['hi']:.3f}] n={m_disc['n']}")
    add_row("S1", market, "DISCOVERY", metric="mean_ret_bps_per_day",
            point=m_disc["point"], lo=m_disc["lo"], hi=m_disc["hi"], n=m_disc["n"])

    m_h1 = mean_ci(df[df["split"] == "H1"][col].dropna())
    m_h2 = mean_ci(df[df["split"] == "H2"][col].dropna())
    log(f"H1: media={m_h1['point']:.3f} bps/dia IC95=[{m_h1['lo']:.3f},{m_h1['hi']:.3f}] n={m_h1['n']}")
    log(f"H2: media={m_h2['point']:.3f} bps/dia IC95=[{m_h2['lo']:.3f},{m_h2['hi']:.3f}] n={m_h2['n']}")
    add_row("S1", market, "H1", metric="mean_ret_bps_per_day",
            point=m_h1["point"], lo=m_h1["lo"], hi=m_h1["hi"], n=m_h1["n"])
    add_row("S1", market, "H2", metric="mean_ret_bps_per_day",
            point=m_h2["point"], lo=m_h2["lo"], hi=m_h2["hi"], n=m_h2["n"])

    by_year = {}
    for yr, g in disc.groupby("year"):
        r = mean_ci(g[col].dropna())
        by_year[int(yr)] = r
        add_row("S1", market, f"year_{yr}", metric="mean_ret_bps_per_day",
                point=r["point"], lo=r["lo"], hi=r["hi"], n=r["n"])
    log("por decada:")
    for dec_start in [2000, 2010, 2020]:
        dec_vals = disc[(disc["year"] >= dec_start) & (disc["year"] < dec_start + 10)][col].dropna()
        if len(dec_vals) == 0:
            continue
        r = mean_ci(dec_vals)
        log(f"  {dec_start}s: n={r['n']} media={r['point']:.3f} bps/dia "
            f"IC95=[{r['lo']:.3f},{r['hi']:.3f}]")

    gate_mag = m_disc["point"] <= S1_MAG_DISC
    gate_ci = (pd.notna(m_disc["lo"]) and pd.notna(m_disc["hi"]) and
               not (m_disc["lo"] <= 0 <= m_disc["hi"]))
    gate_sign = same_sign(m_h1["point"], m_h2["point"])
    disc_pass = bool(gate_mag and gate_ci and gate_sign)
    log(f"GATE DISCOVERY S1 [{market}]: media<={S1_MAG_DISC}bps={gate_mag} "
        f"IC_excl_0={gate_ci} mesmo_sinal_H1H2={gate_sign} -> PASS={disc_pass}")

    confirm_pass = None
    g3_pass = None
    real_pass = None
    m_confirm = None
    g3 = None
    m_real = None

    if disc_pass:
        confirm_vals = df[df["split"] == "CONFIRM"][col].dropna()
        m_confirm = mean_ci(confirm_vals)
        confirm_pass = same_sign(m_disc["point"], m_confirm["point"])
        log(f"CONFIRM: media={m_confirm['point']:.3f} bps/dia "
            f"IC95=[{m_confirm['lo']:.3f},{m_confirm['hi']:.3f}] n={m_confirm['n']} "
            f"-> mesmo_sinal_discovery={confirm_pass}")
        add_row("S1", market, "CONFIRM", metric="mean_ret_bps_per_day",
                point=m_confirm["point"], lo=m_confirm["lo"], hi=m_confirm["hi"],
                n=m_confirm["n"], gate_pass=confirm_pass)

    if confirm_pass:
        g3 = trim_top_contrib_mean(ret_disc)
        g3_pass = pd.notna(g3["point"]) and g3["point"] <= S1_MAG_DISC
        log(f"G3 (remove top5% |contrib|, n_removed={g3['n_removed']}): "
            f"media={g3['point']:.3f} bps/dia IC95=[{g3['lo']:.3f},{g3['hi']:.3f}] "
            f"-> mag<={S1_MAG_DISC}bps={g3_pass}")
        add_row("S1", market, "DISCOVERY_G3", metric="mean_ret_bps_per_day_g3",
                point=g3["point"], lo=g3["lo"], hi=g3["hi"], n=g3["n"],
                note=f"n_removed={g3['n_removed']}", gate_pass=g3_pass)

    if g3_pass:
        real_vals = real_wdo["ret_bps"].dropna()
        m_real = mean_ci(real_vals)
        real_pass = same_sign(m_disc["point"], m_real["point"])
        log(f"REAL-CHECK (WDO real, {REALCHECK_START.date()}->{REALCHECK_END.date()}): "
            f"media={m_real['point']:.3f} bps/dia IC95=[{m_real['lo']:.3f},{m_real['hi']:.3f}] "
            f"n={m_real['n']} -> mesmo_sinal_discovery={real_pass}")
        add_row("S1", market, "REAL_CHECK", metric="mean_ret_bps_per_day",
                point=m_real["point"], lo=m_real["lo"], hi=m_real["hi"], n=m_real["n"],
                gate_pass=real_pass)

    log(f"CASCATA S1 [{market}]: {cascade_str(disc_pass, confirm_pass, g3_pass, real_pass)}")
    overall = bool(disc_pass and confirm_pass and g3_pass and real_pass)
    log(f"S1 [{market}] ADVANCE_CANDIDATE={overall}")

    return dict(pass_=overall, disc_pass=disc_pass, confirm_pass=confirm_pass,
                g3_pass=g3_pass, real_pass=real_pass, n_tests=1)


# ----------------------------------------------------------------------
# S2 -- TSM
# ----------------------------------------------------------------------

def run_s2(market, df, real_df, log):
    global N_TESTS_DISCOVERY
    log(f"\n{'='*78}\nS2 -- TIME-SERIES MOMENTUM [{market}]\n{'='*78}")
    close_col = f"{market}_close"

    disc = df[df["split"].isin(["H1", "H2"])].reset_index(drop=True)
    close_disc = disc[close_col].to_numpy()
    split_arr = disc["split"].to_numpy()
    n_disc = len(disc)

    confirm = df[df["split"] == "CONFIRM"].reset_index(drop=True)
    close_confirm = confirm[close_col].to_numpy()
    n_confirm = len(confirm)

    close_real = real_df["close"].to_numpy()
    n_real = len(real_df)

    combo_results = {}
    any_pass = False

    for L in S2_LS:
        for F in S2_FS:
            idxs = build_grid_indices(n_disc, L, F)
            if len(idxs) < 20:
                log(f"L={L} F={F}: grid DISCOVERY n={len(idxs)} (<20, pulado)")
                combo_results[(L, F)] = dict(skip=True, n_grid=len(idxs))
                continue
            N_TESTS_DISCOVERY += 1

            state_sign = np.empty(len(idxs))
            forward = np.empty(len(idxs))
            row_split = np.empty(len(idxs), dtype=object)
            for k, i in enumerate(idxs):
                state_val = close_disc[i] / close_disc[i - L] - 1.0
                state_sign[k] = np.sign(state_val)
                forward[k] = (close_disc[i + F] / close_disc[i] - 1.0) / F * 10000.0
                row_split[k] = split_arr[i]

            mask_nz = state_sign != 0
            up = forward[mask_nz & (state_sign > 0)]
            down = forward[mask_nz & (state_sign < 0)]
            spread = bootstrap_group_spread(up, down)
            log(f"L={L:>3} F={F:>2} n_grid={len(idxs):>4} n_up={spread['n_a']:>4} "
                f"n_down={spread['n_b']:>4} | up={spread['mean_a']:.3f} down={spread['mean_b']:.3f} "
                f"spread={spread['point']:.3f} bps/dia IC95=[{spread['lo']:.3f},{spread['hi']:.3f}]")
            add_row("S2", market, "DISCOVERY", "L", L, "F", F,
                    metric="spread_up_minus_down_bps_per_day",
                    point=spread["point"], lo=spread["lo"], hi=spread["hi"],
                    n=spread["n_a"] + spread["n_b"])

            sub_h1 = mask_nz & (row_split == "H1")
            sub_h2 = mask_nz & (row_split == "H2")
            spread_h1 = bootstrap_group_spread(forward[sub_h1 & (state_sign > 0)],
                                                forward[sub_h1 & (state_sign < 0)])
            spread_h2 = bootstrap_group_spread(forward[sub_h2 & (state_sign > 0)],
                                                forward[sub_h2 & (state_sign < 0)])
            log(f"         H1: spread={spread_h1['point']:.3f} bps/dia (n={spread_h1['n_a']+spread_h1['n_b']}) "
                f"| H2: spread={spread_h2['point']:.3f} bps/dia (n={spread_h2['n_a']+spread_h2['n_b']})")
            add_row("S2", market, "H1", "L", L, "F", F,
                    metric="spread_up_minus_down_bps_per_day",
                    point=spread_h1["point"], lo=spread_h1["lo"], hi=spread_h1["hi"],
                    n=spread_h1["n_a"] + spread_h1["n_b"])
            add_row("S2", market, "H2", "L", L, "F", F,
                    metric="spread_up_minus_down_bps_per_day",
                    point=spread_h2["point"], lo=spread_h2["lo"], hi=spread_h2["hi"],
                    n=spread_h2["n_a"] + spread_h2["n_b"])

            gate_mag = abs(spread["point"]) >= S2_MAG_DISC
            gate_ci = (pd.notna(spread["lo"]) and pd.notna(spread["hi"]) and
                       not (spread["lo"] <= 0 <= spread["hi"]))
            gate_sign = same_sign(spread_h1["point"], spread_h2["point"])
            disc_pass = bool(gate_mag and gate_ci and gate_sign)
            log(f"         GATE DISCOVERY: mag>={S2_MAG_DISC}bps={gate_mag} IC_excl_0={gate_ci} "
                f"mesmo_sinal_H1H2={gate_sign} -> PASS={disc_pass}")

            confirm_pass = g3_pass = real_pass = None
            spread_confirm = g3 = spread_real = None

            if disc_pass:
                idxs_c = build_grid_indices(n_confirm, L, F)
                if len(idxs_c) < 20:
                    confirm_pass = False
                    log(f"         CONFIRM: grid n={len(idxs_c)} (<20) -> FAIL (n insuficiente)")
                    add_row("S2", market, "CONFIRM", "L", L, "F", F,
                            metric="spread_up_minus_down_bps_per_day",
                            n=len(idxs_c), note="n insuficiente (<20)", gate_pass=False)
                else:
                    ss = np.empty(len(idxs_c)); fw = np.empty(len(idxs_c))
                    for k, i in enumerate(idxs_c):
                        sv = close_confirm[i] / close_confirm[i - L] - 1.0
                        ss[k] = np.sign(sv)
                        fw[k] = (close_confirm[i + F] / close_confirm[i] - 1.0) / F * 10000.0
                    mnz = ss != 0
                    spread_confirm = bootstrap_group_spread(fw[mnz & (ss > 0)], fw[mnz & (ss < 0)])
                    confirm_pass = (same_sign(spread["point"], spread_confirm["point"]) and
                                     abs(spread_confirm["point"]) >= S2_MAG_CONFIRM)
                    log(f"         CONFIRM: n_grid={len(idxs_c)} spread={spread_confirm['point']:.3f} "
                        f"bps/dia IC95=[{spread_confirm['lo']:.3f},{spread_confirm['hi']:.3f}] "
                        f"-> mesmo_sinal={same_sign(spread['point'], spread_confirm['point'])} "
                        f"mag>={S2_MAG_CONFIRM}bps={abs(spread_confirm['point']) >= S2_MAG_CONFIRM} "
                        f"-> PASS={confirm_pass}")
                    add_row("S2", market, "CONFIRM", "L", L, "F", F,
                            metric="spread_up_minus_down_bps_per_day",
                            point=spread_confirm["point"], lo=spread_confirm["lo"],
                            hi=spread_confirm["hi"],
                            n=spread_confirm["n_a"] + spread_confirm["n_b"], gate_pass=confirm_pass)

            if confirm_pass:
                g3 = trim_top_contrib_spread(up, down)
                g3_pass = pd.notna(g3["point"]) and abs(g3["point"]) >= S2_MAG_DISC
                log(f"         G3 (n_removed={g3['n_removed']}): spread={g3['point']:.3f} bps/dia "
                    f"IC95=[{g3['lo']:.3f},{g3['hi']:.3f}] -> mag>={S2_MAG_DISC}bps={g3_pass}")
                add_row("S2", market, "DISCOVERY_G3", "L", L, "F", F,
                        metric="spread_up_minus_down_bps_per_day_g3",
                        point=g3["point"], lo=g3["lo"], hi=g3["hi"],
                        n=g3["n_a"] + g3["n_b"], note=f"n_removed={g3['n_removed']}", gate_pass=g3_pass)

            if g3_pass:
                idxs_r = build_grid_indices(n_real, L, F)
                if len(idxs_r) < 10:
                    real_pass = False
                    log(f"         REAL-CHECK: grid n={len(idxs_r)} (<10) -> FAIL (n insuficiente)")
                    add_row("S2", market, "REAL_CHECK", "L", L, "F", F,
                            metric="spread_up_minus_down_bps_per_day",
                            n=len(idxs_r), note="n insuficiente (<10)", gate_pass=False)
                else:
                    ss = np.empty(len(idxs_r)); fw = np.empty(len(idxs_r))
                    for k, i in enumerate(idxs_r):
                        sv = close_real[i] / close_real[i - L] - 1.0
                        ss[k] = np.sign(sv)
                        fw[k] = (close_real[i + F] / close_real[i] - 1.0) / F * 10000.0
                    mnz = ss != 0
                    spread_real = bootstrap_group_spread(fw[mnz & (ss > 0)], fw[mnz & (ss < 0)])
                    real_pass = same_sign(spread["point"], spread_real["point"])
                    log(f"         REAL-CHECK: n_grid={len(idxs_r)} spread={spread_real['point']:.3f} "
                        f"bps/dia (magnitude reportada, sem exigencia de IC) "
                        f"-> mesmo_sinal_discovery={real_pass}")
                    add_row("S2", market, "REAL_CHECK", "L", L, "F", F,
                            metric="spread_up_minus_down_bps_per_day",
                            point=spread_real["point"], lo=spread_real["lo"], hi=spread_real["hi"],
                            n=spread_real["n_a"] + spread_real["n_b"], gate_pass=real_pass)

            combo_pass = bool(disc_pass and confirm_pass and g3_pass and real_pass)
            if combo_pass:
                any_pass = True
            log(f"         CASCATA L={L} F={F}: {cascade_str(disc_pass, confirm_pass, g3_pass, real_pass)} "
                f"-> ADVANCE_CANDIDATE={combo_pass}")

            combo_results[(L, F)] = dict(skip=False, disc_pass=disc_pass, confirm_pass=confirm_pass,
                                          g3_pass=g3_pass, real_pass=real_pass, pass_=combo_pass)

    log(f"\nS2 [{market}]: any_combo_advance={any_pass}")
    return dict(combos=combo_results, pass_=any_pass)


# ----------------------------------------------------------------------
# S3 -- Reversao
# ----------------------------------------------------------------------

def run_s3(market, df, real_df, log):
    global N_TESTS_DISCOVERY
    log(f"\n{'='*78}\nS3 -- REVERSAO CURTO PRAZO [{market}]\n{'='*78}")
    close_col = f"{market}_close"

    disc = df[df["split"].isin(["H1", "H2"])].reset_index(drop=True)
    close_disc = disc[close_col].to_numpy()
    split_arr = disc["split"].to_numpy()
    n_disc = len(disc)

    confirm = df[df["split"] == "CONFIRM"].reset_index(drop=True)
    close_confirm = confirm[close_col].to_numpy()
    n_confirm = len(confirm)

    close_real = real_df["close"].to_numpy()
    n_real = len(real_df)

    combo_results = {}
    any_pass = False

    for P in S3_PS:
        for F in S3_FS:
            if market == "WDO" and F < 3:
                log(f"P={P} F={F}: pulado (restricao pre-registrada WDO F>=3)")
                combo_results[(P, F)] = dict(skip=True, note="WDO F<3 excluido por pre-registro")
                continue

            idxs = build_grid_indices(n_disc, P, F)
            if len(idxs) < 20:
                log(f"P={P} F={F}: grid DISCOVERY n={len(idxs)} (<20, pulado)")
                combo_results[(P, F)] = dict(skip=True, n_grid=len(idxs))
                continue
            N_TESTS_DISCOVERY += 1

            past = np.empty(len(idxs)); forward = np.empty(len(idxs))
            row_split = np.empty(len(idxs), dtype=object)
            for k, i in enumerate(idxs):
                past[k] = close_disc[i] / close_disc[i - P] - 1.0
                forward[k] = (close_disc[i + F] / close_disc[i] - 1.0) / F * 10000.0
                row_split[k] = split_arr[i]

            thr = quintile_thresholds(past)
            q = quintile_assign(past, thr)
            q1 = forward[q == 1]
            q5 = forward[q == 5]
            spread = bootstrap_group_spread(q1, q5)
            log(f"P={P} F={F} n_grid={len(idxs):>4} n_Q1={spread['n_a']:>4} n_Q5={spread['n_b']:>4} "
                f"| Q1={spread['mean_a']:.3f} Q5={spread['mean_b']:.3f} "
                f"spread(Q1-Q5)={spread['point']:.3f} bps/dia IC95=[{spread['lo']:.3f},{spread['hi']:.3f}]")
            add_row("S3", market, "DISCOVERY", "P", P, "F", F,
                    metric="spread_Q1_minus_Q5_bps_per_day",
                    point=spread["point"], lo=spread["lo"], hi=spread["hi"],
                    n=spread["n_a"] + spread["n_b"])

            spreads_half = {}
            for half in ["H1", "H2"]:
                mask = row_split == half
                past_h, fwd_h = past[mask], forward[mask]
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
                        point=sp["point"], lo=sp["lo"], hi=sp["hi"], n=sp["n_a"] + sp["n_b"])
            sh1, sh2 = spreads_half["H1"], spreads_half["H2"]
            log(f"         H1: spread={sh1['point']:.3f} bps/dia (n={sh1['n_a']+sh1['n_b']}) "
                f"| H2: spread={sh2['point']:.3f} bps/dia (n={sh2['n_a']+sh2['n_b']})")

            gate_mag = pd.notna(spread["point"]) and abs(spread["point"]) >= S3_MAG_DISC
            gate_ci = (pd.notna(spread["lo"]) and pd.notna(spread["hi"]) and
                       not (spread["lo"] <= 0 <= spread["hi"]))
            gate_sign = same_sign(sh1["point"], sh2["point"])
            disc_pass = bool(gate_mag and gate_ci and gate_sign)
            log(f"         GATE DISCOVERY: mag>={S3_MAG_DISC}bps={gate_mag} IC_excl_0={gate_ci} "
                f"mesmo_sinal_H1H2={gate_sign} -> PASS={disc_pass}")

            confirm_pass = g3_pass = real_pass = None
            g3 = None

            if disc_pass:
                idxs_c = build_grid_indices(n_confirm, P, F)
                if len(idxs_c) < 20:
                    confirm_pass = False
                    log(f"         CONFIRM: grid n={len(idxs_c)} (<20) -> FAIL (n insuficiente)")
                    add_row("S3", market, "CONFIRM", "P", P, "F", F,
                            metric="spread_Q1_minus_Q5_bps_per_day",
                            n=len(idxs_c), note="n insuficiente (<20)", gate_pass=False)
                else:
                    past_c = np.empty(len(idxs_c)); fwd_c = np.empty(len(idxs_c))
                    for k, i in enumerate(idxs_c):
                        past_c[k] = close_confirm[i] / close_confirm[i - P] - 1.0
                        fwd_c[k] = (close_confirm[i + F] / close_confirm[i] - 1.0) / F * 10000.0
                    thr_c = quintile_thresholds(past_c)
                    q_c = quintile_assign(past_c, thr_c)
                    spread_confirm = bootstrap_group_spread(fwd_c[q_c == 1], fwd_c[q_c == 5])
                    confirm_pass = (same_sign(spread["point"], spread_confirm["point"]) and
                                     abs(spread_confirm["point"]) >= S3_MAG_CONFIRM)
                    log(f"         CONFIRM: n_grid={len(idxs_c)} spread={spread_confirm['point']:.3f} "
                        f"bps/dia IC95=[{spread_confirm['lo']:.3f},{spread_confirm['hi']:.3f}] "
                        f"-> PASS={confirm_pass}")
                    add_row("S3", market, "CONFIRM", "P", P, "F", F,
                            metric="spread_Q1_minus_Q5_bps_per_day",
                            point=spread_confirm["point"], lo=spread_confirm["lo"],
                            hi=spread_confirm["hi"],
                            n=spread_confirm["n_a"] + spread_confirm["n_b"], gate_pass=confirm_pass)

            if confirm_pass:
                g3 = trim_top_contrib_spread(q1, q5)
                g3_pass = pd.notna(g3["point"]) and abs(g3["point"]) >= S3_MAG_DISC
                log(f"         G3 (n_removed={g3['n_removed']}): spread={g3['point']:.3f} bps/dia "
                    f"IC95=[{g3['lo']:.3f},{g3['hi']:.3f}] -> mag>={S3_MAG_DISC}bps={g3_pass}")
                add_row("S3", market, "DISCOVERY_G3", "P", P, "F", F,
                        metric="spread_Q1_minus_Q5_bps_per_day_g3",
                        point=g3["point"], lo=g3["lo"], hi=g3["hi"],
                        n=g3["n_a"] + g3["n_b"], note=f"n_removed={g3['n_removed']}", gate_pass=g3_pass)

            if g3_pass:
                idxs_r = build_grid_indices(n_real, P, F)
                if len(idxs_r) < 10:
                    real_pass = False
                    log(f"         REAL-CHECK: grid n={len(idxs_r)} (<10) -> FAIL (n insuficiente)")
                    add_row("S3", market, "REAL_CHECK", "P", P, "F", F,
                            metric="spread_Q1_minus_Q5_bps_per_day",
                            n=len(idxs_r), note="n insuficiente (<10)", gate_pass=False)
                else:
                    past_r = np.empty(len(idxs_r)); fwd_r = np.empty(len(idxs_r))
                    for k, i in enumerate(idxs_r):
                        past_r[k] = close_real[i] / close_real[i - P] - 1.0
                        fwd_r[k] = (close_real[i + F] / close_real[i] - 1.0) / F * 10000.0
                    thr_r = quintile_thresholds(past_r)
                    q_r = quintile_assign(past_r, thr_r)
                    spread_real = bootstrap_group_spread(fwd_r[q_r == 1], fwd_r[q_r == 5])
                    real_pass = same_sign(spread["point"], spread_real["point"])
                    log(f"         REAL-CHECK: n_grid={len(idxs_r)} spread={spread_real['point']:.3f} "
                        f"bps/dia (magnitude reportada, sem exigencia de IC) "
                        f"-> mesmo_sinal_discovery={real_pass}")
                    add_row("S3", market, "REAL_CHECK", "P", P, "F", F,
                            metric="spread_Q1_minus_Q5_bps_per_day",
                            point=spread_real["point"], lo=spread_real["lo"], hi=spread_real["hi"],
                            n=spread_real["n_a"] + spread_real["n_b"], gate_pass=real_pass)

            combo_pass = bool(disc_pass and confirm_pass and g3_pass and real_pass)
            if combo_pass:
                any_pass = True
            log(f"         CASCATA P={P} F={F}: {cascade_str(disc_pass, confirm_pass, g3_pass, real_pass)} "
                f"-> ADVANCE_CANDIDATE={combo_pass}")

            combo_results[(P, F)] = dict(skip=False, disc_pass=disc_pass, confirm_pass=confirm_pass,
                                          g3_pass=g3_pass, real_pass=real_pass, pass_=combo_pass)

    log(f"\nS3 [{market}]: any_combo_advance={any_pass}")
    return dict(combos=combo_results, pass_=any_pass)


# ----------------------------------------------------------------------
# S4 -- Calendario (TOM + DOW)
# ----------------------------------------------------------------------

def _tom_flags(sub):
    sub = sub.copy()
    grp = sub.groupby(["year", "month"])
    sub["tdom"] = grp.cumcount() + 1
    sub["month_size"] = grp[sub.columns[0]].transform("size")
    sub["tom"] = (sub["tdom"] <= 3) | (sub["tdom"] == sub["month_size"])
    return sub


def run_s4_tom(market, df, real_df, log):
    global N_TESTS_DISCOVERY
    log(f"\n{'='*78}\nS4-TOM -- TURN OF MONTH [{market}]\n{'='*78}")
    col = f"{market}_ret_bps"
    disc = df[df["split"].isin(["H1", "H2"])].dropna(subset=[col]).reset_index(drop=True)
    disc["_v"] = disc[col]
    disc = _tom_flags(disc)
    N_TESTS_DISCOVERY += 1

    n_tom, n_nontom = int(disc["tom"].sum()), int((~disc["tom"]).sum())
    log(f"dias TOM={n_tom} dias nao-TOM={n_nontom} (total={len(disc)})")

    sp_disc = bootstrap_group_spread(disc.loc[disc["tom"], col], disc.loc[~disc["tom"], col])
    log(f"DISCOVERY: mean_TOM={sp_disc['mean_a']:.3f} mean_nonTOM={sp_disc['mean_b']:.3f} "
        f"diff={sp_disc['point']:.3f} bps/dia IC95=[{sp_disc['lo']:.3f},{sp_disc['hi']:.3f}]")
    add_row("S4_TOM", market, "DISCOVERY", metric="diff_tom_minus_nontom_bps",
            point=sp_disc["point"], lo=sp_disc["lo"], hi=sp_disc["hi"],
            n=sp_disc["n_a"] + sp_disc["n_b"])

    halves = {}
    for half in ["H1", "H2"]:
        sub = disc[disc["split"] == half]
        sp = bootstrap_group_spread(sub.loc[sub["tom"], col], sub.loc[~sub["tom"], col])
        halves[half] = sp
        log(f"{half}: diff={sp['point']:.3f} bps/dia n_tom={sp['n_a']} n_nontom={sp['n_b']}")
        add_row("S4_TOM", market, half, metric="diff_tom_minus_nontom_bps",
                point=sp["point"], lo=sp["lo"], hi=sp["hi"], n=sp["n_a"] + sp["n_b"])

    gate_mag = pd.notna(sp_disc["point"]) and sp_disc["point"] >= S4TOM_MAG_DISC
    gate_ci = (pd.notna(sp_disc["lo"]) and pd.notna(sp_disc["hi"]) and
               not (sp_disc["lo"] <= 0 <= sp_disc["hi"]))
    gate_sign = (same_sign(halves["H1"]["point"], halves["H2"]["point"]) and
                 same_sign(halves["H1"]["point"], sp_disc["point"]))
    disc_pass = bool(gate_mag and gate_ci and gate_sign)
    log(f"GATE DISCOVERY S4-TOM [{market}]: diff>={S4TOM_MAG_DISC}bps={gate_mag} "
        f"IC_excl_0={gate_ci} mesmo_sinal_H1H2={gate_sign} -> PASS={disc_pass}")

    confirm_pass = g3_pass = real_pass = None
    sp_confirm = g3 = sp_real = None

    if disc_pass:
        confirm = df[df["split"] == "CONFIRM"].dropna(subset=[col]).reset_index(drop=True)
        confirm = _tom_flags(confirm)
        sp_confirm = bootstrap_group_spread(confirm.loc[confirm["tom"], col],
                                             confirm.loc[~confirm["tom"], col])
        confirm_pass = same_sign(sp_disc["point"], sp_confirm["point"])
        log(f"CONFIRM: diff={sp_confirm['point']:.3f} bps/dia "
            f"IC95=[{sp_confirm['lo']:.3f},{sp_confirm['hi']:.3f}] "
            f"n_tom={sp_confirm['n_a']} n_nontom={sp_confirm['n_b']} -> PASS={confirm_pass}")
        add_row("S4_TOM", market, "CONFIRM", metric="diff_tom_minus_nontom_bps",
                point=sp_confirm["point"], lo=sp_confirm["lo"], hi=sp_confirm["hi"],
                n=sp_confirm["n_a"] + sp_confirm["n_b"], gate_pass=confirm_pass)

    if confirm_pass:
        g3 = trim_top_contrib_spread(disc.loc[disc["tom"], col], disc.loc[~disc["tom"], col])
        g3_pass = pd.notna(g3["point"]) and g3["point"] >= S4TOM_MAG_DISC
        log(f"G3 (n_removed={g3['n_removed']}): diff={g3['point']:.3f} bps/dia "
            f"IC95=[{g3['lo']:.3f},{g3['hi']:.3f}] -> mag>={S4TOM_MAG_DISC}bps={g3_pass}")
        add_row("S4_TOM", market, "DISCOVERY_G3", metric="diff_tom_minus_nontom_bps_g3",
                point=g3["point"], lo=g3["lo"], hi=g3["hi"], n=g3["n_a"] + g3["n_b"],
                note=f"n_removed={g3['n_removed']}", gate_pass=g3_pass)

    if g3_pass:
        real = real_df.dropna(subset=["ret_bps"]).reset_index(drop=True)
        real["_v"] = real["ret_bps"]
        real = _tom_flags(real)
        sp_real = bootstrap_group_spread(real.loc[real["tom"], "ret_bps"],
                                          real.loc[~real["tom"], "ret_bps"])
        real_pass = same_sign(sp_disc["point"], sp_real["point"])
        log(f"REAL-CHECK: diff={sp_real['point']:.3f} bps/dia (magnitude reportada, sem IC) "
            f"n_tom={sp_real['n_a']} n_nontom={sp_real['n_b']} -> mesmo_sinal_discovery={real_pass}")
        add_row("S4_TOM", market, "REAL_CHECK", metric="diff_tom_minus_nontom_bps",
                point=sp_real["point"], lo=sp_real["lo"], hi=sp_real["hi"],
                n=sp_real["n_a"] + sp_real["n_b"], gate_pass=real_pass)

    log(f"CASCATA S4-TOM [{market}]: {cascade_str(disc_pass, confirm_pass, g3_pass, real_pass)}")
    overall = bool(disc_pass and confirm_pass and g3_pass and real_pass)
    log(f"S4-TOM [{market}] ADVANCE_CANDIDATE={overall}")

    return dict(pass_=overall, gate_mag=gate_mag, gate_ci=gate_ci, gate_sign=gate_sign,
                disc_pass=disc_pass, confirm_pass=confirm_pass, g3_pass=g3_pass, real_pass=real_pass)


def run_s4_dow(market, df, real_df, log):
    global N_TESTS_DISCOVERY
    log(f"\n{'='*78}\nS4-DOW -- DIA DA SEMANA [{market}]\n{'='*78}")
    col = f"{market}_ret_bps"
    disc = df[df["split"].isin(["H1", "H2"])].dropna(subset=[col])
    N_TESTS_DISCOVERY += 1

    by_wd_disc = {}
    for wd, g in disc.groupby("weekday"):
        name = WEEKDAY_NAMES.get(wd, str(wd))
        r = mean_ci(g[col])
        by_wd_disc[wd] = r
        log(f"  {name}: n={r['n']} media={r['point']:.3f} bps IC95=[{r['lo']:.3f},{r['hi']:.3f}]")
        add_row("S4_DOW", market, "DISCOVERY", "weekday", name, metric="mean_ret_bps",
                point=r["point"], lo=r["lo"], hi=r["hi"], n=r["n"])

    best_wd = max(by_wd_disc, key=lambda k: by_wd_disc[k]["point"])
    worst_wd = min(by_wd_disc, key=lambda k: by_wd_disc[k]["point"])
    log(f"melhor dia (DISCOVERY) = {WEEKDAY_NAMES[best_wd]} ({by_wd_disc[best_wd]['point']:.3f} bps); "
        f"pior dia = {WEEKDAY_NAMES[worst_wd]} ({by_wd_disc[worst_wd]['point']:.3f} bps)")

    best_vals = disc.loc[disc["weekday"] == best_wd, col]
    worst_vals = disc.loc[disc["weekday"] == worst_wd, col]
    spread = bootstrap_group_spread(best_vals, worst_vals)
    log(f"spread(melhor-pior) DISCOVERY = {spread['point']:.3f} bps IC95=[{spread['lo']:.3f},{spread['hi']:.3f}]")
    add_row("S4_DOW", market, "DISCOVERY", "spread", f"{WEEKDAY_NAMES[best_wd]}-{WEEKDAY_NAMES[worst_wd]}",
            metric="spread_best_minus_worst_bps", point=spread["point"], lo=spread["lo"], hi=spread["hi"],
            n=spread["n_a"] + spread["n_b"])

    ci_best, ci_worst = by_wd_disc[best_wd], by_wd_disc[worst_wd]
    gate_ci_extremes = (not (ci_best["lo"] <= 0 <= ci_best["hi"]) and
                         not (ci_worst["lo"] <= 0 <= ci_worst["hi"]))
    gate_mag = abs(spread["point"]) >= S4DOW_MAG_DISC

    ranking = {}
    for half in ["H1", "H2"]:
        sub = disc[disc["split"] == half]
        means = {wd: float(g[col].mean()) for wd, g in sub.groupby("weekday") if len(g) > 0}
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
                     ranking["H1"][0] == ranking["H2"][0] and ranking["H1"][1] == ranking["H2"][1])
    disc_pass = bool(gate_mag and gate_ci_extremes and gate_ranking)
    log(f"GATE DISCOVERY S4-DOW [{market}]: |spread|>={S4DOW_MAG_DISC}bps={gate_mag} "
        f"IC_excl_0_ambos_extremos={gate_ci_extremes} mesmo_ranking_H1H2={gate_ranking} -> PASS={disc_pass}")

    confirm_pass = g3_pass = real_pass = None
    spread_confirm = g3 = spread_real = None

    if disc_pass:
        confirm = df[df["split"] == "CONFIRM"].dropna(subset=[col])
        # reusa os MESMOS best_wd/worst_wd fixados no DISCOVERY (nao re-elege campeao)
        best_c = confirm.loc[confirm["weekday"] == best_wd, col]
        worst_c = confirm.loc[confirm["weekday"] == worst_wd, col]
        spread_confirm = bootstrap_group_spread(best_c, worst_c)
        confirm_pass = same_sign(spread["point"], spread_confirm["point"])
        log(f"CONFIRM (reusando {WEEKDAY_NAMES[best_wd]}/{WEEKDAY_NAMES[worst_wd]} do DISCOVERY): "
            f"spread={spread_confirm['point']:.3f} bps IC95=[{spread_confirm['lo']:.3f},{spread_confirm['hi']:.3f}] "
            f"n={spread_confirm['n_a']+spread_confirm['n_b']} -> PASS={confirm_pass}")
        add_row("S4_DOW", market, "CONFIRM", "spread",
                f"{WEEKDAY_NAMES[best_wd]}-{WEEKDAY_NAMES[worst_wd]}",
                metric="spread_best_minus_worst_bps", point=spread_confirm["point"],
                lo=spread_confirm["lo"], hi=spread_confirm["hi"],
                n=spread_confirm["n_a"] + spread_confirm["n_b"], gate_pass=confirm_pass)

    if confirm_pass:
        g3 = trim_top_contrib_spread(best_vals, worst_vals)
        g3_pass = pd.notna(g3["point"]) and abs(g3["point"]) >= S4DOW_MAG_DISC
        log(f"G3 (n_removed={g3['n_removed']}): spread={g3['point']:.3f} bps "
            f"IC95=[{g3['lo']:.3f},{g3['hi']:.3f}] -> mag>={S4DOW_MAG_DISC}bps={g3_pass}")
        add_row("S4_DOW", market, "DISCOVERY_G3", "spread",
                f"{WEEKDAY_NAMES[best_wd]}-{WEEKDAY_NAMES[worst_wd]}",
                metric="spread_best_minus_worst_bps_g3", point=g3["point"], lo=g3["lo"], hi=g3["hi"],
                n=g3["n_a"] + g3["n_b"], note=f"n_removed={g3['n_removed']}", gate_pass=g3_pass)

    if g3_pass:
        real = real_df.dropna(subset=["ret_bps"])
        best_r = real.loc[real["weekday"] == best_wd, "ret_bps"]
        worst_r = real.loc[real["weekday"] == worst_wd, "ret_bps"]
        spread_real = bootstrap_group_spread(best_r, worst_r)
        real_pass = same_sign(spread["point"], spread_real["point"])
        log(f"REAL-CHECK (mesmos dias {WEEKDAY_NAMES[best_wd]}/{WEEKDAY_NAMES[worst_wd]}): "
            f"spread={spread_real['point']:.3f} bps (magnitude reportada, sem IC) "
            f"n={spread_real['n_a']+spread_real['n_b']} -> mesmo_sinal_discovery={real_pass}")
        add_row("S4_DOW", market, "REAL_CHECK", "spread",
                f"{WEEKDAY_NAMES[best_wd]}-{WEEKDAY_NAMES[worst_wd]}",
                metric="spread_best_minus_worst_bps", point=spread_real["point"],
                lo=spread_real["lo"], hi=spread_real["hi"],
                n=spread_real["n_a"] + spread_real["n_b"], gate_pass=real_pass)

    log(f"CASCATA S4-DOW [{market}]: {cascade_str(disc_pass, confirm_pass, g3_pass, real_pass)}")
    overall = bool(disc_pass and confirm_pass and g3_pass and real_pass)
    log(f"S4-DOW [{market}] ADVANCE_CANDIDATE={overall}")

    return dict(pass_=overall, best=best_wd, worst=worst_wd, gate_mag=gate_mag,
                gate_ci_extremes=gate_ci_extremes, gate_ranking=gate_ranking,
                disc_pass=disc_pass, confirm_pass=confirm_pass, g3_pass=g3_pass, real_pass=real_pass)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    summary_lines = []

    def slog(s=""):
        print(s)
        summary_lines.append(s)

    slog("b3_swing_v1_proxy -- Fase 0 (mapa das teses S1-S4 sobre proxies publicas)")
    slog(f"seed={SEED} n_boot={N_BOOT}")
    slog(f"DISCOVERY={DISCOVERY_START.date()}->{DISCOVERY_END.date()} "
         f"(H1<={H1_END.date()}, H2 ate {DISCOVERY_END.date()})")
    slog(f"CONFIRM={CONFIRM_START.date()}->{CONFIRM_END.date()}")
    slog(f"REAL-CHECK (futuros reais ADJprop)={REALCHECK_START.date()}->{REALCHECK_END.date()}")
    slog(f"HARD_CUTOFF (nada alem disso, nenhuma serie)={HARD_CUTOFF.date()}")
    slog("")

    df, n_raw, n_cut, n_wknd = load_proxy()
    slog(f"proxy_raw.parquet: linhas brutas={n_raw} removidas(>{HARD_CUTOFF.date()})={n_cut} "
         f"removidas_fim_de_semana={n_wknd}")
    slog(f"range apos filtro: {df['date'].min().date()} -> {df['date'].max().date()}")
    n_h1 = int((df["split"] == "H1").sum())
    n_h2 = int((df["split"] == "H2").sum())
    n_confirm = int((df["split"] == "CONFIRM").sum())
    slog(f"DISCOVERY n={n_h1+n_h2} (H1 n={n_h1}, H2 n={n_h2})  CONFIRM n={n_confirm}")

    real_data = {}
    for market in MARKETS:
        real_df, n_raw_r, n_dropped_r = load_real(market)
        real_data[market] = real_df
        slog(f"real {market}: linhas brutas={n_raw_r} removidas(fora REAL-CHECK window)={n_dropped_r} "
             f"n_final={len(real_df)} range={real_df['datetime_b3'].min().date()}->"
             f"{real_df['datetime_b3'].max().date()}")
    slog("")

    all_results = {}

    # S1 -- WDO apenas
    slog("#" * 78)
    slog("# S1 -- CARRY (WDO apenas, per pre-registro)")
    slog("#" * 78)
    s1 = run_s1(df, real_data["WDO"], slog)
    all_results["S1"] = {"WDO": s1}

    for market in MARKETS:
        slog("#" * 78)
        slog(f"# MERCADO: {market}")
        slog("#" * 78)
        s2 = run_s2(market, df, real_data[market], slog)
        s3 = run_s3(market, df, real_data[market], slog)
        s4tom = run_s4_tom(market, df, real_data[market], slog)
        s4dow = run_s4_dow(market, df, real_data[market], slog)
        all_results.setdefault("S2", {})[market] = s2
        all_results.setdefault("S3", {})[market] = s3
        all_results.setdefault("S4_TOM", {})[market] = s4tom
        all_results.setdefault("S4_DOW", {})[market] = s4dow
        slog("")

    # ------------------------------------------------------------------
    # Tabela-resumo
    # ------------------------------------------------------------------
    slog("=" * 78)
    slog("TABELA-RESUMO DE GATES (por tese x mercado)")
    slog("=" * 78)

    slog(f"\n--- S1 (WDO apenas) ---")
    slog(f"S1 [WDO]: {cascade_str(s1['disc_pass'], s1['confirm_pass'], s1['g3_pass'], s1['real_pass'])} "
         f"-> ADVANCE_CANDIDATE={s1['pass_']}")

    for market in MARKETS:
        slog(f"\n--- {market} ---")
        s2 = all_results["S2"][market]
        n_s2_combos = len(s2["combos"])
        n_s2_eval = sum(1 for c in s2["combos"].values() if not c.get("skip"))
        n_s2_pass = sum(1 for c in s2["combos"].values() if not c.get("skip") and c.get("pass_"))
        slog(f"S2  (TSM):          {n_s2_pass}/{n_s2_eval} combos avaliados (de {n_s2_combos} definidos) "
             f"advance -> ANY_PASS={s2['pass_']}")

        s3 = all_results["S3"][market]
        n_s3_combos = len(s3["combos"])
        n_s3_eval = sum(1 for c in s3["combos"].values() if not c.get("skip"))
        n_s3_pass = sum(1 for c in s3["combos"].values() if not c.get("skip") and c.get("pass_"))
        slog(f"S3  (reversao):     {n_s3_pass}/{n_s3_eval} combos avaliados (de {n_s3_combos} definidos) "
             f"advance -> ANY_PASS={s3['pass_']}")

        s4t = all_results["S4_TOM"][market]
        slog(f"S4-TOM:             {cascade_str(s4t['disc_pass'], s4t['confirm_pass'], s4t['g3_pass'], s4t['real_pass'])} "
             f"-> ADVANCE_CANDIDATE={s4t['pass_']}")

        s4d = all_results["S4_DOW"][market]
        slog(f"S4-DOW:             {cascade_str(s4d['disc_pass'], s4d['confirm_pass'], s4d['g3_pass'], s4d['real_pass'])} "
             f"-> ADVANCE_CANDIDATE={s4d['pass_']}")

    # ------------------------------------------------------------------
    # Contagem de testes
    # ------------------------------------------------------------------
    n_tests_s1 = 1
    n_tests_s2 = sum(len([c for c in all_results["S2"][m]["combos"].values() if not c.get("skip")])
                      for m in MARKETS)
    n_tests_s3 = sum(len([c for c in all_results["S3"][m]["combos"].values() if not c.get("skip")])
                      for m in MARKETS)
    n_tests_s4tom = len(MARKETS)
    n_tests_s4dow = len(MARKETS)
    n_tests_total = n_tests_s1 + n_tests_s2 + n_tests_s3 + n_tests_s4tom + n_tests_s4dow
    expected_fp = n_tests_total * 0.05

    slog("\n" + "=" * 78)
    slog("CONTAGEM DE TESTES (etapa DISCOVERY) E FALSOS POSITIVOS ESPERADOS")
    slog("=" * 78)
    slog(f"S1:      {n_tests_s1} teste (WDO apenas)")
    slog(f"S2:      {n_tests_s2} testes (combos L x F validos x mercado, de "
         f"{len(S2_LS)*len(S2_FS)*len(MARKETS)} definidos)")
    slog(f"S3:      {n_tests_s3} testes (combos P x F validos x mercado, de "
         f"{len(S3_PS)*len(S3_FS)*len(MARKETS) - len(S3_PS)} definidos com restricao WDO F>=3)")
    slog(f"S4-TOM:  {n_tests_s4tom} testes (1 gate/mercado)")
    slog(f"S4-DOW:  {n_tests_s4dow} testes (1 gate/mercado)")
    slog(f"TOTAL DISCOVERY:   {n_tests_total} testes com IC (contador em runtime: {N_TESTS_DISCOVERY})")
    slog(f"Falsos positivos esperados a 5% na etapa DISCOVERY (sem nenhum efeito real): "
         f"~{expected_fp:.1f} (~{round(expected_fp)})")
    slog("NOTA: cada combo que passa DISCOVERY ainda precisa passar CONFIRM + G3 + "
         "REAL-CHECK antes de virar candidato -- a cascata de 4 etapas e a defesa "
         "contra os falsos positivos esperados do multiple-comparisons acima. S2/S3 "
         "usam regra 'passa se >=1 combo passar a cascata inteira' -- qualquer PASS "
         "isolado ainda deve ser lido com ceticismo proporcional ao numero de combos "
         "testados.")

    # ------------------------------------------------------------------
    # Decisao mecanica por tese
    # ------------------------------------------------------------------
    slog("\n" + "=" * 78)
    slog("DECISAO MECANICA POR TESE")
    slog("=" * 78)

    thesis_decision = {}

    passed_s1 = s1["pass_"]
    thesis_decision["S1"] = ("ADVANCE_TO_PHASE1 (WDO)" if passed_s1 else
                              "PREMISE_REFUTED (WDO nao passou a cascata completa)")
    slog(f"S1: {thesis_decision['S1']}")

    for thesis in ["S2", "S3"]:
        passed_markets = [m for m in MARKETS if all_results[thesis][m]["pass_"]]
        if passed_markets:
            thesis_decision[thesis] = f"ADVANCE_TO_PHASE1 (mercado(s): {', '.join(passed_markets)})"
        else:
            thesis_decision[thesis] = "PREMISE_REFUTED (nenhum mercado passou a cascata completa)"
        slog(f"{thesis}: {thesis_decision[thesis]}")

    for thesis in ["S4_TOM", "S4_DOW"]:
        passed_markets = [m for m in MARKETS if all_results[thesis][m]["pass_"]]
        if passed_markets:
            thesis_decision[thesis] = f"ADVANCE_TO_PHASE1 (mercado(s): {', '.join(passed_markets)})"
        else:
            thesis_decision[thesis] = "PREMISE_REFUTED (nenhum mercado passou a cascata completa)"
        slog(f"{thesis}: {thesis_decision[thesis]}")

    any_advance = any("ADVANCE" in v for v in thesis_decision.values())
    overall = ("ADVANCE_TO_PHASE1 (>=1 tese com >=1 mercado passando a cascata "
               "DISCOVERY+CONFIRM+G3+REAL-CHECK completa)" if any_advance else
               "PREMISE_REFUTED_DEFINITIVO (nenhuma tese passou a cascata completa em "
               "nenhum mercado -- familia swing daily B3 classica encerrada, sem post-hoc, "
               "conforme decisao pre-registrada)")
    slog(f"\nDECISAO GERAL DA CAMPANHA: {overall}")

    # ------------------------------------------------------------------
    # Salvar outputs
    # ------------------------------------------------------------------
    results_df = pd.DataFrame(RESULT_ROWS)
    results_df.to_csv(os.path.join(OUT_DIR, "proxy_map_results.csv"), index=False)
    slog(f"\nproxy_map_results.csv: {len(results_df)} linhas salvas em {OUT_DIR}")

    with open(os.path.join(OUT_DIR, "PROXY_MAP_SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    print(f"\nOutputs salvos em: {OUT_DIR}")


if __name__ == "__main__":
    main()
