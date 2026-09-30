"""
Tests for issue #80 step 1: log the signal candle's volume and its ratio to the
preceding 20-candle average volume, for BASE_LONG/BASE_SHORT signals -- whether
executed, blocked by another filter (is_allowed=False), or blocked by the HTF
directional_bias gate (which collapses to signal_type="NONE" before daemon.py
sees it, same ambiguity #79 unwound for position_open).

Pure instrumentation: `details` gains a ` vol=<abs> vol_ratio=<ratio>` suffix.
No trading decision, `outcome`, or existing text in `details` is changed. Volume
ratio formula matches `scripts/volume_bucket_analysis.py`'s backtest measurement
exactly: candles[-2].volume / mean(candles[-22:-2].volume) -- the signal candle
over the mean of the 20 CLOSED candles immediately before it.
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

from mms4tk.analytics import Candle, SignalResult
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4StateManager


def mk_candle_seq(n=40, price=100.0, spread=0.4, volume=1.0):
    return [
        Candle(
            timestamp=1_700_000_000_000 + i * 900_000,
            open=price, high=price + spread, low=price - spread, close=price, volume=volume,
        )
        for i in range(n)
    ]


class StubEngine:
    def __init__(self, result):
        self.result = result

    def evaluate_signals(self, candles, state_manager, params=None, symbol=None, base_sl_pct=None):
        return self.result


def _last_cycle_details(db: LoggerDB, symbol: str) -> str:
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT details FROM cycle_logs WHERE symbol = ? ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return row["details"] if row else ""


def _run_cycle(tmp_path, signal, open_positions, n_candles=40):
    db = LoggerDB(db_path=os.path.join(tmp_path, "vol_ratio_log.db"))
    sm = MMS4StateManager(trading_symbol="BTC/USDC")

    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=mk_candle_seq(n=n_candles))
    ex.fetch_positions = AsyncMock(return_value=open_positions)

    d = MMS4Daemon(
        executor=ex, state_manager=sm, signal_engine=StubEngine(signal),
        logger_db=db, symbol="BTC/USDC",
    )
    asyncio.run(d.execute_symbol_cycle("BTC/USDC"))
    return db


def test_blocked_base_signal_gets_vol_suffix(tmp_path):
    """A BASE_LONG vetoed by another analytics.py filter (is_allowed=False,
    e.g. TMA slope / high-vol / compression) still carries a real signal_type,
    so it must be instrumented."""
    blocked = SignalResult(
        signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0,
        is_allowed=False, reason="BASE_LONG blocked by TMA Slope Trend Filter",
    )
    db = _run_cycle(tmp_path, blocked, [])

    details = _last_cycle_details(db, "BTC/USDC")
    assert "vol=1.0000" in details
    assert "vol_ratio=1.0000" in details


def test_directional_bias_block_gets_vol_suffix(tmp_path):
    """filter_bias collapses the signal to signal_type='NONE' before daemon.py
    sees it -- the same ambiguity #79 instrumented for position_open. The
    volume ratio must still be logged for this path since it's the largest
    "blocked by another filter" group in production (per #79's 32,627-cycle
    measurement)."""
    blocked = SignalResult(
        signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0,
        is_allowed=False, reason="Signal blocked by directional_bias=SHORT_ONLY",
    )
    db = _run_cycle(tmp_path, blocked, [])

    details = _last_cycle_details(db, "BTC/USDC")
    assert "directional_bias=SHORT_ONLY" in details
    assert "vol=1.0000" in details
    assert "vol_ratio=1.0000" in details


def test_executed_base_signal_gets_vol_suffix(tmp_path):
    """A real, allowed BASE_LONG (whatever daemon.py ends up doing with it --
    execute, skip on sizing, etc.) must carry the suffix too, since the whole
    point is comparing executed vs. blocked outcomes by volume bucket."""
    real_signal = SignalResult(
        signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0,
        is_allowed=True, reason="Base LONG signal: candle hit lower band and closed up",
    )
    db = _run_cycle(tmp_path, real_signal, [])

    details = _last_cycle_details(db, "BTC/USDC")
    assert "vol=1.0000" in details
    assert "vol_ratio=1.0000" in details


def test_plain_no_signal_none_is_not_annotated(tmp_path):
    """A plain "no signal condition met" NONE must not gain the suffix -- this
    is scoped strictly to BASE_LONG/BASE_SHORT signals and the directional_bias
    gate, matching the placement analytics.py would use for a real filter
    (right after filter_bias, only for BASE_* signal types)."""
    plain_none = SignalResult(
        signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0,
        is_allowed=True, reason="No signal condition met",
    )
    db = _run_cycle(tmp_path, plain_none, [])

    details = _last_cycle_details(db, "BTC/USDC")
    assert details == "No signal condition met"
    assert "vol_ratio" not in details


def test_too_few_candles_is_not_annotated(tmp_path):
    """Fewer than 22 candles means there isn't a full 20-candle lookback before
    the signal candle -- must not fabricate a ratio from a short window."""
    real_signal = SignalResult(
        signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0,
        is_allowed=True, reason="Base LONG signal",
    )
    db = _run_cycle(tmp_path, real_signal, [], n_candles=10)

    details = _last_cycle_details(db, "BTC/USDC")
    assert "vol_ratio" not in details
