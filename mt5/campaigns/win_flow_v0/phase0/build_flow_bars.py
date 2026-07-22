"""
win_flow_v0 -- Fase 0a: QA de classificacao de agressor (buy/sell) + construcao
de flow-bars de 1 minuto para o continuo WIN$N (WINSN).

Ver pre-registro: mt5/campaigns/win_flow_v0/PREREGISTRATION.md

Etapas:
  1. QA de cobertura bid/ask numa amostra de 20 dias WINSN -> escolhe
     classificador (Lee-Ready se cobertura >=90%, senao tick-rule puro).
  2. Validacao do classificador escolhido contra a verdade (flags reais de
     agressor) nos 9 dias WINQ26.
  3. Construcao das flow-bars de 1min para os 256 dias WINSN, salvas num
     unico parquet.
  4. QA final de sanidade da construcao.

Script autossuficiente: paths absolutos, apenas pandas/numpy/pyarrow.
Processa dia a dia (nao carrega os 256 arquivos inteiros na RAM de uma vez).
"""

import os
import sys
import time as _time
import traceback

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
WINSN_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\tick_history\WINSN"
WINQ26_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\tick_history\WINQ26"
OUT_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\win_flow_v0\phase0"
OUT_PARQUET = os.path.join(OUT_DIR, "flow_bars_1min.parquet")
OUT_SUMMARY = os.path.join(OUT_DIR, "PHASE0A_SUMMARY.txt")

os.makedirs(OUT_DIR, exist_ok=True)

TICK_FLAG_BID = 2
TICK_FLAG_ASK = 4
TICK_FLAG_LAST = 8
TICK_FLAG_VOLUME = 16
TICK_FLAG_BUY = 32
TICK_FLAG_SELL = 64

SESSION_START = pd.Timedelta(hours=9, minutes=0)
SESSION_END = pd.Timedelta(hours=18, minutes=30)

# VERIFICADO empiricamente (code review 2026-07-21): o epoch de tick do
# servidor XP ja e' wall-clock B3 "rotulado como UTC" — offset ZERO.
# (Subtrair 3h deslocaria a sessao pra 06:00-15:30 e o filtro cortaria errado.)
LOCAL_OFFSET = pd.Timedelta(hours=0)

N_QA_SAMPLE_DAYS = 20
G4_THRESHOLD = 0.70
COVERAGE_THRESHOLD = 0.90

# ----------------------------------------------------------------------------
# Logging helper -- espelha no stdout e acumula pra salvar em arquivo
# ----------------------------------------------------------------------------
_LOG_LINES = []


def log(msg=""):
    msg = str(msg)
    print(msg, flush=True)
    _LOG_LINES.append(msg)


def save_log():
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        f.write("\n".join(_LOG_LINES) + "\n")


# ----------------------------------------------------------------------------
# Leitura + normalizacao de dtypes
# ----------------------------------------------------------------------------
def load_trades(path):
    """Le o parquet, forca dtypes numericos, ordena por time_msc e filtra
    apenas ticks de negocio reais (last>0 e volume>0)."""
    df = pd.read_parquet(path, columns=["time", "bid", "ask", "last", "volume", "time_msc", "flags"])

    # forcar dtypes explicitamente (evita colunas object silenciosas, bug
    # conhecido em leituras parquet com nulos/extension dtypes)
    df["time"] = df["time"].astype("int64")
    df["time_msc"] = df["time_msc"].astype("int64")
    df["bid"] = df["bid"].astype("float64")
    df["ask"] = df["ask"].astype("float64")
    df["last"] = df["last"].astype("float64")
    df["volume"] = df["volume"].astype("float64")
    df["flags"] = df["flags"].astype("int64")

    df = df.sort_values("time_msc", kind="mergesort").reset_index(drop=True)

    trade_mask = (df["last"].to_numpy() > 0) & (df["volume"].to_numpy() > 0)
    df = df.loc[trade_mask].reset_index(drop=True)
    return df


# ----------------------------------------------------------------------------
# Classificadores
# ----------------------------------------------------------------------------
def tick_rule_classify(last_arr):
    """Tick-rule puro sobre a sequencia de precos `last` de um dia.
    uptick->+1, downtick->-1, tie->herda a ultima classe estabelecida.
    Primeiro tick (sem historico) e ties antes da primeira mudanca de preco
    ficam 0 (neutro/nao classificado)."""
    n = len(last_arr)
    diff = np.full(n, np.nan, dtype="float64")
    if n > 1:
        diff[1:] = last_arr[1:] - last_arr[:-1]
    sign = np.sign(diff)  # NaN no indice 0, 0.0 em tie, +-1.0 caso contrario

    s = pd.Series(sign, dtype="float64")
    s = s.where(s != 0.0, other=np.nan)  # ties -> NaN (serao herdados via ffill)
    filled = s.ffill()
    filled = filled.fillna(0.0)  # leading ties / primeiro tick -> neutro
    out = filled.to_numpy(dtype="float64")
    return out


def classify_ticks(df, use_lee_ready):
    """Classifica cada tick como +1 (buy), -1 (sell) ou 0 (neutro/nao
    classificavel). Se use_lee_ready=True: last>=ask->buy, last<=bid->sell,
    senao cai no tick-rule. Se False: tick-rule puro em todos os ticks."""
    last_arr = df["last"].to_numpy(dtype="float64")
    tick_cls = tick_rule_classify(last_arr)

    if not use_lee_ready:
        return tick_cls

    bid = df["bid"].to_numpy(dtype="float64")
    ask = df["ask"].to_numpy(dtype="float64")
    valid_ba = (bid > 0) & (ask > 0) & (bid <= ask)

    cls = np.full(len(df), np.nan, dtype="float64")
    buy_mask = valid_ba & (last_arr >= ask)
    sell_mask = valid_ba & (last_arr <= bid)
    cls[buy_mask] = 1.0
    cls[sell_mask] = -1.0  # em caso de overlap (last==bid==ask), sell tem prioridade -- edge case raro

    fallback_mask = np.isnan(cls)
    cls[fallback_mask] = tick_cls[fallback_mask]
    return cls


# ----------------------------------------------------------------------------
# Etapa 1: QA cobertura bid/ask (amostra de 20 dias WINSN)
# ----------------------------------------------------------------------------
def step1_qa_bidask():
    log("=" * 78)
    log("ETAPA 1 -- QA cobertura bid/ask (WINSN, amostra de dias espalhados)")
    log("=" * 78)

    all_files = sorted(f for f in os.listdir(WINSN_DIR) if f.endswith(".parquet"))
    idx = np.linspace(0, len(all_files) - 1, N_QA_SAMPLE_DAYS).astype(int)
    idx = sorted(set(idx.tolist()))
    sample_files = [all_files[i] for i in idx]

    log(f"n arquivos WINSN totais: {len(all_files)}")
    log(f"amostra QA: {len(sample_files)} dias")
    log("")
    log(f"{'dia':<24}{'n_trades':>12}{'cov_bidask_ok':>16}")

    total_valid = 0
    total_ticks = 0
    rows = []
    for fn in sample_files:
        day = fn.split("_")[1].replace(".parquet", "")
        path = os.path.join(WINSN_DIR, fn)
        try:
            df = load_trades(path)
        except Exception as e:
            log(f"{day:<24}{'ERRO':>12}  {e}")
            continue
        n = len(df)
        if n == 0:
            log(f"{day:<24}{0:>12}{'n/a (vazio)':>16}")
            continue
        bid = df["bid"].to_numpy(dtype="float64")
        ask = df["ask"].to_numpy(dtype="float64")
        valid = (bid > 0) & (ask > 0) & (bid <= ask)
        pct = float(valid.mean())
        total_valid += int(valid.sum())
        total_ticks += n
        rows.append((day, n, pct))
        log(f"{day:<24}{n:>12}{pct:>15.1%}")

    pooled_pct = (total_valid / total_ticks) if total_ticks else 0.0
    log("")
    log(f"cobertura pooled (soma ticks validos / soma ticks amostra): {pooled_pct:.2%}")

    use_lee_ready = pooled_pct >= COVERAGE_THRESHOLD
    classifier_name = "Lee-Ready (fallback tick-rule)" if use_lee_ready else "tick-rule puro"
    log(f"threshold de decisao: {COVERAGE_THRESHOLD:.0%}")
    log(f">>> CLASSIFICADOR ESCOLHIDO: {classifier_name}")
    log("")
    return use_lee_ready, pooled_pct, rows


# ----------------------------------------------------------------------------
# Etapa 2: validacao contra verdade (WINQ26)
# ----------------------------------------------------------------------------
def step2_validation(use_lee_ready):
    log("=" * 78)
    log("ETAPA 2 -- Validacao contra verdade (WINQ26, flags reais de agressor)")
    log("=" * 78)
    classifier_name = "Lee-Ready (fallback tick-rule)" if use_lee_ready else "tick-rule puro"
    log(f"classificador aplicado (mesmo da etapa 1, ignorando flags): {classifier_name}")
    log("")

    files = sorted(f for f in os.listdir(WINQ26_DIR) if f.endswith(".parquet"))
    log(f"n arquivos WINQ26: {len(files)}")
    log("")
    header = f"{'dia':<14}{'n_valid':>10}{'n_ambig':>10}{'n_unclass':>11}{'acc_trade':>11}{'acc_vol':>11}"
    log(header)

    daily_acc_trade = []
    daily_acc_vol = []
    pooled_correct_trade = 0
    pooled_total_trade = 0
    pooled_correct_vol = 0.0
    pooled_total_vol = 0.0
    total_ambig = 0
    total_unclass = 0

    for fn in files:
        day = fn.split("_")[1].replace(".parquet", "")
        path = os.path.join(WINQ26_DIR, fn)
        try:
            df = load_trades(path)
        except Exception as e:
            log(f"{day:<14}  ERRO: {e}")
            continue
        if len(df) == 0:
            log(f"{day:<14}  vazio")
            continue

        pred = classify_ticks(df, use_lee_ready)  # ignora flags -- so preco/bid/ask

        flags = df["flags"].to_numpy(dtype="int64")
        volume = df["volume"].to_numpy(dtype="float64")
        is_buy = (flags & TICK_FLAG_BUY) != 0
        is_sell = (flags & TICK_FLAG_SELL) != 0
        ambiguous = is_buy & is_sell  # ambas as flags
        neither = (~is_buy) & (~is_sell)
        excluded_truth = ambiguous | neither
        truth = np.where(is_buy & ~is_sell, 1.0, np.where(is_sell & ~is_buy, -1.0, 0.0))

        unclassified = (pred == 0.0)

        valid_mask = (~excluded_truth) & (~unclassified)
        n_valid = int(valid_mask.sum())
        n_ambig = int(excluded_truth.sum())
        n_unclass = int((unclassified & ~excluded_truth).sum())

        if n_valid == 0:
            log(f"{day:<14}{n_valid:>10}{n_ambig:>10}{n_unclass:>11}{'n/a':>11}{'n/a':>11}")
            total_ambig += n_ambig
            total_unclass += n_unclass
            continue

        correct = (pred[valid_mask] == truth[valid_mask])
        acc_trade = float(correct.mean())
        vol_valid = volume[valid_mask]
        acc_vol = float((vol_valid * correct).sum() / vol_valid.sum())

        daily_acc_trade.append(acc_trade)
        daily_acc_vol.append(acc_vol)
        pooled_correct_trade += int(correct.sum())
        pooled_total_trade += n_valid
        pooled_correct_vol += float((vol_valid * correct).sum())
        pooled_total_vol += float(vol_valid.sum())
        total_ambig += n_ambig
        total_unclass += n_unclass

        log(f"{day:<14}{n_valid:>10}{n_ambig:>10}{n_unclass:>11}{acc_trade:>10.1%} {acc_vol:>10.1%}")

    log("")
    if daily_acc_trade:
        mean_acc_trade = float(np.mean(daily_acc_trade))
        mean_acc_vol = float(np.mean(daily_acc_vol))
    else:
        mean_acc_trade = float("nan")
        mean_acc_vol = float("nan")
    pooled_acc_trade = (pooled_correct_trade / pooled_total_trade) if pooled_total_trade else float("nan")
    pooled_acc_vol = (pooled_correct_vol / pooled_total_vol) if pooled_total_vol else float("nan")

    log(f"total ticks excluidos por flag ambigua/ausente (ambos ou nenhum): {total_ambig}")
    log(f"total ticks excluidos por classificador nao-classificado (tie sem historico): {total_unclass}")
    log("")
    log(f"acuracia media por trade (media simples dos {len(daily_acc_trade)} dias): {mean_acc_trade:.2%}")
    log(f"acuracia media ponderada por volume (media simples dos dias): {mean_acc_vol:.2%}")
    log(f"acuracia pooled por trade (todos os ticks juntos): {pooled_acc_trade:.2%}")
    log(f"acuracia pooled ponderada por volume: {pooled_acc_vol:.2%}")
    log("")

    gate_pass = (not np.isnan(mean_acc_trade)) and (mean_acc_trade >= G4_THRESHOLD)
    log(f"GATE G4 (acuracia media por trade >= {G4_THRESHOLD:.0%}): {'PASSOU' if gate_pass else 'FALHOU'}"
        f"  (obtido: {mean_acc_trade:.2%})")
    log("")

    return {
        "mean_acc_trade": mean_acc_trade,
        "mean_acc_vol": mean_acc_vol,
        "pooled_acc_trade": pooled_acc_trade,
        "pooled_acc_vol": pooled_acc_vol,
        "gate_g4_pass": gate_pass,
    }


# ----------------------------------------------------------------------------
# Etapa 3: construcao das flow-bars 1min (WINSN, 256 dias)
# ----------------------------------------------------------------------------
def process_winsn_day(path, use_lee_ready):
    df = load_trades(path)
    if len(df) == 0:
        return None, "empty_after_trade_filter"

    side = classify_ticks(df, use_lee_ready)
    volume = df["volume"].to_numpy(dtype="float64")
    last_arr = df["last"].to_numpy(dtype="float64")

    # p90 de volume de trade DO PROPRIO DIA (dia inteiro, antes do filtro de sessao)
    p90 = float(np.percentile(volume, 90))
    large_mask = (volume >= p90)

    # horario local B3 (naive) = UTC - 3h, sem tz
    dt_utc = pd.to_datetime(df["time"].to_numpy(), unit="s", utc=True)
    dt_local = (dt_utc - LOCAL_OFFSET).tz_localize(None)
    minute = dt_local.floor("min")

    tod = minute - minute.normalize()
    session_mask = np.asarray((tod >= SESSION_START) & (tod <= SESSION_END), dtype=bool)

    if not session_mask.any():
        return None, "no_session_ticks"

    side_s = side[session_mask]
    vol_s = volume[session_mask]
    large_s = large_mask[session_mask]
    last_s = last_arr[session_mask]
    minute_s = minute[session_mask]

    is_buy = (side_s == 1.0)
    is_sell = (side_s == -1.0)

    buy_vol = np.where(is_buy, vol_s, 0.0)
    sell_vol = np.where(is_sell, vol_s, 0.0)
    buy_vol_l = np.where(is_buy & large_s, vol_s, 0.0)
    sell_vol_l = np.where(is_sell & large_s, vol_s, 0.0)

    work = pd.DataFrame({
        "minute": minute_s,
        "volume": vol_s,
        "buy_vol": buy_vol,
        "sell_vol": sell_vol,
        "buy_vol_l": buy_vol_l,
        "sell_vol_l": sell_vol_l,
        "last": last_s,
    })

    grouped = work.groupby("minute", sort=True)
    bars = grouped.agg(
        n_trades=("volume", "size"),
        vol_total=("volume", "sum"),
        buy_vol=("buy_vol", "sum"),
        sell_vol=("sell_vol", "sum"),
        buy_vol_l=("buy_vol_l", "sum"),
        sell_vol_l=("sell_vol_l", "sum"),
        close_last=("last", "last"),
    ).reset_index()

    denom = (bars["buy_vol"] + bars["sell_vol"]).to_numpy(dtype="float64")
    num = (bars["buy_vol"] - bars["sell_vol"]).to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        ofi = num / denom
    ofi = np.where(denom > 0, ofi, np.nan)

    denom_l = (bars["buy_vol_l"] + bars["sell_vol_l"]).to_numpy(dtype="float64")
    num_l = (bars["buy_vol_l"] - bars["sell_vol_l"]).to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        large_ofi = num_l / denom_l
    large_ofi = np.where(denom_l > 0, large_ofi, np.nan)

    bars["ofi"] = ofi.astype("float64")
    bars["large_ofi"] = large_ofi.astype("float64")
    bars["date"] = os.path.basename(path).split("_")[1].replace(".parquet", "")
    bars = bars.rename(columns={"minute": "datetime_b3"})
    bars = bars.drop(columns=["buy_vol_l", "sell_vol_l"])
    bars = bars[["date", "datetime_b3", "n_trades", "vol_total", "buy_vol", "sell_vol",
                 "ofi", "large_ofi", "close_last"]]

    # forcar dtypes finais (evita object dtype silencioso)
    bars["date"] = bars["date"].astype("string")
    bars["n_trades"] = bars["n_trades"].astype("int64")
    bars["vol_total"] = bars["vol_total"].astype("float64")
    bars["buy_vol"] = bars["buy_vol"].astype("float64")
    bars["sell_vol"] = bars["sell_vol"].astype("float64")
    bars["ofi"] = bars["ofi"].astype("float64")
    bars["large_ofi"] = bars["large_ofi"].astype("float64")
    bars["close_last"] = bars["close_last"].astype("float64")

    meta = {
        "n_ticks_total": len(df),
        "n_ticks_session": int(session_mask.sum()),
        "n_bars": len(bars),
        "session_first_local": str(dt_local.min()),
        "session_last_local": str(dt_local.max()),
    }
    return bars, meta


def step3_build_flow_bars(use_lee_ready):
    log("=" * 78)
    log("ETAPA 3 -- Construcao das flow-bars 1min (WINSN, 256 dias)")
    log("=" * 78)

    files = sorted(f for f in os.listdir(WINSN_DIR) if f.endswith(".parquet"))
    log(f"n arquivos WINSN: {len(files)}")
    log("")

    all_bars = []
    problems = []
    n_bars_per_day = []
    t0 = _time.time()

    for i, fn in enumerate(files):
        path = os.path.join(WINSN_DIR, fn)
        day = fn.split("_")[1].replace(".parquet", "")
        try:
            bars, meta = process_winsn_day(path, use_lee_ready)
        except Exception as e:
            problems.append((day, f"EXCEPTION: {e}"))
            log(f"[{i+1}/{len(files)}] {day}  ERRO: {e}")
            log(traceback.format_exc())
            continue

        if bars is None:
            problems.append((day, meta))
            log(f"[{i+1}/{len(files)}] {day}  PROBLEMA: {meta}")
            continue

        all_bars.append(bars)
        n_bars_per_day.append(meta["n_bars"])

        # flag dias com sessao suspeita (muito curta vs mediana esperada ~9-10h)
        span = pd.Timestamp(meta["session_last_local"]) - pd.Timestamp(meta["session_first_local"])
        if span < pd.Timedelta(hours=3):
            problems.append((day, f"sessao curta ({span}, first={meta['session_first_local']}, "
                                   f"last={meta['session_last_local']})"))

        if (i + 1) % 25 == 0 or (i + 1) == len(files):
            elapsed = _time.time() - t0
            log(f"[{i+1}/{len(files)}] processados ({elapsed:.0f}s decorridos)...")

    log("")
    log(f"tempo total etapa 3: {_time.time() - t0:.0f}s")

    if not all_bars:
        log("NENHUMA barra construida -- abortando gravacao do parquet.")
        return None, problems, n_bars_per_day

    full = pd.concat(all_bars, ignore_index=True)
    full.to_parquet(OUT_PARQUET, index=False)
    log(f"parquet salvo: {OUT_PARQUET}")
    log(f"shape final: {full.shape}")
    log("")

    return full, problems, n_bars_per_day


# ----------------------------------------------------------------------------
# Etapa 4: QA final de sanidade
# ----------------------------------------------------------------------------
def step4_final_qa(full, problems, n_bars_per_day, n_files_total):
    log("=" * 78)
    log("ETAPA 4 -- QA final de sanidade")
    log("=" * 78)

    n_ok = len(n_bars_per_day)
    log(f"dias processados com sucesso: {n_ok} / {n_files_total}")
    log(f"dias com problemas/anomalias: {len(problems)}")
    for day, reason in problems:
        log(f"  - {day}: {reason}")
    log("")

    if full is None or len(full) == 0:
        log("sem dados para estatisticas -- flow-bars vazio.")
        return

    arr = np.array(n_bars_per_day, dtype="float64")
    log("distribuicao de n_bars/dia:")
    log(f"  min={arr.min():.0f}  p25={np.percentile(arr,25):.0f}  mediana={np.median(arr):.0f}  "
        f"p75={np.percentile(arr,75):.0f}  max={arr.max():.0f}  media={arr.mean():.1f}")
    log("")

    n_total = len(full)
    n_ofi_nan = int(full["ofi"].isna().sum())
    n_large_ofi_nan = int(full["large_ofi"].isna().sum())
    log(f"total de minutos (barras) no parquet: {n_total}")
    log(f"% minutos com ofi NaN (buy_vol+sell_vol==0): {n_ofi_nan / n_total:.2%}")
    log(f"% minutos com large_ofi NaN: {n_large_ofi_nan / n_total:.2%}")
    log("")

    dt = pd.to_datetime(full["datetime_b3"])
    tod = dt - dt.dt.normalize()
    morning_mask = (tod >= pd.Timedelta(hours=9)) & (tod < pd.Timedelta(hours=13))
    afternoon_mask = (tod >= pd.Timedelta(hours=13)) & (tod <= pd.Timedelta(hours=18, minutes=30))

    for label, mask in [("manha (09:00-13:00)", morning_mask), ("tarde (13:00-18:30)", afternoon_mask)]:
        sub = full.loc[mask, "ofi"].dropna()
        if len(sub) == 0:
            log(f"  {label}: sem dados validos de ofi")
            continue
        q = sub.quantile([0.25, 0.5, 0.75])
        log(f"  {label}: n_validos={len(sub)}  mediana={q.loc[0.5]:.4f}  "
            f"Q1={q.loc[0.25]:.4f}  Q3={q.loc[0.75]:.4f}  mean={sub.mean():.4f}")
    log("")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    log(f"win_flow_v0 -- Fase 0a -- build_flow_bars.py")
    log(f"WINSN_DIR={WINSN_DIR}")
    log(f"WINQ26_DIR={WINQ26_DIR}")
    log(f"OUT_PARQUET={OUT_PARQUET}")
    log("")

    use_lee_ready, pooled_pct, qa_rows = step1_qa_bidask()
    val_stats = step2_validation(use_lee_ready)
    full, problems, n_bars_per_day = step3_build_flow_bars(use_lee_ready)

    n_files_total = len([f for f in os.listdir(WINSN_DIR) if f.endswith(".parquet")])
    step4_final_qa(full, problems, n_bars_per_day, n_files_total)

    log("=" * 78)
    log("RESUMO FINAL")
    log("=" * 78)
    log(f"cobertura bid/ask (amostra 20 dias, pooled): {pooled_pct:.2%}")
    log(f"classificador escolhido: {'Lee-Ready (fallback tick-rule)' if use_lee_ready else 'tick-rule puro'}")
    log(f"GATE G4: {'PASSOU' if val_stats['gate_g4_pass'] else 'FALHOU'}  "
        f"(acuracia media por trade = {val_stats['mean_acc_trade']:.2%}, threshold={G4_THRESHOLD:.0%})")
    if full is not None:
        log(f"flow-bars: {len(full)} barras, {full['date'].nunique()} dias unicos")
    log("")

    save_log()
    log(f"summary salvo em: {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
