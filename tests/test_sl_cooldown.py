"""PLAN-008 Phase 2: Post-SL Cooldown regression tests.

A reconciled stop-loss (is_stop_loss=True) engages a cooldown window
(MMS4_SL_COOLDOWN_CANDLES closed candles, default 3) during which new entries for that
symbol are blocked, independent of the Candle-Close Gate. This targets the cascade
stop-out pattern noted in PLAN-008 §1.2 (Inactivity Manager asymmetry): re-entering
immediately after a stop-out, on the same adverse move, tends to just re-lose.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mms4tk.analytics import Candle, SignalResult
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager, SCOUT_LEVERAGE, SCOUT_SIZE_MULT


def mk(seq, base_ts=1_700_000_000_000):
    return [
        Candle(timestamp=base_ts + i * 900_000, open=o, high=h, low=l, close=c, volume=1.0)
        for i, (o, h, l, c) in enumerate(seq)
    ]


def flat_candles(n=60, price=100.0, spread=0.4, base_ts=1_700_000_000_000):
    return mk([(price, price + spread, price - spread, price)] * n, base_ts=base_ts)


class StubEngine:
    def __init__(self, result=None):
        self.next = result

    def evaluate_signals(self, candles, state_manager, params=None, symbol=None):
        return self.next


def position(symbol, side, contracts=4.0, entry=100.0, notional=21.0):
    return {
        "symbol": symbol,
        "side": side,
        "contracts": contracts,
        "entryPrice": entry,
        "notional": notional,
        "info": {"posSide": side, "avgPx": str(entry)},
    }


def sizing_executor(base_ts=1_700_000_000_000):
    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=flat_candles(60, base_ts=base_ts))
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.execute_order = AsyncMock(return_value={"id": "ord_1", "status": "executed"})
    ex.reduce_position = AsyncMock(return_value={"id": "red_1", "status": "closed"})
    ex.calculate_order_amount = MagicMock(return_value=3.0)
    return ex


def make_daemon(db, executor, signal, symbol="ETH/USDC", state=MMS4State.BASE_MODE):
    sm = MMS4StateManager(initial_state=state, trading_symbol=symbol)
    d = MMS4Daemon(
        executor=executor,
        state_manager=sm,
        signal_engine=StubEngine(signal),
        logger_db=db,
        symbol=symbol,
        trade_amount_usd=10.0,
    )
    d.inactivity_manager.set_symbol_config(
        symbol, {"impulse_negation_enabled": False, "stale_candles_threshold": 10**6}
    )
    return d, sm


@pytest.fixture
def db(tmp_path):
    return LoggerDB(db_path=str(tmp_path / "sl_cooldown.db"))


def test_cooldown_engages_and_blocks_same_cycle(db):
    """A reconciled stop-loss engages the cooldown, and — since the daemon computes
    cooldown state after reconciliation runs in the same cycle — a new-entry signal
    evaluated on that very cycle is already blocked (not just from the next cycle on)."""
    ex = sizing_executor()

    # Cycle 0: snapshot a long position.
    none_sig = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0)
    d, sm = make_daemon(db, ex, none_sig)
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    # Cycle 1: position vanishes 2% adverse -> genuine stop-loss. Signal this cycle is
    # already a fresh BASE_LONG re-entry attempt.
    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=98.0, stop_loss=96.0, leverage=3.0)
    ex.fetch_positions = AsyncMock(return_value=[])
    res1 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res1["reconciliation"] and res1["reconciliation"][0]["is_stop_loss"] is True
    assert sm.get_symbol_state("ETH/USDC") == MMS4State.SCOUT_MODE
    assert "ETH/USDC" in d._sl_cooldown_until
    assert res1["outcome"] == "COOLDOWN_ACTIVE"
    assert ex.execute_order.await_count == 0


def test_cooldown_blocks_entries_on_subsequent_cycles_same_candle(db):
    """Once engaged, the cooldown keeps blocking entries on later cycles too, as long as
    the candle timestamp hasn't advanced past the cooldown horizon."""
    ex = sizing_executor()
    none_sig = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0)
    d, sm = make_daemon(db, ex, none_sig)
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=98.0, stop_loss=96.0, leverage=3.0)
    ex.fetch_positions = AsyncMock(return_value=[])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert "ETH/USDC" in d._sl_cooldown_until

    # Cycle 2: same candle set (fetch_candles unchanged), no new reconciliation event
    # (position already gone last cycle), cooldown must still block.
    res2 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res2["outcome"] == "COOLDOWN_ACTIVE"
    assert ex.execute_order.await_count == 0


def test_cooldown_expires_and_allows_entry(db):
    """After MMS4_SL_COOLDOWN_CANDLES closed candles have passed, the cooldown clears
    and a fresh signal is allowed through (subject to the usual guards)."""
    ex = sizing_executor()
    none_sig = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0)
    d, sm = make_daemon(db, ex, none_sig)
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=98.0, stop_loss=96.0, leverage=3.0)
    ex.fetch_positions = AsyncMock(return_value=[])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    cooldown_until = d._sl_cooldown_until["ETH/USDC"]

    # Advance candles well past the cooldown horizon (default 3 candles * 900_000ms).
    ex.fetch_candles = AsyncMock(return_value=flat_candles(60, base_ts=cooldown_until + 1_000_000))
    ex.fetch_positions = AsyncMock(return_value=[])
    res3 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res3["outcome"] == "EXECUTED_BASE_LONG"
    assert "ETH/USDC" not in d._sl_cooldown_until
    assert ex.execute_order.await_count == 1


def test_non_stop_loss_reconciliation_does_not_engage_cooldown(db):
    """A benign, near-breakeven close (is_stop_loss=False) must not trigger a cooldown —
    only genuine stop-losses should."""
    ex = sizing_executor()
    none_sig = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0)
    d, sm = make_daemon(db, ex, none_sig)
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=100.05, stop_loss=98.0, leverage=3.0)
    ex.fetch_positions = AsyncMock(return_value=[])
    res1 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res1["reconciliation"] and res1["reconciliation"][0]["is_stop_loss"] is False
    assert "ETH/USDC" not in d._sl_cooldown_until
    assert res1["outcome"] == "EXECUTED_BASE_LONG"
