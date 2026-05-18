"""
FASE 3.3 — Paper trade do 2C conditional contra Binance Futures (BTCUSDT perp).

Arquitetura:
  - REST polling a cada 5min UTC (sem WS, mais simples e confiavel pra paper)
  - Reutiliza StrategyEngine e FrozenModel do engine_2C.py (importados)
  - Antes de cada sessao NY (13:30 UTC), computa features contextuais via REST
  - Durante sessao, feed candles 5m fechados pro engine candle-a-candle
  - Fills simulados conservadoramente (LIMIT: precisa do preco tocar o nivel)
  - Capital virtual, leverage configuravel (v1=1x, v2=5x default). Lido do
    execution_notes.leverage_target do modelo, override via --leverage.

Logs:
  paper_trade/
    trades_paper.csv     — 1 linha por trade fechado
    orders_paper.csv     — 1 linha por ordem armada (filled ou cancelled)
    decisions_paper.csv  — 1 linha por decisao do engine (skip/armed/break/fill/exit)
    session_log.csv      — 1 linha por candle processado
    state.json           — estado do engine (resume apos restart)
    run.log              — log textual legivel

Uso (roda indefinidamente, CTRL+C pra parar):
    python scripts\\phase3\\paper_trade.py

Parametros:
    --threshold N       # override threshold (default: modelo)
    --once              # processa um unico candle e sai (debug)
    --dry-run-replay N  # replay N dias de klines recentes em vez de polling live
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dtime
import argparse
import json
import logging
import sys
import time as time_mod
import traceback

import numpy as np
import pandas as pd
import requests

# importa classes do engine
sys.path.insert(0, str(Path(__file__).parent))
from engine_2C import FrozenModel, StrategyEngine  # noqa: E402

MODEL_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\models")
DEFAULT_MODEL = MODEL_DIR / "2C_v2_frozen.json"
FALLBACK_MODEL = MODEL_DIR / "2C_v1_frozen.json"
OUT_DIR = Path(r"C:\Users\Notebook\Documents\Claude\Projects\Finanças\paper_trade")
OUT_DIR.mkdir(parents=True, exist_ok=True)

BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
POLL_OFFSET_SEC = 10          # aguarda 10s apos fechamento do candle pra fetch
INITIAL_CAPITAL = 100.0
DEFAULT_LEVERAGE = 5.0  # v2 default; pode ser sobrescrito por --leverage ou execution_notes

SESSION_OPEN = dtime(13, 30)
SESSION_CLOSE = dtime(20, 0)

# -------- logging --------
log_path = OUT_DIR / "run.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(log_path, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("paper")


# -------- Binance REST --------
class BinanceFeed:
    @staticmethod
    def _get(endpoint, params):
        url = f"{BINANCE_FAPI}{endpoint}"
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    @classmethod
    def klines(cls, interval: str, limit: int = 500, end_time_ms: int = None):
        params = {"symbol": SYMBOL, "interval": interval, "limit": limit}
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        raw = cls._get("/fapi/v1/klines", params)
        # Binance kline: [open_time, open, high, low, close, volume, close_time, ...]
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        return df

    @classmethod
    def last_closed_5m(cls):
        """Retorna o ultimo kline 5m fechado (penultimo da lista)."""
        df = cls.klines("5m", limit=2)
        # o ultimo kline pode estar aberto; pegamos o penultimo
        return df.iloc[-2]  # open_time, open, high, low, close, ...

    @classmethod
    def last_funding_rate(cls) -> float:
        """Retorna taxa de funding mais recente (como fracao, ex: 0.0001 = 0.01%)."""
        raw = cls._get("/fapi/v1/fundingRate", {"symbol": SYMBOL, "limit": 1})
        return float(raw[0]["fundingRate"]) if raw else 0.0


# -------- features contextuais em tempo real --------
def compute_context_live(session_date: datetime.date) -> dict:
    """Computa as 7 features contextuais antes da sessao, via REST."""
    day_start = pd.Timestamp(session_date, tz="UTC")
    session_open_ts = day_start + pd.Timedelta(hours=13, minutes=30)
    end_time_ms = int(session_open_ts.timestamp() * 1000)

    # 1h klines: ultimos 1500 (= 62 dias, cobre vol_60d)
    df1h = BinanceFeed.klines("1h", limit=1500, end_time_ms=end_time_ms)
    df1h = df1h.set_index("open_time").sort_index()
    ret1h = df1h["close"].pct_change().dropna()
    vol_60d = ret1h.rolling(60 * 24).std().iloc[-1]
    vol_20d = ret1h.rolling(20 * 24).std().iloc[-1]

    # 1m klines: ultimos 1500 (= 25h, cobre asian range + london leadin do dia)
    df1m = BinanceFeed.klines("1m", limit=1500, end_time_ms=end_time_ms)
    df1m = df1m.set_index("open_time").sort_index()

    # asian range: 00:00 - 08:00 UTC do dia da sessao
    asian_start = day_start
    asian_end = day_start + pd.Timedelta(hours=8)
    asian = df1m[(df1m.index >= asian_start) & (df1m.index < asian_end)]
    if len(asian) > 10:
        asian_range_pct = (asian["high"].max() - asian["low"].min()) / asian["close"].iloc[0] * 100
    else:
        asian_range_pct = 0.0

    # london lead-in: 12:00 - 13:30 UTC
    leadin = df1m[(df1m.index >= day_start + pd.Timedelta(hours=12)) &
                  (df1m.index < session_open_ts)]
    if len(leadin) > 10:
        leadin_ret = (leadin["close"].iloc[-1] - leadin["close"].iloc[0]) / leadin["close"].iloc[0] * 100
    else:
        leadin_ret = 0.0

    # 1d klines para SMA200
    df1d = BinanceFeed.klines("1d", limit=300, end_time_ms=end_time_ms)
    df1d = df1d.set_index("open_time").sort_index()
    sma200 = df1d["close"].rolling(200).mean().iloc[-1]
    close_prev = df1d["close"].iloc[-1]
    btc_vs_sma200_pct = (close_prev - sma200) / sma200 * 100 if sma200 > 0 else 0.0

    # funding
    funding_last_pct = BinanceFeed.last_funding_rate() * 100

    dow = float(pd.Timestamp(session_date).dayofweek)

    ctx = {
        "vol_60d_pct": float(vol_60d * 100),
        "vol_20d_pct": float(vol_20d * 100),
        "asian_range_pct": float(asian_range_pct),
        "london_leadin_return": float(leadin_ret),
        "btc_vs_sma200_pct": float(btc_vs_sma200_pct),
        "dow": dow,
        "funding_last_pct": float(funding_last_pct),
    }
    log.info(f"[ctx {session_date}] vol60d={ctx['vol_60d_pct']:.3f} "
             f"asian_range={ctx['asian_range_pct']:.2f} leadin={ctx['london_leadin_return']:+.3f} "
             f"sma200={ctx['btc_vs_sma200_pct']:+.2f} funding={ctx['funding_last_pct']:+.4f}")
    return ctx


# -------- paper trader --------
class PaperTrader:
    def __init__(self, model: FrozenModel, threshold: float, leverage: float):
        self.engine = StrategyEngine(model, threshold)
        self.threshold = threshold
        self.leverage = leverage
        self.equity = INITIAL_CAPITAL
        self.capital_history = [INITIAL_CAPITAL]

        self.trades_csv = OUT_DIR / "trades_paper.csv"
        self.orders_csv = OUT_DIR / "orders_paper.csv"
        self.decisions_csv = OUT_DIR / "decisions_paper.csv"
        self.session_csv = OUT_DIR / "session_log.csv"
        self.state_json = OUT_DIR / "state.json"

        self._init_csvs()
        self.last_ctx_date = None
        self.processed_trades = 0

    def _init_csvs(self):
        if not self.trades_csv.exists():
            pd.DataFrame(columns=["ts_exit", "date", "direction", "entry_price",
                                  "exit_price", "exit_reason", "pnl_pct_net",
                                  "equity_before", "equity_after", "leverage"]
                         ).to_csv(self.trades_csv, index=False)
        if not self.orders_csv.exists():
            pd.DataFrame(columns=["ts_armed", "date", "direction", "extreme",
                                  "p_win", "threshold", "outcome"]).to_csv(self.orders_csv, index=False)
        if not self.decisions_csv.exists():
            pd.DataFrame(columns=["ts", "date", "decision", "details"]
                         ).to_csv(self.decisions_csv, index=False)
        if not self.session_csv.exists():
            pd.DataFrame(columns=["ts", "state", "close", "equity"]
                         ).to_csv(self.session_csv, index=False)

    def save_state(self):
        # snapshot minimo pra resumir
        st = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity": self.equity,
            "last_ctx_date": str(self.last_ctx_date) if self.last_ctx_date else None,
            "processed_trades": self.processed_trades,
            "engine_state": self.engine.state,
            "engine_current_date": str(self.engine.current_date) if self.engine.current_date else None,
        }
        with open(self.state_json, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)

    def _append_row(self, csv_path, row: dict):
        pd.DataFrame([row]).to_csv(csv_path, mode="a", header=False, index=False)

    def on_new_candle(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float):
        """Chamado pra cada novo candle 5m fechado."""
        session_date = ts.date()
        t = ts.time()

        # se estamos no periodo da sessao e nao setamos contexto ainda HOJE, setar
        if SESSION_OPEN <= t < SESSION_CLOSE and self.last_ctx_date != session_date:
            try:
                ctx = compute_context_live(session_date)
                self.engine.on_session_context(session_date, ctx)
                self.last_ctx_date = session_date
            except Exception:
                log.error("Falha ao computar ctx:\n" + traceback.format_exc())
                return

        trades_before = len(self.engine.trades)
        decisions_before = len(self.engine.decisions)

        self.engine.on_candle_5m(ts, o, h, l, c)

        # persistir novas decisoes
        for d in self.engine.decisions[decisions_before:]:
            row = {"ts": ts.isoformat(), "date": str(d.get("date")),
                   "decision": d.get("decision"),
                   "details": json.dumps({k: v for k, v in d.items()
                                          if k not in ("date", "decision")}, default=str)}
            self._append_row(self.decisions_csv, row)
            if d.get("decision") == "armed":
                log.info(f"[armed {d.get('date')}] dir={d.get('direction')} "
                         f"extreme={d.get('extreme'):.2f}")
            elif d.get("decision") == "break_p":
                verdict = "ACCEPT" if d.get("p_win", 0) >= self.threshold else "REJECT"
                log.info(f"[break_p {d.get('date')}] p_win={d.get('p_win'):.3f} "
                         f"thr={self.threshold} {verdict}  os={d.get('os_close'):.3f}% "
                         f"ttb={d.get('ttb'):.1f}min")
            elif d.get("decision") == "filled":
                log.info(f"[FILLED {d.get('date')}] entry={d.get('entry'):.2f}")
            elif d.get("decision") == "retest_timeout":
                log.info(f"[retest_timeout {d.get('date')}] ordem cancelada")

        # persistir novos trades
        for tr in self.engine.trades[trades_before:]:
            pnl_fraction = tr["pnl_pct_net"] / 100.0 * self.leverage
            equity_before = self.equity
            self.equity = self.equity * (1 + pnl_fraction)
            self.capital_history.append(self.equity)
            row = {"ts_exit": ts.isoformat(), "date": str(tr["date"].date()),
                   "direction": tr["direction"], "entry_price": tr["entry_price"],
                   "exit_price": tr["exit_price"], "exit_reason": tr["exit_reason"],
                   "pnl_pct_net": tr["pnl_pct_net"], "equity_before": equity_before,
                   "equity_after": self.equity, "leverage": self.leverage}
            self._append_row(self.trades_csv, row)
            self.processed_trades += 1
            log.info(f"[EXIT {tr['date'].date()}] reason={tr['exit_reason']} "
                     f"pnl={tr['pnl_pct_net']:+.3f}%  "
                     f"equity=${equity_before:.2f} -> ${self.equity:.2f}")

        # log da barra
        self._append_row(self.session_csv, {
            "ts": ts.isoformat(), "state": self.engine.state,
            "close": c, "equity": self.equity,
        })
        self.save_state()


# -------- orchestration --------
def seconds_until_next_5m_boundary(offset_sec: int = POLL_OFFSET_SEC):
    """Retorna segundos ate o proximo momento (minuto%5==0) + offset.

    Implementacao via epoch seconds pra evitar overflow em cruzamento
    de meia-noite UTC (bug encontrado na instalacao VPS 2026-04-20).
    """
    five_min = 5 * 60
    now_epoch = datetime.now(timezone.utc).timestamp()
    next_boundary = (int(now_epoch) // five_min + 1) * five_min + offset_sec
    wait = next_boundary - now_epoch
    if wait <= 0:
        wait += five_min
    return wait


def run_live(trader: PaperTrader):
    log.info("Paper trade live iniciado. Aguardando proximo fechamento de candle 5m UTC...")
    last_processed_ts = None
    while True:
        try:
            wait = seconds_until_next_5m_boundary()
            log.info(f"[sleep] aguardando {wait:.1f}s ate proximo candle...")
            time_mod.sleep(max(wait, 0.5))
            kline = BinanceFeed.last_closed_5m()
            ts = pd.Timestamp(kline["open_time"]).tz_convert("UTC").tz_localize(None)
            if last_processed_ts is not None and ts <= last_processed_ts:
                log.warning(f"[dup] candle {ts} ja processado; aguardando")
                time_mod.sleep(20)
                continue
            o = float(kline["open"]); h = float(kline["high"])
            l = float(kline["low"]); c = float(kline["close"])
            trader.on_new_candle(ts, o, h, l, c)
            last_processed_ts = ts
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — encerrando. Estado salvo.")
            trader.save_state()
            return
        except Exception:
            log.error("erro no loop:\n" + traceback.format_exc())
            time_mod.sleep(30)


def run_replay(trader: PaperTrader, days: int):
    """Replay dos ultimos N dias usando REST (pra smoke test)."""
    log.info(f"Replay dos ultimos {days} dias de klines 5m da Binance...")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    # pega em chunks (cada chunk = 1500 klines 5m = ~5.2 dias)
    all_df = []
    while True:
        chunk = BinanceFeed.klines("5m", limit=1500, end_time_ms=end_ms)
        if len(chunk) == 0:
            break
        all_df.append(chunk)
        earliest = chunk["open_time"].min()
        span_days = (pd.Timestamp.now(tz="UTC") - earliest).days
        if span_days >= days:
            break
        end_ms = int(earliest.timestamp() * 1000) - 1
    df = pd.concat(all_df, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    df = df[df["open_time"] >= cutoff].reset_index(drop=True)
    log.info(f"  {len(df)} candles 5m no replay range")
    for _, row in df.iterrows():
        ts = pd.Timestamp(row["open_time"]).tz_convert("UTC").tz_localize(None)
        trader.on_new_candle(ts, row["open"], row["high"], row["low"], row["close"])
    log.info(f"Replay concluido. Trades: {trader.processed_trades}  Equity final: ${trader.equity:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--leverage", type=float, default=None,
                    help="override leverage (default: execution_notes.leverage_target ou 5.0)")
    ap.add_argument("--model", type=str, default=None,
                    help="caminho ao modelo frozen (default: v2 se existir, senao v1)")
    ap.add_argument("--dry-run-replay", type=int, default=0,
                    help="replay dos ultimos N dias via REST (smoke test)")
    args = ap.parse_args()

    if args.model:
        model_path = Path(args.model)
    elif DEFAULT_MODEL.exists():
        model_path = DEFAULT_MODEL
    elif FALLBACK_MODEL.exists():
        model_path = FALLBACK_MODEL
    else:
        log.error(f"modelo nao encontrado. Esperava {DEFAULT_MODEL} ou {FALLBACK_MODEL}.")
        log.error("Rode freeze_2C_v2_model.py antes.")
        return 1

    model = FrozenModel(model_path)
    thr = args.threshold if args.threshold is not None else model.threshold
    if args.leverage is not None:
        leverage = float(args.leverage)
    else:
        leverage = float(model.d.get("execution_notes", {}).get("leverage_target", DEFAULT_LEVERAGE))

    log.info(f"modelo={model.d['model_version']} ({model_path.name})  treino={model.d['train_date_utc']}")
    log.info(f"threshold={thr}  leverage={leverage}x  capital_inicial=${INITIAL_CAPITAL}")
    log.info(f"stop_kind={model.strat.get('stop_kind', 'fixed')}")
    if leverage >= 3:
        log.warning(f"[ALERTA] leverage {leverage}x — slippage em fills LIMIT pode ser material. "
                    f"Monitorar fill quality nos primeiros dias.")

    trader = PaperTrader(model, thr, leverage)

    if args.dry_run_replay > 0:
        run_replay(trader, args.dry_run_replay)
    else:
        run_live(trader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
