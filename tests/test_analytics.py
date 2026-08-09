import pytest
import math
import pandas as pd
from mms4tk.analytics import Candle, IndicatorParams, SignalResult, TMAIndicator, MMS4SignalEngine
from mms4tk.state_manager import MMS4StateManager, MMS4State


def test_tma_indicator_math():
    """Verify TMA and ATR math accuracy against known formulas."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)
    # half_length = math.ceil(10 / 2) = 5

    candles = []
    # 15 candles with flat price 100.0, high 102.0, low 98.0
    for i in range(15):
        candles.append(Candle(
            timestamp=i * 60,
            open=100.0,
            high=102.0,
            low=98.0,
            close=100.0,
            volume=1000.0
        ))

    df = TMAIndicator.calculate(candles, params)

    # For constant close 100.0, TMA must be 100.0 after warm-up
    assert not pd.isna(df["tma"].iloc[-1])
    assert df["tma"].iloc[-1] == pytest.approx(100.0)

    # TR = max(102 - 98, abs(102 - 100), abs(98 - 100)) = 4.0
    # ATR for period 5 of constant TR 4.0 must be 4.0
    assert df["atr"].iloc[-1] == pytest.approx(4.0)

    # Upper Band = 100.0 + 2.0 * 4.0 = 108.0
    # Lower Band = 100.0 - 2.0 * 4.0 = 92.0
    assert df["upper_band"].iloc[-1] == pytest.approx(108.0)
    assert df["lower_band"].iloc[-1] == pytest.approx(92.0)


def test_base_short_signal():
    """Verify BASE_SHORT signal trigger when candle hits upper band and closes down."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    # Candle 15 breaks upper band (upper band is ~108) and closes down
    candles.append(Candle(
        timestamp=15 * 60,
        open=112.0,
        high=115.0,
        low=107.0,
        close=109.0,  # close < open (109 < 112) and close/high >= 108
        volume=500.0
    ))
    # Unclosed live candle
    candles.append(Candle(timestamp=16 * 60, open=109.0, high=110.0, low=108.0, close=109.5, volume=10.0))

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(candles, state_mgr, params)
    assert result.signal_type == "BASE_SHORT"
    assert result.is_allowed is True
    assert result.leverage == 1.0
    assert result.price == 109.0
    assert result.stop_loss == pytest.approx(round(109.0 * 1.02, 6))


def test_base_long_signal():
    """Verify BASE_LONG signal trigger when candle hits lower band and closes up."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    # Candle 15 breaks lower band (lower band is ~92) and closes up
    candles.append(Candle(
        timestamp=15 * 60,
        open=88.0,
        high=93.0,
        low=85.0,
        close=91.0,  # close > open (91 > 88) and low/close <= 92
        volume=500.0
    ))
    # Unclosed live candle
    candles.append(Candle(timestamp=16 * 60, open=91.0, high=92.0, low=90.0, close=91.5, volume=10.0))

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(candles, state_mgr, params)
    assert result.signal_type == "BASE_LONG"
    assert result.is_allowed is True
    assert result.leverage == 1.0
    assert result.price == 91.0
    assert result.stop_loss == pytest.approx(round(91.0 * 0.98, 6))


def test_high_risk_add_evaluation_base_mode():
    """Verify HIGH_RISK_ADD signals in BASE_MODE."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    # Candle 15: Base SHORT signal
    candles.append(Candle(
        timestamp=15 * 60,
        open=112.0,
        high=115.0,
        low=107.0,
        close=109.0,
        volume=500.0
    ))

    # Candle 16: Next candle closes down in trend direction (close 105 < open 108)
    candles.append(Candle(
        timestamp=16 * 60,
        open=108.0,
        high=109.0,
        low=104.0,
        close=105.0,
        volume=500.0
    ))
    # Unclosed live candle
    candles.append(Candle(timestamp=17 * 60, open=105.0, high=106.0, low=104.0, close=105.5, volume=10.0))

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(candles, state_mgr, params)
    assert result.signal_type == "HIGH_RISK_ADD_SHORT"
    assert result.is_allowed is True
    assert result.leverage == 1.0
    assert result.price == 105.0
    assert result.stop_loss == pytest.approx(round(105.0 * 1.01, 6))
