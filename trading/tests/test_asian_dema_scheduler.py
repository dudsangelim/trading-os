from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from trading.asian_dema_paper.main import _should_run_strategy_tick


def _trader(position):
    return SimpleNamespace(engine=SimpleNamespace(position=position))


def test_asian_dema_ticks_during_entry_session_when_flat():
    now = datetime(2026, 6, 3, 6, 2, tzinfo=timezone.utc)

    assert _should_run_strategy_tick(now, _trader(None)) is True


def test_asian_dema_ticks_outside_entry_session_when_position_open():
    now = datetime(2026, 6, 3, 10, 2, tzinfo=timezone.utc)

    assert _should_run_strategy_tick(now, _trader("short")) is True


def test_asian_dema_does_not_tick_outside_entry_session_when_flat():
    now = datetime(2026, 6, 3, 10, 2, tzinfo=timezone.utc)

    assert _should_run_strategy_tick(now, _trader(None)) is False


def test_asian_dema_only_ticks_after_hourly_bar_close():
    now = datetime(2026, 6, 3, 10, 1, tzinfo=timezone.utc)

    assert _should_run_strategy_tick(now, _trader("short")) is False
