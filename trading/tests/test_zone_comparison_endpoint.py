"""Unit tests for GET /operator/zone-comparison-report endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_client():
    """Import app with mocked pool to avoid real DB connection."""
    import trading.apps.api.main as api_main

    mock_pool = MagicMock()
    api_main._pool = mock_pool
    api_main._derivatives_pool = None
    return TestClient(api_main.app, raise_server_exceptions=True)


def test_zone_comparison_report_no_auth_returns_data():
    """Without OPERATOR_TOKEN configured, endpoint is open."""
    import trading.apps.api.main as api_main

    mock_pool = MagicMock()
    api_main._pool = mock_pool
    api_main._derivatives_pool = None

    fake_stats = {
        "window_days": 30,
        "variants": {
            "legacy": {"n_trades_closed": 50, "win_rate": 0.52, "avg_pnl_pct": 0.01, "sharpe_ratio": 0.8, "expectancy_r": 0.3},
        },
        "statistical_comparison": {},
        "per_engine_breakdown": {},
    }

    with patch.object(api_main, "OPERATOR_TOKEN", ""):
        with patch("trading.apps.api.main.TradingRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get_zone_comparison_stats = AsyncMock(return_value=fake_stats)
            MockRepo.return_value = mock_repo

            client = TestClient(api_main.app, raise_server_exceptions=True)
            response = client.get("/operator/zone-comparison-report")

    assert response.status_code == 200
    data = response.json()
    assert "window_days" in data
    assert "variants" in data


def test_zone_comparison_report_since_days_param():
    """since_days query param is forwarded to get_zone_comparison_stats."""
    import trading.apps.api.main as api_main

    api_main._pool = MagicMock()
    api_main._derivatives_pool = None

    with patch.object(api_main, "OPERATOR_TOKEN", ""):
        with patch("trading.apps.api.main.TradingRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get_zone_comparison_stats = AsyncMock(return_value={"window_days": 7, "variants": {}, "statistical_comparison": {}, "per_engine_breakdown": {}})
            MockRepo.return_value = mock_repo

            client = TestClient(api_main.app, raise_server_exceptions=True)
            response = client.get("/operator/zone-comparison-report?since_days=7")

    assert response.status_code == 200
    mock_repo.get_zone_comparison_stats.assert_awaited_once_with(since_days=7, engine_id=None, min_trades_per_variant=30)


def test_zone_comparison_report_engine_filter():
    """engine_id param is forwarded to get_zone_comparison_stats."""
    import trading.apps.api.main as api_main

    api_main._pool = MagicMock()
    api_main._derivatives_pool = None

    with patch.object(api_main, "OPERATOR_TOKEN", ""):
        with patch("trading.apps.api.main.TradingRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get_zone_comparison_stats = AsyncMock(return_value={"window_days": 30, "variants": {}, "statistical_comparison": {}, "per_engine_breakdown": {}})
            MockRepo.return_value = mock_repo

            client = TestClient(api_main.app, raise_server_exceptions=True)
            response = client.get("/operator/zone-comparison-report?engine_id=cn1")

    assert response.status_code == 200
    mock_repo.get_zone_comparison_stats.assert_awaited_once_with(since_days=30, engine_id="cn1", min_trades_per_variant=30)


def test_zone_comparison_report_auth_required():
    """With OPERATOR_TOKEN set, requests without it return 401."""
    import trading.apps.api.main as api_main

    api_main._pool = MagicMock()
    api_main._derivatives_pool = None

    with patch.object(api_main, "OPERATOR_TOKEN", "secret123"):
        client = TestClient(api_main.app, raise_server_exceptions=True)
        response = client.get("/operator/zone-comparison-report")

    assert response.status_code == 401
