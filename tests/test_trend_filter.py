"""PLAN-008 Phase 3: TMA Slope Trend Filter regression tests.

90-day per-symbol calibration (2026-08-28) found the SAME lookback/threshold helps
BTC/USDC broadly (14/16 grid cells beat baseline on both R:R and total PnL) but hurts
ETH/USDC in 15/16 cells — so the filter must be per-symbol and default OFF, never a
single global switch.
"""
import pandas as pd
import pytest

from mms4tk.analytics import Candle, IndicatorParams, MMS4SignalEngine, TMAIndicator
from mms4tk.state_manager import MMS4StateManager, MMS4State


def _uptrend_base_short_candles():
    """Flat 100 for 15 candles, then a spike that touches the upper band and closes
    red — a textbook BASE_SHORT setup, but forming during a nascent uptrend (TMA
    slope > 0 at the decision candle, see test_trend_filter_uptrend_slope_is_positive)."""
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    candles.append(Candle(timestamp=15 * 60, open=112.0, high=115.0, low=107.0, close=109.0, volume=500.0))
    candles.append(Candle(timestamp=16 * 60, open=109.0, high=110.0, low=108.0, close=109.5, volume=10.0))
    return candles


def _downtrend_base_long_candles():
    """Mirror of the above: a downward spike that touches the lower band and closes
    green, during a nascent downtrend (TMA slope < 0)."""
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    candles.append(Candle(timestamp=15 * 60, open=88.0, high=93.0, low=85.0, close=91.0, volume=500.0))
    candles.append(Candle(timestamp=16 * 60, open=91.0, high=92.0, low=90.0, close=91.5, volume=10.0))
    return candles


def test_tma_slope_column_present_and_correct_sign():
    """TMAIndicator.calculate() must expose a tma_slope column: positive during the
    uptrend-forming scenario, negative during the downtrend-forming one."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0, tma_slope_lookback=5)

    df_up = TMAIndicator.calculate(_uptrend_base_short_candles()[:-1], params)
    assert "tma_slope" in df_up.columns
    assert df_up["tma_slope"].iloc[-1] > 0

    df_down = TMAIndicator.calculate(_downtrend_base_long_candles()[:-1], params)
    assert df_down["tma_slope"].iloc[-1] < 0


def test_trend_filter_disabled_by_default_does_not_change_existing_behavior():
    """Backward compatibility: with tma_trend_filter_enabled left at its default
    (False), BASE_SHORT/BASE_LONG signals fire exactly as before Phase 3."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)
    assert params.tma_trend_filter_enabled is False

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(_uptrend_base_short_candles(), state_mgr, params)
    assert result.signal_type == "BASE_SHORT"
    assert result.is_allowed is True


def test_trend_filter_blocks_counter_trend_base_short():
    """A BASE_SHORT signal forming during an uptrend (slope > threshold) must be
    vetoed (is_allowed=False) when the filter is enabled for this symbol."""
    params = IndicatorParams(
        tma_length=10, atr_period=5, atr_mult=2.0,
        tma_trend_filter_enabled=True, tma_slope_lookback=5, tma_slope_threshold=0.002,
    )
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(_uptrend_base_short_candles(), state_mgr, params)
    assert result.signal_type == "BASE_SHORT"
    assert result.is_allowed is False
    assert "Trend Filter" in result.reason


def test_trend_filter_blocks_counter_trend_base_long():
    """A BASE_LONG signal forming during a downtrend (slope < -threshold) must be
    vetoed when the filter is enabled."""
    params = IndicatorParams(
        tma_length=10, atr_period=5, atr_mult=2.0,
        tma_trend_filter_enabled=True, tma_slope_lookback=5, tma_slope_threshold=0.002,
    )
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(_downtrend_base_long_candles(), state_mgr, params)
    assert result.signal_type == "BASE_LONG"
    assert result.is_allowed is False
    assert "Trend Filter" in result.reason


def test_trend_filter_allows_signal_below_threshold():
    """The same uptrend scenario must NOT be blocked when the threshold is set above
    the actual observed slope — the filter only vetoes when it is exceeded."""
    params = IndicatorParams(
        tma_length=10, atr_period=5, atr_mult=2.0,
        tma_trend_filter_enabled=True, tma_slope_lookback=5, tma_slope_threshold=0.05,
    )
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(_uptrend_base_short_candles(), state_mgr, params)
    assert result.signal_type == "BASE_SHORT"
    assert result.is_allowed is True


def test_trend_filter_independent_of_engine_mode():
    """The filter must apply regardless of engine_mode/is_adaptive — it is a
    strategy-level filter, not part of the Adaptive Hybrid Engine's fast-path."""
    params = IndicatorParams(
        tma_length=10, atr_period=5, atr_mult=2.0, engine_mode="CLASSIC",
        tma_trend_filter_enabled=True, tma_slope_lookback=5, tma_slope_threshold=0.002,
    )
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    engine = MMS4SignalEngine()

    result = engine.evaluate_signals(_uptrend_base_short_candles(), state_mgr, params)
    assert result.is_allowed is False
