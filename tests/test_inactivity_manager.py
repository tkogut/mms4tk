"""
Unit tests for MMS4InactivityManager and Stale Position Management.
"""
import time
import pytest
from mms4tk.inactivity_manager import MMS4InactivityManager, StaleAction, PositionTracker


def test_inactivity_manager_lifecycle():
    manager = MMS4InactivityManager()
    
    # 1. Track position
    manager.track_position(
        inst_id="BTC/USDC",
        entry_price=60000.0,
        entry_time=time.time(),
        side="long",
        atr=1000.0
    )
    
    assert manager.is_tracking("BTC/USDC")
    tracker = manager.get_tracker("BTC/USDC")
    assert tracker is not None
    assert tracker.entry_price == 60000.0
    assert tracker.side == "long"
    assert tracker.candle_count == 0
    assert not tracker.is_split
    
    # 2. Stop tracking
    manager.stop_tracking("BTC/USDC")
    assert not manager.is_tracking("BTC/USDC")


def test_time_decompression_two_stage_ladder():
    manager = MMS4InactivityManager()
    manager.set_symbol_config("ETH/USDC", {
        "stale_candles_threshold": 12,
        "stale_atr_threshold": 0.5,
        "grace_candles": 5,
        "impulse_negation_enabled": False
    })
    
    manager.track_position(
        inst_id="ETH/USDC",
        entry_price=3000.0,
        entry_time=time.time(),
        side="long",
        atr=100.0  # ATR=100 => threshold = 50.0
    )
    
    # Simulate 11 candles inside stagnation zone (price = 3010, diff = 10 < 50)
    for _ in range(11):
        action = manager.update_and_check("ETH/USDC", current_price=3010.0, current_atr=100.0, new_candle_closed=True)
        assert action == StaleAction.HOLD
        
    # 12th candle -> SPLIT_50
    action = manager.update_and_check("ETH/USDC", current_price=3010.0, current_atr=100.0, new_candle_closed=True)
    assert action == StaleAction.SPLIT_50
    tracker = manager.get_tracker("ETH/USDC")
    assert tracker.is_split is True
    assert tracker.split_at_candle == 12
    
    # Next 4 candles -> grace period holds
    for _ in range(4):
        action = manager.update_and_check("ETH/USDC", current_price=3010.0, current_atr=100.0, new_candle_closed=True)
        assert action == StaleAction.HOLD
        
    # 5th candle post-split (candle_count = 17) -> FORCE_CLOSE_BE
    action = manager.update_and_check("ETH/USDC", current_price=3010.0, current_atr=100.0, new_candle_closed=True)
    assert action == StaleAction.FORCE_CLOSE_BE


def test_impulse_negation_long():
    manager = MMS4InactivityManager()
    manager.track_position(
        inst_id="SOL/USDC",
        entry_price=150.0,
        entry_time=time.time(),
        side="long",
        atr=10.0  # min impulse = 3.0
    )
    
    # Price rises to 160 (+10 impulse)
    manager.update_and_check("SOL/USDC", current_price=160.0, current_atr=10.0, new_candle_closed=True)
    tracker = manager.get_tracker("SOL/USDC")
    assert tracker.max_observed_price == 160.0
    
    # 50% retracement level: 160 - (10 * 0.5) = 155.0.
    # Price drops to 154 (< 155) -> SPLIT_50
    action = manager.update_and_check("SOL/USDC", current_price=154.0, current_atr=10.0, new_candle_closed=True)
    assert action == StaleAction.SPLIT_50


def test_be_escape_rule():
    manager = MMS4InactivityManager()
    manager.track_position(
        inst_id="ICP/USDC",
        entry_price=10.0,
        entry_time=time.time(),
        side="long",
        atr=1.0
    )
    tracker = manager.get_tracker("ICP/USDC")
    tracker.stale_detected = True  # Simulated prior stagnation flag
    
    # Price touches entry within 0.2% and >= 0.12% cost allowance (10.015 vs 10.0) -> BE_ESCAPE
    action = manager.update_and_check("ICP/USDC", current_price=10.015, current_atr=1.0, new_candle_closed=True)
    assert action == StaleAction.BE_ESCAPE
