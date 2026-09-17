import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from src.mms4tk.api import app
from src.mms4tk.analytics import Candle

client = TestClient(app)

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-api-auth-key-unit-test"}

@patch("src.mms4tk.api.executor.fetch_candles", new_callable=AsyncMock)
def test_get_history_candles_with_tma(mock_fetch_candles, auth_headers):
    candles = [
        Candle(timestamp="2026-09-17T10:00:00Z", open=50000, high=51000, low=49000, close=50500, volume=100),
        Candle(timestamp="2026-09-17T11:00:00Z", open=50500, high=52000, low=50000, close=51500, volume=150)
    ]
    mock_fetch_candles.return_value = candles

    response = client.get("/api/v1/history/candles?symbol=BTC/USDC&timeframe=1h&limit=2", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    
    assert "tma" in data[0]
    assert "upper_band" in data[0]
    assert "lower_band" in data[0]
    assert "atr" in data[0]

@patch("src.mms4tk.api.logger_db")
def test_get_history_addons(mock_logger_db, auth_headers):
    mock_logger_db.get_addon_events.return_value = [
        {"timestamp": "2026-09-17T10:00:00Z", "symbol": "BTC/USDC", "price": 50500.0, "side": "long"}
    ]
    
    response = client.get("/api/v1/history/addons?symbol=BTC/USDC", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    
    assert len(data) == 1
    assert data[0]["price"] == 50500.0
    mock_logger_db.get_addon_events.assert_called_once_with(symbol="BTC/USDC", limit=100)


@patch("src.mms4tk.api.executor.fetch_positions", new_callable=AsyncMock)
def test_get_active_positions_includes_entry_timestamp(mock_fetch_positions, auth_headers):
    """
    /api/v1/positions must surface OKX's own position-open timestamp (info.cTime,
    epoch ms) so the dashboard chart can place a time-anchored marker at the
    moment an open position was actually opened, alongside the existing
    price-level reference line.
    """
    mock_fetch_positions.return_value = [
        {
            "symbol": "ETH/USD:USD-310404",
            "side": "short",
            "contracts": 85.0,
            "notional": 208.08595,
            "entryPrice": 2450.38,
            "markPrice": 2448.07,
            "unrealizedPnl": 0.19635,
            "leverage": 3.0,
            "info": {"instId": "ETH-USD_UM_XPERP-310404", "cTime": "1789657534528"},
        }
    ]

    response = client.get("/api/v1/positions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["count"] == 1
    assert data["positions"][0]["entry_timestamp"] == 1789657534528


@patch("src.mms4tk.api.executor.fetch_positions", new_callable=AsyncMock)
def test_get_active_positions_entry_timestamp_absent_is_none(mock_fetch_positions, auth_headers):
    """A position whose raw OKX payload has no cTime must report entry_timestamp
    as None rather than raising or fabricating a value."""
    mock_fetch_positions.return_value = [
        {
            "symbol": "BTC/USD:USD-310404",
            "side": "long",
            "contracts": 1.0,
            "notional": 100.0,
            "entryPrice": 60000.0,
            "markPrice": 60100.0,
            "unrealizedPnl": 1.0,
            "leverage": 3.0,
            "info": {"instId": "BTC-USD_UM_XPERP-310404"},
        }
    ]

    response = client.get("/api/v1/positions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["positions"][0]["entry_timestamp"] is None
