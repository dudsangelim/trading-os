"""
b3_swing_v1_proxy -- Fase 1: mecanica TSM WDO (L=126 travado) com custos e
walk-forward de avaliacao. Pre-registro normativo:
    mt5/campaigns/b3_swing_v1_proxy/PREREG_PHASE1.md
Contexto Fase 0:
    mt5/campaigns/b3_swing_v1_proxy/phase0_closeout.md
    mt5/campaigns/b3_swing_v1_proxy/phase0/independent_check.py  (replicacao Fable)
    mt5/campaigns/b3_swing_v1_proxy/phase0/build_proxy_map.py    (execucao original)

Script auto-suficiente. So pandas/numpy/pyarrow. Roda com:
    python run_phase1.py

===========================================================================
DECISOES METODOLOGICAS DECLARADAS (o pre-registro nao especifica o mecanismo
exato em alguns pontos -- documentado aqui no mesmo espirito de transparencia
usado em build_proxy_map.py):
===========================================================================

D1. Sinal/execucao/retorno (regra 2 do pre-registro):
    Sinal calculado no fechamento do dia de formacao D = sign(close_D /
    close_{D-126} - 1). Posicao E ASSUMIDA no fechamento de D+1 (delay de
    1 pregao). Retornos da posicao comecam em D+2 (isto e, pos[D+1] fica
    "parada" sem render no proprio dia D+1; o primeiro retorno realizado
    e ret_{D+2} = close_{D+2}/close_{D+1}-1, multiplicado pela posicao).
    Cadencia: reformacao a cada F pregoes, grade nao-sobreposta identica
    a Fase 0 (indices espacados exatamente F). A posicao formada em D fica
    ativa para os retornos de D+2 ate D+1+F (F dias de retorno), o que
    encaixa sem sobreposicao nem lacuna com a proxima formacao em D+F.

D2. Vol-target (regra 2): vol realizada = desvio-padrao amostral (ddof=1)
    dos retornos diarios brutos do instrumento nos 20 pregoes ATE D
    (incluindo D), anualizada por sqrt(252). Leverage = min(3, 10%/vol_ann).
    Sem piso (se vol muito alta, leverage pode cair bem abaixo de 1x; sem
    exigencia de leverage minimo).

D3. Custo de mudanca de posicao (regra 3): o pre-registro define
    literalmente so 2 casos (flat->long = 1 RT; long->short = 2 RT). Para
    generalizar a sizing continuo (vol-target, onde o tamanho pode mudar
    de ciclo p/ ciclo mesmo com o mesmo sinal), adoto:
        rt_equivalente = |novo - velho|                    se mesmo sinal
                          (ou um dos dois = 0, incl. entrada/saida)
                       = |novo| + |velho|                   se sinais opostos
    Essa formula reduz-se EXATAMENTE aos 2 casos literais quando sizing=1x
    fixo (flat->long: |1-0|=1 RT; long->short: |1|+|-1|=2 RT). Custo =
    rt_equivalente * cost_bps. Aplicado no dia de EXECUCAO (D+1) -- momento
    em que o trade e efetivamente feito.

D4. Rolagem mensal (regra 3): 6 bps no ultimo pregao de cada mes civil SE
    posicao != 0. Decisao nao especificada no pre-registro: escalo o custo
    de rolagem pelo tamanho da posicao (|pos_t| * cost_bps), pois rolar 3x
    de notional custa proporcionalmente mais que rolar 1x. Comparacao de
    sensibilidade (regra 3, "0 e 3 bps") aplicada ao MESMO cost_bps tanto
    para custo de mudanca de posicao quanto para rolagem (nivel unico de
    custo testado: 0 / 3 / 6 bps).

D5. Gates (regra 6): avaliados mecanicamente SO no nivel de custo 6 bps
    ("gate no 6" -- regra 3, ultima frase). Os niveis 0 e 3 bps sao
    reportados como sensibilidade, sem gate.

D6. Grade fechada -- construcao ADJprop (regra 4/5): "construcao ADJprop
    real (2021+ apenas)" NAO passa pelo walk-forward de 22 janelas nem
    pelos gates 1-4/6 (regra 5 do pre-registro: "so proxy; ADJprop e teste
    de consistencia a parte"). E usada apenas (a) para reportar metricas
    full-period no periodo disponivel (2021-07 a 2024-12) como referencia,
    e (b) para o GATE 5 (comparacao de posicoes proxy vs ADJprop no
    overlap 2021-2024).

D7. Gate 5: overlap = dias de pregao comuns as duas series (proxy e
    ADJprop), restrito a 2021-01-01 - 2024-12-31 (a serie ADJprop so
    comeca em 2021-07-19; a intersecao efetiva comeca dai + lookback).
    "Mesmo sinal de posicao" conta empate flat==flat como match (dia em
    que as duas construcoes ficam de fora do mercado tambem e consistencia).
    Net>0 do gate 5 e medido na CONSTRUCAO ADJprop (retorno liquido real)
    sobre o overlap, custo 6 bps.

D8. Bootstrap (regra 7): reamostragem de blocos de rebalance = retorno
    liquido COMPOSTO dentro de cada ciclo de F dias (2002-2024, custo
    6bps), 1000 reamostragens com reposicao, seed=42. Anualizacao de cada
    reamostragem: (1+media_dos_blocos)^(252/F) - 1. Gate passa se o IC95
    exclui zero (limite inferior > 0).

D9. Full-period para gates 2/3/4/6 = 2002-01-01 -> 2024-12-31 (igual ao
    ultimo ano das janelas WF), terços = 2002-09 / 2010-16 / 2017-24
    (bordas de ano-calendario, conforme os rotulos do pre-registro).

D10. HOLDOUT 2025-01-01 -> 2026-07-17: separado do parquet/ADJprop
    IMEDIATAMENTE apos o load. Nenhuma estatistica roda sobre essas datas
    ate a secao final do script, e so se >=1 config passar os 6 gates.
"""

import os
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

BASE_DIR = r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\mt5\campaigns\b3_swing_v1_proxy"
PROXY_PATH = os.path.join(BASE_DIR, "proxy_raw.parquet")
ADJ_PATH = r"C:\Users\Notebook\Documents\Claude\Projects\B3 Futuros\mt5_history\WDO_cont_ADJprop_D1.parquet"
OUT_DIR = os.path.join(BASE_DIR, "phase1")
os.makedirs(OUT_DIR, exist_ok=True)

HOLDOUT_START = pd.Timestamp("2025-01-01")
HOLDOUT_END = pd.Timestamp("2026-07-17")

FULL_START = pd.Timestamp("2002-01-01")
FULL_END = pd.Timestamp("2024-12-31")

THIRDS = [
    ("2002-2009", pd.Timestamp("2002-01-01"), pd.Timestamp("2009-12-31")),
    ("2010-2016", pd.Timestamp("2010-01-01"), pd.Timestamp("2016-12-31")),
    ("2017-2024", pd.Timestamp("2017-01-01"), pd.Timestamp("2024-12-31")),
]

WF_WINDOWS = [
    (y, pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y+1}-12-31"))
    for y in range(2002, 2024)
]  # 2002-2003 .. 2023-2024 -> 22 janelas

L = 126
FS = [5, 21]
SIZINGS = ["fixed_1x", "voltarget_10pct_cap3x"]
GATE_COST_BPS = 6.0
SENSITIVITY_BPS = [0.0, 3.0, 6.0]
VOL_TARGET = 0.10
VOL_CAP = 3.0
VOL_WINDOW = 20

SEED = 42
N_BOOT = 1000

ADJ_CONSISTENCY_START = pd.Timestamp("2021-01-01")
ADJ_CONSISTENCY_END = pd.Timestamp("2024-12-31")

TRADING_DAYS_YEAR = 252


# ---------------------------------------------------------------------
# Data loading (holdout embargo aplicado AQUI, imediatamente apos o load)
# ---------------------------------------------------------------------

def load_proxy():
    df = pd.read_parquet(PROXY_PATH).sort_values("date").reset_index(drop=True)
    wd = df["date"].dt.weekday
    if (wd >= 5).any():
        df = df[wd < 5].reset_index(drop=True)
    df["ret_usdbrl"] = df["usdbrl"].pct_change()
    df["ffr_ad"] = (1.0 + df["ffr_aa"] / 100.0) ** (1.0 / 252.0) - 1.0
    df["ret"] = df["ret_usdbrl"] - (df["cdi_ad"] / 100.0 - df["ffr_ad"])
    df = df.dropna(subset=["ret"]).reset_index(drop=True)
    out = df[["date", "ret"]].copy()
    work = out[out["date"] < HOLDOUT_START].reset_index(drop=True)
    holdout = out[(out["date"] >= HOLDOUT_START) & (out["date"] <= HOLDOUT_END)].reset_index(drop=True)
    return work, holdout


def load_adjprop():
    df = pd.read_parquet(ADJ_PATH)
    df = df[["datetime_b3", "close"]].copy()
    df["date"] = pd.to_datetime(df["datetime_b3"]).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    df = df.dropna(subset=["ret"]).reset_index(drop=True)
    out = df[["date", "ret"]].copy()
    work = out[out["date"] < HOLDOUT_START].reset_index(drop=True)
    holdout = out[(out["date"] >= HOLDOUT_START) & (out["date"] <= HOLDOUT_END)].reset_index(drop=True)
    return work, holdout


# ---------------------------------------------------------------------
# Sanity check: reproduzir o numero-ancora da Fase 0 (independent_check.py)
# S2 WDO TSM L=126 F=5, CONFIRM 2019-2024, spread up-minus-down bps/dia,
# grid nao-sobreposta, SEM delay de execucao e SEM custos (mesma
# construcao do check independente da Fase 0).
# ---------------------------------------------------------------------

def sanity_check_phase0_anchor(proxy_work):
    r = proxy_work.set_index("date")["ret"]
    r = r[(r.index >= "2019-01-01") & (r.index <= "2024-12-31")]
    p = (1 + r).cumprod()
    n = len(p)
    past, fwd = 126, 5
    idx = list(range(past, n - fwd, fwd))
    sig = np.array([p.iloc[i] / p.iloc[i - past] - 1 for i in idx])
    f = np.array([(p.iloc[i + fwd] / p.iloc[i] - 1) / fwd * 1e4 for i in idx])
    up, dn = f[sig > 0], f[sig < 0]
    spread = up.mean() - dn.mean()
    return dict(n_grid=len(idx), n_up=len(up), n_dn=len(dn), spread_bps_dia=spread)


# ---------------------------------------------------------------------
# Engine: formacao / sizing / custos / posicao diaria
# ---------------------------------------------------------------------

def build_formation_idxs(n, L, F):
    # precisa de i-L >= 0 e i+1+F <= n-1 (ultimo dia de retorno realizado valido)
    return [i for i in range(L, n - F - 1, F)]


def size_from_signal(sign_val, vol_ann, sizing):
    if sizing == "fixed_1x":
        return sign_val * 1.0
    # voltarget_10pct_cap3x
    if vol_ann is None or not np.isfinite(vol_ann) or vol_ann <= 1e-9:
        lev = VOL_CAP
    else:
        lev = min(VOL_CAP, VOL_TARGET / vol_ann)
    return sign_val * lev


def build_positions(dates, rets, L, F, sizing):
    """Retorna pos[t] (posicao ativa DURANTE o dia t, i.e. multiplica rets[t])
    e a lista de registros de formacao (uma linha por reformacao)."""
    n = len(rets)
    price = np.cumprod(1.0 + rets)
    idxs = build_formation_idxs(n, L, F)
    pos = np.zeros(n)
    records = []
    for i in idxs:
        state = price[i] / price[i - L] - 1.0
        sgn = float(np.sign(state))
        vol_win = rets[i - VOL_WINDOW + 1: i + 1]
        vol_ann = np.std(vol_win, ddof=1) * np.sqrt(TRADING_DAYS_YEAR) if len(vol_win) > 1 else np.nan
        size = size_from_signal(sgn, vol_ann, sizing)
        start, end = i + 2, i + 1 + F
        if start <= end < n:
            pos[start:end + 1] = size
        elif start < n:
            pos[start:n] = size
        records.append(dict(idx=i, date=dates[i], sign=sgn, vol_ann=vol_ann, size=size))
    return pos, records


def apply_costs(dates, pos, records, cost_bps):
    """Custo de mudanca de posicao (RT-equivalente, D3) no dia de execucao
    D+1, + rolagem mensal (D4) no ultimo pregao de cada mes civil se
    posicao != 0. Retorna array de custo (fracao) por dia."""
    n = len(pos)
    cost = np.zeros(n)
    prev_size = 0.0
    for rec in records:
        i, size = rec["idx"], rec["size"]
        if (prev_size >= 0 and size >= 0) or (prev_size <= 0 and size <= 0):
            rt_equiv = abs(size - prev_size)
        else:
            rt_equiv = abs(size) + abs(prev_size)
        exec_day = i + 1
        if rt_equiv > 0 and exec_day < n:
            cost[exec_day] += rt_equiv * (cost_bps / 10000.0)
        prev_size = size

    months = pd.DatetimeIndex(dates).to_period("M")
    month_df = pd.DataFrame({"i": np.arange(n), "m": months})
    last_idx_per_month = month_df.groupby("m")["i"].max().values
    for t in last_idx_per_month:
        if pos[t] != 0:
            cost[t] += abs(pos[t]) * (cost_bps / 10000.0)
    return cost


def net_returns(pos, rets, cost):
    gross = pos * rets
    return (1.0 + gross) * (1.0 - cost) - 1.0


# ---------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------

def metrics_over_range(dates, net_ret, records, start, end):
    mask = (dates >= start) & (dates <= end)
    r = net_ret[mask]
    n_days = len(r)
    if n_days == 0:
        return dict(net_ann=np.nan, sharpe=np.nan, maxdd=np.nan, n_rebal=0,
                    n_days=0, total_ret=np.nan)
    equity = np.cumprod(1.0 + r)
    total_ret = float(equity[-1] - 1.0)
    years = n_days / TRADING_DAYS_YEAR
    net_ann = float(equity[-1] ** (1.0 / years) - 1.0) if years > 0 else np.nan
    sd = np.std(r, ddof=1)
    sharpe = float((np.mean(r) / sd) * np.sqrt(TRADING_DAYS_YEAR)) if sd > 0 else np.nan
    running_max = np.maximum.accumulate(equity)
    dd = equity / running_max - 1.0
    maxdd = float(dd.min())
    n_rebal = sum(1 for rec in records if start <= rec["date"] <= end)
    return dict(net_ann=net_ann, sharpe=sharpe, maxdd=maxdd, n_rebal=n_rebal,
                n_days=n_days, total_ret=total_ret)


def block_returns(dates, net_ret, records, F, start, end):
    """Retorno liquido composto de cada ciclo de reformacao (bloco de F dias
    de retorno realizado), restrito ao periodo [start, end]."""
    n = len(net_ret)
    blocks = []
    for rec in records:
        if not (start <= rec["date"] <= end):
            continue
        i = rec["idx"]
        s, e = i + 2, min(i + 1 + F, n - 1)
        if s > e:
            continue
        seg = net_ret[s:e + 1]
        if len(seg) == 0:
            continue
        blocks.append(float(np.prod(1.0 + seg) - 1.0))
    return np.array(blocks)


def bootstrap_ci_ann(blocks, F, n_boot=N_BOOT, seed=SEED):
    b = blocks[~np.isnan(blocks)]
    n = len(b)
    if n == 0:
        return dict(lo=np.nan, hi=np.nan, point=np.nan, n=0)
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for k in range(n_boot):
        sample = b[rng.integers(0, n, n)]
        mean_block = sample.mean()
        boots[k] = (1.0 + mean_block) ** (TRADING_DAYS_YEAR / F) - 1.0
    lo, hi = np.percentile(boots, [2.5, 97.5])
    point = (1.0 + b.mean()) ** (TRADING_DAYS_YEAR / F) - 1.0
    return dict(lo=float(lo), hi=float(hi), point=float(point), n=n)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    log_lines = []

    def log(msg=""):
        print(msg)
        log_lines.append(str(msg))

    log("=" * 78)
    log("b3_swing_v1_proxy -- Fase 1: mecanica TSM WDO L=126, custos, walk-forward")
    log("=" * 78)

    proxy_work, proxy_holdout = load_proxy()
    adj_work, adj_holdout = load_adjprop()

    log(f"\nproxy_work: n={len(proxy_work)} range={proxy_work['date'].min().date()} -> {proxy_work['date'].max().date()}")
    log(f"proxy_holdout (EMBARGADO): n={len(proxy_holdout)} range={proxy_holdout['date'].min().date() if len(proxy_holdout) else None} -> {proxy_holdout['date'].max().date() if len(proxy_holdout) else None}")
    log(f"adj_work: n={len(adj_work)} range={adj_work['date'].min().date()} -> {adj_work['date'].max().date()}")
    log(f"adj_holdout (EMBARGADO): n={len(adj_holdout)} range={adj_holdout['date'].min().date() if len(adj_holdout) else None} -> {adj_holdout['date'].max().date() if len(adj_holdout) else None}")

    # ---------------- SANITY CHECK ----------------
    log("\n" + "=" * 78)
    log("SANITY CHECK vs ancora da Fase 0")
    log("=" * 78)
    anchor_bps = 3.55  # phase0_closeout.md: "confirm +3.55" (replicacao independente, L=126/F=5)
    sc = sanity_check_phase0_anchor(proxy_work)
    log(f"Fase 0 (phase0_closeout.md, replicacao independente): CONFIRM 2019-2024 "
        f"L=126 F=5 spread up-dn = +{anchor_bps:.2f} bps/dia")
    log(f"Fase 0 (build_proxy_map.py execucao original): CONFIRM spread = +3.551 bps/dia (n_grid=273)")
    log(f"Minha reproducao (mesma construcao: grid nao-sobreposta, preco local ao "
        f"split, sem delay de execucao, sem custos): n_grid={sc['n_grid']} "
        f"n_up={sc['n_up']} n_dn={sc['n_dn']} spread = {sc['spread_bps_dia']:+.3f} bps/dia")
    rel_diff = abs(sc["spread_bps_dia"] - anchor_bps) / abs(anchor_bps)
    log(f"Divergencia relativa vs ancora ({anchor_bps:.2f}): {rel_diff*100:.1f}%")
    sanity_pass = rel_diff <= 0.10
    log(f"SANITY {'PASS' if sanity_pass else 'FAIL'} (limite 10% de divergencia relativa)")
    if not sanity_pass:
        log("\nPARANDO: divergencia > 10% da ancora da Fase 0. Nao prosseguir com o "
            "walk-forward de uma implementacao nao replicada corretamente.")
        with open(os.path.join(OUT_DIR, "summary.md"), "w", encoding="utf-8") as fh:
            fh.write("# Fase 1 -- PARADO NO SANITY CHECK\n\n")
            fh.write("\n".join(f"    {l}" for l in log_lines))
        return

    # ---------------- Configs (grade fechada, 8 combos) ----------------
    configs = []
    for F in FS:
        for sizing in SIZINGS:
            configs.append(dict(F=F, sizing=sizing, construction="proxy"))
    for F in FS:
        for sizing in SIZINGS:
            configs.append(dict(F=F, sizing=sizing, construction="adjprop"))

    proxy_dates = proxy_work["date"].to_numpy()
    proxy_rets = proxy_work["ret"].to_numpy()
    adj_dates = adj_work["date"].to_numpy()
    adj_rets = adj_work["ret"].to_numpy()

    # cache: (F, sizing, construction, cost_bps) -> (pos, records, net_ret, dates)
    cache = {}

    def get_run(F, sizing, construction, cost_bps):
        key = (F, sizing, construction, cost_bps)
        if key in cache:
            return cache[key]
        if construction == "proxy":
            dates, rets = proxy_dates, proxy_rets
        else:
            dates, rets = adj_dates, adj_rets
        pos, records = build_positions(dates, rets, L, F, sizing)
        cost = apply_costs(dates, pos, records, cost_bps)
        net_ret = net_returns(pos, rets, cost)
        cache[key] = (pos, records, net_ret, dates)
        return cache[key]

    results_rows = []
    wf_rows = []

    log("\n" + "=" * 78)
    log("FULL-PERIOD + WALK-FORWARD + GATES (por config)")
    log("=" * 78)

    proxy_gate_summary = {}  # (F, sizing) -> dict com pos/records/net_ret p/ gate5

    for F in FS:
        for sizing in SIZINGS:
            cfg_id = f"proxy_F{F}_{sizing}"
            log(f"\n--- {cfg_id} ---")

            sens = {}
            for cb in SENSITIVITY_BPS:
                pos, records, net_ret, dates = get_run(F, sizing, "proxy", cb)
                m_full = metrics_over_range(dates, net_ret, records, FULL_START, FULL_END)
                sens[cb] = m_full
                log(f"  cost={cb:.0f}bps full-period 2002-2024: net_ann={m_full['net_ann']*100:+.2f}% "
                    f"sharpe={m_full['sharpe']:.2f} maxdd={m_full['maxdd']*100:.2f}% n_rebal={m_full['n_rebal']}")

            # gates avaliados no cost=6bps (D5)
            pos, records, net_ret, dates = get_run(F, sizing, "proxy", GATE_COST_BPS)
            proxy_gate_summary[(F, sizing)] = dict(pos=pos, records=records, net_ret=net_ret, dates=dates)

            m_full6 = sens[GATE_COST_BPS]

            # Gate 1: WF windows
            wf_net = []
            for (yr, ws, we) in WF_WINDOWS:
                m_w = metrics_over_range(dates, net_ret, records, ws, we)
                wf_rows.append(dict(config=cfg_id, F=F, sizing=sizing, window=f"{yr}-{yr+1}",
                                     window_start=ws.date().isoformat(), window_end=we.date().isoformat(),
                                     net_ann=m_w["net_ann"], sharpe=m_w["sharpe"], maxdd=m_w["maxdd"],
                                     n_rebal=m_w["n_rebal"], n_days=m_w["n_days"]))
                if not np.isnan(m_w["net_ann"]):
                    wf_net.append(m_w["net_ann"])
            wf_net = np.array(wf_net)
            pct_pos = float((wf_net > 0).mean()) if len(wf_net) else np.nan
            gate1 = bool(pct_pos >= 0.70)
            log(f"  GATE1 (% janelas WF net>0 >=70%): {pct_pos*100:.1f}% ({int((wf_net>0).sum())}/{len(wf_net)}) -> {gate1}")

            # Gate 2: Sharpe full-period >= 0.8
            gate2 = bool(not np.isnan(m_full6["sharpe"]) and m_full6["sharpe"] >= 0.8)
            log(f"  GATE2 (Sharpe full-period >=0.8): {m_full6['sharpe']:.2f} -> {gate2}")

            # Gate 3: maxDD full-period
            dd_limit = -0.15 if sizing == "fixed_1x" else -0.20
            gate3 = bool(not np.isnan(m_full6["maxdd"]) and m_full6["maxdd"] >= dd_limit)
            log(f"  GATE3 (maxDD full-period >= {dd_limit*100:.0f}%): {m_full6['maxdd']*100:.2f}% -> {gate3}")

            # Gate 4: net>0 em cada terco
            thirds_res = {}
            for (label, ts, te) in THIRDS:
                m_t = metrics_over_range(dates, net_ret, records, ts, te)
                thirds_res[label] = m_t
                log(f"    terco {label}: net_ann={m_t['net_ann']*100:+.2f}% (n_rebal={m_t['n_rebal']})")
            gate4 = bool(all((not np.isnan(thirds_res[l]["net_ann"])) and thirds_res[l]["net_ann"] > 0 for l, _, _ in THIRDS))
            log(f"  GATE4 (net>0 em cada terco): -> {gate4}")

            # Gate 6: bootstrap
            blocks = block_returns(dates, net_ret, records, F, FULL_START, FULL_END)
            boot = bootstrap_ci_ann(blocks, F)
            gate6 = bool(not np.isnan(boot["lo"]) and boot["lo"] > 0)
            log(f"  GATE6 (bootstrap IC95 net anual exclui 0): point={boot['point']*100:+.2f}% "
                f"IC95=[{boot['lo']*100:+.2f}%,{boot['hi']*100:+.2f}%] n_blocks={boot['n']} -> {gate6}")

            row = dict(
                config=cfg_id, F=F, sizing=sizing, construction="proxy",
                net_ann_0bps=sens[0.0]["net_ann"], sharpe_0bps=sens[0.0]["sharpe"], maxdd_0bps=sens[0.0]["maxdd"],
                net_ann_3bps=sens[3.0]["net_ann"], sharpe_3bps=sens[3.0]["sharpe"], maxdd_3bps=sens[3.0]["maxdd"],
                net_ann_6bps=sens[6.0]["net_ann"], sharpe_6bps=sens[6.0]["sharpe"], maxdd_6bps=sens[6.0]["maxdd"],
                n_rebal_full=m_full6["n_rebal"],
                wf_pct_positive=pct_pos, wf_n_windows=len(wf_net),
                gate1_wf_pct=gate1, gate2_sharpe=gate2, gate3_maxdd=gate3, gate4_thirds=gate4,
                gate5_consistency=None, gate5_net_adj=None, gate5_sign_match_pct=None,
                gate6_bootstrap=gate6, boot_ci_lo=boot["lo"], boot_ci_hi=boot["hi"], boot_point=boot["point"],
            )
            results_rows.append(row)

    log("\n" + "=" * 78)
    log("GATE 5 -- consistencia proxy vs ADJprop (overlap 2021-2024)")
    log("=" * 78)

    for F in FS:
        for sizing in SIZINGS:
            cfg_id = f"proxy_F{F}_{sizing}"
            proxy_run = proxy_gate_summary[(F, sizing)]
            adj_pos, adj_records, adj_net, adj_d = get_run(F, sizing, "adjprop", GATE_COST_BPS)

            # overlap: dias em comum nas duas series, dentro de 2021-2024
            df_p = pd.DataFrame({"date": proxy_run["dates"], "pos": proxy_run["pos"]})
            df_a = pd.DataFrame({"date": adj_d, "pos": adj_pos, "net": adj_net})
            ov = df_p.merge(df_a, on="date", how="inner", suffixes=("_proxy", "_adj"))
            ov = ov[(ov["date"] >= ADJ_CONSISTENCY_START) & (ov["date"] <= ADJ_CONSISTENCY_END)]

            sign_p = np.sign(ov["pos_proxy"].to_numpy())
            sign_a = np.sign(ov["pos_adj"].to_numpy())
            match_pct = float((sign_p == sign_a).mean()) if len(ov) else np.nan

            m_adj_overlap = metrics_over_range(adj_d, adj_net, adj_records, ADJ_CONSISTENCY_START, ADJ_CONSISTENCY_END)
            net_adj_pos = bool(not np.isnan(m_adj_overlap["net_ann"]) and m_adj_overlap["net_ann"] > 0)

            gate5 = bool(net_adj_pos and (not np.isnan(match_pct)) and match_pct >= 0.80)
            log(f"  {cfg_id}: overlap n_dias={len(ov)} sign_match={match_pct*100:.1f}% "
                f"net_ann_ADJprop_overlap={m_adj_overlap['net_ann']*100:+.2f}% -> GATE5={gate5}")

            for r in results_rows:
                if r["config"] == cfg_id:
                    r["gate5_consistency"] = gate5
                    r["gate5_net_adj"] = m_adj_overlap["net_ann"]
                    r["gate5_sign_match_pct"] = match_pct
                    r["overlap_n_days"] = len(ov)

    # overall pass por config proxy
    for r in results_rows:
        gates = [r["gate1_wf_pct"], r["gate2_sharpe"], r["gate3_maxdd"], r["gate4_thirds"],
                 r["gate5_consistency"], r["gate6_bootstrap"]]
        r["all_gates_pass"] = bool(all(bool(g) for g in gates if g is not None) and all(g is not None for g in gates))

    # ---------------- linhas ADJprop-construction (referencia, sem WF/gates 1-4/6) ----------------
    log("\n" + "=" * 78)
    log("Construcao ADJprop (2021-2024) -- referencia full-period, SEM walk-forward (regra 5)")
    log("=" * 78)
    for F in FS:
        for sizing in SIZINGS:
            cfg_id = f"adjprop_F{F}_{sizing}"
            sens = {}
            for cb in SENSITIVITY_BPS:
                pos, records, net_ret, dates = get_run(F, sizing, "adjprop", cb)
                m = metrics_over_range(dates, net_ret, records, ADJ_CONSISTENCY_START, ADJ_CONSISTENCY_END)
                sens[cb] = m
            log(f"  {cfg_id}: net_ann(6bps)={sens[6.0]['net_ann']*100:+.2f}% sharpe={sens[6.0]['sharpe']:.2f} "
                f"maxdd={sens[6.0]['maxdd']*100:.2f}% n_rebal={sens[6.0]['n_rebal']} (2021-2024, ref. p/ gate5)")
            results_rows.append(dict(
                config=cfg_id, F=F, sizing=sizing, construction="adjprop",
                net_ann_0bps=sens[0.0]["net_ann"], sharpe_0bps=sens[0.0]["sharpe"], maxdd_0bps=sens[0.0]["maxdd"],
                net_ann_3bps=sens[3.0]["net_ann"], sharpe_3bps=sens[3.0]["sharpe"], maxdd_3bps=sens[3.0]["maxdd"],
                net_ann_6bps=sens[6.0]["net_ann"], sharpe_6bps=sens[6.0]["sharpe"], maxdd_6bps=sens[6.0]["maxdd"],
                n_rebal_full=sens[6.0]["n_rebal"],
                wf_pct_positive=None, wf_n_windows=None,
                gate1_wf_pct=None, gate2_sharpe=None, gate3_maxdd=None, gate4_thirds=None,
                gate5_consistency=None, gate5_net_adj=None, gate5_sign_match_pct=None,
                gate6_bootstrap=None, boot_ci_lo=None, boot_ci_hi=None, boot_point=None,
                all_gates_pass=None, overlap_n_days=None,
            ))

    results_df = pd.DataFrame(results_rows)
    wf_df = pd.DataFrame(wf_rows)

    bool_cols = ["gate1_wf_pct", "gate2_sharpe", "gate3_maxdd", "gate4_thirds",
                 "gate5_consistency", "gate6_bootstrap", "all_gates_pass"]
    for c in bool_cols:
        results_df[c] = results_df[c].astype("boolean")

    results_path = os.path.join(OUT_DIR, "results.csv")
    wf_path = os.path.join(OUT_DIR, "wf_windows.csv")
    results_df.to_csv(results_path, index=False)
    wf_df.to_csv(wf_path, index=False)
    log(f"\nsalvo: {results_path} ({len(results_df)} linhas)")
    log(f"salvo: {wf_path} ({len(wf_df)} linhas)")

    # ---------------- HOLDOUT (so se >=1 config passar todos os 6 gates) ----------------
    log("\n" + "=" * 78)
    log("HOLDOUT (2025-01-01 -> 2026-07-17)")
    log("=" * 78)

    passing = [r for r in results_rows if r.get("construction") == "proxy" and r.get("all_gates_pass")]
    holdout_rows = []
    if not passing:
        log("Nenhuma config proxy passou os 6 gates -> HOLDOUT NAO ABERTO (permanece intocado).")
    else:
        log(f"{len(passing)} config(s) passaram todos os 6 gates -> abrindo holdout (avaliacao unica).")
        for r in passing:
            F, sizing = r["F"], r["sizing"]
            cfg_id = r["config"]
            proxy_run = proxy_gate_summary[(F, sizing)]
            # concatena work+holdout p/ ter lookback correto ate holdout, e mede so no holdout
            full_dates = np.concatenate([proxy_dates, proxy_holdout["date"].to_numpy()])
            full_rets = np.concatenate([proxy_rets, proxy_holdout["ret"].to_numpy()])
            pos_h, records_h = build_positions(full_dates, full_rets, L, F, sizing)
            cost_h = apply_costs(full_dates, pos_h, records_h, GATE_COST_BPS)
            net_h = net_returns(pos_h, full_rets, cost_h)
            m_h = metrics_over_range(full_dates, net_h, records_h, HOLDOUT_START, HOLDOUT_END)
            dd_limit = -0.15 if sizing == "fixed_1x" else -0.20
            holdout_pass = bool((not np.isnan(m_h["net_ann"])) and m_h["net_ann"] > 0 and m_h["maxdd"] >= dd_limit)
            note = "F=21 ja viu 2025+ no factory (re-confirmacao, nao descoberta)" if F == 21 else "F=5: 2025+ virgem ate agora"
            log(f"  {cfg_id}: net_ann_holdout={m_h['net_ann']*100:+.2f}% maxdd={m_h['maxdd']*100:.2f}% "
                f"n_rebal={m_h['n_rebal']} n_days={m_h['n_days']} -> PASS={holdout_pass} ({note})")
            holdout_rows.append(dict(config=cfg_id, F=F, sizing=sizing, net_ann=m_h["net_ann"],
                                      sharpe=m_h["sharpe"], maxdd=m_h["maxdd"], n_rebal=m_h["n_rebal"],
                                      n_days=m_h["n_days"], holdout_pass=holdout_pass, note=note))
        holdout_df = pd.DataFrame(holdout_rows)
        holdout_path = os.path.join(OUT_DIR, "holdout_result.csv")
        holdout_df.to_csv(holdout_path, index=False)
        log(f"salvo: {holdout_path}")

    # ---------------- summary.md ----------------
    write_summary(OUT_DIR, log_lines, sc, anchor_bps, rel_diff, sanity_pass, results_df, wf_df, holdout_rows)
    log("\nsalvo: summary.md")


def write_summary(out_dir, log_lines, sanity, anchor_bps, rel_diff, sanity_pass,
                   results_df, wf_df, holdout_rows):
    lines = []
    lines.append("# b3_swing_v1_proxy -- Fase 1: resultado (executor Sonnet)\n")
    lines.append("Pre-registro: `PREREG_PHASE1.md`. Mecanica TSM WDO, L=126 travado, "
                  "F in {5,21}, sizing in {1x fixo, vol-target 10%aa cap 3x}, "
                  "construcao in {proxy, ADJprop real}.\n")

    lines.append("## Sanity check vs ancora da Fase 0\n")
    lines.append(f"- Ancora (`phase0_closeout.md`): CONFIRM 2019-2024, WDO TSM L=126 F=5, "
                 f"spread up-down = **+{anchor_bps:.2f} bps/dia** (replicacao independente Fable). "
                 f"`build_proxy_map.py` (execucao original) deu +3.551 bps/dia -- ambos concordam.")
    lines.append(f"- Minha reproducao (mesma construcao exata do `independent_check.py`: grid "
                 f"nao-sobreposta, preco reconstruido localmente dentro do split CONFIRM, sem "
                 f"delay de execucao, sem custos): n_grid={sanity['n_grid']} "
                 f"spread=**{sanity['spread_bps_dia']:+.3f} bps/dia**")
    lines.append(f"- Divergencia relativa: **{rel_diff*100:.1f}%** (limite 10%) -> "
                 f"{'PASS, prossegui com o walk-forward' if sanity_pass else 'FAIL -- PAREI, nao rodei o walk-forward'}\n")

    if not sanity_pass:
        lines.append("\n**Execucao interrompida no sanity check.** Ver log completo abaixo.\n")
        lines.append("## Log completo\n\n```\n" + "\n".join(log_lines) + "\n```\n")
        with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return

    lines.append("## Gates por config (proxy, 2002-2024, custo=6bps)\n")
    lines.append("| config | F | sizing | G1 WF%pos | G2 Sharpe | G3 maxDD | G4 tercos | "
                 "G5 consist. ADJprop | G6 bootstrap | TODOS OS GATES |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    proxy_rows = results_df[results_df["construction"] == "proxy"]
    for _, r in proxy_rows.iterrows():
        def fmt(g):
            if g is None or (isinstance(g, float) and np.isnan(g)):
                return "N/A"
            return "PASS" if g else "FAIL"
        lines.append(f"| {r['config']} | {r['F']} | {r['sizing']} | "
                     f"{fmt(r['gate1_wf_pct'])} ({r['wf_pct_positive']*100:.0f}%) | "
                     f"{fmt(r['gate2_sharpe'])} ({r['sharpe_6bps']:.2f}) | "
                     f"{fmt(r['gate3_maxdd'])} ({r['maxdd_6bps']*100:.1f}%) | "
                     f"{fmt(r['gate4_thirds'])} | "
                     f"{fmt(r['gate5_consistency'])} ({r['gate5_sign_match_pct']*100:.0f}% sinal) | "
                     f"{fmt(r['gate6_bootstrap'])} | "
                     f"**{fmt(r['all_gates_pass'])}** |")
    lines.append("")

    lines.append("## Full-period (2002-2024, proxy) nos 3 niveis de custo\n")
    lines.append("| config | net_ann 0bps | net_ann 3bps | net_ann 6bps | sharpe 0bps | "
                 "sharpe 3bps | sharpe 6bps | maxdd 6bps |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, r in proxy_rows.iterrows():
        lines.append(f"| {r['config']} | {r['net_ann_0bps']*100:+.2f}% | {r['net_ann_3bps']*100:+.2f}% | "
                     f"{r['net_ann_6bps']*100:+.2f}% | {r['sharpe_0bps']:.2f} | {r['sharpe_3bps']:.2f} | "
                     f"{r['sharpe_6bps']:.2f} | {r['maxdd_6bps']*100:.1f}% |")
    lines.append("")

    lines.append("## Construcao ADJprop (2021-2024, referencia -- sem walk-forward, regra 5)\n")
    lines.append("| config | net_ann 6bps | sharpe 6bps | maxdd 6bps | n_rebal |")
    lines.append("|---|---|---|---|---|")
    adj_rows = results_df[results_df["construction"] == "adjprop"]
    for _, r in adj_rows.iterrows():
        lines.append(f"| {r['config']} | {r['net_ann_6bps']*100:+.2f}% | {r['sharpe_6bps']:.2f} | "
                     f"{r['maxdd_6bps']*100:.1f}% | {r['n_rebal_full']} |")
    lines.append("")

    lines.append("## Walk-forward resumido (22 janelas de 2 anos, 2002-2003 -> 2023-2024)\n")
    lines.append("| config | % janelas net>0 | n janelas |")
    lines.append("|---|---|---|")
    for cfg in proxy_rows["config"]:
        sub = wf_df[wf_df["config"] == cfg]
        pos_pct = (sub["net_ann"] > 0).mean() * 100 if len(sub) else float("nan")
        lines.append(f"| {cfg} | {pos_pct:.1f}% | {len(sub)} |")
    lines.append("")

    lines.append("## Holdout (2025-01-01 -> 2026-07-17)\n")
    if not holdout_rows:
        lines.append("**Holdout NAO ABERTO.** Nenhuma config proxy passou os 6 gates do "
                     "walk-forward -- por regra do pre-registro, o holdout permanece intocado.\n")
    else:
        lines.append("Holdout aberto (>=1 config passou os 6 gates). Avaliacao UNICA, sem iteracao:\n")
        lines.append("| config | net_ann holdout | sharpe | maxdd | n_rebal | n_dias | PASS | nota |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for hr in holdout_rows:
            lines.append(f"| {hr['config']} | {hr['net_ann']*100:+.2f}% | {hr['sharpe']:.2f} | "
                         f"{hr['maxdd']*100:.1f}% | {hr['n_rebal']} | {hr['n_days']} | "
                         f"{'PASS' if hr['holdout_pass'] else 'FAIL'} | {hr['note']} |")
        lines.append("\n**Nota de contaminacao (pre-registrada)**: a variante F=21 vol-target "
                     "ja tinha visto 2025+ no holdout do factory (E1, PASS +3.3%aa). Qualquer "
                     "leitura do holdout F=21 aqui e re-confirmacao, nao descoberta nova. O "
                     "2025+ era virgem apenas para F=5.\n")

    lines.append("## Ressalvas\n")
    lines.append("- Decisoes metodologicas nao especificadas literalmente no pre-registro "
                 "(custo RT-equivalente para sizing continuo, escala da rolagem por tamanho "
                 "de posicao, timing exato do custo de transacao) estao documentadas no topo "
                 "de `run_phase1.py` (blocos D1-D10). Nenhuma foi ajustada apos ver os "
                 "resultados -- foram fixadas antes de rodar o walk-forward.")
    lines.append("- Gates avaliados apenas no nivel de custo 6 bps, conforme regra 3 do "
                 "pre-registro (\"gate no 6\"); 0 e 3 bps sao so sensibilidade.")
    lines.append("- ADJprop-construction (2021-2024) nao passa pelo walk-forward de 22 janelas "
                 "nem pelos gates 1-4/6 -- serve so de referencia e para o gate 5, conforme "
                 "regra 5 do pre-registro.")
    lines.append("- Veredito de promocao NAO e deste script. Revisao (Fable) e decisao de "
                 "governanca (Eduardo, via Manifesto) sao os proximos passos.")
    lines.append("")
    lines.append("## Log completo\n")
    lines.append("```")
    lines.extend(log_lines)
    lines.append("```")

    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
