"""
Structured JSON-lines logger for the trading system.
Usage:
    from trading.core.monitoring.structured_log import log
    log("m2_btc", "signal_generated", bar="2026-03-24T09:55:00Z", direction="LONG", action="ENTER")
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def log(engine: str, event: str, **kwargs: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine": engine,
        "event": event,
        **kwargs,
    }
    print(json.dumps(record), flush=True)


def log_health_snapshot(worker_status: str, **kwargs: Any) -> None:
    log("worker", "health_snapshot", worker_status=worker_status, **kwargs)
