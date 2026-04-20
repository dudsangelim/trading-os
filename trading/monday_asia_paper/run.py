"""
Runner principal do Monday Asia Open ORB em modo paper.

Loop:
  - Polling da Hyperliquid info API a cada POLL_INTERVAL_SEC
  - Pra cada ativo, puxa ultimos 12*60 candles 1min
  - Chama MondayAsiaORB.on_tick() com last_price e history
  - Processa Events: loga em CSV + manda Telegram

Modo paper puro: nao coloca ordens, so simula. Estrutura pronta pra plugar Hyperliquid SDK depois.
"""
from __future__ import annotations
import argparse
import csv
import json
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from strategy import (
    MondayAsiaORB, StrategyConfig, AssetState, State,
    load_state, save_state, Event,
)
from hyperliquid_feed import fetch_candles, fetch_last_n_minutes
from telegram_notifier import TelegramNotifier

log = logging.getLogger("runner")

POLL_INTERVAL_SEC = 20  # consulta feed a cada 20s


def setup_logging(logdir: Path):
    logdir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logdir / "runner.log"),
        ],
    )


def append_event_csv(csv_path: Path, event: Event):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time_utc", "kind", "asset", "data_json"])
        w.writerow([event.time_utc, event.kind, event.asset, json.dumps(event.data)])


def append_trade_csv(trades_path: Path, state: AssetState):
    """So escreve quando um trade FECHA (state transition -> DONE com side setado)."""
    if state.side is None or state.exit_reason is None:
        return
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not trades_path.exists()
    with trades_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["week", "asset", "side", "entry_price", "exit_price",
                        "entry_time_utc", "exit_time_utc", "exit_reason",
                        "pnl_bps_gross", "pnl_bps_net",
                        "pnl_usd_gross", "pnl_usd_net",
                        "range_hi", "range_lo"])
        w.writerow([
            state.week_key, state.asset, state.side,
            state.entry_price, state.exit_price,
            state.entry_time_utc, state.exit_time_utc, state.exit_reason,
            state.pnl_bps_gross, state.pnl_bps_net,
            state.pnl_usd_gross, state.pnl_usd_net,
            state.range_hi, state.range_lo,
        ])


class Runner:
    def __init__(self, config_path: Path):
        cfg = json.loads(config_path.read_text())
        self.cfg = cfg
        self.state_dir = Path(cfg.get("state_dir", "./state"))
        self.state_file = self.state_dir / "state.json"
        self.events_csv = self.state_dir / "events.csv"
        self.trades_csv = self.state_dir / "trades.csv"

        setup_logging(self.state_dir)

        strat_cfg = StrategyConfig(
            sunday_window_h=cfg.get("sunday_window_h", 10),
            sunday_end_hour_utc=cfg.get("sunday_end_hour_utc", 22),
            tp_multiplier=cfg.get("tp_multiplier", 1.5),
            exit_hour_utc=cfg.get("exit_hour_utc", 12),
            entry_filter_minutes=cfg.get("entry_filter_minutes", 180),
            cost_bps_per_side=cfg.get("cost_bps_per_side", 3.0),
            position_size_usd=cfg.get("position_size_usd", 1000.0),
        )

        self.assets = cfg.get("assets", ["BTC", "ETH", "SOL"])
        self.strats = {a: MondayAsiaORB(a, strat_cfg) for a in self.assets}

        # Restaura state se existir
        loaded = load_state(self.state_file)
        for a in self.assets:
            if a in loaded:
                self.strats[a].s = loaded[a]

        tcfg = cfg.get("telegram", {})
        self.tn = TelegramNotifier(
            bot_token=tcfg.get("bot_token", ""),
            chat_id=tcfg.get("chat_id", ""),
            enabled=tcfg.get("enabled", True),
        )
        self._running = True
        self._last_heartbeat = 0

    def stop(self, *a):
        log.info("Stopping...")
        self._running = False

    def _handle_events(self, asset: str, events: list[Event]):
        state = self.strats[asset].s
        for ev in events:
            append_event_csv(self.events_csv, ev)
            log.info(f"EVENT {ev.kind} {asset} {ev.data}")

            if ev.kind == "range_computed":
                self.tn.range_computed(asset, ev.data["range_hi"], ev.data["range_lo"],
                                        ev.data["range_bps"], ev.data["week"])
            elif ev.kind == "entered":
                self.tn.entered(asset, ev.data["side"], ev.data["entry_price"],
                                ev.data["sl"], ev.data["tp"])
            elif ev.kind == "exited":
                self.tn.exited(asset, ev.data["side"], ev.data["entry_price"],
                                ev.data["exit_price"], ev.data["exit_reason"],
                                ev.data["pnl_bps_net"])
                append_trade_csv(self.trades_csv, state)
            elif ev.kind == "cancelled":
                self.tn.cancelled(asset, ev.data.get("reason", ""))

    def _maybe_heartbeat(self, now_utc: datetime):
        # Heartbeat Telegram 1x/dia (00:30 UTC)
        hour = now_utc.hour
        minute = now_utc.minute
        if hour == 0 and 30 <= minute < 32:
            key = now_utc.date().isoformat()
            if self._last_heartbeat != key:
                summary = []
                for a, s in self.strats.items():
                    summary.append(f"{a}: {s.s.state}")
                self.tn.heartbeat("\n".join(summary))
                self._last_heartbeat = key

    def run(self):
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        log.info(f"Runner started. Assets: {self.assets}. Poll interval: {POLL_INTERVAL_SEC}s")
        self.tn.send(f"🚀 Monday Asia ORB paper started\nAssets: {', '.join(self.assets)}")

        # Pre-carrega historico grande na 1a iteracao (para ter Sunday window completa)
        # e depois so pega os ultimos ~15min em cada tick
        history = {a: [] for a in self.assets}
        first_pass = True

        while self._running:
            now_utc = datetime.now(tz=timezone.utc)
            try:
                for asset in self.assets:
                    if first_pass or now_utc.minute == 0:
                        # carrega ultimas 15h pra ter margem sobre a janela de 10h + 3h de filtro
                        candles = fetch_last_n_minutes(asset, 15 * 60)
                    else:
                        # so ultimos 10 candles (merge no history)
                        recent = fetch_last_n_minutes(asset, 10)
                        # merge por timestamp
                        seen = {c["t"] for c in history[asset]}
                        for c in recent:
                            if c["t"] not in seen:
                                history[asset].append(c)
                        # trunca historia a ~20h
                        if len(history[asset]) > 20*60:
                            history[asset] = history[asset][-20*60:]
                        candles = history[asset]
                    history[asset] = candles[-20*60:] if len(candles) > 20*60 else candles

                    if not candles:
                        continue
                    last = candles[-1]
                    last_price = last["c"]

                    events = self.strats[asset].on_tick(now_utc, last_price, candles)
                    if events:
                        self._handle_events(asset, events)

                first_pass = False
                save_state(self.state_file, {a: s.s for a, s in self.strats.items()})
                self._maybe_heartbeat(now_utc)
            except Exception as e:
                log.exception(f"tick error: {e}")

            # Sleep preciso
            for _ in range(POLL_INTERVAL_SEC):
                if not self._running: break
                time.sleep(1)

        save_state(self.state_file, {a: s.s for a, s in self.strats.items()})
        log.info("Runner stopped.")
        self.tn.send("🛑 Monday Asia ORB paper stopped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    Runner(Path(args.config)).run()


if __name__ == "__main__":
    main()
