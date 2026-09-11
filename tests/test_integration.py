import pytest
import asyncio
from mms4tk.analytics import Candle, IndicatorParams, MMS4SignalEngine
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.state_manager import SCOUT_LEVERAGE, SCOUT_SIZE_MULT
from mms4tk.api import calculate_analytics, get_latest_signal, CalculateRequest, state_manager as api_state_manager


def test_scout_mode_blocks_high_risk_add():
    """Integration test proving SCOUT_MODE blocks HIGH_RISK_ADD signals."""
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)

    # Initially in BASE_MODE
    assert state_mgr.current_state == MMS4State.BASE_MODE
    assert state_mgr.is_high_risk_add_allowed() is True

    # Trigger transition to SCOUT_MODE via 2.5% Stop-Loss hit
    transitioned = state_mgr.on_stop_loss_hit(0.025)
    assert transitioned is True
    assert state_mgr.current_state == MMS4State.SCOUT_MODE
    assert state_mgr.leverage == SCOUT_LEVERAGE
    assert state_mgr.size_multiplier == SCOUT_SIZE_MULT
    assert state_mgr.is_high_risk_add_allowed() is False

    # Setup candles leading to High Risk Add SHORT candidate
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    # Candle 15: Base SHORT signal
    candles.append(Candle(
        timestamp=15 * 60, open=112.0, high=115.0, low=107.0, close=109.0, volume=500.0
    ))
    # Candle 16: Next candle closes down in trend direction
    candles.append(Candle(
        timestamp=16 * 60, open=108.0, high=109.0, low=104.0, close=105.0, volume=500.0
    ))
    # Unclosed live candle
    candles.append(Candle(timestamp=17 * 60, open=105.0, high=106.0, low=104.0, close=105.5, volume=10.0))

    result = engine.evaluate_signals(candles, state_mgr, params)

    assert result.signal_type == "HIGH_RISK_ADD_SHORT"
    assert result.is_allowed is False
    assert result.reason == "HIGH_RISK_ADD blocked in SCOUT_MODE (reduced position size, not leverage)"


def test_take_profit_restores_base_mode_and_enables_high_risk_add():
    """Integration test proving Take Profit restores BASE_MODE and enables HIGH_RISK_ADD."""
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)

    # Transition to SCOUT_MODE
    state_mgr.on_stop_loss_hit(0.02)
    assert state_mgr.current_state == MMS4State.SCOUT_MODE

    # Hit Take Profit -> back to BASE_MODE
    restored = state_mgr.on_take_profit_hit()
    assert restored is True
    assert state_mgr.current_state == MMS4State.BASE_MODE
    assert state_mgr.is_high_risk_add_allowed() is True

    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    # Base LONG signal
    candles.append(Candle(
        timestamp=15 * 60, open=88.0, high=93.0, low=85.0, close=91.0, volume=500.0
    ))
    # Confirming candle closes up
    candles.append(Candle(
        timestamp=16 * 60, open=92.0, high=97.0, low=91.0, close=95.0, volume=500.0
    ))
    # Unclosed live candle
    candles.append(Candle(timestamp=17 * 60, open=95.0, high=96.0, low=94.0, close=95.5, volume=10.0))

    result = engine.evaluate_signals(candles, state_mgr, params)
    assert result.signal_type == "HIGH_RISK_ADD_LONG"
    assert result.is_allowed is True
    assert result.leverage == 1.0


def test_api_analytics_endpoints_direct():
    """Test API calculate and signals endpoints directly."""
    async def _async_test():
        candles = [
            Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
            for i in range(15)
        ]
        req = CalculateRequest(candles=candles, params=IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0))

        calc_res = await calculate_analytics(req)
        assert "tma" in calc_res
        assert "atr" in calc_res
        assert "upper_band" in calc_res
        assert "lower_band" in calc_res
        assert "signal" in calc_res
        assert calc_res["signal"].signal_type == "NONE"

        # Test get_latest_signal endpoint
        latest_sig = await get_latest_signal()
        assert latest_sig.signal_type == "NONE"

    asyncio.run(_async_test())
