"""PLAN-008 Phase 2: Candle-Close Gate regression tests.

Confirmed root cause (see .agents/plans/PLAN-008-strategy-fix-trend-filter.md §1.1):
execute_symbol_cycle() re-evaluates and re-dispatches BASE_SHORT/BASE_LONG/HIGH_RISK_ADD
signals every 15s cycle, with the only duplicate-order defense being the Single Position
Guard — which depends on a fresh, non-stale fetch_positions() response. When OKX's
position feed lags behind a just-filled order (the same class of transient-empty-response
issue that caused the earlier addon-ledger bug), the guard is blind and the daemon
re-executes the same signal repeatedly (observed: 10-15x EXECUTED_BASE_SHORT on 24.08).

The Candle-Close Gate closes this gap independently of exchange-state reliability: once
a signal type has been dispatched for a given closed candle, it will not be dispatched
again until a genuinely new candle closes — regardless of what fetch_positions() reports.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mms4tk.analytics import Candle, SignalResult
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager


def mk(seq, base_ts=1_700_000_000_000):
    return [
        Candle(timestamp=base_ts + i * 900_000, open=o, high=h, low=l, close=c, volume=1.0)
        for i, (o, h, l, c) in enumerate(seq)
    ]


def flat_candles(n=60, price=100.0, spread=0.4, base_ts=1_700_000_000_000):
    return mk([(price, price + spread, price - spread, price)] * n, base_ts=base_ts)


class StubEngine:
    """Signal engine that returns whatever the test asks for, or a per-call list."""

    def __init__(self, result=None):
        self.next = result

    def evaluate_signals(self, candles, state_manager, params=None, symbol=None, base_sl_pct=None):
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
    return LoggerDB(db_path=str(tmp_path / "candle_gate.db"))


def test_candle_gate_prevents_duplicate_execution_same_candle(db):
    """The 24.08 scenario: fetch_positions() stays empty (stale/lagging) across two 15s
    cycles on the same closed candle. Without the gate this would execute_order() twice;
    with it, the second cycle must be skipped and the order count must stay at 1."""
    sig = SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0)
    ex = sizing_executor()
    d, _ = make_daemon(db, ex, sig)

    res1 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res1["outcome"] == "EXECUTED_BASE_LONG"
    assert ex.execute_order.await_count == 1

    # Same candle set (fetch_candles unchanged) — simulates OKX still not reflecting the
    # fill 15s later. Old behavior: guard sees no position, executes again. New behavior:
    # candle gate blocks it regardless of what fetch_positions() reports.
    res2 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res2["outcome"] == "CANDLE_GATE_SKIP_BASE_LONG"
    assert ex.execute_order.await_count == 1  # no duplicate order


def test_candle_gate_resets_on_new_candle(db):
    """A genuinely new closed candle must clear the gate for that symbol."""
    sig = SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0)
    ex = sizing_executor()
    d, _ = make_daemon(db, ex, sig)

    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert ex.execute_order.await_count == 1

    # Advance to a new candle set (new closed-candle timestamp).
    ex.fetch_candles = AsyncMock(return_value=flat_candles(60, base_ts=1_700_100_000_000))
    res3 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res3["outcome"] == "EXECUTED_BASE_LONG"
    assert ex.execute_order.await_count == 2


def test_candle_gate_does_not_block_reconciliation(db):
    """Safety invariant (PLAN-008 §4.1): the gate only suppresses repeat *entry dispatch*.
    Position reconciliation must keep running on a cycle where the gate skips a new entry
    (cooldown-engaging SL reconciliation is covered separately in test_sl_cooldown.py —
    this test uses a benign, non-stop-loss close so the two mechanisms stay isolated)."""
    ex = sizing_executor()

    # Cycle 0: snapshot a long position via a NONE-signal cycle (no entry dispatch).
    none_sig = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0)
    d, sm = make_daemon(db, ex, none_sig)
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE

    # Cycle 1: switch to a BASE_LONG signal, matching position still exists on OKX ->
    # Single Position Guard blocks it (SKIPPED, not gated), but this still marks the
    # candle as "evaluated" for BASE_LONG (gate marks it regardless of which internal
    # guard produced the SKIP).
    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0)
    res1 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res1["outcome"] == "SKIPPED_BASE_LONG"

    # Cycle 2: SAME closed candle. The position vanishes near break-even (< 2% move) ->
    # a benign reconciliation event (is_stop_loss=False, no cooldown engaged). BASE_LONG
    # is gated (already evaluated this candle in cycle 1) but reconciliation must still
    # run and be reported.
    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=100.05, stop_loss=98.0, leverage=3.0)
    ex.fetch_positions = AsyncMock(return_value=[])
    res2 = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res2["outcome"] == "CANDLE_GATE_SKIP_BASE_LONG"
    assert res2["reconciliation"], "reconciliation must still run on a gate-skip cycle"
    assert res2["reconciliation"][0]["is_stop_loss"] is False
