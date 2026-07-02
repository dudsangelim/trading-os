"""
Paper trade do DOW 3 pernas + skip_low (versão CORRENTE, validado 2026-04-28).

Mecânica:
  - L_Mon: long Sun 23:55 UTC -> Mon 23:55 UTC
  - L_Wed: long Tue 23:55 UTC -> Wed 23:55 UTC
  - S_Thu: short Wed 23:55 UTC -> Thu 23:55 UTC
  - Gate skip_low: só ABRE se ATR(14d) > P20(60d) do regime — pula dias parados
  - Leverage default: 1.5x

Backtest reference (parquet 2023-01-01 -> 2026-04-22):
  PF 1.62, Sharpe 1.85, weekly +1.02% (lev=1) / +1.46% (lev=1.5x), DD ~31%, walk-forward 3/3.

Arquitetura:
  - Polling REST contra Binance Futures /ticker/price (BTCUSDT)
  - Daily klines pra computar ATR gate (refresh 1x/dia)
  - Estado persistido em state.json
  - Logs em trades.csv, decisions.csv, equity_curve.csv, run.log
  - Notificações Telegram opcional

Uso:
    python paper_trade_dow.py [--once] [--replay-days N] [--port 8095]

Env vars:
    DOW_OUTDIR          (default: ./paper_trade_dow_v2)
    DOW_CAPITAL         (default: 50.0)
    DOW_LEVERAGE        (default: 1.5)
    DOW_HEALTH_PORT     (default: 8095)
    DOW_DISABLE_GATE    (default: false — se "true", desliga ATR gate)
    TELEGRAM_BOT_TOKEN  (opcional)
    TELEGRAM_CHAT_ID    (opcional)
    TELEGRAM_TAG        (default "DOW")
"""
import argparse
import json
import logging
import os
import sys
import time as time_mod
import traceback
import threading
import http.server
import socketserver
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from engine_dow import (
    Dow3LegsSkipLowEngine,
    _is_trigger_time,
    _logical_dow,
)

OUT_DIR = Path(os.environ.get("DOW_OUTDIR", "./paper_trade_dow_v2"))
INITIAL_CAPITAL = float(os.environ.get("DOW_CAPITAL", "50.0"))
LEVERAGE = float(os.environ.get("DOW_LEVERAGE", "1.5"))
HEALTH_PORT = int(os.environ.get("DOW_HEALTH_PORT", "8095"))
DISABLE_GATE = os.environ.get("DOW_DISABLE_GATE", "false").lower() == "true"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TAG = os.environ.get("TELEGRAM_TAG", "DOW").strip()

BINANCE_FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
POLL_INTERVAL_SEC = 60

# Gate parameters (validated)
ATR_LEN = 14
REGIME_WINDOW = 60
Q_LOW = 0.20

OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(OUT_DIR / "run.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dow_paper_v2")


# -------- Telegram notifier --------
class Telegram:
    enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    @classmethod
    def send(cls, text: str, parse_mode: str = "Markdown"):
        if not cls.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text,
                       "parse_mode": parse_mode, "disable_web_page_preview": True}
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code != 200:
                log.warning(f"telegram falhou: {r.status_code} {r.text[:200]}")
        except Exception:
            log.warning("telegram exc:\n" + traceback.format_exc())

    @classmethod
    def notify_open(cls, leg, side, ts, price, equity, gate_status):
        emoji = "🟢" if side == "long" else "🔴"
        cls.send(
            f"#{TELEGRAM_TAG} #open_{side} #{leg}\n"
            f"*OPEN {side.upper()}* ({leg}) {emoji}\n"
            f"ts: `{ts}` UTC\n"
            f"price: `{price:.2f}`\n"
            f"gate: `{gate_status}`\n"
            f"equity: `${equity:.4f}`"
        )

    @classmethod
    def notify_close(cls, leg, side, ts, price, ret_pct, equity):
        emoji = "✅" if ret_pct > 0 else "❌"
        cls.send(
            f"#{TELEGRAM_TAG} #close_{side} #{leg}\n"
            f"*CLOSE {side.upper()}* ({leg})\n"
            f"ts: `{ts}` UTC\n"
            f"price: `{price:.2f}`\n"
            f"pnl_net: {emoji} `{ret_pct:+.3f}%`\n"
            f"equity: `${equity:.4f}`"
        )

    @classmethod
    def notify_close_open(cls, ts, price, ret_pct, equity):
        emoji = "✅" if ret_pct > 0 else "❌"
        cls.send(
            f"#{TELEGRAM_TAG} #close_long #open_short\n"
            f"*CLOSE L_Wed -> OPEN S_Thu*\n"
            f"ts: `{ts}` UTC\n"
            f"price: `{price:.2f}`\n"
            f"L_Wed pnl_net: {emoji} `{ret_pct:+.3f}%`\n"
            f"equity: `${equity:.4f}`"
        )

    @classmethod
    def notify_skip(cls, ts, intended_leg, atr_pct, p20):
        cls.send(
            f"#{TELEGRAM_TAG} #skip\n"
            f"*SKIP* ({intended_leg}) — vol baixa\n"
            f"ts: `{ts}` UTC\n"
            f"atr%: `{atr_pct:.3f}` < p20: `{p20:.3f}`"
        )

    @classmethod
    def notify_startup(cls, capital, leverage, mode, gate_enabled):
        cls.send(
            f"#{TELEGRAM_TAG} #startup\n"
            f"*PAPER TRADER ONLINE (v2 3-pernas)*\n"
            f"capital: `${capital:.2f}`\n"
            f"leverage: `{leverage}x`\n"
            f"fee_rt: `0.10%`\n"
            f"mode: `{mode}`\n"
            f"atr_gate: `{'ON' if gate_enabled else 'OFF'}`\n"
            f"trigger: Sun/Mon/Tue/Wed/Thu 23:55 UTC"
        )

    @classmethod
    def notify_error(cls, where, err_msg):
        cls.send(f"#{TELEGRAM_TAG} #error\n*ERROR @ {where}*\n```\n{err_msg[:500]}\n```")


# -------- ATR Gate --------
class AtrGate:
    """Calcula ATR(14d) e P20(60d) do regime via daily klines da Binance.

    Refresh automático: 1x/dia. Cache do último valor.
    Se DISABLE_GATE=true, sempre retorna True (passa).
    """

    def __init__(self, symbol: str = SYMBOL):
        self.symbol = symbol
        self.last_refresh: Optional[pd.Timestamp] = None
        self.atr_pct_today: Optional[float] = None
        self.p20_today: Optional[float] = None
        self.disabled = DISABLE_GATE

    def _fetch_daily_klines(self, days: int = 90) -> pd.DataFrame:
        """Busca daily klines (1d) das últimas N dias da Binance Futures."""
        end_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
        start_ms = end_ms - days * 24 * 60 * 60 * 1000
        url = f"{BINANCE_FAPI}/fapi/v1/klines"
        r = requests.get(url, params={"symbol": self.symbol, "interval": "1d",
                                       "startTime": start_ms, "endTime": end_ms,
                                       "limit": 200}, timeout=15)
        r.raise_for_status()
        raw = r.json()
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        df = df.set_index("open_time").sort_index()
        return df[["open", "high", "low", "close"]]

    def refresh(self, ts_now: pd.Timestamp) -> dict:
        """Recalcula ATR gate. Chamar 1x/dia (idealmente após 00:00 UTC)."""
        df = self._fetch_daily_klines(days=90)
        if len(df) < ATR_LEN + REGIME_WINDOW:
            log.warning(f"poucos candles ({len(df)}); gate fica OFF como segurança")
            self.atr_pct_today = None
            self.p20_today = None
            return {"status": "insufficient_data", "n": len(df)}

        # ATR clássico
        df["tr"] = np.maximum.reduce([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ])
        df["atr"] = df["tr"].rolling(ATR_LEN).mean()
        df["atr_pct"] = df["atr"] / df["close"] * 100
        df["p20"] = df["atr_pct"].rolling(REGIME_WINDOW).quantile(Q_LOW)

        # USA O DIA ANTERIOR pra evitar look-ahead (dia atual ainda incompleto)
        prev = df.iloc[-2] if len(df) >= 2 else None
        if prev is None or pd.isna(prev["atr_pct"]) or pd.isna(prev["p20"]):
            self.atr_pct_today = None
            self.p20_today = None
            return {"status": "nan_in_gate"}

        self.atr_pct_today = float(prev["atr_pct"])
        self.p20_today = float(prev["p20"])
        self.last_refresh = ts_now
        return {"status": "ok", "atr_pct": self.atr_pct_today, "p20": self.p20_today,
                "based_on_day": prev.name.isoformat()}

    def is_pass(self, ts_now: pd.Timestamp) -> tuple[bool, dict]:
        """Returns (pass: bool, info: dict)."""
        if self.disabled:
            return True, {"gate": "disabled"}
        # refresh se nunca rodou ou se passou >12h desde último refresh
        if self.last_refresh is None or (ts_now - self.last_refresh).total_seconds() > 12 * 3600:
            try:
                info = self.refresh(ts_now)
                log.info(f"atr_gate refresh: {info}")
            except Exception:
                log.error("atr_gate refresh exc:\n" + traceback.format_exc())
                return False, {"gate": "error_refresh"}
        if self.atr_pct_today is None or self.p20_today is None:
            return False, {"gate": "no_data"}
        passed = self.atr_pct_today > self.p20_today
        return passed, {
            "gate": "pass" if passed else "fail",
            "atr_pct": round(self.atr_pct_today, 3),
            "p20": round(self.p20_today, 3),
        }


# -------- Binance feed --------
class Feed:
    @staticmethod
    def get_price(symbol: str = SYMBOL) -> float:
        url = f"{BINANCE_FAPI}/fapi/v1/ticker/price"
        r = requests.get(url, params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])


# -------- PaperTrader --------
class PaperTrader:
    def __init__(self, leverage: float = LEVERAGE):
        self.engine = Dow3LegsSkipLowEngine(leverage=leverage)
        self.gate = AtrGate(SYMBOL)
        self.equity = INITIAL_CAPITAL
        self.trades_csv = OUT_DIR / "trades.csv"
        self.decisions_csv = OUT_DIR / "decisions.csv"
        self.state_json = OUT_DIR / "state.json"
        self.equity_csv = OUT_DIR / "equity_curve.csv"
        self._init_csvs()
        self._load_state()
        self.last_health = dict(state="init", ts=None)

    def _init_csvs(self):
        if not self.trades_csv.exists():
            pd.DataFrame(columns=["entry_ts", "exit_ts", "side", "leg", "entry_price",
                                  "exit_price", "ret_pct_gross", "ret_pct_net",
                                  "fee_pct", "leverage", "equity_after"]).to_csv(
                self.trades_csv, index=False)
        if not self.decisions_csv.exists():
            pd.DataFrame(columns=["ts", "action", "price", "leg", "state_after",
                                  "gate_status", "extra_json"]).to_csv(
                self.decisions_csv, index=False)
        if not self.equity_csv.exists():
            pd.DataFrame(columns=["ts", "equity", "state"]).to_csv(self.equity_csv, index=False)

    def _load_state(self):
        if self.state_json.exists():
            try:
                with open(self.state_json) as f:
                    st = json.load(f)
                self.engine.load_state_dict(st.get("engine", {}))
                self.equity = st.get("equity", INITIAL_CAPITAL)
                log.info(f"state carregado: state={self.engine.state} equity=${self.equity:.4f}")
            except Exception:
                log.error("falha ao carregar state.json:\n" + traceback.format_exc())

    def _save_state(self):
        with open(self.state_json, "w", encoding="utf-8") as f:
            json.dump(dict(timestamp=datetime.now(timezone.utc).isoformat(),
                           equity=self.equity,
                           engine=self.engine.to_state_dict(),
                           gate=dict(disabled=self.gate.disabled,
                                     last_refresh=self.gate.last_refresh.isoformat() if self.gate.last_refresh else None,
                                     atr_pct=self.gate.atr_pct_today,
                                     p20=self.gate.p20_today)), f, indent=2)

    def _append(self, csv_path: Path, row: dict):
        pd.DataFrame([row]).to_csv(csv_path, mode="a", header=False, index=False)

    def on_tick(self, ts: pd.Timestamp, price: float):
        n_trades_before = len(self.engine.trades)

        # Avalia gate ATR antes de chamar engine
        gate_pass, gate_info = self.gate.is_pass(ts)

        d = self.engine.on_tick(ts, price, pass_atr_gate=gate_pass)
        if d is not None:
            extras = {k: v for k, v in d.items()
                      if k not in ("ts", "action", "price", "state_after", "leg")}
            extras.update(gate_info)
            self._append(self.decisions_csv, dict(
                ts=d["ts"], action=d["action"], price=d["price"],
                leg=d.get("leg") or d.get("intended_leg") or "",
                state_after=d["state_after"],
                gate_status=gate_info.get("gate", "unknown"),
                extra_json=json.dumps(extras, default=str),
            ))
            log.info(f"[DECISION {d['action']}] ts={d['ts']} price={price:.2f} "
                     f"leg={d.get('leg') or d.get('intended_leg') or '-'} "
                     f"state->{d['state_after']} gate={gate_info.get('gate')}")

        # Aplicar trades fechados ao equity
        if len(self.engine.trades) > n_trades_before:
            for tr in self.engine.trades[n_trades_before:]:
                self.equity *= (1 + tr["ret_pct_net"] / 100.0)
                row = {**tr, "equity_after": round(self.equity, 6)}
                self._append(self.trades_csv, row)
                log.info(f"[TRADE CLOSED] {tr['side']} {tr['leg']} "
                         f"{tr['entry_price']:.2f}->{tr['exit_price']:.2f} "
                         f"ret_net={tr['ret_pct_net']:+.3f}% equity=${self.equity:.4f}")

        # Telegram
        if d is not None:
            ts_str = d["ts"]
            action = d["action"]
            if action == "open_long":
                Telegram.notify_open(d.get("leg", "?"), "long", ts_str, price,
                                     self.equity, gate_info.get("gate", "?"))
            elif action == "open_short":
                Telegram.notify_open(d.get("leg", "?"), "short", ts_str, price,
                                     self.equity, gate_info.get("gate", "?"))
            elif action == "close_long":
                Telegram.notify_close(d.get("leg", "?"), "long", ts_str, price,
                                      d.get("long_ret_net_pct", 0.0), self.equity)
            elif action == "close_short":
                Telegram.notify_close(d.get("leg", "?"), "short", ts_str, price,
                                      d.get("short_ret_net_pct", 0.0), self.equity)
            elif action == "close_long_open_short":
                Telegram.notify_close_open(ts_str, price,
                                           d.get("long_ret_net_pct", 0.0), self.equity)
            elif action == "skip_open":
                Telegram.notify_skip(ts_str, d.get("intended_leg", "?"),
                                     gate_info.get("atr_pct", float("nan")),
                                     gate_info.get("p20", float("nan")))

        self._append(self.equity_csv, dict(ts=ts.isoformat(),
                                            equity=round(self.equity, 6),
                                            state=self.engine.state))
        self._save_state()
        self.last_health = dict(state=self.engine.state, ts=ts.isoformat(),
                                price=price, equity=self.equity,
                                n_trades=len(self.engine.trades),
                                gate=gate_info)


# -------- health endpoint --------
class HealthHandler(http.server.BaseHTTPRequestHandler):
    trader_ref: PaperTrader = None

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = dict(status="ok", **(self.trader_ref.last_health if self.trader_ref else {}))
            self.wfile.write(json.dumps(payload, default=str).encode())
        elif self.path == "/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.trader_ref.engine.to_state_dict(), default=str).encode())
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server(trader: PaperTrader, port: int):
    HealthHandler.trader_ref = trader
    srv = socketserver.TCPServer(("0.0.0.0", port), HealthHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    log.info(f"health endpoint em :{port}/health")


# -------- orchestration --------
def seconds_until_next_minute():
    now = datetime.now(timezone.utc)
    next_min = (now + timedelta(seconds=60 - now.second + 1)).replace(microsecond=0)
    return max(1, (next_min - now).total_seconds())


def run_live(trader: PaperTrader):
    log.info(f"DOW 3-legs paper trade live. capital_inicial=${INITIAL_CAPITAL} "
             f"lev={LEVERAGE}x fee_rt=0.10% gate={'ON' if not DISABLE_GATE else 'OFF'}")
    log.info("gatilhos: Sun/Mon/Tue/Wed/Thu 23:55 UTC (3 pernas)")
    while True:
        try:
            wait = seconds_until_next_minute()
            time_mod.sleep(wait)
            ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
            # Janela de gatilho ampliada: 23:50-00:14 UTC todo dia (5 dias dispara)
            in_window = (ts.hour == 23 and ts.minute >= 50) or (ts.hour == 0 and ts.minute < 15)
            if in_window or (ts.minute % 30 == 0 and ts.second < 5):
                price = Feed.get_price()
                trader.on_tick(ts, price)
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt - encerrando")
            trader._save_state()
            return
        except Exception:
            err = traceback.format_exc()
            log.error("erro no loop:\n" + err)
            Telegram.notify_error("run_live", err)
            time_mod.sleep(30)


def run_replay(trader: PaperTrader, days: int):
    """Replay histórico via REST: simula trader pra trás N dias.
    NOTA: o gate ATR usa o estado ATUAL (não o histórico). Pra replay fiel
    do skip_low histórico, use scripts/backtest_dow_skiplow_validate.py."""
    log.info(f"replay dos ultimos {days} dias usando 1m klines da Binance")
    log.info("AVISO: ATR gate usa estado atual, não histórico. Replay aproximado.")
    end = pd.Timestamp.now(tz="UTC").tz_localize(None).replace(microsecond=0)
    start = end - pd.Timedelta(days=days)
    cur_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    all_dfs = []
    while cur_ms < end_ms:
        url = f"{BINANCE_FAPI}/fapi/v1/klines"
        r = requests.get(url, params={"symbol": SYMBOL, "interval": "1m",
                                       "startTime": cur_ms, "limit": 1500}, timeout=15)
        r.raise_for_status()
        raw = r.json()
        if not raw:
            break
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert("UTC").dt.tz_localize(None)
        df["close"] = df["close"].astype(float)
        all_dfs.append(df)
        cur_ms = int(raw[-1][0]) + 60_000
        if len(raw) < 1500:
            break
    if not all_dfs:
        log.warning("sem klines no replay")
        return
    df = pd.concat(all_dfs).drop_duplicates("open_time").sort_values("open_time")
    log.info(f"  {len(df):,} candles 1m no replay range")
    df["dow"] = df["open_time"].dt.dayofweek
    df["hour"] = df["open_time"].dt.hour
    df["minute"] = df["open_time"].dt.minute
    # Janela de gatilho: 23:55-23:59 OR 00:00-00:09 (todos os 7 dows; engine filtra)
    mask = ((df["hour"] == 23) & (df["minute"] >= 55)) | \
           ((df["hour"] == 0) & (df["minute"] < 10))
    df_filt = df[mask].sort_values("open_time")
    log.info(f"  {len(df_filt)} velas em janela de gatilho")
    for _, row in df_filt.iterrows():
        trader.on_tick(row["open_time"], float(row["close"]))
    log.info(f"replay concluido. trades={len(trader.engine.trades)} equity_final=${trader.equity:.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="processa um tick e sai")
    ap.add_argument("--replay-days", type=int, default=0, help="replay N dias (smoke test)")
    ap.add_argument("--port", type=int, default=HEALTH_PORT)
    args = ap.parse_args()

    trader = PaperTrader()
    start_health_server(trader, args.port)

    log.info(f"telegram enabled={Telegram.enabled} tag={TELEGRAM_TAG!r}")
    log.info(f"engine version: {trader.engine.VERSION}")

    if args.replay_days > 0:
        run_replay(trader, args.replay_days)
        return 0

    Telegram.notify_startup(trader.equity, LEVERAGE,
                            mode=("once" if args.once else "live"),
                            gate_enabled=not DISABLE_GATE)

    if args.once:
        ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
        price = Feed.get_price()
        trader.on_tick(ts, price)
        return 0

    run_live(trader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
