"""Faza 3.2 production wiring: `htf_directional_gate_enabled` (per-symbol,
default OFF), `MMS4StateManager.get_effective_directional_bias`/
`set_symbol_htf_bias`, and `MMS4Daemon._refresh_htf_directional_gate`.

The backtest measurement (task.md, "Faza 3.2") lives in
`tests/test_htf_directional_gate.py` and `scripts/htf_directional_gate_analysis.py`.
This file covers only the PRODUCTION mirror: config persistence
(`_get_indicator_params`/`AddSymbolRequest`/`_trend_filter_fields`) and the
live H4 fetch -> per-symbol bias -> effective bias resolution chain, prepared
per task.md's verdict but NOT deployed.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mms4tk.analytics import Candle, IndicatorParams, MMS4SignalEngine
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager, StrategyProfile


def _make_daemon(tmp_path: Path) -> MMS4Daemon:
    db = LoggerDB(db_path=str(tmp_path / "htf_gate_prod.db"))
    executor = OKXExecutor()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.fetch_positions = AsyncMock(return_value=[])
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    return MMS4Daemon(executor=executor, state_manager=state_mgr, logger_db=db, interval_seconds=0.1)


def _h4_candles_above() -> list:
    """A short, strongly rising synthetic H4 series whose last close sits above
    the TMA upper band -> h4_tma_position == "ABOVE"."""
    candles = []
    price = 100.0
    for i in range(40):
        price *= 1.03
        candles.append(Candle(timestamp=i * 14_400_000, open=price * 0.99, high=price * 1.01,
                               low=price * 0.98, close=price, volume=1.0))
    return candles


def _h4_candles_below() -> list:
    candles = []
    price = 300.0
    for i in range(40):
        price *= 0.97
        candles.append(Candle(timestamp=i * 14_400_000, open=price * 1.01, high=price * 1.02,
                               low=price * 0.99, close=price, volume=1.0))
    return candles


def _h4_candles_flat() -> list:
    candles = []
    for i in range(40):
        c = 100.0 + (0.3 if i % 2 == 0 else -0.3)
        candles.append(Candle(timestamp=i * 14_400_000, open=100.0, high=100.5, low=99.5, close=c, volume=1.0))
    return candles


# ── IndicatorParams default ──

def test_htf_directional_gate_enabled_defaults_false():
    assert IndicatorParams().htf_directional_gate_enabled is False


# ── MMS4StateManager.get_effective_directional_bias / set_symbol_htf_bias ──

def test_effective_bias_with_no_symbol_matches_manual_field_exactly():
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    assert sm.get_effective_directional_bias(None) == "BOTH"
    sm.update_strategy_profile(StrategyProfile(directional_bias="LONG_ONLY"))
    assert sm.get_effective_directional_bias(None) == "LONG_ONLY"


def test_effective_bias_defaults_both_when_htf_gate_never_set():
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    assert sm.get_symbol_htf_bias("BTC/USDC") == "BOTH"
    assert sm.get_effective_directional_bias("BTC/USDC") == "BOTH"


def test_effective_bias_applies_per_symbol_htf_gate_when_manual_is_both():
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    sm.set_symbol_htf_bias("BTC/USDC", "LONG_ONLY")
    assert sm.get_effective_directional_bias("BTC/USDC") == "LONG_ONLY"
    # A different symbol is unaffected -- no cross-symbol leakage.
    assert sm.get_effective_directional_bias("ETH/USDC") == "BOTH"


def test_manual_override_always_wins_over_htf_gate():
    """A Hermes-set FLAT/LONG_ONLY/SHORT_ONLY must never be silently overridden by
    the automatic per-symbol HTF reading."""
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    sm.set_symbol_htf_bias("BTC/USDC", "SHORT_ONLY")
    sm.update_strategy_profile(StrategyProfile(directional_bias="FLAT"))
    assert sm.get_effective_directional_bias("BTC/USDC") == "FLAT"

    sm.update_strategy_profile(StrategyProfile(directional_bias="LONG_ONLY"))
    assert sm.get_effective_directional_bias("BTC/USDC") == "LONG_ONLY"


def test_set_symbol_htf_bias_does_not_touch_global_strategy_profile():
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    sm.set_symbol_htf_bias("BTC/USDC", "LONG_ONLY")
    assert sm.strategy_profile.directional_bias == "BOTH"


def test_evaluate_signals_uses_effective_bias_when_symbol_passed():
    """End-to-end through the real signal engine: a per-symbol HTF override alone
    (manual field left at BOTH) must block a counter-trend signal exactly like a
    manual override does, with the same reason string. Candle setup mirrors
    test_adaptive_tactics.py's test_directional_bias_signal_filtering (known to
    reliably trigger BASE_SHORT)."""
    candles_short = [
        Candle(timestamp=i * 60, open=100.0, high=100.5, low=99.5, close=100.0)
        for i in range(25)
    ]
    candles_short[-2] = Candle(timestamp=23 * 60, open=100.0, high=108.0, low=99.0, close=99.2)
    candles_short[-1] = Candle(timestamp=24 * 60, open=99.2, high=99.5, low=99.0, close=99.3)

    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC")
    sm.set_symbol_htf_bias("BTC/USDC", "LONG_ONLY")
    engine = MMS4SignalEngine()

    # Without a symbol, the per-symbol HTF gate must NOT apply (existing behavior
    # for any pre-Faza-3.2 caller) -- signal passes through ungated.
    result_no_symbol = engine.evaluate_signals(candles_short, sm)
    assert result_no_symbol.signal_type == "BASE_SHORT"
    assert result_no_symbol.is_allowed is True

    # With the symbol passed, the per-symbol HTF override blocks it exactly like a
    # manual directional_bias=LONG_ONLY would.
    result = engine.evaluate_signals(candles_short, sm, symbol="BTC/USDC")
    assert result.signal_type == "NONE"
    assert result.is_allowed is False
    assert "directional_bias=LONG_ONLY" in result.reason


# ── MMS4Daemon._refresh_htf_directional_gate ──
# No pytest-asyncio in this repo (grepped: none of the existing async daemon tests
# use it) -- the established pattern is a plain `asyncio.run()` wrapper, matching
# test_daemon.py/test_trend_filter_per_symbol_params.py.

def test_refresh_htf_gate_is_noop_when_disabled(tmp_path: Path):
    async def _run():
        daemon = _make_daemon(tmp_path)
        params = IndicatorParams(htf_directional_gate_enabled=False)
        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        daemon.executor.fetch_candles.assert_not_called()
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "BOTH"
    asyncio.run(_run())


def test_refresh_htf_gate_sets_long_only_when_h4_above_band(tmp_path: Path):
    async def _run():
        daemon = _make_daemon(tmp_path)
        daemon.executor.fetch_candles = AsyncMock(return_value=_h4_candles_above())
        params = IndicatorParams(htf_directional_gate_enabled=True)
        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        daemon.executor.fetch_candles.assert_awaited_once_with(
            symbol="BTC/USDC", timeframe="4h", limit=50, allow_mock_fallback=False
        )
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "LONG_ONLY"
    asyncio.run(_run())


def test_refresh_htf_gate_sets_short_only_when_h4_below_band(tmp_path: Path):
    async def _run():
        daemon = _make_daemon(tmp_path)
        daemon.executor.fetch_candles = AsyncMock(return_value=_h4_candles_below())
        params = IndicatorParams(htf_directional_gate_enabled=True)
        await daemon._refresh_htf_directional_gate("ETH/USDC", params)
        assert daemon.state_manager.get_symbol_htf_bias("ETH/USDC") == "SHORT_ONLY"
    asyncio.run(_run())


def test_refresh_htf_gate_sets_both_when_h4_inside_band(tmp_path: Path):
    async def _run():
        daemon = _make_daemon(tmp_path)
        daemon.executor.fetch_candles = AsyncMock(return_value=_h4_candles_flat())
        params = IndicatorParams(htf_directional_gate_enabled=True)
        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "BOTH"
    asyncio.run(_run())


def test_refresh_htf_gate_leaves_previous_reading_on_fetch_failure(tmp_path: Path):
    async def _run():
        daemon = _make_daemon(tmp_path)
        daemon.executor.fetch_candles = AsyncMock(return_value=_h4_candles_above())
        params = IndicatorParams(htf_directional_gate_enabled=True)
        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "LONG_ONLY"

        daemon.executor.fetch_candles = AsyncMock(side_effect=RuntimeError("network down"))
        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        # A transient fetch failure must not reset an active gate back to "BOTH".
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "LONG_ONLY"
    asyncio.run(_run())


@pytest.mark.allow_real_okx_network
def test_refresh_htf_gate_freezes_on_real_fetch_failure_not_mock_data(tmp_path: Path):
    """Audit Phase 3.2-followup (2026-09-11, real bug found and fixed): the test
    above (`test_refresh_htf_gate_leaves_previous_reading_on_fetch_failure`)
    replaces `daemon.executor.fetch_candles` with an `AsyncMock(side_effect=...)`
    directly -- that bypasses the REAL `OKXExecutor.fetch_candles`, which never
    used to raise at all (it swallowed every failure and returned deterministic,
    mildly upward-drifting mock candles instead). That made the daemon's
    `except Exception` fail-safe dead code for a genuine OKX outage: instead of
    freezing the last known-good bias, it silently recomputed one from fabricated
    trending-up data.

    This test uses a REAL `OKXExecutor` (opted out of tests/conftest.py's global
    fake via the marker above) with only `.exchange` swapped for a deterministic
    stand-in -- exercising the exact integration path the bug lived in. It first
    establishes SHORT_ONLY from real (fake-exchange) DECLINING H4 data, then makes
    the exchange fail. Pre-fix, this would have flipped to LONG_ONLY (the mock
    generator's upward drift crosses the H4 upper band) instead of freezing --
    this assertion would have failed before the fix and passes after it.
    """
    class _DecliningExchange:
        async def fetch_ohlcv(self, *args, **kwargs):
            raw = []
            price = 300.0
            ts = 0
            for _ in range(40):
                price *= 0.97
                raw.append([ts, price * 1.01, price * 1.02, price * 0.99, price, 1.0])
                ts += 14_400_000
            return raw

    class _AlwaysFailsExchange:
        async def fetch_ohlcv(self, *args, **kwargs):
            raise ConnectionError("simulated OKX outage")

    async def _run():
        db = LoggerDB(db_path=str(tmp_path / "htf_gate_real_fetch.db"))
        executor = OKXExecutor()
        executor.exchange = _DecliningExchange()
        executor.fetch_positions = AsyncMock(return_value=[])
        state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
        daemon = MMS4Daemon(executor=executor, state_manager=state_mgr, logger_db=db, interval_seconds=0.1)
        params = IndicatorParams(htf_directional_gate_enabled=True)

        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "SHORT_ONLY"

        executor.exchange = _AlwaysFailsExchange()
        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        # Frozen at the last known-good reading -- NOT recomputed from mock data.
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "SHORT_ONLY"
    asyncio.run(_run())


def test_refresh_htf_gate_does_not_touch_other_symbols(tmp_path: Path):
    async def _run():
        daemon = _make_daemon(tmp_path)
        daemon.executor.fetch_candles = AsyncMock(return_value=_h4_candles_above())
        params = IndicatorParams(htf_directional_gate_enabled=True)
        await daemon._refresh_htf_directional_gate("BTC/USDC", params)
        assert daemon.state_manager.get_symbol_htf_bias("BTC/USDC") == "LONG_ONLY"
        assert daemon.state_manager.get_symbol_htf_bias("ETH/USDC") == "BOTH"
    asyncio.run(_run())


# ── _get_indicator_params per-symbol persistence (mirrors tma_trend_filter_enabled) ──

def test_indicator_params_defaults_disabled_when_no_override(tmp_path: Path):
    daemon = _make_daemon(tmp_path)
    params = daemon._get_indicator_params("BTC/USDC")
    assert params.htf_directional_gate_enabled is False


def test_indicator_params_per_symbol_override_survives_reload(tmp_path: Path):
    daemon = _make_daemon(tmp_path)
    daemon.logger_db.set_settings_bulk(
        {"htf_directional_gate_enabled:BTC/USDC": True}, updated_by="test",
    )

    btc_params = daemon._get_indicator_params("BTC/USDC")
    assert btc_params.htf_directional_gate_enabled is True

    eth_params = daemon._get_indicator_params("ETH/USDC")
    assert eth_params.htf_directional_gate_enabled is False

    reloaded_db = LoggerDB(db_path=daemon.logger_db.db_path)
    daemon.logger_db = reloaded_db
    btc_params_after_reload = daemon._get_indicator_params("BTC/USDC")
    assert btc_params_after_reload.htf_directional_gate_enabled is True
