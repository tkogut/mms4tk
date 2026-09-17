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
