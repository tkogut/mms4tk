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

def test_profit_giveback_protection():
    manager = MMS4InactivityManager()
    manager.set_symbol_config("BTC/USDC", {
        "giveback_min_peak_atr": 1.0,
        "stale_candles_threshold": 12,
        "grace_candles": 5,
    })
    
    manager.track_position(
        inst_id="BTC/USDC",
        entry_price=60000.0,
        entry_time=time.time(),
        side="long",
        atr=1000.0
    )
    
    # 1. Price goes to 61500 (1.5 ATR profit)
    action = manager.update_and_check("BTC/USDC", current_price=61500.0, current_atr=1000.0, new_candle_closed=True)
    assert action == StaleAction.HOLD
    
    tracker = manager.get_tracker("BTC/USDC")
    assert tracker.peak_profit_atr == 1.5
    
    # 2. Fast reversal intra-candle (no new_candle_closed). 
    # Price drops to 60700 (0.7 ATR profit). This is < 0.75 ATR (which is 1.5 * 0.5)
    action = manager.update_and_check("BTC/USDC", current_price=60700.0, current_atr=1000.0, new_candle_closed=False)
    assert action == StaleAction.SPLIT_50
    assert tracker.is_split is True


import asyncio
from unittest.mock import MagicMock, AsyncMock
from mms4tk.daemon import MMS4Daemon
from mms4tk.analytics import SignalResult, Candle

def test_daemon_passes_active_candle_close_to_inactivity_manager():
    async def _async_test():
        daemon = MMS4Daemon()
        daemon.inactivity_manager = MagicMock()
        daemon.inactivity_manager.is_tracking.return_value = True
        daemon.inactivity_manager.update_and_check.return_value = StaleAction.HOLD
        
        daemon.executor = AsyncMock()
        # Provide 2 mock candles. The active candle is candles[-1], its close is 102.
        candles = [
            Candle(timestamp=1000, open=100, high=105, low=95, close=100, volume=10),
            Candle(timestamp=2000, open=100, high=110, low=90, close=102, volume=10),
        ]
        daemon.executor.fetch_candles.return_value = candles
        daemon.executor.fetch_positions.return_value = [{"symbol": "BTC/USDC", "side": "long", "contracts": 1.0, "entryPrice": 90}]
        daemon.executor.fetch_mark_price.return_value = 100
        daemon.executor.fetch_order_book_spread.return_value = (100.0, 100.1)
        
        daemon.signal_engine = MagicMock()
        daemon.signal_engine.evaluate_signals.return_value = SignalResult(
            signal_type="NONE", price=100.0, stop_loss=90.0, leverage=1.0
        )
        
        daemon.state_manager = MagicMock()
        daemon.state_manager.get_risk_profile_snapshot.return_value = {
            "state": "BASE_MODE", "leverage": 1.0, "size_multiplier": 1.0, "notional_reduction_factor": 0.0
        }
        
        daemon.logger_db = MagicMock()
        daemon.logger_db.get_position_snapshots.return_value = []
        
        # Run a single cycle (we just want to see what update_and_check gets called with)
        await daemon.execute_symbol_cycle("BTC/USDC")
        
        # Verify update_and_check was called with current_price=102 (candles[-1].close), NOT 100 (signal.price)
        daemon.inactivity_manager.update_and_check.assert_called_once()
        kwargs = daemon.inactivity_manager.update_and_check.call_args.kwargs
        assert kwargs["current_price"] == 102.0

    asyncio.run(_async_test())


# ---------------------------------------------------------------------------
# compute_negation_level (issue #69: ratchet the exchange-side SL after a
# protective split toward this level). Public accessor for the same Rule I
# math `_check_impulse_negation` uses internally — must never drift from it.
# ---------------------------------------------------------------------------

def test_compute_negation_level_short_matches_real_production_example():
    """Real production tracker state (XAG/USDC short, 2026-09-28 incident):
    entry=64.2498, min_observed=60.83, small ATR -> negation level ~62.14."""
    manager = MMS4InactivityManager()
    manager.track_position(inst_id="XAG/USDC", entry_price=64.2498, entry_time=time.time(), side="short", atr=0.058)
    tracker = manager.get_tracker("XAG/USDC")
    tracker.min_observed_price = 60.83

    level = manager.compute_negation_level("XAG/USDC", atr=0.058)
    assert level == pytest.approx(62.136, abs=0.01)


def test_compute_negation_level_long_matches_check_impulse_negation():
    """The public accessor and the internal check must agree exactly — same
    formula, same inputs."""
    manager = MMS4InactivityManager()
    manager.track_position(inst_id="BTC/USDC", entry_price=60000.0, entry_time=time.time(), side="long", atr=500.0)
    tracker = manager.get_tracker("BTC/USDC")
    tracker.max_observed_price = 62000.0

    level = manager.compute_negation_level("BTC/USDC", atr=500.0)
    expected = manager._negation_level_for("long", 60000.0, tracker.min_observed_price, 62000.0, 500.0)
    assert level == pytest.approx(expected)


def test_compute_negation_level_returns_none_when_not_tracking():
    manager = MMS4InactivityManager()
    assert manager.compute_negation_level("BTC/USDC", atr=500.0) is None


def test_compute_negation_level_returns_none_when_extreme_zeroed_out():
    """Defensive guard: a tracker whose relevant extreme reads as 0.0 (e.g. a
    deserialized/partially-restored tracker, not one built via track_position
    — that always seeds the extreme to entry_price in __post_init__) must not
    produce a nonsense negation level."""
    manager = MMS4InactivityManager()
    manager.track_position(inst_id="ETH/USDC", entry_price=3000.0, entry_time=time.time(), side="short", atr=50.0)
    manager.get_tracker("ETH/USDC").min_observed_price = 0.0
    assert manager.compute_negation_level("ETH/USDC", atr=50.0) is None


def test_compute_negation_level_returns_none_for_nonpositive_atr():
    manager = MMS4InactivityManager()
    manager.track_position(inst_id="BTC/USDC", entry_price=60000.0, entry_time=time.time(), side="long", atr=500.0)
    manager.get_tracker("BTC/USDC").max_observed_price = 62000.0
    assert manager.compute_negation_level("BTC/USDC", atr=0.0) is None

