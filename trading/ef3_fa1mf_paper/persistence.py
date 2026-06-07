"""CSV append and state.json helpers for EF3 FA1Mf."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from trading.ef3_fa1mf_paper.config import DATA_DIR


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def init_csvs() -> None:
    _ensure_dirs()
    _create_if_missing(
        DATA_DIR / "trades.csv",
        ["entry_ts", "exit_ts", "side", "entry_price", "exit_price",
         "ret_pct_gross", "ret_pct_net", "exit_type", "equity_after"],
    )
    _create_if_missing(
        DATA_DIR / "decisions.csv",
        ["ts", "action", "price", "state_after", "extra_json"],
    )
    _create_if_missing(DATA_DIR / "equity_curve.csv", ["ts", "equity", "state"])


def _create_if_missing(path: Path, columns: list[str]) -> None:
    if not path.exists():
        pd.DataFrame(columns=columns).to_csv(path, index=False)


def append_row(filename: str, row: Dict[str, Any]) -> None:
    _ensure_dirs()
    pd.DataFrame([row]).to_csv(DATA_DIR / filename, mode="a", header=False, index=False)


def save_state(state: Dict[str, Any]) -> None:
    _ensure_dirs()
    state["saved_at"] = datetime.now(timezone.utc).isoformat()
    (DATA_DIR / "state.json").write_text(json.dumps(state, indent=2))


def load_state() -> Dict[str, Any]:
    path = DATA_DIR / "state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}
