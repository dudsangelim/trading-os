from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPLIED_AT = datetime.now(timezone.utc).isoformat()


RSI_TRADES = [
    {
        "entry_ts": "2026-05-23T07:45:00",
        "exit_ts": "2026-05-23T10:25:00",
        "side": "long",
        "entry_px": 82.47,
        "exit_px": 82.32,
        "entry_rsi": 8.73,
        "exit_rsi": 50.15,
        "net_ret_pct": -0.3119,
        "pnl_usd": -3.1188,
    },
    {
        "entry_ts": "2026-05-23T20:55:00",
        "exit_ts": "2026-05-23T22:25:00",
        "side": "short",
        "entry_px": 87.08,
        "exit_px": 86.08,
        "entry_rsi": 85.59,
        "exit_rsi": 49.56,
        "net_ret_pct": 1.0184,
        "pnl_usd": 10.1837,
    },
]


ASIAN_TRADES = [
    {
        "entry_ts": "2026-05-25T07:00:00+00:00",
        "exit_ts": "2026-05-25T20:00:00+00:00",
        "side": "long",
        "entry_px": 85.96,
        "exit_px": 85.41,
        "gross_ret_pct": -0.6398,
        "net_ret_pct": -0.7698,
        "pnl_usd": -7.6983,
        "notional_usd": 1000.0,
        "duration_h": 13,
    },
]


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def backup(path: Path) -> None:
    if path.exists():
        dst = path.with_suffix(path.suffix + f".bak_retro_20260601")
        if not dst.exists():
            dst.write_text(path.read_text())


def key(row: dict[str, object]) -> tuple[str, str, str]:
    return (str(row.get("entry_ts", ""))[:19], str(row.get("exit_ts", ""))[:19], str(row.get("side", "")))


def apply_rsi() -> dict[str, object]:
    data_dir = ROOT / "rsi_reversion_paper" / "data"
    trades_path = data_dir / "trades.csv"
    equity_path = data_dir / "equity_curve.csv"
    state_path = data_dir / "state.json"
    for path in (trades_path, equity_path, state_path):
        backup(path)

    fields, rows = read_rows(trades_path)
    existing = {key(row) for row in rows}
    added = []
    pnl_total = sum(float(row.get("pnl_usd") or 0) for row in rows)
    n_wins = sum(1 for row in rows if float(row.get("pnl_usd") or 0) > 0)

    for trade in RSI_TRADES:
        if key(trade) in existing:
            continue
        pnl_total += trade["pnl_usd"]
        out = {
            **trade,
            "entry_ts": trade["entry_ts"][:19],
            "exit_ts": trade["exit_ts"][:19],
            "pnl_total": round(pnl_total, 4),
        }
        rows.append(out)
        added.append(out)
        existing.add(key(out))
        if trade["pnl_usd"] > 0:
            n_wins += 1

    if added:
        rows.sort(key=lambda r: (r.get("exit_ts", ""), r.get("entry_ts", "")))
        write_rows(trades_path, fields, rows)

    _, equity_rows = read_rows(equity_path)
    equity_existing = {(row.get("ts", ""), row.get("n_trades", "")) for row in equity_rows}
    running_total = 0.0
    running_count = 0
    for row in rows:
        running_total += float(row.get("pnl_usd") or 0)
        running_count += 1
        equity = {
            "ts": row["exit_ts"][:19],
            "pnl_total": round(running_total, 4),
            "state": "flat",
            "n_trades": running_count,
        }
        if (equity["ts"], str(equity["n_trades"])) not in equity_existing:
            equity_rows.append(equity)
    equity_rows.sort(key=lambda r: (r.get("ts", ""), int(r.get("n_trades") or 0)))
    write_rows(equity_path, ["ts", "pnl_total", "state", "n_trades"], equity_rows)

    state = json.loads(state_path.read_text())
    state.update({
        "position": None,
        "entry_ts": None,
        "entry_price": None,
        "entry_rsi": None,
        "signal_bar_ts": None,
        "pnl_total": round(sum(float(row.get("pnl_usd") or 0) for row in rows), 4),
        "n_trades": len(rows),
        "n_wins": sum(1 for row in rows if float(row.get("pnl_usd") or 0) > 0),
        "halted": False,
        "trade_results": [float(row.get("pnl_usd") or 0) > 0 for row in rows],
    })
    state_path.write_text(json.dumps(state, indent=2))
    return {"added": len(added), "n_trades": len(rows), "pnl_total": state["pnl_total"]}


def apply_asian() -> dict[str, object]:
    data_dir = ROOT / "asian_dema_paper" / "data"
    trades_path = data_dir / "trades.csv"
    equity_path = data_dir / "equity_curve.csv"
    state_path = data_dir / "state.json"
    for path in (trades_path, equity_path, state_path):
        backup(path)

    fields, rows = read_rows(trades_path)
    existing = {key(row) for row in rows}
    added = []
    pnl_total = sum(float(row.get("pnl_usd") or 0) for row in rows)
    for trade in ASIAN_TRADES:
        if key(trade) in existing:
            continue
        pnl_total += trade["pnl_usd"]
        out = {**trade, "pnl_total_after": round(pnl_total, 4)}
        rows.append(out)
        added.append(out)
        existing.add(key(out))

    if added:
        rows.sort(key=lambda r: (r.get("exit_ts", ""), r.get("entry_ts", "")))
        write_rows(trades_path, fields, rows)

    _, equity_rows = read_rows(equity_path)
    equity_existing = {(row.get("ts", ""), row.get("n_trades", "")) for row in equity_rows}
    running_total = 0.0
    running_count = 0
    for row in rows:
        running_total += float(row.get("pnl_usd") or 0)
        running_count += 1
        equity = {
            "ts": row["exit_ts"],
            "pnl_total": round(running_total, 4),
            "state": "flat",
            "n_trades": running_count,
        }
        if (equity["ts"], str(equity["n_trades"])) not in equity_existing:
            equity_rows.append(equity)
    equity_rows.sort(key=lambda r: (r.get("ts", ""), int(r.get("n_trades") or 0)))
    write_rows(equity_path, ["ts", "pnl_total", "state", "n_trades"], equity_rows)

    state = json.loads(state_path.read_text())
    engine = state.setdefault("engine", {})
    engine.update({
        "position": None,
        "entry_ts": None,
        "entry_price": None,
        "entry_dema_fast": None,
        "signal_bar_ts": None,
        "pnl_total": round(sum(float(row.get("pnl_usd") or 0) for row in rows), 4),
        "trades": rows,
        "n_trades": len(rows),
        "n_wins": sum(1 for row in rows if float(row.get("pnl_usd") or 0) > 0),
        "halted": False,
    })
    state["saved_at"] = APPLIED_AT
    state_path.write_text(json.dumps(state, indent=2))
    return {"added": len(added), "n_trades": len(rows), "pnl_total": engine["pnl_total"]}


def main() -> None:
    result = {
        "applied_at": APPLIED_AT,
        "rsi_reversion_paper": apply_rsi(),
        "asian_dema_paper": apply_asian(),
    }
    audit_path = ROOT / "research_log" / "bug_retroactive_trades_2026-06-01.json"
    backup(audit_path)
    audit_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
