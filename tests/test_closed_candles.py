import pytest
from mms4tk.analytics import Candle, IndicatorParams, MMS4SignalEngine
from mms4tk.state_manager import MMS4StateManager, MMS4State


def test_insufficient_candles():
    """Verify evaluation with fewer than 2 candles returns NONE signal with specific reason."""
    engine = MMS4SignalEngine()
    sm = MMS4StateManager()
    
    # 0 candles
    res0 = engine.evaluate_signals([], sm)
    assert res0.signal_type == "NONE"
    assert res0.price == 0.0
    assert res0.reason == "Insufficient candles for closed candle analysis"

    # 1 candle — price must fall back to the last known close, never 0.0.
    # A zero here propagates into the inactivity tracker as a real market price and
    # permanently corrupts its impulse-negation maths (audit finding P1-3).
    c1 = Candle(timestamp=100, open=100.0, high=101.0, low=99.0, close=100.0)
    res1 = engine.evaluate_signals([c1], sm)
    assert res1.signal_type == "NONE"
    assert res1.price == 100.0
    assert res1.reason == "Insufficient candles for closed candle analysis"


def test_closed_candles_ignores_unclosed_live_spike():
    """Verify live unclosed candle price spikes are ignored because evaluation uses candles[:-1]."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    # 15 flat candles
    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    # Candle 15 (closed): normal candle with close=100.0
    candles.append(Candle(timestamp=15 * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0))

    # Candle 16 (unclosed live candle): massive price spike to 150.0 (way above upper band ~108)
    candles.append(Candle(timestamp=16 * 60, open=100.0, high=150.0, low=100.0, close=145.0, volume=5000.0))

    # Evaluate signals: closed_candles is candles[:-1] (ends at Candle 15)
    result = engine.evaluate_signals(candles, sm, params)

    # Signal MUST be NONE because live spike on Candle 16 is excluded from closed_candles
    assert result.signal_type == "NONE"
    assert result.price == 100.0  # close of Candle 15 (the latest closed candle)


def test_closed_candle_signal_evaluated_when_next_live_candle_present():
    """Verify signal on Candle 15 is detected when unclosed Candle 16 is present."""
    params = IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0)
    engine = MMS4SignalEngine()
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    candles = [
        Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
        for i in range(15)
    ]
    # Candle 15 (closed): breaks upper band (~108) and closes down
    candles.append(Candle(timestamp=15 * 60, open=112.0, high=115.0, low=107.0, close=109.0, volume=500.0))

    # Candle 16 (unclosed live candle): normal candle
    candles.append(Candle(timestamp=16 * 60, open=109.0, high=110.0, low=108.0, close=109.2, volume=50.0))

    result = engine.evaluate_signals(candles, sm, params)

    # BASE_SHORT triggered on Candle 15
    assert result.signal_type == "BASE_SHORT"
    assert result.price == 109.0
