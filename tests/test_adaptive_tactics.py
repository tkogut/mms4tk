import os
import pytest
from fastapi.testclient import TestClient

from mms4tk.api import app, state_manager
from mms4tk.analytics import Candle, IndicatorParams, HigherTimeframeAnalytics, MMS4SignalEngine
from mms4tk.state_manager import MMS4StateManager, MMS4State, StrategyProfile
from mms4tk.logger_db import LoggerDB


@pytest.fixture
def client():
    return TestClient(app)


def test_higher_timeframe_analytics():
    """Test H4/D1 higher timeframe calculations, D1 ATR expansion ratio, TMA position, and candle formations."""
    # Generate D1 candles with increasing range
    candles_d1 = [
        Candle(timestamp=i * 86400, open=100.0, high=105.0, low=95.0, close=100.0, volume=1000.0)
        for i in range(20)
    ]
    # Make latest D1 candle range larger to test expansion ratio
    candles_d1[-1] = Candle(timestamp=20 * 86400, open=100.0, high=120.0, low=90.0, close=110.0, volume=2000.0)

    # Generate H4 candles (Sweep Wick & Marubozu & Normal)
    candles_h4_sweep = [
        Candle(timestamp=i * 14400, open=100.0, high=102.0, low=98.0, close=100.0, volume=500.0)
        for i in range(20)
    ]
    # SWEEP_WICK candle: long wick relative to body
    candles_h4_sweep[-1] = Candle(timestamp=20 * 14400, open=100.0, high=110.0, low=95.0, close=100.0, volume=500.0)

    res_sweep = HigherTimeframeAnalytics.calculate_higher_tf(candles_h4_sweep, candles_d1)
    assert res_sweep["atr_d1"] > 0
    assert res_sweep["atr_d1_expansion_ratio"] > 0
    assert res_sweep["h4_candle_formation"] == "SWEEP_WICK"

    # MARUBOZU_BREAKOUT candle: large body relative to range
    candles_h4_marubozu = list(candles_h4_sweep)
    candles_h4_marubozu[-1] = Candle(timestamp=20 * 14400, open=100.0, high=110.0, low=99.0, close=109.0, volume=500.0)
    res_marubozu = HigherTimeframeAnalytics.calculate_higher_tf(candles_h4_marubozu, candles_d1)
    assert res_marubozu["h4_candle_formation"] == "MARUBOZU_BREAKOUT"


def test_strategy_profile_api(client):
    """Test GET and POST endpoints for StrategyProfile with X-MMS4-API-KEY auth."""
    # Reset strategy profile
    state_manager.update_strategy_profile(StrategyProfile())

    # 1. GET current profile
    response = client.get("/api/v1/strategy/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["directional_bias"] == "BOTH"
    assert data["risk_profile"] == "MODERATE"
    assert data["scout_override"] is False

    # 2. POST update without header -> 401
    bad_resp = client.post("/api/v1/strategy/profile", json={"directional_bias": "LONG_ONLY"})
    assert bad_resp.status_code == 401

    # 3. POST update with wrong key -> 401
    bad_key_resp = client.post(
        "/api/v1/strategy/profile",
        headers={"X-MMS4-API-KEY": "invalid_key"},
        json={"directional_bias": "LONG_ONLY"}
    )
    assert bad_key_resp.status_code == 401

    # 4. POST update with valid key -> 200 OK
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
    valid_resp = client.post(
        "/api/v1/strategy/profile",
        headers={"X-MMS4-API-KEY": api_key},
        json={
            "directional_bias": "LONG_ONLY",
            "risk_profile": "AGGRESSIVE",
            "scout_override": False,
            "reason": "Test update via API"
        }
    )
    assert valid_resp.status_code == 200
    updated_data = valid_resp.json()
    assert updated_data["directional_bias"] == "LONG_ONLY"
    assert updated_data["risk_profile"] == "AGGRESSIVE"

    # 5. GET profile verifies persistent update
    get_resp = client.get("/api/v1/strategy/profile")
    assert get_resp.status_code == 200
    assert get_resp.json()["directional_bias"] == "LONG_ONLY"


def test_directional_bias_signal_filtering():
    """Test filtering of trading signals by directional_bias (LONG_ONLY, SHORT_ONLY, FLAT)."""
    engine = MMS4SignalEngine()

    # Create candles that trigger BASE_SHORT (candle hits upper band and closes down)
    candles_short = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    # Make last closed candle hit high upper band and close down
    candles_short[-2] = Candle(timestamp=23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_short[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    # Test LONG_ONLY blocks SHORT signal
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    sm.update_strategy_profile(StrategyProfile(directional_bias="LONG_ONLY"))
    sig_long_only = engine.evaluate_signals(candles_short, sm)
    assert sig_long_only.signal_type == "NONE"
    assert "Signal blocked by directional_bias=LONG_ONLY" in sig_long_only.reason

    # Test FLAT blocks signals
    sm.update_strategy_profile(StrategyProfile(directional_bias="FLAT"))
    sig_flat = engine.evaluate_signals(candles_short, sm)
    assert sig_flat.signal_type == "NONE"
    assert "Signal blocked by directional_bias=FLAT" in sig_flat.reason

    # Create candles that trigger BASE_LONG (candle hits lower band and closes up)
    candles_long = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_long[-2] = Candle(timestamp=23 * 60, open=100.0, high=100.5, low=92.0, close=100.8)
    candles_long[-1] = Candle(timestamp=24 * 60, open=100.8, high=101.0, low=100.5, close=100.9)

    # Test SHORT_ONLY blocks LONG signal
    sm.update_strategy_profile(StrategyProfile(directional_bias="SHORT_ONLY"))
    sig_short_only = engine.evaluate_signals(candles_long, sm)
    assert sig_short_only.signal_type == "NONE"
    assert "Signal blocked by directional_bias=SHORT_ONLY" in sig_short_only.reason


def test_scout_mode_relaxation_by_scout_override(client, tmp_path):
    """Test SCOUT_MODE relaxation to BASE_MODE triggered by scout_override."""
    db_file = os.path.join(str(tmp_path), "test_scout.db")
    logger_db = LoggerDB(db_path=db_file)
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=logger_db)

    # Trigger SCOUT_MODE transition via SL hit >= 2%
    transitioned = sm.on_stop_loss_hit(0.025)
    assert transitioned is True
    assert sm.current_state == MMS4State.SCOUT_MODE

    # Test relax_scout_mode direct call
    relaxed = sm.relax_scout_mode()
    assert relaxed is True
    assert sm.current_state == MMS4State.BASE_MODE

    # Re-trigger SCOUT_MODE
    sm.on_stop_loss_hit(0.025)
    assert sm.current_state == MMS4State.SCOUT_MODE

    # Test relaxation via API endpoint with scout_override=True
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
    resp = client.post(
        "/api/v1/strategy/profile",
        headers={"X-MMS4-API-KEY": api_key},
        json={
            "directional_bias": "BOTH",
            "risk_profile": "MODERATE",
            "scout_override": True,
            "reason": "Hermes Scout Mode Relaxation"
        }
    )
    assert resp.status_code == 200
    assert state_manager.current_state == MMS4State.BASE_MODE


def test_risk_profile_leverage_modulation():
    """Test leverage modulation by CONSERVATIVE (0.5x, block add) and AGGRESSIVE (2.0x base)."""
    engine = MMS4SignalEngine()
    candles_short = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_short[-2] = Candle(timestamp=23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_short[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    # CONSERVATIVE caps base leverage at 0.5x
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    sm.update_strategy_profile(StrategyProfile(risk_profile="CONSERVATIVE"))
    sig_cons = engine.evaluate_signals(candles_short, sm)
    assert sig_cons.signal_type == "BASE_SHORT"
    assert sig_cons.leverage == 0.5

    # AGGRESSIVE sets base leverage to 2.0x
    sm.update_strategy_profile(StrategyProfile(risk_profile="AGGRESSIVE"))
    sig_agg = engine.evaluate_signals(candles_short, sm)
    assert sig_agg.signal_type == "BASE_SHORT"
    assert sig_agg.leverage == 2.0
