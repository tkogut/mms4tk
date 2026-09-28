"""
Unit tests for PLAN-009 (v2.0) — Midline Partial Take-Profit and Inactivity Manager Optimizations.
"""
import os
import time
import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mms4tk.analytics import Candle, IndicatorParams, SignalResult, TMAIndicator
from mms4tk.daemon import MMS4Daemon
from mms4tk.inactivity_manager import MMS4InactivityManager, StaleAction, PositionTracker
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager


def mk_candle_seq(seq):
    return [
        Candle(timestamp=1_700_000_000_000 + i * 900_000, open=o, high=h, low=l, close=c, volume=1.0)
        for i, (o, h, l, c) in enumerate(seq)
    ]


def flat_candles(n=40, price=100.0, spread=0.4):
    return mk_candle_seq([(price, price + spread, price - spread, price)] * n)


class StubEngine:
    def __init__(self, result=None):
        self.next = result

    def evaluate_signals(self, candles, state_manager, params=None, symbol=None):
        return self.next or SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0)


def position_dict(symbol, side, contracts=4.0, entry=100.0, notional=20.0):
    return {
        "symbol": symbol,
        "side": side,
        "contracts": contracts,
        "entryPrice": entry,
        "notional": notional,
        "info": {"posSide": side, "avgPx": str(entry)},
    }


# ── 1. Profit Stall Gate (4 candles stalled at uPnL >= 0.6 ATR) ─────────

def test_profit_stall_gate_4_candles():
    """Position with uPnL >= 0.6 ATR stalls for 4 candles without new ATH -> emits SPLIT_50."""
    manager = MMS4InactivityManager()
    manager.set_symbol_config("BTC/USDC", {
        "impulse_negation_enabled": False,
        "stale_candles_threshold": 20,
    })
    
    manager.track_position(
        inst_id="BTC/USDC",
        entry_price=100.0,
        entry_time=time.time(),
        side="long",
        atr=10.0,
    )
    
    # 1st candle: profit = (107 - 100) / 10 = 0.7 ATR (>= 0.6) -> new peak at 107.0
    a1 = manager.update_and_check("BTC/USDC", current_price=107.0, current_atr=10.0, new_candle_closed=True)
    assert a1 == StaleAction.HOLD
    t = manager.get_tracker("BTC/USDC")
    assert t.last_peak_price == 107.0
    assert t.profit_stall_candles == 0

    # 2nd candle: 106.8 (no new peak) -> stall count = 1
    a2 = manager.update_and_check("BTC/USDC", current_price=106.8, current_atr=10.0, new_candle_closed=True)
    assert a2 == StaleAction.HOLD
    assert t.profit_stall_candles == 1

    # 3rd candle: 106.9 (no new peak) -> stall count = 2
    a3 = manager.update_and_check("BTC/USDC", current_price=106.9, current_atr=10.0, new_candle_closed=True)
    assert a3 == StaleAction.HOLD
    assert t.profit_stall_candles == 2

    # 4th candle: 106.7 (no new peak) -> stall count = 3
    a4 = manager.update_and_check("BTC/USDC", current_price=106.7, current_atr=10.0, new_candle_closed=True)
    assert a4 == StaleAction.HOLD
    assert t.profit_stall_candles == 3

    # 5th candle: 106.6 (no new peak) -> stall count = 4 -> triggers SPLIT_50
    a5 = manager.update_and_check("BTC/USDC", current_price=106.6, current_atr=10.0, new_candle_closed=True)
    assert a5 == StaleAction.SPLIT_50
    assert t.is_split is True


# ── 2. Impulse Negation with 38.2% Fibonacci threshold (impulse >= 0.8 ATR)

def test_impulse_negation_fibonacci_382():
    """Impulse >= 0.8 ATR retraces 38.2% -> emits SPLIT_50."""
    manager = MMS4InactivityManager()
    manager.track_position(
        inst_id="ETH/USDC",
        entry_price=100.0,
        entry_time=time.time(),
        side="long",
        atr=10.0,
    )

    # Impulse of 9.0 (>= 0.8 * 10.0 = 8.0 ATR)
    manager.update_and_check("ETH/USDC", current_price=109.0, current_atr=10.0, new_candle_closed=True)
    t = manager.get_tracker("ETH/USDC")
    assert t.max_observed_price == 109.0

    # 38.2% Fibonacci retracement level: 109.0 - (9.0 * 0.382) = 105.562
    # Price at 106.0 sits above 105.562 -> HOLD
    a1 = manager.update_and_check("ETH/USDC", current_price=106.0, current_atr=10.0, new_candle_closed=True)
    assert a1 == StaleAction.HOLD

    # Price drops to 105.0 (< 105.562) -> triggers SPLIT_50
    a2 = manager.update_and_check("ETH/USDC", current_price=105.0, current_atr=10.0, new_candle_closed=True)
    assert a2 == StaleAction.SPLIT_50


# ── 3. Break-Even Escape with Fee Buffer ─────────────────────────────────

def test_be_escape_fee_buffer():
    """BE_ESCAPE requires >= 0.12% profit allowance to cover round-trip fees."""
    manager = MMS4InactivityManager()
    manager.set_symbol_config("SOL/USDC", {
        "impulse_negation_enabled": False,
        "round_trip_cost_pct": 0.0012,  # 0.12%
        "be_escape_threshold": 0.0020,  # 0.20% band
    })
    manager.track_position(
        inst_id="SOL/USDC",
        entry_price=1000.0,
        entry_time=time.time(),
        side="long",
        atr=100.0,
    )
    t = manager.get_tracker("SOL/USDC")
    t.stale_detected = True  # Stagnation previously detected

    # Price at 1000.5 -> +0.05% profit < 0.12% cost allowance -> deferred (HOLD)
    a1 = manager.update_and_check("SOL/USDC", current_price=1000.5, current_atr=100.0, new_candle_closed=True)
    assert a1 == StaleAction.HOLD

    # Price at 1001.5 -> +0.15% profit >= 0.12% cost allowance and <= 0.20% band -> BE_ESCAPE
    a2 = manager.update_and_check("SOL/USDC", current_price=1001.5, current_atr=100.0, new_candle_closed=True)
    assert a2 == StaleAction.BE_ESCAPE


# ── 4. Fail-Closed Pattern in tp_midline_ledger ─────────────────────────

def test_tp_midline_ledger_fail_closed(tmp_path):
    """Force DB failure / OperationalError in is_midline_tp_active() -> returns True (fail-closed)."""
    db_path = os.path.join(tmp_path, "test_ledger.db")
    db = LoggerDB(db_path=db_path)

    # Clean state -> returns False
    assert db.is_midline_tp_active("BTC/USDC", "long") is False

    # Record midline TP -> returns True
    db.record_midline_tp(
        symbol="BTC/USDC",
        side="long",
        entry_price=60000.0,
        midline_price=61000.0,
        reduced_contracts=2.0,
        remaining_contracts=2.0,
        pnl_usd=200.0,
    )
    assert db.is_midline_tp_active("BTC/USDC", "long") is True
    assert db.is_midline_tp_active("BTC/USDC", "short") is False

    # Clear midline TP -> returns False
    db.clear_midline_tp("BTC/USDC", "long")
    assert db.is_midline_tp_active("BTC/USDC", "long") is False

    # Fail-Closed Test: simulate DB error
    with patch.object(db, "_get_connection", side_effect=sqlite3.OperationalError("database is locked")):
        assert db.is_midline_tp_active("BTC/USDC", "long") is True
        assert db.is_midline_tp_active("BTC/USDC", "short") is True


# ── 5. Midline TP Hard SL Preservation ──────────────────────────────────

def test_midline_tp_hard_sl_preservation(tmp_path):
    """Midline TP scales SL order size while preserving exact 2.0% stop loss distance."""
    db = LoggerDB(db_path=os.path.join(tmp_path, "daemon_tp.db"))
    # Audit P9.1 (2026-09-08): midline_partial_tp_enabled now defaults to False
    # (rejected 6/6 in backtest) — this test specifically exercises the feature, so
    # it must opt in explicitly rather than rely on the old fail-open default.
    db.set_setting("midline_partial_tp_enabled", "true")
    sm = MMS4StateManager(trading_symbol="ETH/USDC")
    
    ex = MagicMock(spec=OKXExecutor)
    # Generate 50 candles with TMA around 105.0, price at 106.0
    candles = mk_candle_seq([(105.0, 106.0, 104.0, 105.5)] * 50)
    ex.fetch_candles = AsyncMock(return_value=candles)
    ex.fetch_positions = AsyncMock(return_value=[
        position_dict("ETH/USDC", "long", contracts=4.0, entry=100.0, notional=20.0)
    ])
    ex.reduce_position = AsyncMock(return_value={"id": "red_1", "status": "closed"})
    ex.attach_stop_loss = AsyncMock(return_value={"status": "attached"})
    ex.reduction_amount = MagicMock(return_value=2.0)
    ex.calculate_order_amount = MagicMock(return_value=2.0)

    sig = SignalResult(signal_type="NONE", price=106.0, stop_loss=0.0, leverage=3.0)
    d = MMS4Daemon(
        executor=ex,
        state_manager=sm,
        signal_engine=StubEngine(sig),
        logger_db=db,
        symbol="ETH/USDC",
    )

    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res["outcome"] == "MIDLINE_PARTIAL_TP"
    assert ex.reduce_position.await_count == 1
    # Verify attach_stop_loss called with entry_price=100.0, stop_loss_pct=0.02 (2.0% SL, not BE)
    assert ex.attach_stop_loss.await_count == 1
    call_kwargs = ex.attach_stop_loss.call_args.kwargs
    assert call_kwargs["entry_price"] == 100.0
    assert call_kwargs["stop_loss_pct"] == 0.02
    assert db.is_midline_tp_active("ETH/USDC", "long") is True


def test_midline_tp_reattaches_sl_at_base_entry_price_not_avgpx(tmp_path):
    """
    P3 V2 (independent review, round 3 — blocker fix): before this fix, the
    Midline TP re-attach anchored the hard SL to the LIVE avgPx
    (active_entry_price), not base_entry_price — silently reproducing the exact
    determinism bug V2 fixes on any addon-carrying position. Once the live avgPx
    diverges from base_entry_price (e.g. an addon averaged it from 100.0 down to
    95.0), the re-attach must still use 100.0 — the same anchor
    reconcile_positions and the Self-Healing Guard use — not 95.0.
    """
    db = LoggerDB(db_path=os.path.join(tmp_path, "daemon_tp_addon.db"))
    db.set_setting("midline_partial_tp_enabled", "true")
    # base opened at 100.0; a later addon fill averaged the live position down to
    # 95.0 — base_entry_price must stay pinned at 100.0.
    db.save_position_snapshot(symbol="ETH/USDC", side="long", contracts=4.0, entry_price=100.0, last_price=100.0)
    db.save_position_snapshot(symbol="ETH/USDC", side="long", contracts=7.0, entry_price=95.0, last_price=95.0)
    db.record_addon("ETH/USDC", "long")

    sm = MMS4StateManager(trading_symbol="ETH/USDC")

    ex = MagicMock(spec=OKXExecutor)
    candles = mk_candle_seq([(105.0, 106.0, 104.0, 105.5)] * 50)
    ex.fetch_candles = AsyncMock(return_value=candles)
    ex.fetch_positions = AsyncMock(return_value=[
        position_dict("ETH/USDC", "long", contracts=7.0, entry=95.0, notional=20.0)
    ])
    ex.reduce_position = AsyncMock(return_value={"id": "red_1", "status": "closed"})
    ex.attach_stop_loss = AsyncMock(return_value={"status": "attached"})
    ex.reduction_amount = MagicMock(return_value=3.5)
    ex.calculate_order_amount = MagicMock(return_value=3.5)

    sig = SignalResult(signal_type="NONE", price=106.0, stop_loss=0.0, leverage=3.0)
    d = MMS4Daemon(
        executor=ex,
        state_manager=sm,
        signal_engine=StubEngine(sig),
        logger_db=db,
        symbol="ETH/USDC",
    )

    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res["outcome"] == "MIDLINE_PARTIAL_TP"
    assert ex.attach_stop_loss.await_count == 1
    call_kwargs = ex.attach_stop_loss.call_args.kwargs
    assert call_kwargs["entry_price"] == 100.0  # base_entry_price, NOT avgPx (95.0)
    assert call_kwargs["stop_loss_pct"] == 0.02


def test_midline_tp_uses_closed_candle_not_forming_candle_for_trigger(tmp_path):
    """Audit Phase 3.1 (2026-09-11): daemon.py computed the Midline TP threshold
    (tma_mid) and the touch check (candle_high/candle_low) off the RAW fetched
    candle list, whose last element is the still-forming candle — every
    OKX/ccxt `fetch_ohlcv` call returns it that way, the exact reason
    `closed_candles = candles[:-1]` exists for the ATR calc two lines above in
    the same function, and the same discipline `MMS4SignalEngine.evaluate_signals`
    / `HigherTimeframeAnalytics.calculate_higher_tf` enforce elsewhere. Build a
    candle history whose CLOSED data does not touch the midline, then append one
    forming candle with an extreme high that would have (pre-fix) crossed it and
    fired a partial close before the bar that produced it had even closed."""
    # Declining-then-flat history so the (lagging) TMA sits above the recent
    # closes/highs — computed for real via TMAIndicator, not hand-derived.
    closes = [140.0 - i for i in range(30)] + [110.0] * 12
    hist_candles = mk_candle_seq([(c, c + 0.3, c - 0.3, c) for c in closes])
    params = IndicatorParams()
    df = TMAIndicator.calculate(hist_candles, params)
    tma_mid_closed = float(df["tma"].iloc[-1])
    assert hist_candles[-1].high < tma_mid_closed, (
        "fixture must not touch the midline on closed data alone"
    )

    # Forming (unclosed) candle appended last, with an extreme high that WOULD
    # cross tma_mid if it (wrongly) fed the TMA/touch-check computation.
    spike = Candle(
        timestamp=hist_candles[-1].timestamp + 900_000,
        open=110.0, high=tma_mid_closed + 50.0, low=109.0, close=110.0, volume=1.0,
    )
    candles = hist_candles + [spike]

    db = LoggerDB(db_path=os.path.join(tmp_path, "midline_closed_candle.db"))
    db.set_setting("midline_partial_tp_enabled", "true")
    sm = MMS4StateManager(trading_symbol="ETH/USDC")

    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=candles)
    entry_price = tma_mid_closed - 5.0  # comfortably below tma_mid: "entry<=tma_mid" always true
    ex.fetch_positions = AsyncMock(return_value=[
        position_dict("ETH/USDC", "long", contracts=4.0, entry=entry_price, notional=20.0)
    ])
    ex.reduce_position = AsyncMock(return_value={"id": "red_1", "status": "closed"})
    ex.attach_stop_loss = AsyncMock(return_value={"status": "attached"})
    ex.reduction_amount = MagicMock(return_value=2.0)
    ex.calculate_order_amount = MagicMock(return_value=2.0)

    sig = SignalResult(signal_type="NONE", price=entry_price, stop_loss=0.0, leverage=3.0)
    d = MMS4Daemon(
        executor=ex,
        state_manager=sm,
        signal_engine=StubEngine(sig),
        logger_db=db,
        symbol="ETH/USDC",
    )

    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res["outcome"] != "MIDLINE_PARTIAL_TP"
    assert ex.reduce_position.await_count == 0


# ── 6. Micro-Position Indivisibility ─────────────────────────────────────

def test_micro_position_indivisibility(tmp_path):
    """1-contract micro-position skips T1 partial reduction without error."""
    db = LoggerDB(db_path=os.path.join(tmp_path, "micro_pos.db"))
    sm = MMS4StateManager(trading_symbol="ETH/USDC")

    ex = MagicMock(spec=OKXExecutor)
    candles = mk_candle_seq([(105.0, 106.0, 104.0, 105.5)] * 50)
    ex.fetch_candles = AsyncMock(return_value=candles)
    # 1 single contract position
    ex.fetch_positions = AsyncMock(return_value=[
        position_dict("ETH/USDC", "long", contracts=1.0, entry=100.0, notional=5.0)
    ])
    ex.reduce_position = AsyncMock()
    ex.reduction_amount = MagicMock(return_value=None)  # None for 1 contract halving

    sig = SignalResult(signal_type="NONE", price=106.0, stop_loss=0.0, leverage=3.0)
    d = MMS4Daemon(
        executor=ex,
        state_manager=sm,
        signal_engine=StubEngine(sig),
        logger_db=db,
        symbol="ETH/USDC",
    )

    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    # Must NOT reduce position
    assert ex.reduce_position.await_count == 0
    assert db.is_midline_tp_active("ETH/USDC", "long") is False


# ── 7. State Manager SCOUT_MODE Lock on Partial TP ───────────────────────

def test_state_manager_scout_mode_lock():
    """Partial Take-Profit (is_partial=True) in SCOUT_MODE does NOT reset to BASE_MODE."""
    sm = MMS4StateManager(trading_symbol="BTC/USDC")
    sm.set_symbol_state("BTC/USDC", MMS4State.SCOUT_MODE)
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE

    # Record partial TP win
    sm.record_trade(
        trade_id="partial_1",
        symbol="BTC/USDC",
        side="long",
        entry_price=60000.0,
        exit_price=61000.0,
        amount=2.0,
        pnl=200.0,
        pnl_percent=0.0167,
        is_take_profit=True,
        is_partial=True,
    )
    # Must remain in SCOUT_MODE
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE

    # Record full TP closure
    sm.record_trade(
        trade_id="full_1",
        symbol="BTC/USDC",
        side="long",
        entry_price=60000.0,
        exit_price=62000.0,
        amount=2.0,
        pnl=400.0,
        pnl_percent=0.0333,
        is_take_profit=True,
        is_partial=False,
    )
    # Transitions to BASE_MODE
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE


# ── 7. SL Ratchet After Protective Split (issue #69) ─────────────────────

def _daemon_with_mocked_inactivity_manager(tmp_path, side="short", entry=64.2498, contracts=305.0):
    """A daemon whose InactivityManager is a MagicMock returning a real
    StaleAction.SPLIT_50 (so daemon.py's `stale_action == StaleAction.SPLIT_50`
    branch runs for real) with a controllable `compute_negation_level`."""
    db = LoggerDB(db_path=os.path.join(tmp_path, "ratchet.db"))
    db.save_position_snapshot(symbol="XAG/USDC", side=side, contracts=contracts, entry_price=entry, last_price=entry)
    sm = MMS4StateManager(trading_symbol="XAG/USDC")

    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=flat_candles(price=61.0))
    ex.fetch_positions = AsyncMock(return_value=[
        position_dict("XAG/USDC", side, contracts=contracts, entry=entry, notional=20.0)
    ])
    ex.reduce_position = AsyncMock(return_value={"id": "red_1", "status": "executed"})
    ex.attach_stop_loss = AsyncMock(return_value={"status": "attached"})

    sig = SignalResult(signal_type="NONE", price=61.0, stop_loss=0.0, leverage=3.0)
    d = MMS4Daemon(executor=ex, state_manager=sm, signal_engine=StubEngine(sig), logger_db=db, symbol="XAG/USDC")

    d.inactivity_manager = MagicMock()
    d.inactivity_manager.is_tracking = MagicMock(return_value=True)
    d.inactivity_manager.update_and_check = MagicMock(return_value=StaleAction.SPLIT_50)
    tracker = MagicMock()
    tracker.side = side
    tracker.entry_price = entry
    tracker.candle_count = 18
    tracker.is_split = True
    d.inactivity_manager.get_tracker = MagicMock(return_value=tracker)

    return d, ex


def test_split_50_ratchets_sl_to_negation_level_when_more_protective(tmp_path):
    """The real bug this closes: SPLIT_50 previously left the exchange SL at
    the original -2% anchor no matter how much profit the split was reacting
    to. A SHORT split via Profit Giveback Protection (real 2026-09-28 incident
    shape) must ratchet the SL toward the computed Impulse Negation level."""
    d, ex = _daemon_with_mocked_inactivity_manager(tmp_path, side="short", entry=64.2498)
    d.inactivity_manager.compute_negation_level = MagicMock(return_value=62.14)  # more protective than 64.2498*1.02=65.53

    res = asyncio.run(d.execute_symbol_cycle("XAG/USDC"))

    assert res["outcome"] == "STALE_SPLIT_50"
    ex.attach_stop_loss.assert_awaited_once()
    call_kwargs = ex.attach_stop_loss.call_args.kwargs
    assert call_kwargs["trigger_price"] == 62.14
    assert call_kwargs["pos_side"] == "short"
    assert call_kwargs["side"] == "buy"  # closing a short = buy


def test_split_50_does_not_ratchet_when_negation_level_less_protective(tmp_path):
    """Defensive guard: a computed level worse than the existing -2% anchor
    (e.g. a near-entry stagnation split with a noisy ATR) must never be
    applied — the split itself still executes."""
    d, ex = _daemon_with_mocked_inactivity_manager(tmp_path, side="short", entry=64.2498)
    # 66.0 is ABOVE the existing 65.5348 anchor for a short -> LESS protective, must be rejected.
    d.inactivity_manager.compute_negation_level = MagicMock(return_value=66.0)

    res = asyncio.run(d.execute_symbol_cycle("XAG/USDC"))

    assert res["outcome"] == "STALE_SPLIT_50"
    ex.attach_stop_loss.assert_not_awaited()


def test_split_50_does_not_ratchet_when_no_negation_level_available(tmp_path):
    """compute_negation_level returning None (e.g. no extreme observed yet)
    must not raise and must not attempt to attach a nonsense SL."""
    d, ex = _daemon_with_mocked_inactivity_manager(tmp_path, side="short", entry=64.2498)
    d.inactivity_manager.compute_negation_level = MagicMock(return_value=None)

    res = asyncio.run(d.execute_symbol_cycle("XAG/USDC"))

    assert res["outcome"] == "STALE_SPLIT_50"
    ex.attach_stop_loss.assert_not_awaited()


def test_split_50_ratchets_long_side_correctly():
    """LONG side: the negation level must be ABOVE the existing -2% anchor to
    count as more protective (loss side is below entry for a long)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_path:
        d, ex = _daemon_with_mocked_inactivity_manager(tmp_path, side="long", entry=100.0)
        # existing anchor = 100 * 0.98 = 98.0; 99.5 is more protective (closer to entry/profit).
        d.inactivity_manager.compute_negation_level = MagicMock(return_value=99.5)

        res = asyncio.run(d.execute_symbol_cycle("XAG/USDC"))

        assert res["outcome"] == "STALE_SPLIT_50"
        ex.attach_stop_loss.assert_awaited_once()
        call_kwargs = ex.attach_stop_loss.call_args.kwargs
        assert call_kwargs["trigger_price"] == 99.5
        assert call_kwargs["pos_side"] == "long"
        assert call_kwargs["side"] == "sell"  # closing a long = sell
