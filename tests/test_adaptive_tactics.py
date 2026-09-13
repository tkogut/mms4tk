import os
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from mms4tk.api import app, state_manager, daemon as api_daemon, executor as api_executor
from mms4tk.analytics import Candle, IndicatorParams, HigherTimeframeAnalytics, MMS4SignalEngine
from mms4tk.state_manager import MMS4StateManager, MMS4State, StrategyProfile
from mms4tk.logger_db import LoggerDB


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def restore_adaptive_settings():
    """
    POST /api/v1/settings/update mutates process-wide os.environ, logger_db, and the shared
    `daemon` singleton directly. Snapshot and restore both around any test that calls this endpoint.
    """
    from mms4tk.api import logger_db as api_logger_db
    env_keys = [
        "MMS4_ENGINE_MODE", "MMS4_ADAPTIVE_VR_HIGH", "MMS4_ADAPTIVE_VR_LOW",
        "MMS4_TIME_LOCK_START", "MMS4_TIME_LOCK_END",
    ]
    saved_env = {k: os.environ.get(k) for k in env_keys}
    saved_db = api_logger_db.get_all_settings() if api_logger_db else {}
    saved_cap = api_daemon.total_max_portfolio_margin_usd
    yield
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if api_logger_db:
        api_logger_db.set_settings_bulk({
            "engine_mode": saved_env.get("MMS4_ENGINE_MODE", "CLASSIC"),
            "adaptive_vr_high": saved_env.get("MMS4_ADAPTIVE_VR_HIGH", "1.6"),
            "adaptive_vr_low": saved_env.get("MMS4_ADAPTIVE_VR_LOW", "0.8"),
            "time_lock_start": saved_env.get("MMS4_TIME_LOCK_START", "13:30"),
            "time_lock_end": saved_env.get("MMS4_TIME_LOCK_END", "14:30"),
        }, updated_by="fixture_cleanup")
    api_daemon.total_max_portfolio_margin_usd = saved_cap



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

    # calculate_higher_tf drops the still-forming last candle (no repainting), so append
    # a live bar and let the candle under test be the newest CLOSED one.
    live_h4 = Candle(timestamp=21 * 14400, open=100.0, high=100.5, low=99.5, close=100.0, volume=10.0)
    live_d1 = Candle(timestamp=21 * 86400, open=110.0, high=111.0, low=109.0, close=110.0, volume=10.0)
    candles_d1 = candles_d1 + [live_d1]
    candles_h4_sweep = candles_h4_sweep + [live_h4]

    res_sweep = HigherTimeframeAnalytics.calculate_higher_tf(candles_h4_sweep, candles_d1)
    assert res_sweep["atr_d1"] > 0
    assert res_sweep["atr_d1_expansion_ratio"] > 0
    assert res_sweep["h4_candle_formation"] == "SWEEP_WICK"

    # MARUBOZU_BREAKOUT candle: large body relative to range
    candles_h4_marubozu = list(candles_h4_sweep)
    candles_h4_marubozu[-2] = Candle(timestamp=20 * 14400, open=100.0, high=110.0, low=99.0, close=109.0, volume=500.0)
    res_marubozu = HigherTimeframeAnalytics.calculate_higher_tf(candles_h4_marubozu, candles_d1)
    assert res_marubozu["h4_candle_formation"] == "MARUBOZU_BREAKOUT"


def test_higher_tf_endpoint_reports_data_stale_on_okx_outage(client):
    """Faza 6 C.1.3: a real OKX outage during GET /api/v1/analytics/higher_tf
    must surface `data_stale=true`, not silently compute a fake reading (mock
    candles) or a 500. Same bug class already fixed for the internal H4 gate
    (`daemon._refresh_htf_directional_gate`, `allow_mock_fallback=False`)."""
    original_fetch = api_executor.fetch_candles
    api_executor.fetch_candles = AsyncMock(side_effect=Exception("Simulated OKX outage"))
    try:
        resp = client.get("/api/v1/analytics/higher_tf")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data_stale"] is True
        # Best-effort neutral values, not a real reading presented as genuine.
        assert data["h4_tma_position"] == "INSIDE"
        assert data["h4_candle_formation"] == "NEUTRAL"
    finally:
        api_executor.fetch_candles = original_fetch


def test_higher_tf_endpoint_data_stale_false_on_success(client):
    """Regression: a normal successful fetch must NOT be flagged stale."""
    candles_h4 = [
        Candle(timestamp=i * 14400, open=100.0, high=100.5, low=99.5, close=100.0, volume=1.0)
        for i in range(30)
    ]
    candles_d1 = [
        Candle(timestamp=i * 86400, open=100.0, high=105.0, low=95.0, close=100.0, volume=1.0)
        for i in range(30)
    ]
    original_fetch = api_executor.fetch_candles

    async def _fake_fetch_candles(*args, **kwargs):
        return candles_h4 if kwargs.get("timeframe") == "4h" else candles_d1

    api_executor.fetch_candles = _fake_fetch_candles
    try:
        resp = client.get("/api/v1/analytics/higher_tf")
        assert resp.status_code == 200
        assert resp.json()["data_stale"] is False
    finally:
        api_executor.fetch_candles = original_fetch


def test_strategy_profile_api(client):
    """Test GET and POST endpoints for StrategyProfile with X-MMS4-API-KEY auth."""
    # Reset strategy profile
    state_manager.update_strategy_profile(StrategyProfile())
    os.environ["MMS4_API_KEY"] = "test-api-auth-key-unit-test"

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


def test_strategy_profile_partial_update_does_not_clobber_other_fields(client):
    """Faza 6 C.1.2: POST /api/v1/strategy/profile with only ONE field set must
    leave every other field of the existing persisted profile untouched --
    the bug this fixes: `directional_bias` used to reset to "BOTH" (Pydantic
    default) on ANY write that didn't explicitly repeat it."""
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
    os.environ["MMS4_API_KEY"] = api_key

    # Seed a known, non-default baseline profile.
    state_manager.update_strategy_profile(
        StrategyProfile(
            directional_bias="LONG_ONLY",
            risk_profile="MODERATE",
            scout_override=False,
            reason="Seeded baseline",
        )
    )

    # Send ONLY risk_profile.
    resp = client.post(
        "/api/v1/strategy/profile",
        headers={"X-MMS4-API-KEY": api_key},
        json={"risk_profile": "AGGRESSIVE"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["risk_profile"] == "AGGRESSIVE"
    # directional_bias, scout_override, reason must survive untouched.
    assert data["directional_bias"] == "LONG_ONLY"
    assert data["scout_override"] is False
    assert data["reason"] == "Seeded baseline"

    # GET confirms persistence of the merge, not just the response body.
    get_resp = client.get("/api/v1/strategy/profile")
    assert get_resp.json()["directional_bias"] == "LONG_ONLY"
    assert get_resp.json()["risk_profile"] == "AGGRESSIVE"

    # Reset for other tests.
    state_manager.update_strategy_profile(StrategyProfile())


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
    """Test that BASE_SHORT signal leverage tracks state_manager's own per-profile
    resolution (BASE_LEVERAGE_BY_PROFILE: CONSERVATIVE=1.0, AGGRESSIVE=10.0 by
    default), the single source of truth daemon.py actually executes orders with.

    Audit P9.6 (2026-09-08): previously this asserted 0.5x/2.0x, values produced by a
    second, REDUNDANT risk_prof->leverage computation inside analytics.py itself
    (local `lev`) that daemon.py never read (it reads alloc['leverage'], sourced from
    state_manager.leverage) and that didn't even agree with state_manager's own
    resolution for the same profiles. That dead, inconsistent computation was
    removed; SignalResult.leverage now reports active_leverage (== state_manager's
    leverage) like every other branch in this function already did."""
    engine = MMS4SignalEngine()
    candles_short = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_short[-2] = Candle(timestamp=23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_short[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    # CONSERVATIVE -> BASE_LEVERAGE_BY_PROFILE["CONSERVATIVE"] (default 1.0x)
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    sm.update_strategy_profile(StrategyProfile(risk_profile="CONSERVATIVE"))
    sig_cons = engine.evaluate_signals(candles_short, sm)
    assert sig_cons.signal_type == "BASE_SHORT"
    assert sig_cons.leverage == sm.leverage

    # AGGRESSIVE -> BASE_LEVERAGE_BY_PROFILE["AGGRESSIVE"] (default 10.0x)
    sm.update_strategy_profile(StrategyProfile(risk_profile="AGGRESSIVE"))
    sig_agg = engine.evaluate_signals(candles_short, sm)
    assert sig_agg.signal_type == "BASE_SHORT"
    assert sig_agg.leverage == sm.leverage
    # The two profiles must actually resolve to different leverage, or this test
    # would pass vacuously.
    assert sig_cons.leverage != sig_agg.leverage


# ─────────────────────────────────────────────────────────────────────────────
# ⚡ Adaptive Hybrid Engine Tests (Fast-Path Supervision)
# ─────────────────────────────────────────────────────────────────────────────

def test_adaptive_engine_classic_mode_bypass():
    """In CLASSIC mode, all adaptive VR and time-lock filters must be bypassed (Early Return)."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    # 14:00 UTC (inside 13:30-14:30 window)
    ts = 1787148000  # epoch seconds for a 14:00 UTC timestamp

    candles_short = [
        Candle(timestamp=ts + i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    # Standard short trigger without extreme wick
    candles_short[-2] = Candle(timestamp=ts + 23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_short[-1] = Candle(timestamp=ts + 24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    params_classic = IndicatorParams(engine_mode="CLASSIC", time_lock_start="13:30", time_lock_end="14:30")
    sig = engine.evaluate_signals(candles_short, sm, params=params_classic)
    assert sig.signal_type == "BASE_SHORT"
    assert sig.is_allowed is True
    assert "Base SHORT signal" in sig.reason


def test_adaptive_engine_volatility_ratio_high_blocks_addons():
    """In ADAPTIVE mode, VR > adaptive_vr_high blocks HIGH_RISK_ADD signals."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    # Create candles where prev was base_short and curr closes down (standard addon trigger)
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    # Prev was base short
    candles[-3] = Candle(timestamp=22 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    # Curr is addon short
    candles[-2] = Candle(timestamp=23 * 60, open=99.2, high=99.4, low=98.0, close=98.5)
    candles[-1] = Candle(timestamp=24 * 60, open=98.5, high=98.8, low=98.2, close=98.6)

    # Force adaptive mode with very low vr_high threshold (0.5) so any normal market triggers VR > 0.5
    params_adaptive = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=0.1)
    sig = engine.evaluate_signals(candles, sm, params=params_adaptive)

    assert sig.signal_type == "HIGH_RISK_ADD_SHORT"
    assert sig.is_allowed is False
    assert "SKIPPED_HIGH_VOLATILITY" in sig.reason
    assert sig.volatility_ratio is not None


def test_adaptive_engine_volatility_ratio_high_blocks_addons_long():
    """LONG mirror of the SHORT case above — the engine has a separate, duplicated
    code branch for HIGH_RISK_ADD_LONG that the SHORT-only test never exercises."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    candles = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    # Prev was base long: touches lower band and closes up
    candles[-3] = Candle(timestamp=22 * 60, open=100.0, high=101.0, low=92.0, close=100.8)
    # Curr is addon long: confirms the up-move
    candles[-2] = Candle(timestamp=23 * 60, open=100.8, high=101.6, low=100.0, close=101.5)
    candles[-1] = Candle(timestamp=24 * 60, open=101.5, high=101.8, low=101.2, close=101.6)

    params_adaptive = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=0.1)
    sig = engine.evaluate_signals(candles, sm, params=params_adaptive)

    assert sig.signal_type == "HIGH_RISK_ADD_LONG"
    assert sig.is_allowed is False
    assert "SKIPPED_HIGH_VOLATILITY" in sig.reason
    assert sig.volatility_ratio is not None


def test_adaptive_engine_high_volatility_requires_extreme_sweep_for_base_short():
    """In ADAPTIVE mode, VR > adaptive_vr_high blocks BASE_SHORT unless an EXTREME
    Sweep Rejection wick (>=40%, same bar as Time-Lock) is present. Added after a
    90-day backtest audit (2026-09-08) found ETH/USDC BASE entries during ATR spikes
    (band_width 3x+ normal) getting stopped out disproportionately vs BTC/USDC in the
    same window — the VR filter previously only protected HIGH_RISK_ADD, never BASE."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    # Force the high-volatility branch (vr_high=0.1 -> always exceeded); keep
    # compression/time-lock out of the way so only this filter is under test.
    params_high_vol = IndicatorParams(
        engine_mode="ADAPTIVE", adaptive_vr_high=0.1, adaptive_vr_low=0.0,
        time_lock_start="00:00", time_lock_end="00:01",
        high_vol_base_filter_enabled=True,
    )

    # 1. Moderate upper wick: 3.0 / 9.0 = 33.3% -- passes compression's 20% bar but
    # not the extreme 40% bar this filter requires.
    candles_mod_wick = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_mod_wick[-2] = Candle(timestamp=23 * 60, open=105.0, high=108.0, low=99.0, close=99.2)
    candles_mod_wick[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    sig_blocked = engine.evaluate_signals(candles_mod_wick, sm, params=params_high_vol)
    assert sig_blocked.signal_type == "BASE_SHORT"
    assert sig_blocked.is_allowed is False
    assert "SKIPPED_HIGH_VOLATILITY_BASE" in sig_blocked.reason

    # 2. Extreme upper wick: 8.0 / 9.0 = 88.9% -> allowed
    candles_extreme_wick = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_extreme_wick[-2] = Candle(timestamp=23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_extreme_wick[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    sig_allowed = engine.evaluate_signals(candles_extreme_wick, sm, params=params_high_vol)
    assert sig_allowed.signal_type == "BASE_SHORT"
    assert sig_allowed.is_allowed is True
    assert "Base SHORT signal" in sig_allowed.reason


def test_adaptive_engine_high_volatility_base_filter_disabled_by_default():
    """high_vol_base_filter_enabled defaults to False (like tma_trend_filter_enabled)
    — a backtest audit (2026-09-08) found the filter itself makes results worse in
    3 of 4 tested half-windows, so it must stay opt-in, unvalidated-by-default, exactly
    like the trend filter's own history. The SAME moderate-wick candle that gets
    blocked when the flag is explicitly on (test above) must be ALLOWED when it is
    left at its default, even though VR is still > adaptive_vr_high."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    params_default = IndicatorParams(
        engine_mode="ADAPTIVE", adaptive_vr_high=0.1, adaptive_vr_low=0.0,
        time_lock_start="00:00", time_lock_end="00:01",
        # high_vol_base_filter_enabled intentionally omitted -> must default to False
    )
    assert params_default.high_vol_base_filter_enabled is False

    candles_mod_wick = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_mod_wick[-2] = Candle(timestamp=23 * 60, open=105.0, high=108.0, low=99.0, close=99.2)
    candles_mod_wick[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    sig = engine.evaluate_signals(candles_mod_wick, sm, params=params_default)
    assert sig.signal_type == "BASE_SHORT"
    assert sig.is_allowed is True
    assert "Base SHORT signal" in sig.reason


def test_adaptive_engine_high_volatility_requires_extreme_sweep_for_base_long():
    """LONG mirror of the high-volatility test above — BASE_LONG has its own,
    separately-coded high-volatility branch (no Time-Lock filter to interleave with,
    since that one only ever applied to BASE_SHORT per MMS v4 spec)."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    params_high_vol = IndicatorParams(
        engine_mode="ADAPTIVE", adaptive_vr_high=0.1, adaptive_vr_low=0.0,
        time_lock_start="00:00", time_lock_end="00:01",
        high_vol_base_filter_enabled=True,
    )

    # 1. Moderate lower wick: 3.0 / 9.0 = 33.3% -> blocked
    candles_mod_wick = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_mod_wick[-2] = Candle(timestamp=23 * 60, open=95.0, high=101.0, low=92.0, close=100.8)
    candles_mod_wick[-1] = Candle(timestamp=24 * 60, open=100.8, high=101.0, low=100.5, close=100.9)

    sig_blocked = engine.evaluate_signals(candles_mod_wick, sm, params=params_high_vol)
    assert sig_blocked.signal_type == "BASE_LONG"
    assert sig_blocked.is_allowed is False
    assert "SKIPPED_HIGH_VOLATILITY_BASE" in sig_blocked.reason

    # 2. Extreme lower wick: 8.0 / 9.0 = 88.9% -> allowed
    candles_extreme_wick = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_extreme_wick[-2] = Candle(timestamp=23 * 60, open=100.0, high=101.0, low=92.0, close=100.8)
    candles_extreme_wick[-1] = Candle(timestamp=24 * 60, open=100.8, high=101.0, low=100.5, close=100.9)

    sig_allowed = engine.evaluate_signals(candles_extreme_wick, sm, params=params_high_vol)
    assert sig_allowed.signal_type == "BASE_LONG"
    assert sig_allowed.is_allowed is True
    assert "Base LONG signal" in sig_allowed.reason


def test_adaptive_engine_volatility_ratio_low_requires_sweep_rejection():
    """In ADAPTIVE mode, VR < adaptive_vr_low blocks base entries unless Sweep Rejection is present."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    # 1. Base Short WITHOUT upper wick rejection (e.g. open=100, high=108, close=99.8, low=99.7 -> tiny wick)
    candles_no_sweep = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    # High is barely above open (wick is small)
    candles_no_sweep[-2] = Candle(timestamp=23 * 60, open=107.8, high=108.0, low=99.0, close=99.2)
    candles_no_sweep[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    # High vr_low threshold (e.g. 5.0) forces compression check
    params_compression = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_low=5.0, time_lock_start="00:00", time_lock_end="00:01")
    sig_blocked = engine.evaluate_signals(candles_no_sweep, sm, params=params_compression)

    assert sig_blocked.signal_type == "BASE_SHORT"
    assert sig_blocked.is_allowed is False
    assert "SKIPPED_COMPRESSION_NO_SWEEP" in sig_blocked.reason

    # 2. Base Short WITH prominent upper wick rejection (open=100, high=108, close=99.2 -> upper wick 8.0 / 9.0 = 88%)
    candles_with_sweep = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_with_sweep[-2] = Candle(timestamp=23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_with_sweep[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    sig_allowed = engine.evaluate_signals(candles_with_sweep, sm, params=params_compression)
    assert sig_allowed.signal_type == "BASE_SHORT"
    assert sig_allowed.is_allowed is True
    assert "Base SHORT signal" in sig_allowed.reason


def test_adaptive_engine_volatility_ratio_low_requires_sweep_rejection_long():
    """LONG mirror of the compression/sweep-rejection test above — BASE_LONG has its
    own, separately-coded compression branch that the SHORT-only test never touches."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    # 1. Base Long WITHOUT lower wick rejection (tiny wick)
    candles_no_sweep = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    # Low is barely below open (wick is small)
    candles_no_sweep[-2] = Candle(timestamp=23 * 60, open=92.2, high=101.0, low=92.0, close=100.8)
    candles_no_sweep[-1] = Candle(timestamp=24 * 60, open=100.8, high=101.0, low=100.5, close=100.9)

    params_compression = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_low=5.0, time_lock_start="00:00", time_lock_end="00:01")
    sig_blocked = engine.evaluate_signals(candles_no_sweep, sm, params=params_compression)

    assert sig_blocked.signal_type == "BASE_LONG"
    assert sig_blocked.is_allowed is False
    assert "SKIPPED_COMPRESSION_NO_SWEEP" in sig_blocked.reason

    # 2. Base Long WITH prominent lower wick rejection (open=100, high=101, low=92, close=100.8 -> lower wick 8.0 / 9.0 = 88.9%)
    candles_with_sweep = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_with_sweep[-2] = Candle(timestamp=23 * 60, open=100.0, high=101.0, low=92.0, close=100.8)
    candles_with_sweep[-1] = Candle(timestamp=24 * 60, open=100.8, high=101.0, low=100.5, close=100.9)

    sig_allowed = engine.evaluate_signals(candles_with_sweep, sm, params=params_compression)
    assert sig_allowed.signal_type == "BASE_LONG"
    assert sig_allowed.is_allowed is True
    assert "Base LONG signal" in sig_allowed.reason


def test_adaptive_engine_us_open_time_lock_filtering():
    """In ADAPTIVE mode, time lock window (13:30 - 14:30 UTC) blocks BASE_SHORT without extreme sweep."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    # 14:00:00 UTC timestamp (2026-08-19 14:00:00 UTC)
    ts_inside_lock = 1787148000

    # 1. Base Short inside time lock with moderate wick (wick ~25-33% < 40%) -> BLOCKED
    candles_mod_wick = [
        Candle(timestamp=ts_inside_lock + i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    # Upper wick is 3.0 / 9.0 = 33.3% (< 40% extreme threshold, but >= 20% standard sweep)
    candles_mod_wick[-2] = Candle(timestamp=ts_inside_lock + 23 * 60, open=105.0, high=108.0, low=99.0, close=99.2)
    candles_mod_wick[-1] = Candle(timestamp=ts_inside_lock + 24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    params_timelock = IndicatorParams(engine_mode="ADAPTIVE", time_lock_start="13:30", time_lock_end="14:30", adaptive_vr_low=0.0)
    sig_blocked = engine.evaluate_signals(candles_mod_wick, sm, params=params_timelock)

    assert sig_blocked.signal_type == "BASE_SHORT"
    assert sig_blocked.is_allowed is False
    assert "SKIPPED_TIME_LOCK" in sig_blocked.reason

    # 2. Base Short inside time lock with EXTREME upper wick (8.0 / 9.0 = 88.8% >= 40%) -> ALLOWED
    candles_extreme_wick = [
        Candle(timestamp=ts_inside_lock + i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_extreme_wick[-2] = Candle(timestamp=ts_inside_lock + 23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_extreme_wick[-1] = Candle(timestamp=ts_inside_lock + 24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    sig_allowed = engine.evaluate_signals(candles_extreme_wick, sm, params=params_timelock)
    assert sig_allowed.signal_type == "BASE_SHORT"
    assert sig_allowed.is_allowed is True


def test_settings_api_adaptive_configuration(client, restore_adaptive_settings):
    """Test GET /api/v1/settings and POST /api/v1/settings/update for adaptive parameters."""
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")

    # 1. GET current settings
    get_res = client.get("/api/v1/settings")
    assert get_res.status_code == 200
    s_data = get_res.json()
    assert "engine_mode" in s_data
    assert "adaptive_vr_high" in s_data
    assert "adaptive_vr_low" in s_data
    assert "time_lock_start" in s_data
    assert "time_lock_end" in s_data

    # 2. POST update settings
    post_res = client.post(
        "/api/v1/settings/update",
        headers={"X-MMS4-API-KEY": api_key},
        json={
            "engine_mode": "ADAPTIVE",
            "adaptive_vr_high": 1.75,
            "adaptive_vr_low": 0.75,
            "time_lock_start": "14:00",
            "time_lock_end": "15:00",
            "total_max_portfolio_margin_usd": 1200.0
        }
    )
    assert post_res.status_code == 200
    updated = post_res.json()
    assert updated["engine_mode"] == "ADAPTIVE"
    assert updated["adaptive_vr_high"] == 1.75
    assert updated["adaptive_vr_low"] == 0.75
    assert updated["time_lock_start"] == "14:00"
    assert updated["time_lock_end"] == "15:00"
    assert updated["total_max_portfolio_margin_usd"] == 1200.0

    # 3. Verify GET reflects update
    verify_get = client.get("/api/v1/settings")
    assert verify_get.status_code == 200
    assert verify_get.json()["engine_mode"] == "ADAPTIVE"
    assert verify_get.json()["adaptive_vr_high"] == 1.75


def test_settings_api_rejects_inverted_vr_thresholds(client, restore_adaptive_settings):
    """adaptive_vr_low >= adaptive_vr_high sent together must be rejected — a valid
    combo means every VR reading falls in exactly one band, never both or neither."""
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")

    res = client.post(
        "/api/v1/settings/update",
        headers={"X-MMS4-API-KEY": api_key},
        json={"adaptive_vr_high": 1.0, "adaptive_vr_low": 1.5},
    )
    assert res.status_code == 400
    assert "adaptive_vr_low" in res.json()["detail"]

    # Neither threshold may have been applied — no partial write on a rejected combo.
    assert float(os.getenv("MMS4_ADAPTIVE_VR_HIGH", "1.6")) == 1.6
    assert float(os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8")) == 0.8


def test_settings_api_rejects_vr_low_against_existing_high(client, restore_adaptive_settings):
    """Sending only adaptive_vr_low must be validated against the CURRENTLY active
    adaptive_vr_high, not skip the cross-field check just because it wasn't resent."""
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")

    # Lower the high threshold first...
    ok = client.post(
        "/api/v1/settings/update",
        headers={"X-MMS4-API-KEY": api_key},
        json={"adaptive_vr_high": 1.0},
    )
    assert ok.status_code == 200

    # ...then try to raise the low threshold above it, without resending vr_high.
    res = client.post(
        "/api/v1/settings/update",
        headers={"X-MMS4-API-KEY": api_key},
        json={"adaptive_vr_low": 1.2},
    )
    assert res.status_code == 400
    assert float(os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8")) == 0.8


def test_settings_api_rejects_negative_portfolio_cap(client, restore_adaptive_settings):
    """A negative cap would silently disable the guard (cap<=0 means 'off'), which
    reads as the opposite of what someone setting a negative number likely intends."""
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
    saved_cap = api_daemon.total_max_portfolio_margin_usd

    res = client.post(
        "/api/v1/settings/update",
        headers={"X-MMS4-API-KEY": api_key},
        json={"total_max_portfolio_margin_usd": -50.0},
    )
    assert res.status_code == 400
    assert api_daemon.total_max_portfolio_margin_usd == saved_cap

    # 0 is the documented, legitimate way to disable the guard — must still work.
    ok = client.post(
        "/api/v1/settings/update",
        headers={"X-MMS4-API-KEY": api_key},
        json={"total_max_portfolio_margin_usd": 0.0},
    )
    assert ok.status_code == 200
    assert api_daemon.total_max_portfolio_margin_usd == 0.0

