"""
Tests for issue #76: log whether a position was open when the HTF
directional-bias gate (analytics.py's filter_bias) blocked a signal.

filter_bias collapses a directionally-vetoed signal to signal_type="NONE"
before daemon.py ever sees which entry/exit intent it had, so cycle_logs
couldn't previously tell a blocked fresh entry apart from a blocked
opposite-band take-profit signal on an already-open position (the exact
ambiguity issue #70 left unresolved). This is pure instrumentation — the
signal is still suppressed exactly as before; only `details` gains a
`(position_open=true/false)` suffix.
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

from mms4tk.analytics import Candle, SignalResult
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4StateManager


def mk_candle_seq(n=40, price=100.0, spread=0.4):
    return [
        Candle(
            timestamp=1_700_000_000_000 + i * 900_000,
            open=price, high=price + spread, low=price - spread, close=price, volume=1.0,
        )
        for i in range(n)
    ]


def position_dict(symbol, side, contracts=4.0, entry=100.0):
    return {
        "symbol": symbol,
        "side": side,
        "contracts": contracts,
        "entryPrice": entry,
        "notional": entry * contracts,
        "info": {"posSide": side, "avgPx": str(entry)},
    }


class StubEngine:
    def __init__(self, result):
        self.result = result

    def evaluate_signals(self, candles, state_manager, params=None, symbol=None):
        return self.result


def _last_cycle_details(db: LoggerDB, symbol: str) -> str:
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT details FROM cycle_logs WHERE symbol = ? ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return row["details"] if row else ""


def _run_cycle(tmp_path, blocked_signal, open_positions):
    db = LoggerDB(db_path=os.path.join(tmp_path, "htf_gate_log.db"))
    sm = MMS4StateManager(trading_symbol="BTC/USDC")

    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=mk_candle_seq())
    ex.fetch_positions = AsyncMock(return_value=open_positions)

    d = MMS4Daemon(
        executor=ex, state_manager=sm, signal_engine=StubEngine(blocked_signal),
        logger_db=db, symbol="BTC/USDC",
    )
    asyncio.run(d.execute_symbol_cycle("BTC/USDC"))
    return db


def test_logs_position_open_true_when_htf_gate_blocks_with_open_position(tmp_path):
    """The exact ambiguity from issue #70: a directional_bias block while a
    position is already open COULD be suppressing that position's own
    opposite-band take-profit exit."""
    blocked = SignalResult(
        signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0,
        is_allowed=False, reason="Signal blocked by directional_bias=SHORT_ONLY",
    )
    db = _run_cycle(tmp_path, blocked, [position_dict("BTC/USDC", "long", entry=100.0)])

    details = _last_cycle_details(db, "BTC/USDC")
    assert "directional_bias=SHORT_ONLY" in details
    assert "(position_open=true)" in details


def test_logs_position_open_false_when_htf_gate_blocks_a_flat_book(tmp_path):
    """No open position -> this was purely a blocked fresh entry, not a
    suppressed exit."""
    blocked = SignalResult(
        signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0,
        is_allowed=False, reason="Signal blocked by directional_bias=LONG_ONLY",
    )
    db = _run_cycle(tmp_path, blocked, [])

    details = _last_cycle_details(db, "BTC/USDC")
    assert "directional_bias=LONG_ONLY" in details
    assert "(position_open=false)" in details


def test_does_not_annotate_non_directional_bias_none_signals(tmp_path):
    """A plain "no signal condition met" NONE result must not gain the
    suffix — this is scoped strictly to the HTF directional_bias gate."""
    plain_none = SignalResult(
        signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0,
        is_allowed=True, reason="No signal condition met",
    )
    db = _run_cycle(tmp_path, plain_none, [position_dict("BTC/USDC", "long", entry=100.0)])

    details = _last_cycle_details(db, "BTC/USDC")
    assert details == "No signal condition met"
    assert "position_open" not in details


def test_does_not_annotate_real_executed_signals(tmp_path):
    """An actual BASE_LONG entry (not a blocked NONE) must not be touched by
    this instrumentation — it's scoped to signal_type == 'NONE' only."""
    real_signal = SignalResult(
        signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0,
        is_allowed=True, reason="Base LONG signal",
    )
    db = _run_cycle(tmp_path, real_signal, [])

    details = _last_cycle_details(db, "BTC/USDC")
    assert "position_open" not in details
