"""JSON state + CSV append helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from trading.bw_jawcross_paper.config import DATA_DIR, STRATEGY_ID


STATE_PATH = DATA_DIR / f"{STRATEGY_ID}_state.json"
TRADES_PATH = DATA_DIR / f"{STRATEGY_ID}_trades.csv"
EQUITY_PATH = DATA_DIR / f"{STRATEGY_ID}_equity_curve.csv"


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def init_csvs() -> None:
    _ensure()
    _touch(TRADES_PATH, [
        "entry_ts", "exit_ts", "side", "entry_px", "exit_px",
        "bars_held", "gross_ret_pct", "net_ret_pct",
        "notional_usd", "pnl_usd", "equity_after", "leverage", "stake_pct",
        "signal_entry_ts", "signal_exit_ts", "signal_close", "signal_lips",
    ])
    _touch(EQUITY_PATH, ["ts", "equity", "state", "n_trades"])


def _touch(path: Path, columns: list) -> None:
    if not path.exists():
        pd.DataFrame(columns=columns).to_csv(path, index=False)


_TRADE_COLS = [
    "entry_ts", "exit_ts", "side", "entry_px", "exit_px",
    "bars_held", "gross_ret_pct", "net_ret_pct",
    "notional_usd", "pnl_usd", "equity_after", "leverage", "stake_pct",
    "signal_entry_ts", "signal_exit_ts", "signal_close", "signal_lips",
]


def append_trade(trade: Dict[str, Any]) -> None:
    _ensure()
    row = {col: trade.get(col) for col in _TRADE_COLS}
    if row["signal_entry_ts"] is None:
        row["signal_entry_ts"] = trade.get("signal_ts")
    pd.DataFrame([row])[_TRADE_COLS].to_csv(
        TRADES_PATH, mode="a", header=False, index=False
    )


def append_equity(ts: str, equity: float, state: str, n_trades: int) -> None:
    _ensure()
    pd.DataFrame([{"ts": ts, "equity": round(equity, 4),
                   "state": state, "n_trades": n_trades}]).to_csv(
        EQUITY_PATH, mode="a", header=False, index=False
    )


def save_state(state: Dict[str, Any]) -> None:
    _ensure()
    state["saved_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def load_state() -> Dict[str, Any]:
    path = STATE_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}
