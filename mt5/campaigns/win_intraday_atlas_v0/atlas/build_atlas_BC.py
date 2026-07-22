"""
win_intraday_atlas_v0 -- Baterias B (gaps e niveis D-1) e C (preditores de direcao)

Script autossuficiente (paths absolutos, sem env vars). Rodar:
    python build_atlas_BC.py

Le PROTOCOL.md em ..\PROTOCOL.md para o desenho anti-data-mining. Resumo das
regras respeitadas aqui:
  - OOS 2025+ NUNCA entra em nenhuma estatistica (filtrado no load).
  - DISCOVERY = ate 2023-12-31 ; CONFIRM = 2024 inteiro. Tudo calculado nos
    dois splits, rotulado.
  - Zero trades simulados -- so descritivo (contagens, medias, bootstrap).
  - Grade B3/B4 EXATAMENTE a do protocolo: N em {10,20,30} bps, M em {5,10} bps.
  - Bootstrap 1000x seed 42 para IC de medias.
  - Conta e imprime o numero total de celulas/testes estatisticos reportados.

Saidas em atlas/ (mesma pasta deste script):
  BC_gaps.csv, BC_gapfill.csv, BC_levels_touch.csv, BC_firsttouch.csv,
  BC_postbreak.csv, BC_magnets.csv, BC_direction_matrix.csv,
  BC_interactions.csv, BC_SUMMARY.txt
"""

import numpy as np
import pandas as pd
from pathlib import Path
import sys
import time

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

BASE_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\win_intraday_atlas_v0\atlas")
DATA_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history")
M15_PATH = DATA_DIR / "WIN_cont_N_M15.parquet"
M5_PATH = DATA_DIR / "WIN_cont_N_M5.parquet"

DISCOVERY_END = pd.Timestamp("2023-12-31")
CONFIRM_START = pd.Timestamp("2024-01-01")
CONFIRM_END = pd.Timestamp("2024-12-31")
OOS_START = pd.Timestamp("2025-01-01")  # tudo >= isso eh descartado no load

N_BOOT = 1000
SEED = 42

GRID_N_BPS = [10, 20, 30]   # limiar de reversao para "bounce"
GRID_M_BPS = [5, 10]        # limiar de avanco para "break"

BREAK_CONFIRM_M_BPS = 10.0  # limiar de "break confirmado" usado na B4 (fixo, documentado)
RETEST_WINDOW_MIN = 60
FOLLOWTHROUGH_HORIZONS_MIN = [15, 30, 60]

GAP_BUCKET_EDGES = [
    ("(-inf,-40]", -np.inf, -40, "closed_right"),
    ("(-40,-15]", -40, -15, "closed_right"),
    ("(-15,0)", -15, 0, "open_right"),
    ("{0}", 0, 0, "exact"),
    ("(0,15]", 0, 15, "closed_right_excl_left"),
    ("(15,40]", 15, 40, "closed_right"),
    ("(40,inf)", 40, np.inf, "open"),
]

OUT = BASE_DIR
OUT.mkdir(parents=True, exist_ok=True)

# global registry of every bootstrap-CI "effect" test computed, for the
# candidate scan + total-test-count at the end.
ALL_EFFECT_TESTS = []   # list of dict rows
ALL_CELLS_COUNT = 0     # total de linhas reportadas em qualquer CSV (contexto mult. comparacoes)

rng_master = np.random.default_rng(SEED)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------

def bootstrap_mean_ci(values, n_boot=N_BOOT, seed=SEED):
    """Retorna (mean, ci_lo, ci_hi, n) via bootstrap percentil 2.5/97.5."""
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = arr.size
    if n == 0:
        return np.nan, np.nan, np.nan, 0
    if n == 1:
        return arr[0], np.nan, np.nan, 1
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    boot_means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(boot_means, [2.5, 97.5])
    return float(arr.mean()), float(lo), float(hi), int(n)


def record_effect_test(test_id, description, split, n, effect_bps, ci_lo, ci_hi, extra=None):
    """Registra uma celula de efeito (bps) com IC bootstrap no registro global."""
    row = dict(test_id=test_id, description=description, split=split, n=n,
               effect_bps=effect_bps, ci_lo=ci_lo, ci_hi=ci_hi)
    if extra:
        row.update(extra)
    ALL_EFFECT_TESTS.append(row)


def count_cells(n):
    global ALL_CELLS_COUNT
    ALL_CELLS_COUNT += n


def sign_bucket(x):
    if pd.isna(x):
        return np.nan
    if x > 0:
        return "pos"
    if x < 0:
        return "neg"
    return "zero"


def gap_bucket(x):
    if pd.isna(x):
        return np.nan
    if x <= -40:
        return "(-inf,-40]"
    if x <= -15:
        return "(-40,-15]"
    if x < 0:
        return "(-15,0)"
    if x == 0:
        return "{0}"
    if x <= 15:
        return "(0,15]"
    if x <= 40:
        return "(15,40]"
    return "(40,inf)"


def tercile_bucket(pos):
    if pd.isna(pos):
        return np.nan
    if pos < 1 / 3:
        return "baixo"
    if pos < 2 / 3:
        return "meio"
    return "alto"


def split_of(date):
    ts = pd.Timestamp(date)
    if ts <= DISCOVERY_END:
        return "discovery"
    if CONFIRM_START <= ts <= CONFIRM_END:
        return "confirm"
    return None  # nao deveria acontecer pos-filtro


# --------------------------------------------------------------------------
# LOAD & PREPROCESS
# --------------------------------------------------------------------------

def load_bars(path):
    df = pd.read_parquet(path)
    df = df[df["datetime_b3"] < OOS_START].copy()   # OOS sagrado fora, sempre
    df["date"] = df["datetime_b3"].dt.date
    df["time"] = df["datetime_b3"].dt.time
    return df


def build_daily(m15):
    """Constroi tabela diaria a partir das barras M15, ja filtrada para dias
    que abrem exatamente as 09:00 (descarta Cinzas / sessoes curtas)."""
    g = m15.sort_values("datetime_b3").groupby("date", sort=True)

    first_time = g["time"].min()
    valid_days = first_time[first_time == pd.to_datetime("09:00:00").time()].index
    n_dropped = first_time.shape[0] - len(valid_days)
    log(f"Dias descartados por nao abrir as 09:00 (Cinzas/sessao curta): {n_dropped}")

    rows = []
    for date, grp in g:
        if date not in valid_days:
            continue
        grp = grp.sort_values("datetime_b3").reset_index(drop=True)
        n_bars = len(grp)
        open_ = grp.loc[0, "open"]
        close = grp.loc[n_bars - 1, "close"]
        high = grp["high"].max()
        low = grp["low"].min()
        first_bar_close = grp.loc[0, "close"]
        # 1a hora = 09:00-10:00 -> barras M15 idx 0..3, fecha em 10:00 = close da barra idx3
        close_10 = grp.loc[3, "close"] if n_bars > 3 else np.nan
        # open->11:00 -> barras idx 0..7, fecha em 11:00 = close da barra idx7
        close_11 = grp.loc[7, "close"] if n_bars > 7 else np.nan
        weekday_num = pd.Timestamp(date).weekday()
        weekday_name = pd.Timestamp(date).day_name()
        rows.append(dict(date=date, n_bars=n_bars, open=open_, close=close, high=high,
                          low=low, first_bar_close=first_bar_close, close_10=close_10,
                          close_11=close_11, weekday_num=weekday_num, weekday_name=weekday_name))

    daily = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    daily["date"] = pd.to_datetime(daily["date"])

    # D-1 / D-2 lookups (proximo dia de sessao VALIDO anterior, ignora gaps de calendario)
    for col in ["open", "close", "high", "low"]:
        daily[f"{col}_d1"] = daily[col].shift(1)
        daily[f"{col}_d2"] = daily[col].shift(2)
    daily["date_d1"] = daily["date"].shift(1)

    daily["ret_open_close_bps"] = (daily["close"] - daily["open"]) / daily["open"] * 1e4
    daily["ret_open_11_bps"] = (daily["close_11"] - daily["open"]) / daily["open"] * 1e4
    daily["ret_10_close_bps"] = (daily["close"] - daily["close_10"]) / daily["close_10"] * 1e4
    daily["ret_bar1_bps"] = (daily["first_bar_close"] - daily["open"]) / daily["open"] * 1e4
    daily["ret_hour1_bps"] = (daily["close_10"] - daily["open"]) / daily["open"] * 1e4
    daily["ret_d1_bps"] = (daily["close_d1"] - daily["open_d1"]) / daily["open_d1"] * 1e4

    daily["gap_bps"] = (daily["open"] - daily["close_d1"]) / daily["close_d1"] * 1e4
    daily["gap_bucket"] = daily["gap_bps"].apply(gap_bucket)
    daily["gap_sign"] = daily["gap_bps"].apply(sign_bucket)
    daily["day_sign"] = daily["ret_open_close_bps"].apply(sign_bucket)
    daily["bar1_sign"] = daily["ret_bar1_bps"].apply(sign_bucket)
    daily["hour1_sign"] = daily["ret_hour1_bps"].apply(sign_bucket)
    daily["d1_sign"] = daily["ret_d1_bps"].apply(sign_bucket)

    daily["pdh"] = daily["high_d1"]
    daily["pdl"] = daily["low_d1"]
    daily["mid_d1"] = (daily["pdh"] + daily["pdl"]) / 2

    daily["inside_d1"] = (daily["high_d1"] <= daily["high_d2"]) & (daily["low_d1"] >= daily["low_d2"])

    d1_range = daily["high_d1"] - daily["low_d1"]
    open_pos = (daily["open"] - daily["low_d1"]) / d1_range.replace(0, np.nan)
    daily["open_pos_d1"] = open_pos.clip(0, 1)
    daily["open_tercile_d1"] = daily["open_pos_d1"].apply(tercile_bucket)

    # flag de dia de rolagem: |gap| > 3x std rolante 60d (trailing, exclui dia atual) OU |gap|>80bps
    roll_std = daily["gap_bps"].rolling(window=60, min_periods=20).std().shift(1)
    daily["gap_roll_std60"] = roll_std
    daily["is_rollover"] = (daily["gap_bps"].abs() > 3 * roll_std) | (daily["gap_bps"].abs() > 80)
    daily["is_rollover"] = daily["is_rollover"].fillna(False)

    daily["split"] = daily["date"].apply(split_of)
    daily["year"] = daily["date"].dt.year

    # primeira linha (sem D-1) e segunda (sem D-2) ficam com NaN nos campos D-1/D-2 -> ok, dropna por metrica
    return daily


# --------------------------------------------------------------------------
# BATERIA B1 -- gaps
# --------------------------------------------------------------------------

def bateria_b1(daily):
    log("B1: distribuicao de gaps...")
    rows_stats = []
    rows_bucket = []

    def stats_block(sub, split_label, year_label, rollover_label):
        if len(sub) == 0:
            return
        g = sub["gap_bps"].dropna()
        if len(g) == 0:
            return
        rows_stats.append(dict(
            row_type="stats", split=split_label, year=year_label, rollover_filter=rollover_label,
            n_days=len(g), median_bps=g.median(), q25_bps=g.quantile(.25), q75_bps=g.quantile(.75),
            mean_bps=g.mean(), pct_positive=(g > 0).mean() * 100,
        ))
        count_cells(1)
        for bname, _, _, _ in GAP_BUCKET_EDGES:
            bsub = sub[sub["gap_bucket"] == bname]
            rows_bucket.append(dict(
                row_type="bucket", split=split_label, year=year_label, rollover_filter=rollover_label,
                bucket=bname, n=len(bsub), pct_of_days=len(bsub) / len(sub) * 100 if len(sub) else np.nan,
            ))
            count_cells(1)

    valid = daily.dropna(subset=["gap_bps", "split"])
    for split_label in ["discovery", "confirm"]:
        sub_split = valid[valid["split"] == split_label]
        for rollover_label, sub_roll in [("all_days", sub_split), ("excl_rollover", sub_split[~sub_split["is_rollover"]])]:
            stats_block(sub_roll, split_label, "ALL", rollover_label)
            for yr, sub_yr in sub_roll.groupby("year"):
                stats_block(sub_yr, split_label, str(yr), rollover_label)

    df_stats = pd.DataFrame(rows_stats)
    df_bucket = pd.DataFrame(rows_bucket)
    out = pd.concat([df_stats, df_bucket], ignore_index=True, sort=False)
    out.to_csv(OUT / "BC_gaps.csv", index=False)
    log(f"  -> BC_gaps.csv ({len(out)} linhas)")
    return out


# --------------------------------------------------------------------------
# BATERIA B2 -- gap fill
# --------------------------------------------------------------------------

def first_touch_in_day(bars_sorted, level, day_open_dt):
    """bars_sorted: DataFrame de barras de UM dia, ordenado, com datetime_b3/low/high.
    Retorna (touched: bool, minutes_to_touch: float)."""
    if pd.isna(level) or len(bars_sorted) == 0:
        return False, np.nan
    lows = bars_sorted["low"].values
    highs = bars_sorted["high"].values
    touched_mask = (lows <= level) & (highs >= level)
    if not touched_mask.any():
        return False, np.nan
    idx = np.argmax(touched_mask)
    t = bars_sorted["datetime_b3"].values[idx]
    minutes = (pd.Timestamp(t) - day_open_dt).total_seconds() / 60.0
    return True, minutes


def bateria_b2(daily, m15):
    log("B2: gap fill + continuation/fade...")
    m15_by_date = {d: grp.sort_values("datetime_b3").reset_index(drop=True)
                   for d, grp in m15.groupby("date")}

    fill_flags = []
    fill_minutes = []
    for _, row in daily.iterrows():
        date = row["date"].date()
        level = row["close_d1"]
        bars = m15_by_date.get(date)
        if bars is None:
            fill_flags.append(np.nan); fill_minutes.append(np.nan); continue
        day_open_dt = pd.Timestamp(date) + pd.Timedelta(hours=9)
        touched, minutes = first_touch_in_day(bars, level, day_open_dt)
        fill_flags.append(touched)
        fill_minutes.append(minutes)
    daily = daily.copy()
    # boolean+NaN vindo de lista Python vira dtype object -> sum()/mean() quebram
    # silenciosamente (viram "any"/logico em vez de contagem aritmetica); usar
    # dtype nullable "boolean" corrige isso.
    daily["fill_closeD1_touched"] = pd.Series(fill_flags, index=daily.index).astype("boolean")
    daily["fill_closeD1_minutes"] = fill_minutes

    rows = []
    valid = daily.dropna(subset=["gap_bucket", "split"])
    for split_label in ["discovery", "confirm"]:
        sub_split = valid[valid["split"] == split_label]
        for rollover_label, sub_roll_split in [("all_days", sub_split), ("excl_rollover", sub_split[~sub_split["is_rollover"]])]:
            for bname, _, _, _ in GAP_BUCKET_EDGES:
                sub = sub_roll_split[sub_roll_split["gap_bucket"] == bname]
                n_days = len(sub)
                if n_days == 0:
                    continue
                pct_fill = sub["fill_closeD1_touched"].mean() * 100
                n_filled = int(sub["fill_closeD1_touched"].sum())
                med_min = sub.loc[sub["fill_closeD1_touched"] == True, "fill_closeD1_minutes"].median()

                m_open11, lo_open11, hi_open11, n_open11 = bootstrap_mean_ci(sub["ret_open_11_bps"])
                m_openclose, lo_openclose, hi_openclose, n_openclose = bootstrap_mean_ci(sub["ret_open_close_bps"])

                # so registra no scan de candidatos a versao 'all_days' (canonica), p/ evitar
                # test_id duplicado; 'excl_rollover' fica so como checagem de robustez no CSV.
                if rollover_label == "all_days":
                    tid_11 = f"B2_contfade_open11_{bname}"
                    tid_close = f"B2_contfade_openclose_{bname}"
                    record_effect_test(tid_11, f"B2 gap bucket {bname}: ret open->11:00", split_label, n_open11, m_open11, lo_open11, hi_open11)
                    record_effect_test(tid_close, f"B2 gap bucket {bname}: ret open->close", split_label, n_openclose, m_openclose, lo_openclose, hi_openclose)
                    count_cells(2)

                rows.append(dict(
                    split=split_label, rollover_filter=rollover_label, bucket=bname, n_days=n_days,
                    pct_fill_same_day=pct_fill, n_filled=n_filled, median_minutes_to_fill=med_min,
                    ret_open11_mean_bps=m_open11, ret_open11_ci_lo=lo_open11, ret_open11_ci_hi=hi_open11,
                    ret_openclose_mean_bps=m_openclose, ret_openclose_ci_lo=lo_openclose, ret_openclose_ci_hi=hi_openclose,
                ))
                count_cells(1)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "BC_gapfill.csv", index=False)
    log(f"  -> BC_gapfill.csv ({len(out)} linhas)")
    return daily, out


# --------------------------------------------------------------------------
# BATERIA B3 -- toque PDH/PDL (% de dias) + first-touch bounce/break grid
# --------------------------------------------------------------------------

def bateria_b3_touch(daily, m15):
    log("B3: % de dias que tocam PDH/PDL...")
    m15_by_date = {d: grp.sort_values("datetime_b3").reset_index(drop=True)
                   for d, grp in m15.groupby("date")}

    touch_pdh = []
    touch_pdl = []
    for _, row in daily.iterrows():
        date = row["date"].date()
        bars = m15_by_date.get(date)
        if bars is None:
            touch_pdh.append(np.nan); touch_pdl.append(np.nan); continue
        pdh, pdl = row["pdh"], row["pdl"]
        th = ((bars["low"] <= pdh) & (bars["high"] >= pdh)).any() if pd.notna(pdh) else np.nan
        tl = ((bars["low"] <= pdl) & (bars["high"] >= pdl)).any() if pd.notna(pdl) else np.nan
        touch_pdh.append(th)
        touch_pdl.append(tl)
    daily = daily.copy()
    # ver nota em bateria_b2 sobre dtype "boolean" nullable (object quebra sum/mean)
    daily["touch_pdh"] = pd.Series(touch_pdh, index=daily.index).astype("boolean")
    daily["touch_pdl"] = pd.Series(touch_pdl, index=daily.index).astype("boolean")

    rows = []
    valid = daily.dropna(subset=["touch_pdh", "touch_pdl", "split"])
    for split_label in ["discovery", "confirm"]:
        sub = valid[valid["split"] == split_label]
        n = len(sub)
        both = (sub["touch_pdh"] & sub["touch_pdl"]).mean() * 100
        none = (~sub["touch_pdh"] & ~sub["touch_pdl"]).mean() * 100
        rows.append(dict(split=split_label, n_days=n,
                          pct_touch_pdh=sub["touch_pdh"].mean() * 100,
                          pct_touch_pdl=sub["touch_pdl"].mean() * 100,
                          pct_touch_both=both, pct_touch_none=none))
        count_cells(1)
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "BC_levels_touch.csv", index=False)
    log(f"  -> BC_levels_touch.csv ({len(out)} linhas)")
    return daily, out


def walk_day_bars(bars, level, direction):
    """bars: DataFrame ordenado de um dia (M5 ou M15), com datetime_b3/open/high/low/close.
    level: preco do nivel (PDH ou PDL). direction: +1 se o nivel eh resistencia (PDH,
    'break' = subir), -1 se suporte (PDL, 'break' = descer).
    Retorna dict com arrays de extensao (bps, direcao do break) e reversao (bps, direcao oposta),
    cummax, e indices/tempos, a partir da barra de 1o toque (inclusive)."""
    lows = bars["low"].values
    highs = bars["high"].values
    closes = bars["close"].values
    times = bars["datetime_b3"].values
    touched_mask = (lows <= level) & (highs >= level)
    if not touched_mask.any():
        return None
    touch_idx = int(np.argmax(touched_mask))
    sub_high = highs[touch_idx:]
    sub_low = lows[touch_idx:]
    sub_close = closes[touch_idx:]
    sub_time = times[touch_idx:]

    if direction == 1:  # PDH: break = subir alem do nivel; reversao = cair abaixo do nivel
        bar_ext = np.maximum(0, (sub_high - level) / level * 1e4)
        bar_rev = np.maximum(0, (level - sub_low) / level * 1e4)
    else:  # PDL: break = cair abaixo; reversao = subir acima
        bar_ext = np.maximum(0, (level - sub_low) / level * 1e4)
        bar_rev = np.maximum(0, (sub_high - level) / level * 1e4)

    cummax_ext = np.maximum.accumulate(bar_ext)
    cummax_rev = np.maximum.accumulate(bar_rev)
    return dict(touch_idx=touch_idx, sub_time=sub_time, sub_close=sub_close,
                cummax_ext=cummax_ext, cummax_rev=cummax_rev,
                sub_high=sub_high, sub_low=sub_low)


def first_idx_ge(cummax_arr, threshold):
    idx = np.argmax(cummax_arr >= threshold)
    if cummax_arr[idx] < threshold:
        return None  # nunca atingiu
    return int(idx)


def bateria_b3_firsttouch(daily, m15, m5):
    log("B3: first-touch bounce/break grid (M5 onde disponivel, senao M15)...")
    m15_by_date = {d: grp.sort_values("datetime_b3").reset_index(drop=True)
                   for d, grp in m15.groupby("date")}
    m5_by_date = {d: grp.sort_values("datetime_b3").reset_index(drop=True)
                  for d, grp in m5.groupby("date")}

    # resultados por dia x nivel: classificacao por combo (N,M)
    records = []  # cada elem: dict com date, split, level, resolucao usada, walk dict

    for _, row in daily.iterrows():
        date = row["date"].date()
        split_label = row["split"]
        if split_label is None:
            continue
        bars5 = m5_by_date.get(date)
        bars15 = m15_by_date.get(date)
        use_m5 = bars5 is not None and len(bars5) > 0
        bars = bars5 if use_m5 else bars15
        if bars is None or len(bars) == 0:
            continue
        for level_name, direction in [("PDH", 1), ("PDL", -1)]:
            level = row["pdh"] if level_name == "PDH" else row["pdl"]
            if pd.isna(level):
                continue
            walk = walk_day_bars(bars, level, direction)
            if walk is None:
                continue
            records.append(dict(date=date, split=split_label, level=level_name,
                                 resolution="M5" if use_m5 else "M15", walk=walk, row=row))

    log(f"  dias-nivel com toque: {len(records)}")

    rows_grid = []
    for level_name in ["PDH", "PDL"]:
        for split_label in ["discovery", "confirm"]:
            recs = [r for r in records if r["level"] == level_name and r["split"] == split_label]
            for N in GRID_N_BPS:
                for M in GRID_M_BPS:
                    n_bounce = n_break = n_tie = n_censored = 0
                    for r in recs:
                        w = r["walk"]
                        idx_b = first_idx_ge(w["cummax_rev"], N)
                        idx_k = first_idx_ge(w["cummax_ext"], M)
                        if idx_b is None and idx_k is None:
                            n_censored += 1
                        elif idx_b is not None and (idx_k is None or idx_b < idx_k):
                            n_bounce += 1
                        elif idx_k is not None and (idx_b is None or idx_k < idx_b):
                            n_break += 1
                        else:
                            # idx_b == idx_k: tie no mesmo bar -> tiebreak pelo close do bar
                            close_px = w["sub_close"][idx_b]
                            level = r["row"]["pdh"] if level_name == "PDH" else r["row"]["pdl"]
                            direction = 1 if level_name == "PDH" else -1
                            beyond = (close_px - level) * direction > 0
                            if beyond:
                                n_break += 1
                            else:
                                n_bounce += 1
                            n_tie += 1
                    n_total = len(recs)
                    n_decided = n_bounce + n_break
                    rows_grid.append(dict(
                        level=level_name, split=split_label, N_bps=N, M_bps=M,
                        n_touches=n_total, n_bounce=n_bounce, n_break=n_break,
                        n_tie=n_tie, n_censored=n_censored,
                        pct_bounce_of_decided=n_bounce / n_decided * 100 if n_decided else np.nan,
                        pct_break_of_decided=n_break / n_decided * 100 if n_decided else np.nan,
                        pct_bounce_of_all=n_bounce / n_total * 100 if n_total else np.nan,
                        pct_break_of_all=n_break / n_total * 100 if n_total else np.nan,
                        pct_censored=n_censored / n_total * 100 if n_total else np.nan,
                    ))
                    count_cells(1)

    out = pd.DataFrame(rows_grid)
    out.to_csv(OUT / "BC_firsttouch.csv", index=False)
    log(f"  -> BC_firsttouch.csv ({len(out)} linhas)")
    return records, out


# --------------------------------------------------------------------------
# BATERIA B4 -- pos-break: follow-through, MFE/MAE, retest
# --------------------------------------------------------------------------

def bateria_b4(records, m15):
    log(f"B4: pos-break (break confirmado = fecha alem do nivel por >= {BREAK_CONFIRM_M_BPS} bps)...")
    m15_by_date = {d: grp.sort_values("datetime_b3").reset_index(drop=True)
                   for d, grp in m15.groupby("date")}

    rows_out = []
    for level_name in ["PDH", "PDL"]:
        for split_label in ["discovery", "confirm"]:
            recs = [r for r in records if r["level"] == level_name and r["split"] == split_label]
            ft = {h: [] for h in FOLLOWTHROUGH_HORIZONS_MIN}
            mfe_list, mae_list, retest_list = [], [], []
            n_breaks = 0
            for r in recs:
                w = r["walk"]
                direction = 1 if level_name == "PDH" else -1
                idx_k = first_idx_ge(w["cummax_ext"], BREAK_CONFIRM_M_BPS)
                if idx_k is None:
                    continue
                n_breaks += 1
                break_time = pd.Timestamp(w["sub_time"][idx_k])
                break_close = w["sub_close"][idx_k]

                date = r["date"]
                m15_bars = m15_by_date.get(date)
                if m15_bars is None:
                    continue
                m15_bars = m15_bars[m15_bars["datetime_b3"] >= break_time].reset_index(drop=True)
                if len(m15_bars) == 0:
                    continue

                for h in FOLLOWTHROUGH_HORIZONS_MIN:
                    target_t = break_time + pd.Timedelta(minutes=h)
                    cand = m15_bars[m15_bars["datetime_b3"] <= target_t]
                    if len(cand) == 0:
                        continue
                    px = cand.iloc[-1]["close"]
                    ret_bps = (px - break_close) / break_close * 1e4 * direction  # signed na direcao do break
                    ft[h].append(ret_bps)

                horizon_bars = m15_bars[m15_bars["datetime_b3"] <= break_time + pd.Timedelta(minutes=60)]
                if len(horizon_bars) > 0:
                    if direction == 1:
                        mfe = (horizon_bars["high"].max() - break_close) / break_close * 1e4
                        mae = (break_close - horizon_bars["low"].min()) / break_close * 1e4
                    else:
                        mfe = (break_close - horizon_bars["low"].min()) / break_close * 1e4
                        mae = (horizon_bars["high"].max() - break_close) / break_close * 1e4
                    mfe_list.append(mfe)
                    mae_list.append(mae)

                    level = r["row"]["pdh"] if level_name == "PDH" else r["row"]["pdl"]
                    retested = ((horizon_bars["low"] <= level) & (horizon_bars["high"] >= level)).any()
                    retest_list.append(bool(retested))

            row = dict(level=level_name, split=split_label, n_breaks=n_breaks)
            for h in FOLLOWTHROUGH_HORIZONS_MIN:
                m_, lo_, hi_, n_ = bootstrap_mean_ci(ft[h])
                row[f"ft_{h}_mean_bps"] = m_
                row[f"ft_{h}_ci_lo"] = lo_
                row[f"ft_{h}_ci_hi"] = hi_
                row[f"ft_{h}_n"] = n_
                record_effect_test(f"B4_ft{h}_{level_name}", f"B4 {level_name} follow-through +{h}min", split_label, n_, m_, lo_, hi_)
                count_cells(1)

            m_mfe, lo_mfe, hi_mfe, n_mfe = bootstrap_mean_ci(mfe_list)
            m_mae, lo_mae, hi_mae, n_mae = bootstrap_mean_ci(mae_list)
            row["mfe_mean_bps"] = m_mfe; row["mfe_ci_lo"] = lo_mfe; row["mfe_ci_hi"] = hi_mfe; row["mfe_n"] = n_mfe
            row["mae_mean_bps"] = m_mae; row["mae_ci_lo"] = lo_mae; row["mae_ci_hi"] = hi_mae; row["mae_n"] = n_mae
            record_effect_test(f"B4_mfe_{level_name}", f"B4 {level_name} MFE 60min", split_label, n_mfe, m_mfe, lo_mfe, hi_mfe)
            record_effect_test(f"B4_mae_{level_name}", f"B4 {level_name} MAE 60min", split_label, n_mae, m_mae, lo_mae, hi_mae)
            count_cells(2)

            row["pct_retest_60min"] = np.mean(retest_list) * 100 if retest_list else np.nan
            row["retest_n"] = len(retest_list)
            count_cells(1)

            rows_out.append(row)

    out = pd.DataFrame(rows_out)
    out.to_csv(OUT / "BC_postbreak.csv", index=False)
    log(f"  -> BC_postbreak.csv ({len(out)} linhas)")
    return out


# --------------------------------------------------------------------------
# BATERIA B5 -- close D-1 e mid D-1 como imas
# --------------------------------------------------------------------------

def bateria_b5(daily, m15):
    log("B5: close D-1 e mid D-1 como imas...")
    m15_by_date = {d: grp.sort_values("datetime_b3").reset_index(drop=True)
                   for d, grp in m15.groupby("date")}

    rows = []
    for magnet_name, col in [("close_D-1", "close_d1"), ("mid_D-1", "mid_d1")]:
        touched_flags = []
        ret30_list_by_idx = {}
        rets30 = []
        idxs_touched = []
        for i, row in daily.iterrows():
            date = row["date"].date()
            level = row[col]
            bars = m15_by_date.get(date)
            if bars is None or pd.isna(level):
                touched_flags.append(np.nan)
                continue
            touched_mask = (bars["low"] <= level) & (bars["high"] >= level)
            if touched_mask.any():
                touched_flags.append(True)
                touch_i = int(np.argmax(touched_mask.values))
                touch_time = bars.iloc[touch_i]["datetime_b3"]
                touch_price = level
                fwd = bars[bars["datetime_b3"] >= touch_time + pd.Timedelta(minutes=30)]
                if len(fwd) > 0:
                    px30 = fwd.iloc[0]["close"]
                    ret30 = (px30 - touch_price) / touch_price * 1e4
                    rets30.append(ret30)
            else:
                touched_flags.append(False)

        daily_col = f"touch_{col}"
        daily[daily_col] = pd.Series(touched_flags, index=daily.index).astype("boolean")

        valid = daily.dropna(subset=[daily_col, "split"])
        for split_label in ["discovery", "confirm"]:
            sub = valid[valid["split"] == split_label]
            n_days = len(sub)
            n_touch = int(sub[daily_col].sum())
            pct_touch = sub[daily_col].mean() * 100 if n_days else np.nan
            # ret30 recomputado so para o split (restrito aos dias do sub)
            rets30_split = []
            for i, row in sub.iterrows():
                date = row["date"].date()
                level = row[col]
                if pd.isna(level) or not row[daily_col]:
                    continue
                bars = m15_by_date.get(date)
                if bars is None:
                    continue
                touched_mask = (bars["low"] <= level) & (bars["high"] >= level)
                if not touched_mask.any():
                    continue
                touch_i = int(np.argmax(touched_mask.values))
                touch_time = bars.iloc[touch_i]["datetime_b3"]
                fwd = bars[bars["datetime_b3"] >= touch_time + pd.Timedelta(minutes=30)]
                if len(fwd) > 0:
                    px30 = fwd.iloc[0]["close"]
                    rets30_split.append((px30 - level) / level * 1e4)

            m_, lo_, hi_, n_ = bootstrap_mean_ci(rets30_split)
            record_effect_test(f"B5_ret30_{magnet_name}", f"B5 {magnet_name} ret 30min pos-toque", split_label, n_, m_, lo_, hi_)
            count_cells(1)

            rows.append(dict(magnet=magnet_name, split=split_label, n_days=n_days, n_touched=n_touch,
                              pct_touch=pct_touch, ret30_mean_bps=m_, ret30_ci_lo=lo_, ret30_ci_hi=hi_, ret30_n=n_))
            count_cells(1)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "BC_magnets.csv", index=False)
    log(f"  -> BC_magnets.csv ({len(out)} linhas)")
    return out


# --------------------------------------------------------------------------
# BATERIA C1 -- matriz condicional de direcao do dia
# --------------------------------------------------------------------------

PREDICTORS = [
    ("gap_sign", "gap_sign", True),
    ("bar1_sign", "bar1_sign", True),
    ("hour1_sign", "hour1_sign", True),
    ("d1_sign", "d1_sign", True),
    ("weekday", "weekday_name", False),
    ("inside_day_d1", "inside_d1", False),
    ("open_tercile_d1", "open_tercile_d1", False),
]


def bateria_c1(daily):
    log("C1: matriz condicional de direcao do dia...")
    rows = []
    valid_base = daily.dropna(subset=["day_sign", "ret_open_close_bps", "split"])

    for pred_name, col, is_directional in PREDICTORS:
        sub_all = valid_base.dropna(subset=[col])
        for split_label in ["discovery", "confirm"]:
            sub_split = sub_all[sub_all["split"] == split_label]
            for value, sub in sub_split.groupby(col):
                n = len(sub)
                pct_pos = (sub["day_sign"] == "pos").mean() * 100
                hit_rate_match = np.nan
                if is_directional and value in ("pos", "neg"):
                    hit_rate_match = (sub["day_sign"] == value).mean() * 100

                m_oc, lo_oc, hi_oc, n_oc = bootstrap_mean_ci(sub["ret_open_close_bps"])
                tid = f"C1_{pred_name}_{value}_openclose"
                record_effect_test(tid, f"C1 {pred_name}={value}: ret open->close", split_label, n_oc, m_oc, lo_oc, hi_oc)
                count_cells(1)

                row = dict(predictor=pred_name, value=str(value), split=split_label, n=n,
                           pct_positive_day=pct_pos, hit_rate_matching_sign=hit_rate_match,
                           target="ret_open_close_bps", mean_bps=m_oc, ci_lo=lo_oc, ci_hi=hi_oc)
                rows.append(row)
                count_cells(1)

                if pred_name == "hour1_sign":
                    m_10, lo_10, hi_10, n_10 = bootstrap_mean_ci(sub["ret_10_close_bps"])
                    tid2 = f"C1_{pred_name}_{value}_10close"
                    record_effect_test(tid2, f"C1 {pred_name}={value}: ret 10:00->close", split_label, n_10, m_10, lo_10, hi_10)
                    count_cells(1)
                    row2 = dict(predictor=pred_name, value=str(value), split=split_label, n=n_10,
                                pct_positive_day=pct_pos, hit_rate_matching_sign=hit_rate_match,
                                target="ret_10_close_bps", mean_bps=m_10, ci_lo=lo_10, ci_hi=hi_10)
                    rows.append(row2)
                    count_cells(1)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "BC_direction_matrix.csv", index=False)
    log(f"  -> BC_direction_matrix.csv ({len(out)} linhas)")
    return out


# --------------------------------------------------------------------------
# BATERIA C2 -- interacoes pre-listadas
# --------------------------------------------------------------------------

INTERACTIONS = [
    ("gap_x_hour1", "gap_sign", "hour1_sign", "ret_10_close_bps"),
    ("d1_x_gap", "d1_sign", "gap_sign", "ret_open_close_bps"),
    ("weekday_x_hour1", "weekday_name", "hour1_sign", "ret_10_close_bps"),
]

MIN_N_DISCOVERY = 30


def bateria_c2(daily):
    log("C2: interacoes pre-listadas...")
    rows = []
    valid_base = daily.dropna(subset=["day_sign", "split"])

    for inter_name, colA, colB, target_col in INTERACTIONS:
        sub_all = valid_base.dropna(subset=[colA, colB, target_col])
        # n no discovery por celula, calculado primeiro p/ gating
        disc = sub_all[sub_all["split"] == "discovery"]
        n_disc_by_cell = disc.groupby([colA, colB]).size()

        for split_label in ["discovery", "confirm"]:
            sub_split = sub_all[sub_all["split"] == split_label]
            for (va, vb), sub in sub_split.groupby([colA, colB]):
                n = len(sub)
                n_disc_cell = n_disc_by_cell.get((va, vb), 0)
                is_na = n_disc_cell < MIN_N_DISCOVERY
                if is_na:
                    m_, lo_, hi_, n_ = np.nan, np.nan, np.nan, n
                else:
                    m_, lo_, hi_, n_ = bootstrap_mean_ci(sub[target_col])
                    tid = f"C2_{inter_name}_{va}_{vb}"
                    record_effect_test(tid, f"C2 {inter_name}=({va},{vb}) target={target_col}", split_label, n_, m_, lo_, hi_)
                    count_cells(1)

                rows.append(dict(interaction=inter_name, value_A=str(va), value_B=str(vb), split=split_label,
                                  n=n, n_discovery_cell=int(n_disc_cell), na_low_n=is_na,
                                  target=target_col, mean_bps=m_, ci_lo=lo_, ci_hi=hi_))
                count_cells(1)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "BC_interactions.csv", index=False)
    log(f"  -> BC_interactions.csv ({len(out)} linhas)")
    return out


# --------------------------------------------------------------------------
# CANDIDATE SCAN + SUMMARY
# --------------------------------------------------------------------------

def candidate_scan():
    df = pd.DataFrame(ALL_EFFECT_TESTS)
    disc = df[df["split"] == "discovery"].copy()
    conf = df[df["split"] == "confirm"].copy()
    conf_idx = conf.set_index("test_id")

    disc["ci_excludes_zero"] = (disc["ci_lo"] > 0) | (disc["ci_hi"] < 0)
    cand = disc[(disc["effect_bps"].abs() >= 6) & disc["ci_excludes_zero"]].copy()

    out_rows = []
    for _, r in cand.iterrows():
        tid = r["test_id"]
        conf_row = conf_idx.loc[tid] if tid in conf_idx.index else None
        out_rows.append(dict(
            test_id=tid, description=r["description"],
            discovery_n=r["n"], discovery_effect_bps=r["effect_bps"],
            discovery_ci_lo=r["ci_lo"], discovery_ci_hi=r["ci_hi"],
            confirm_n=conf_row["n"] if conf_row is not None else np.nan,
            confirm_effect_bps=conf_row["effect_bps"] if conf_row is not None else np.nan,
            confirm_ci_lo=conf_row["ci_lo"] if conf_row is not None else np.nan,
            confirm_ci_hi=conf_row["ci_hi"] if conf_row is not None else np.nan,
        ))
    out = pd.DataFrame(out_rows)
    if len(out) > 0:
        out["same_sign_confirm"] = np.sign(out["discovery_effect_bps"]) == np.sign(out["confirm_effect_bps"])
        out["confirm_effect_ge3bps"] = out["confirm_effect_bps"].abs() >= 3
        out = out.sort_values("discovery_effect_bps", key=lambda s: s.abs(), ascending=False)
    return out, df


def write_summary(daily, b1, b2out, b3touch, b3grid, b4out, b5out, c1out, c2out, cand, all_tests_df):
    lines = []
    lines.append("=" * 78)
    lines.append("win_intraday_atlas_v0 -- Baterias B (gaps/niveis D-1) e C (preditores)")
    lines.append("=" * 78)
    lines.append("")
    lines.append(f"Dados: {M15_PATH.name} (principal), {M5_PATH.name} (first-touch fino B3/B4)")
    lines.append(f"OOS 2025+ excluido no load. Splits: DISCOVERY <= {DISCOVERY_END.date()} ; "
                 f"CONFIRM = {CONFIRM_START.date()}..{CONFIRM_END.date()}")
    n_disc = (daily["split"] == "discovery").sum()
    n_conf = (daily["split"] == "confirm").sum()
    n_rollover = int(daily["is_rollover"].sum())
    lines.append(f"Dias validos (abrem 09:00): discovery={n_disc}, confirm={n_conf}, total={n_disc+n_conf}")
    lines.append(f"Dias flagados como rolagem (|gap|>3*std60 OU |gap|>80bps): {n_rollover}")
    lines.append(f"Grade B3/B4: N_bps={GRID_N_BPS}, M_bps={GRID_M_BPS} (6 combos) | break confirmado B4 usa M={BREAK_CONFIRM_M_BPS}bps fixo")
    lines.append(f"Bootstrap: {N_BOOT}x, seed={SEED}, IC percentil 2.5/97.5")
    lines.append("")

    lines.append("-" * 78)
    lines.append("B1 -- GAPS (resumo ALL days, discovery vs confirm)")
    lines.append("-" * 78)
    b1_stats = b1[(b1["row_type"] == "stats") & (b1["year"] == "ALL") & (b1["rollover_filter"] == "all_days")]
    for _, r in b1_stats.iterrows():
        lines.append(f"  {r['split']:10s} n={r['n_days']:5.0f}  mediana={r['median_bps']:+7.1f}bps  "
                     f"q25/q75=[{r['q25_bps']:+.1f},{r['q75_bps']:+.1f}]  %pos={r['pct_positive']:.1f}%")
    lines.append("  Buckets (discovery, all_days):")
    bd = b1[(b1["row_type"] == "bucket") & (b1["split"] == "discovery") & (b1["year"] == "ALL") & (b1["rollover_filter"] == "all_days")]
    for _, r in bd.iterrows():
        lines.append(f"    {r['bucket']:14s} n={r['n']:4.0f}  {r['pct_of_days']:.1f}%")
    lines.append("")

    lines.append("-" * 78)
    lines.append("B2 -- GAP FILL / CONTINUATION-FADE (por bucket x split, all_days; excl_rollover no CSV)")
    lines.append("-" * 78)
    for _, r in b2out[b2out["rollover_filter"] == "all_days"].iterrows():
        lines.append(f"  [{r['split']:10s}] {r['bucket']:14s} n={r['n_days']:4.0f} fill={r['pct_fill_same_day']:.1f}% "
                     f"med_min={r['median_minutes_to_fill']}  ret_o->11={r['ret_open11_mean_bps']:+.1f}bps "
                     f"[{r['ret_open11_ci_lo']:+.1f},{r['ret_open11_ci_hi']:+.1f}]  "
                     f"ret_o->c={r['ret_openclose_mean_bps']:+.1f}bps [{r['ret_openclose_ci_lo']:+.1f},{r['ret_openclose_ci_hi']:+.1f}]")
    lines.append("")

    lines.append("-" * 78)
    lines.append("B3 -- TOQUE PDH/PDL (% dias) e FIRST-TOUCH BOUNCE/BREAK")
    lines.append("-" * 78)
    for _, r in b3touch.iterrows():
        lines.append(f"  [{r['split']:10s}] n={r['n_days']:.0f}  touch_PDH={r['pct_touch_pdh']:.1f}%  "
                     f"touch_PDL={r['pct_touch_pdl']:.1f}%  ambos={r['pct_touch_both']:.1f}%  nenhum={r['pct_touch_none']:.1f}%")
    lines.append("  Grid N x M (pct entre decididos, exclui censura/empate):")
    for _, r in b3grid.iterrows():
        lines.append(f"    {r['level']:4s} [{r['split']:10s}] N={r['N_bps']:2.0f} M={r['M_bps']:2.0f}  "
                     f"n={r['n_touches']:4.0f}  bounce={r['pct_bounce_of_decided']:.1f}%  break={r['pct_break_of_decided']:.1f}%  "
                     f"censored={r['pct_censored']:.1f}%")
    lines.append("")

    lines.append("-" * 78)
    lines.append("B4 -- POS-BREAK (follow-through, MFE/MAE, retest)")
    lines.append("-" * 78)
    for _, r in b4out.iterrows():
        lines.append(f"  {r['level']:4s} [{r['split']:10s}] n_breaks={r['n_breaks']:.0f}  "
                     f"ft15={r['ft_15_mean_bps']:+.1f}bps  ft30={r['ft_30_mean_bps']:+.1f}bps  ft60={r['ft_60_mean_bps']:+.1f}bps  "
                     f"MFE={r['mfe_mean_bps']:+.1f}  MAE={r['mae_mean_bps']:+.1f}  retest60={r['pct_retest_60min']:.1f}%")
    lines.append("")

    lines.append("-" * 78)
    lines.append("B5 -- CLOSE D-1 / MID D-1 COMO IMAS")
    lines.append("-" * 78)
    for _, r in b5out.iterrows():
        lines.append(f"  {r['magnet']:10s} [{r['split']:10s}] n={r['n_days']:.0f}  touch={r['pct_touch']:.1f}%  "
                     f"ret30={r['ret30_mean_bps']:+.1f}bps [{r['ret30_ci_lo']:+.1f},{r['ret30_ci_hi']:+.1f}]")
    lines.append("")

    lines.append("-" * 78)
    lines.append("C1 -- MATRIZ CONDICIONAL DE DIRECAO (celulas com |efeito|>=4bps, discovery, p/ leitura rapida)")
    lines.append("-" * 78)
    c1_disc = c1out[(c1out["split"] == "discovery") & (c1out["mean_bps"].abs() >= 4)].sort_values("mean_bps", key=lambda s: s.abs(), ascending=False)
    for _, r in c1_disc.iterrows():
        lines.append(f"  {r['predictor']:16s}={r['value']:8s} target={r['target']:18s} n={r['n']:4.0f}  "
                     f"{r['mean_bps']:+.1f}bps [{r['ci_lo']:+.1f},{r['ci_hi']:+.1f}]  hit_match={r['hit_rate_matching_sign']}")
    lines.append("")

    lines.append("-" * 78)
    lines.append("C2 -- INTERACOES (discovery, n_discovery>=30)")
    lines.append("-" * 78)
    c2_disc = c2out[(c2out["split"] == "discovery") & (~c2out["na_low_n"])].sort_values("mean_bps", key=lambda s: s.abs() if s.notna().all() else 0, ascending=False)
    for _, r in c2_disc.iterrows():
        lines.append(f"  {r['interaction']:16s} ({r['value_A']},{r['value_B']})  n={r['n']:4.0f}  "
                     f"{r['mean_bps']:+.1f}bps [{r['ci_lo']:+.1f},{r['ci_hi']:+.1f}]")
    lines.append("")

    lines.append("=" * 78)
    lines.append("CANDIDATOS (criterio protocolo: |efeito|>=6bps E IC95 excl. 0 no DISCOVERY)")
    lines.append("=" * 78)
    if len(cand) == 0:
        lines.append("  NENHUMA celula passou o criterio de candidato no DISCOVERY.")
    else:
        for _, r in cand.iterrows():
            flag = "** mesmo sinal e |efeito|>=3bps no CONFIRM **" if (r.get("same_sign_confirm") and r.get("confirm_effect_ge3bps")) else "(nao replica em CONFIRM)"
            lines.append(f"  {r['description']}")
            lines.append(f"      DISCOVERY: n={r['discovery_n']:.0f}  {r['discovery_effect_bps']:+.1f}bps  "
                         f"IC[{r['discovery_ci_lo']:+.1f},{r['discovery_ci_hi']:+.1f}]")
            lines.append(f"      CONFIRM  : n={r['confirm_n']:.0f}  {r['confirm_effect_bps']:+.1f}bps  "
                         f"IC[{r['confirm_ci_lo']:+.1f},{r['confirm_ci_hi']:+.1f}]  {flag}")
    lines.append("")

    lines.append("=" * 78)
    lines.append("CONTAGEM DE TESTES (contexto de comparacoes multiplas)")
    lines.append("=" * 78)
    lines.append(f"  Testes de efeito com bootstrap IC (bps) registrados: {len(all_tests_df)}")
    lines.append(f"    discovery: {(all_tests_df['split']=='discovery').sum()}   confirm: {(all_tests_df['split']=='confirm').sum()}")
    lines.append(f"  Total de CELULAS reportadas em todos os CSVs (linhas de tabela, inclui contagens sem IC): {ALL_CELLS_COUNT}")
    lines.append(f"  Candidatos que passaram o filtro |efeito|>=6bps + IC95 excl 0 no discovery: {len(cand)}")
    lines.append("")
    lines.append("NOTA: com ~"+str(len(all_tests_df))+" testes de efeito, a 5% de significancia nominal "
                 "esperar-se-iam ~"+f"{0.05*len(all_tests_df):.0f}"+" 'falsos positivos' por acaso. O criterio de "
                 "promocao exige tambem replicacao em CONFIRM 2024 (mesmo sinal, |efeito|>=3bps) e n>=100 "
                 "-- ainda assim, candidatos aqui sao HIPOTESES, nao edges validados.")

    text = "\n".join(lines)
    (OUT / "BC_SUMMARY.txt").write_text(text, encoding="utf-8")
    log("  -> BC_SUMMARY.txt")
    return text


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    log(f"Lendo {M15_PATH}")
    m15 = load_bars(M15_PATH)
    log(f"Lendo {M5_PATH}")
    m5 = load_bars(M5_PATH)
    log(f"M15: {len(m15)} barras, {m15['date'].nunique()} dias | M5: {len(m5)} barras, {m5['date'].nunique()} dias")

    daily = build_daily(m15)
    log(f"Tabela diaria construida: {len(daily)} dias validos")

    b1 = bateria_b1(daily)
    daily, b2out = bateria_b2(daily, m15)
    daily, b3touch = bateria_b3_touch(daily, m15)
    records, b3grid = bateria_b3_firsttouch(daily, m15, m5)
    b4out = bateria_b4(records, m15)
    b5out = bateria_b5(daily, m15)
    c1out = bateria_c1(daily)
    c2out = bateria_c2(daily)

    cand, all_tests_df = candidate_scan()

    summary_text = write_summary(daily, b1, b2out, b3touch, b3grid, b4out, b5out, c1out, c2out, cand, all_tests_df)

    log("Concluido.")
    print("\n" + summary_text)


if __name__ == "__main__":
    main()
