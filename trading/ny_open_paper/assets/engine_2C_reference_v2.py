"""
FASE 3 — Engine event-driven do 2C conditional.

Consome candles 5m em sequencia (um por um, sem peek no futuro) e executa a
mecanica fade NY open filtrada pelo modelo frozen. Deve produzir resultados
byte-a-byte identicos ao backtest vetorizado.

Arquitetura:
  - classe StrategyEngine: estado + logica
  - classe FrozenModel: carrega JSON, expone predict_proba(features)
  - replay(): le parquet, alimenta engine candle por candle, compara com oracle
  - Oracle = trades_all.csv gerado pelo freeze_2C_model.py

Estados (FSM):
  IDLE                 - fora da sessao ou ja tradeou no dia
  COLLECTING           - 13:30 ate 14:00 UTC, acumulando high/low do primeiro 30m
  DECIDING             - exatamente em 14:00, calcula features + modelo
  WAITING_BREAK        - ordem armada, espera break do extremo
  WAITING_RETEST       - breakou, espera retest em <=15min
  IN_POSITION          - entrou, acompanha stop/TP1/TP2
  CLOSED               - saiu, aguarda fim de sessao pra resetar

Uso:
    python scripts\\phase3\\engine_2C.py                   # replay + regressao
    python scripts\\phase3\\engine_2C.py --threshold 0.65  # override threshold
"""
from pathlib import Path
from datetime import time as dtime, datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, List
import argparse
import json
import numpy as np
import pandas as pd

MODEL_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\models")
DEFAULT_MODEL = MODEL_DIR / "2C_v2_frozen.json"
FALLBACK_MODEL = MODEL_DIR / "2C_v1_frozen.json"
PARQUET_PATH = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\BTCUSDT_1m_2021_2026.parquet")
FUNDING_PATH = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\BTCUSDT_funding_2021_2026.parquet")
ORACLE_V1 = MODEL_DIR / "trades_all_with_pwin.csv"
ORACLE_V2 = MODEL_DIR / "trades_v2_with_pwin.csv"
OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\backtests\phase3_engine_replay")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------- modelo ----------
class FrozenModel:
    def __init__(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            self.d = json.load(f)
        self.feature_names = self.d["feature_names"]
        self.mean = np.array(self.d["feature_mean"], dtype=float)
        self.std = np.array(self.d["feature_std"], dtype=float)
        self.std_safe = np.where(self.std == 0, 1.0, self.std)
        self.bias = self.d["weights"]["bias"]
        self.coef = np.array(self.d["weights"]["coef"], dtype=float)
        self.threshold = self.d["recommended_threshold"]
        self.strat = self.d["strategy_params"]
        self.fees = self.d["fee_schedule"]

    def predict_proba(self, features: Dict[str, float]) -> float:
        x = np.array([features[n] for n in self.feature_names], dtype=float)
        x_z = (x - self.mean) / self.std_safe
        z = self.bias + x_z @ self.coef
        return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


# ---------- engine ----------
@dataclass
class Position:
    date: object
    direction: int          # -1 short (fade high), +1 long (fade low)
    entry_price: float
    stop_price: float
    mid: float              # TP1
    opposite: float         # TP2
    hit_tp1: bool = False
    partial_fill: Optional[float] = None


@dataclass
class ArmedOrder:
    date: object
    direction: int
    entry_price: float      # extremo onde LIMIT esta colocada
    break_level: float
    stop_price: float
    mid: float
    opposite: float
    session_open_price: float
    range_first_pct: float
    first_start_ts: pd.Timestamp  # primeiro candle apos 30m (pra time_to_break)
    breakouted: bool = False
    break_bar_idx: Optional[int] = None  # indice da barra apos 30m onde rompeu
    bars_since_break: int = 0


class StrategyEngine:
    def __init__(self, model: FrozenModel, threshold: float, verbose: bool = False):
        self.m = model
        self.thr = threshold
        self.verbose = verbose
        self.session_open = self._parse_time(model.strat["session_open_utc"])
        self.session_close = self._parse_time(model.strat["session_close_utc"])
        self.window_min = model.strat["window_min"]
        self.break_buffer = model.strat["break_buffer"]
        # stop: v1 = fixo (stop_pct), v2 = alpha x range com clamp (stop_kind="alpha")
        self.stop_kind = model.strat.get("stop_kind", "fixed")
        if self.stop_kind == "alpha":
            self.stop_alpha = float(model.strat["stop_alpha"])
            self.stop_min_pct = float(model.strat["stop_min_pct"])
            self.stop_max_pct = float(model.strat["stop_max_pct"])
            self.stop_pct = None  # nao aplicavel
        else:
            self.stop_pct = float(model.strat["stop_pct"])
        self.max_wait_retest_min = model.strat["max_wait_retest_min"]
        self.max_wait_retest_bars_5m = self.max_wait_retest_min // 5
        self.fee_maker = model.fees["maker"]
        self.fee_taker = model.fees["taker"]

        self.state = "IDLE"
        self.current_date = None
        self.armed: Optional[ArmedOrder] = None
        self.pos: Optional[Position] = None

        self.first_bars = []   # barras 5m do primeiro 30m (acumuladas)
        self.post_bars = []    # barras 5m apos 14:00 (pos_idx em ordem)
        self.trades: List[dict] = []
        self.decisions: List[dict] = []  # log de skip/arm/abort

    def _stop_pct_for(self, range_first_pct: float) -> float:
        """Calcula stop_pct (fracao) dado o range em pct."""
        if self.stop_kind == "alpha":
            raw = self.stop_alpha * (range_first_pct / 100.0)
            return max(self.stop_min_pct, min(self.stop_max_pct, raw))
        return self.stop_pct

    @staticmethod
    def _parse_time(s: str) -> dtime:
        h, m = s.split(":")
        return dtime(int(h), int(m))

    def _end_first_time(self) -> dtime:
        total = self.session_open.hour * 60 + self.session_open.minute + self.window_min
        return dtime(total // 60, total % 60)

    def on_session_context(self, date, ctx: Dict[str, float]):
        """Chamado ANTES da sessao comecar. Provides contextual features."""
        self._ctx = ctx
        self._ctx_date = date

    def on_candle_5m(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float):
        t = ts.time()
        date = ts.date()

        # pula weekends (match com iter_sessions do vetorizado)
        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            if self.state != "IDLE":
                self._close_session(force_time_exit=True, ts=ts, price=c)
            return

        # reset no comeco da sessao (ou fora dela)
        if t < self.session_open or t >= self.session_close:
            if self.state != "IDLE":
                self._close_session(force_time_exit=(t >= self.session_close), ts=ts, price=c)
            return

        # mudou de dia?
        if self.current_date != date:
            self._reset_for_new_session(date)

        # --- dentro da sessao ---
        end_first = self._end_first_time()
        if t < end_first:
            # COLLECTING primeiros 30min
            self.state = "COLLECTING"
            self.first_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            return

        # t >= end_first: fase pos-30m
        if self.state == "COLLECTING":
            # primeira barra apos 30m — DECIDING
            self._decide(ts, o, h, l, c)
            # apos decidir, esta barra tambem eh a primeira do "rest" do dia
            self.post_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            if self.state == "WAITING_BREAK":
                self._check_break_and_retest(ts, o, h, l, c)
            return

        if self.state in ("WAITING_BREAK", "WAITING_RETEST"):
            self.post_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            self._check_break_and_retest(ts, o, h, l, c)
            return

        if self.state == "IN_POSITION":
            self.post_bars.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
            self._check_in_position(ts, o, h, l, c)
            return

        if self.state == "CLOSED":
            # apenas acumula pro eventual time exit se nao tivesse sido fechado
            return

    # ---------- private helpers ----------
    def _reset_for_new_session(self, date):
        self.current_date = date
        self.armed = None
        self.pos = None
        self.first_bars = []
        self.post_bars = []
        self.state = "IDLE"

    def _decide(self, ts, o, h, l, c):
        """Em t=14:00 UTC. Primeiro candle do rest."""
        if len(self.first_bars) < 3:
            self.state = "IDLE"
            self.decisions.append({"date": self.current_date, "decision": "skip_short_first"})
            return
        high_first = max(b["h"] for b in self.first_bars)
        low_first = min(b["l"] for b in self.first_bars)
        sess_open = self.first_bars[0]["o"]
        if high_first - low_first <= 0:
            self.state = "IDLE"
            self.decisions.append({"date": self.current_date, "decision": "skip_zero_range"})
            return
        mid = (high_first + low_first) / 2
        range_first = high_first - low_first
        range_first_pct = range_first / sess_open * 100
        up = (high_first - sess_open) / sess_open
        dn = (sess_open - low_first) / sess_open
        if up >= dn:
            extreme = high_first; opposite = low_first
            break_level = extreme * (1 + self.break_buffer)
            direction = -1; direction_first = 1.0
        else:
            extreme = low_first; opposite = high_first
            break_level = extreme * (1 - self.break_buffer)
            direction = 1; direction_first = -1.0

        # features intra-session
        # overshoot_close e time_to_break dependem da barra de break → só conhecidos
        # quando break ocorrer. Mas o v1/v2 do WF calculam features no momento do setup
        # usando OS e TTB "oracularmente". Pra replicar byte-a-byte, calcula-se features
        # no momento em que houver break. Entao aqui apenas armamos o ArmedOrder e o
        # modelo eh consultado DEPOIS que o break ocorrer.
        stop_pct_eff = self._stop_pct_for(range_first_pct)
        self.armed = ArmedOrder(
            date=self.current_date,
            direction=direction,
            entry_price=extreme,
            break_level=break_level,
            stop_price=extreme * (1 + stop_pct_eff) if direction == -1 else extreme * (1 - stop_pct_eff),
            mid=mid,
            opposite=opposite,
            session_open_price=sess_open,
            range_first_pct=range_first_pct,
            first_start_ts=ts,  # primeiro candle do rest
        )
        # features contextuais salvas em self._ctx; direction_first vai ao dict tambem
        self._feat_partial = {
            "range_first_30m_pct": range_first_pct,
            "direction_first_30m": direction_first,
            **self._ctx,
        }
        self.state = "WAITING_BREAK"
        self.decisions.append({"date": self.current_date, "decision": "armed",
                              "direction": direction, "extreme": extreme,
                              "range_first_pct": range_first_pct})

    def _check_break_and_retest(self, ts, o, h, l, c):
        a = self.armed
        # checa break
        if not a.breakouted:
            broke = (a.direction == -1 and h >= a.break_level) or \
                    (a.direction == 1 and l <= a.break_level)
            if broke:
                # calcula features que dependem do break
                if a.direction == -1:
                    os_close = (c - a.entry_price) / a.entry_price * 100
                else:
                    os_close = (a.entry_price - c) / a.entry_price * 100
                time_to_break_min = (ts - a.first_start_ts).total_seconds() / 60.0

                features = dict(self._feat_partial)
                features["overshoot_close_pct"] = os_close
                features["time_to_break_min"] = time_to_break_min
                p = self.m.predict_proba(features)

                self.decisions.append({"date": self.current_date, "decision": "break_p",
                                      "p_win": float(p), "threshold": self.thr,
                                      "os_close": os_close, "ttb": time_to_break_min})

                if p < self.thr:
                    # filtra fora
                    self.state = "CLOSED"
                    self.armed = None
                    return

                a.breakouted = True
                a.break_bar_idx = len(self.post_bars) - 1
                a.bars_since_break = 0
                self.state = "WAITING_RETEST"

                # MESMO BAR: o vetorizado checa retest comecando do break_pos, inclusive
                # o proprio candle de break. No 5m, se direction==-1, retest = low <= extreme.
                # Eh possivel que na mesma barra que rompeu tambem retesta.
                retested = (a.direction == -1 and l <= a.entry_price) or \
                           (a.direction == 1 and h >= a.entry_price)
                if retested:
                    self._fill(ts)
                return
            return

        # ja breakou, procura retest
        a.bars_since_break += 1
        # timeout ANTES de testar retest (match vetorizado: range(break_pos, break_pos+4) = 4 bars)
        if a.bars_since_break > self.max_wait_retest_bars_5m:
            self.state = "CLOSED"
            self.armed = None
            self.decisions.append({"date": self.current_date, "decision": "retest_timeout"})
            return
        retested = (a.direction == -1 and l <= a.entry_price) or \
                   (a.direction == 1 and h >= a.entry_price)
        if retested:
            self._fill(ts)
            return

    def _fill(self, ts):
        a = self.armed
        self.pos = Position(
            date=self.current_date,
            direction=a.direction,
            entry_price=a.entry_price,
            stop_price=a.stop_price,
            mid=a.mid,
            opposite=a.opposite,
        )
        self.state = "IN_POSITION"
        self.armed = None
        self.decisions.append({"date": self.current_date, "decision": "filled",
                              "entry": a.entry_price})
        # importante: o vetorizado itera "after = rest.iloc[retest_pos:]" — inclui a
        # propria barra do retest. Entao vamos checar a barra do retest TAMBEM.
        # A barra atual ja foi adicionada em self.post_bars. Processa agora.
        bar = self.post_bars[-1]
        self._check_in_position(bar["ts"], bar["o"], bar["h"], bar["l"], bar["c"])

    def _check_in_position(self, ts, o, h, l, c):
        p = self.pos
        # stop primeiro (conservador, igual vetorizado)
        stop_hit = (p.direction == -1 and h >= p.stop_price) or \
                   (p.direction == 1 and l <= p.stop_price)
        if stop_hit:
            if p.hit_tp1:
                exit_price = (p.partial_fill + p.entry_price) / 2
                exit_reason = "trail_stop_be"
            else:
                exit_price = p.stop_price
                exit_reason = "stop"
            self._close_position(ts, exit_price, exit_reason)
            return

        # match vetorizado: if/elif impede TP2 na MESMA barra do TP1
        if not p.hit_tp1:
            if (p.direction == -1 and l <= p.mid) or (p.direction == 1 and h >= p.mid):
                p.hit_tp1 = True
                p.partial_fill = p.mid
        else:
            if (p.direction == -1 and l <= p.opposite) or (p.direction == 1 and h >= p.opposite):
                exit_price = (p.partial_fill + p.opposite) / 2
                self._close_position(ts, exit_price, "tp2")
                return

    def _close_session(self, force_time_exit: bool, ts, price: float):
        """Chamado quando sessao acaba. Se ainda houver posicao aberta, fecha por tempo."""
        if self.state == "IN_POSITION" and self.pos is not None:
            p = self.pos
            last_close = self.post_bars[-1]["c"] if self.post_bars else price
            if p.hit_tp1:
                exit_price = (p.partial_fill + last_close) / 2
                exit_reason = "time_partial"
            else:
                exit_price = last_close
                exit_reason = "time"
            self._close_position(ts, exit_price, exit_reason)
        self._reset_for_new_session(None)

    def _close_position(self, ts, exit_price: float, exit_reason: str):
        p = self.pos
        pnl_gross = p.direction * (exit_price - p.entry_price) / p.entry_price * 100
        m = self.fee_maker * 100
        t = self.fee_taker * 100
        if exit_reason in ("tp", "tp2"):
            exit_fee = m
        elif exit_reason in ("trail_stop_be", "time_partial"):
            exit_fee = 0.5 * m + 0.5 * t
        else:
            exit_fee = t
        fee_total = m + exit_fee
        pnl_net = pnl_gross - fee_total
        self.trades.append({
            "date": pd.Timestamp(p.date),
            "direction": p.direction,
            "entry_price": p.entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pct_net": pnl_net,
        })
        self.state = "CLOSED"
        self.pos = None


# ---------- features contextuais (copy freeze) ----------
def precompute_context(df1m, funding_df):
    df1h = df1m["close"].resample("1h").last().dropna()
    ret1h = df1h.pct_change().dropna()
    vol_60d = ret1h.rolling(60 * 24).std()
    vol_20d = ret1h.rolling(20 * 24).std()
    daily = df1m["close"].resample("1D").last().dropna()
    sma200 = daily.rolling(200).mean()

    ctx = {}
    all_dates = sorted(set(df1m.index.date))
    for date in all_dates:
        if pd.Timestamp(date).day_name() in ("Saturday", "Sunday"):
            continue
        day_start = pd.Timestamp(date)
        asian = df1m[(df1m.index >= day_start) & (df1m.index < day_start + pd.Timedelta(hours=8))]
        asian_range_pct = (asian["high"].max() - asian["low"].min()) / asian["close"].iloc[0] * 100 if len(asian) > 10 else np.nan
        leadin_start = day_start + pd.Timedelta(hours=12)
        leadin_end = day_start + pd.Timedelta(hours=13, minutes=30)
        leadin = df1m[(df1m.index >= leadin_start) & (df1m.index < leadin_end)]
        if len(leadin) > 10:
            c0 = leadin["close"].iloc[0]; c1 = leadin["close"].iloc[-1]
            leadin_ret = (c1 - c0) / c0 * 100 if c0 > 0 else 0
        else:
            leadin_ret = np.nan
        ts = day_start + pd.Timedelta(hours=13, minutes=30)
        try:
            v60 = vol_60d.asof(ts - pd.Timedelta(minutes=1)) * 100
            v20 = vol_20d.asof(ts - pd.Timedelta(minutes=1)) * 100
        except Exception:
            v60, v20 = np.nan, np.nan
        try:
            ts_prev = day_start - pd.Timedelta(minutes=1)
            c_prev = daily.asof(ts_prev)
            sma_prev = sma200.asof(ts_prev)
            btc_sma_pct = (c_prev - sma_prev) / sma_prev * 100 if sma_prev and sma_prev > 0 else np.nan
        except Exception:
            btc_sma_pct = np.nan
        funding_last = 0.0
        if len(funding_df) > 0:
            cutoff = day_start + pd.Timedelta(hours=13, minutes=30)
            fmask = funding_df["datetime"] < cutoff
            if fmask.any():
                funding_last = funding_df.loc[fmask, "funding_rate"].iloc[-1] * 100
        dow = pd.Timestamp(date).dayofweek
        ctx[date] = {
            "vol_60d_pct": v60 if not pd.isna(v60) else 0.0,
            "vol_20d_pct": v20 if not pd.isna(v20) else 0.0,
            "asian_range_pct": asian_range_pct if not pd.isna(asian_range_pct) else 0.0,
            "london_leadin_return": leadin_ret if not pd.isna(leadin_ret) else 0.0,
            "btc_vs_sma200_pct": btc_sma_pct if not pd.isna(btc_sma_pct) else 0.0,
            "dow": float(dow),
            "funding_last_pct": funding_last,
        }
    return ctx


def load_1m():
    df = pd.read_parquet(PARQUET_PATH, columns=["datetime", "open", "high", "low", "close", "volume_usdt"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.set_index("datetime").sort_index()


def resample_5m(df1m):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume_usdt": "sum"}
    return df1m.resample("5min", label="left", closed="left").agg(agg).dropna()


def load_funding():
    if not FUNDING_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(FUNDING_PATH)
    if "datetime" not in df.columns:
        for alt in ["fundingTime", "time", "timestamp"]:
            if alt in df.columns:
                df = df.rename(columns={alt: "datetime"})
                break
    if "funding_rate" not in df.columns:
        for alt in ["fundingRate", "rate"]:
            if alt in df.columns:
                df = df.rename(columns={alt: "funding_rate"})
                break
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df[["datetime", "funding_rate"]].sort_values("datetime").reset_index(drop=True)


def run_replay(model: FrozenModel, threshold: float):
    print("[1/3] Carregando dados...")
    df1m = load_1m()
    df5m = resample_5m(df1m)
    funding = load_funding()
    print(f"  5m={len(df5m):,}")

    print("\n[2/3] Contexto diario...")
    ctx = precompute_context(df1m, funding)
    print(f"  dias={len(ctx):,}")

    print(f"\n[3/3] Replay event-driven (threshold={threshold})...")
    eng = StrategyEngine(model, threshold)
    current_ctx_date = None
    # itera candles em ordem
    for ts, row in df5m.iterrows():
        date = ts.date()
        # configura contexto do dia na primeira barra
        if date != current_ctx_date and date in ctx:
            eng.on_session_context(date, ctx[date])
            current_ctx_date = date
        eng.on_candle_5m(ts, row["open"], row["high"], row["low"], row["close"])

    # fecha posicao residual se sessao terminou sem time exit
    eng._close_session(True, df5m.index[-1], df5m["close"].iloc[-1])

    trades = pd.DataFrame(eng.trades)
    print(f"  Trades executados pelo engine: {len(trades)}")
    if len(trades):
        trades["date"] = pd.to_datetime(trades["date"])
        trades = trades.sort_values("date").reset_index(drop=True)
        trades.to_csv(OUT_DIR / f"engine_trades_thr{threshold:.2f}.csv", index=False)
        pnl = trades["pnl_pct_net"].values
        wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
        pf = (wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else float("nan")
        eq = 100 * np.prod(1 + pnl / 100)
        print(f"  PF={pf:.3f}  Exp={pnl.mean():+.4f}  Eq=${eq:.2f}  WR={(pnl>0).mean():.3f}")
    pd.DataFrame(eng.decisions).to_csv(OUT_DIR / f"engine_decisions_thr{threshold:.2f}.csv", index=False)
    return trades


def compare_with_oracle(engine_trades: pd.DataFrame, model: FrozenModel, threshold: float, oracle_path: Path):
    """Se oracle_path existe, compara engine vs oracle trade-a-trade."""
    if not oracle_path.exists():
        print(f"\n[regressao] oracle CSV nao existe em {oracle_path}")
        print("  (rode freeze_2C_*_model.py primeiro, ou pule esta etapa)")
        return
    print(f"\n[regressao] comparando engine vs oracle {oracle_path.name}...")
    oracle = pd.read_csv(oracle_path)
    oracle["date"] = pd.to_datetime(oracle["date"])
    # aplica threshold no oracle
    if "p_win" in oracle.columns:
        oracle = oracle[oracle["p_win"] >= threshold].copy()
    else:
        print("  oracle nao tem coluna p_win; pulando")
        return
    oracle = oracle.sort_values("date").reset_index(drop=True)
    print(f"  oracle n={len(oracle)}  engine n={len(engine_trades)}")

    o_dates = set(oracle["date"].dt.date)
    e_dates = set(engine_trades["date"].dt.date)
    only_oracle = o_dates - e_dates
    only_engine = e_dates - o_dates
    print(f"  datas: ambos={len(o_dates & e_dates)}  so_oracle={len(only_oracle)}  so_engine={len(only_engine)}")
    if only_oracle:
        print(f"    exemplos so_oracle: {sorted(only_oracle)[:5]}")
    if only_engine:
        print(f"    exemplos so_engine: {sorted(only_engine)[:5]}")

    # para datas em ambos, compara pnl
    merged = pd.merge(
        oracle[["date", "pnl_pct_net"]].rename(columns={"pnl_pct_net": "pnl_oracle"}),
        engine_trades[["date", "pnl_pct_net"]].rename(columns={"pnl_pct_net": "pnl_engine"}),
        on="date", how="inner",
    )
    if len(merged) == 0:
        print("  nenhuma data em comum — algo errado")
        return
    merged["diff"] = merged["pnl_engine"] - merged["pnl_oracle"]
    max_diff = merged["diff"].abs().max()
    n_mismatch = (merged["diff"].abs() > 1e-6).sum()
    print(f"  max |diff| pnl_pct_net: {max_diff:.8f}")
    print(f"  trades com diff > 1e-6: {n_mismatch}/{len(merged)}")
    if n_mismatch > 0:
        print("  exemplos:")
        print(merged[merged["diff"].abs() > 1e-6].head(10).to_string(index=False))
    if n_mismatch == 0 and len(only_oracle) == 0 and len(only_engine) == 0:
        print("  [OK] ENGINE BATE BYTE-A-BYTE COM ORACLE")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=None, help="override threshold (default = model's recommended)")
    ap.add_argument("--model", type=str, default=None, help="caminho ao modelo frozen (default: v2 se existir, senao v1)")
    args = ap.parse_args()

    if args.model:
        model_path = Path(args.model)
    elif DEFAULT_MODEL.exists():
        model_path = DEFAULT_MODEL
    elif FALLBACK_MODEL.exists():
        model_path = FALLBACK_MODEL
    else:
        print(f"[ERRO] nenhum modelo encontrado. Esperava {DEFAULT_MODEL} ou {FALLBACK_MODEL}")
        print("       rode primeiro: python scripts\\phase3\\freeze_2C_v2_model.py")
        return 1

    model = FrozenModel(model_path)
    thr = args.threshold if args.threshold is not None else model.threshold
    version = model.d["model_version"]
    oracle_path = ORACLE_V2 if version.endswith("v2") else ORACLE_V1
    print(f"[engine 2C] modelo={version} ({model_path.name})  treino={model.d['train_date_utc']}")
    print(f"[engine 2C] threshold={thr}  stop_kind={model.strat.get('stop_kind', 'fixed')}")

    trades = run_replay(model, thr)
    compare_with_oracle(trades, model, thr, oracle_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
