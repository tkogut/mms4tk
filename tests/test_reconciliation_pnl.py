import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.okx_executor import OKXExecutor
from mms4tk.analytics import SignalResult, Candle
from mms4tk.logger_db import LoggerDB


@pytest.fixture
def temp_db(tmp_path: Path) -> LoggerDB:
    db_file = tmp_path / "test_recon_pnl.db"
    return LoggerDB(db_path=str(db_file))


def test_reconciliation_pnl_btc(temp_db: LoggerDB):
    """
    Test reconciliation PnL calculation for BTC (contractSize=0.0001).
    37 contracts of BTC at entry $79542 and exit $79359 should yield PnL USD ~ -0.677 USD (NOT -6771 USD).
    """
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {"BTC/USDC": {"contractSize": 0.0001}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.0001})

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.record_trade = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol="BTC/USDC",
    )

    # Save a position snapshot representing 37 contracts of BTC long
    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=37.0,
        entry_price=79542.0,
        last_price=79542.0,
    )

    # Position is now absent from live positions, current price is $79359
    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=79359.0,
    )

    # Verify record_trade was called with contractSize-scaled PnL
    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    pnl = kwargs["pnl"]
    
    # Expected pnl: ((79359 - 79542) / 79542) * 79542 * 37 * 0.0001 = -183 * 37 * 0.0001 = -0.6771 USD
    assert pnl == pytest.approx(-0.6771, rel=1e-3)
    assert abs(pnl) < 1.0  # Must NOT be ~ -6763 or -6771 USD


def test_reconciliation_pnl_eth(temp_db: LoggerDB):
    """
    Test reconciliation PnL calculation for ETH (contractSize=0.001).
    84 contracts of ETH SHORT at entry $2491.23 and exit $2540.78 should yield PnL USD ~ -4.162 USD (NOT -4162 USD).
    """
    executor = OKXExecutor()
    # Test fallback contract size without exchange.markets populated
    executor.exchange = MagicMock()
    executor.exchange.markets = None

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.record_trade = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol="ETH/USDC",
    )

    # Save a position snapshot representing 84 contracts of ETH short
    temp_db.save_position_snapshot(
        symbol="ETH/USDC",
        side="short",
        contracts=84.0,
        entry_price=2491.23,
        last_price=2491.23,
    )

    # Position is now absent from live positions, current price is $2540.78
    daemon.reconcile_positions(
        target_symbol="ETH/USDC",
        live_positions=[],
        current_price=2540.78,
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    pnl = kwargs["pnl"]

    # Expected pnl: ((2491.23 - 2540.78) / 2491.23) * 2491.23 * 84 * 0.001 = -49.55 * 84 * 0.001 = -4.1622 USD
    assert pnl == pytest.approx(-4.1622, rel=1e-3)
    assert abs(pnl) < 10.0  # Must NOT be ~ -4162 USD


def _make_daemon(temp_db: LoggerDB, symbol: str = "BTC/USDC") -> "tuple[MMS4Daemon, MagicMock]":
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {symbol: {"contractSize": 0.0001}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.0001})

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.record_trade = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol=symbol,
    )
    return daemon, state_mgr


def test_reconciliation_classifies_addon_stop_out_at_real_trigger_price(temp_db: LoggerDB):
    """
    P3 (corrected per independent review): the composite SL for an addon-carrying
    position is anchored to the ADDON's own fill price, not to avgPx — a %
    threshold measured from avgPx cannot detect it reliably. This is the audit's
    own canonical example (§1.1): SHORT base@100, addon@99, avgPx=99.70, composite
    SL = 99 * 1.0125 = 100.2375. The exchange's own SL then fires at ~100.24 — a
    loss of only ~0.54% from avgPx, which the OLD base_sl_pct*0.75 (1.5%) threshold
    (and even the superseded addon_sl_pct*0.75 draft threshold, 0.9375%) would both
    have missed. Only comparing against the real, persisted trigger price catches it.
    """
    daemon, state_mgr = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="short",
        contracts=37.0,
        entry_price=99.70,
        last_price=99.70,
    )
    temp_db.record_addon("BTC/USDC", "short", sl_trigger_price=100.2375)

    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=100.24,
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    assert kwargs["pnl_percent"] == pytest.approx(-0.00542, abs=1e-4)
    assert kwargs["is_stop_loss"] is True


def test_reconciliation_addon_position_far_from_trigger_is_not_stop_out(temp_db: LoggerDB):
    """The same addon-carrying position, closed near avgPx (nowhere near the
    persisted composite SL trigger price) — e.g. a take-profit flip whose
    snapshot wasn't cleared in time — must NOT be misclassified as a stop-out."""
    daemon, state_mgr = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="short",
        contracts=37.0,
        entry_price=99.70,
        last_price=99.70,
    )
    temp_db.record_addon("BTC/USDC", "short", sl_trigger_price=100.2375)

    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=99.50,  # closed favorably, far short of the 100.2375 trigger
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    assert kwargs["is_stop_loss"] is False


def test_reconciliation_addon_without_captured_trigger_price_falls_back_to_base_threshold(temp_db: LoggerDB):
    """If the addon was recorded but its trigger price is unknown (0.0 — e.g. the
    SL attach's reference price couldn't be resolved), reconciliation must fall
    back to the base-only % heuristic rather than trust a fabricated price level."""
    daemon, state_mgr = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=37.0,
        entry_price=100.0,
        last_price=100.0,
    )
    temp_db.record_addon("BTC/USDC", "long")  # sl_trigger_price defaults to 0.0

    # 1.2% loss: below base_sl_pct*0.75 (1.5%) — the fallback heuristic says "no".
    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=98.8,
    )
    state_mgr.record_trade.assert_called_once()
    assert state_mgr.record_trade.call_args[1]["is_stop_loss"] is False


def test_reconciliation_without_addon_uses_base_threshold(temp_db: LoggerDB):
    """Regression guard: a position with no addon at all uses the base_sl_pct
    threshold. A loss comfortably past it (2.1%) is a stop-out; the same loss
    used in the addon tests above (1.2%) is not (see test above)."""
    daemon, state_mgr = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=37.0,
        entry_price=100.0,
        last_price=100.0,
    )
    # No addon recorded for this position.

    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=97.9,  # 2.1% loss, past base_sl_pct*0.75 (1.5%)
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    assert kwargs["is_stop_loss"] is True


def test_reconciliation_addon_trigger_lookup_failure_falls_back_gracefully(temp_db: LoggerDB, monkeypatch):
    """If reading the persisted trigger price blows up (DB error), reconciliation
    must not crash — it logs and falls back to the base % heuristic (fail-open,
    same as the pre-P3 behavior), not raise out of the whole reconciliation cycle."""
    daemon, state_mgr = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=37.0,
        entry_price=100.0,
        last_price=100.0,
    )
    temp_db.record_addon("BTC/USDC", "long", sl_trigger_price=95.0)

    def _boom(*args, **kwargs):
        raise RuntimeError("db is on fire")
    monkeypatch.setattr(temp_db, "get_addon_sl_trigger_price", _boom)

    # Must not raise.
    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=97.9,  # 2.1% loss — past the base fallback threshold
    )

    state_mgr.record_trade.assert_called_once()
    assert state_mgr.record_trade.call_args[1]["is_stop_loss"] is True


def test_update_addon_sl_trigger_price_syncs_ledger_without_touching_count(temp_db: LoggerDB):
    """
    P3: the Self-Healing SL Guard re-attaches an addon-carrying position's SL
    anchored to avgPx, not the original addon fill price — daemon.py syncs the
    ledger's trigger price to match via _update_addon_sl_trigger_price so a later
    reconciliation classifies against what OKX actually now holds. This must only
    update the price, never the addon count (that would silently permit another
    add past the Single Addon Guard).
    """
    daemon, _ = _make_daemon(temp_db)

    temp_db.record_addon("BTC/USDC", "long", sl_trigger_price=95.0)
    assert temp_db.get_addon_count("BTC/USDC", "long") == 1

    daemon._update_addon_sl_trigger_price("BTC/USDC", "long", 96.5)

    assert temp_db.get_addon_sl_trigger_price("BTC/USDC", "long") == pytest.approx(96.5)
    assert temp_db.get_addon_count("BTC/USDC", "long") == 1  # unchanged


def test_update_addon_sl_trigger_price_is_noop_without_an_addon(temp_db: LoggerDB):
    """No addon row exists yet — syncing a trigger price must not fabricate one
    (that would make a position look like it has an addon when it doesn't)."""
    daemon, _ = _make_daemon(temp_db)

    daemon._update_addon_sl_trigger_price("BTC/USDC", "long", 96.5)

    assert temp_db.get_addon_count("BTC/USDC", "long") == 0
    assert temp_db.get_addon_sl_trigger_price("BTC/USDC", "long") == 0.0


def test_take_profit_pnl_with_contract_size(temp_db: LoggerDB):
    """
    Test Take Profit PnL calculation scaled by contract size.
    """
    async def _async_test():
        executor = OKXExecutor()
        executor.exchange = MagicMock()
        executor.exchange.markets = {"ETH/USDC": {"contractSize": 0.001}}
        executor.exchange.market = MagicMock(return_value={"contractSize": 0.001})

        # Mock active SHORT position of 50 contracts on ETH/USDC
        mock_pos = {
            "symbol": "ETH/USDC",
            "instId": "ETH-USDC-SWAP",
            "posSide": "short",
            "side": "short",
            "contracts": 50.0,
            "notionalUsd": 125.0,
            "entryPrice": 2500.0,
            "markPrice": 2450.0,
            "unrealizedPnl": 2.5,
        }
        executor.fetch_positions = AsyncMock(return_value=[mock_pos])
        executor.reduce_position = AsyncMock(return_value={"id": "reduce_123", "status": "closed"})
        executor.execute_order = AsyncMock(return_value={"id": "buy_456", "status": "executed"})
        executor.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=1000, open=2500, high=2510, low=2490, close=2500, volume=10),
            Candle(timestamp=2000, open=2500, high=2505, low=2445, close=2450, volume=15),
            Candle(timestamp=3000, open=2450, high=2460, low=2440, close=2450, volume=5),
        ])

        from mms4tk.api import configured_symbols_store
        configured_symbols_store["ETH/USDC"] = {"allocation_usd": 200.0, "is_active": True, "base_ratio": 70, "add_ratio": 30}

        state_mgr = MMS4StateManager(initial_state=MMS4State.SCOUT_MODE)
        state_mgr.record_trade = MagicMock()

        daemon = MMS4Daemon(
            executor=executor,
            state_manager=state_mgr,
            logger_db=temp_db,
            symbol="ETH/USDC",
            trade_amount_usd=200.0,
        )

        long_signal = SignalResult(
            signal_type="BASE_LONG",
            price=2450.0,
            stop_loss=2400.0,
            leverage=1.0,
            is_allowed=True,
            reason="Opposite TMA Band reached"
        )
        daemon.signal_engine.evaluate_signals = MagicMock(return_value=long_signal)

        await daemon.execute_symbol_cycle("ETH/USDC")

        # Verify record_trade was called for take profit with contractSize scaling
        state_mgr.record_trade.assert_called_once()
        kwargs = state_mgr.record_trade.call_args[1]
        pnl = kwargs["pnl"]
        
        # Expected: ((2500 - 2450) / 2500) * 2500 * 50 * 0.001 = 50 * 50 * 0.001 = +2.50 USD
        assert pnl == pytest.approx(2.50, rel=1e-3)
        assert kwargs["is_take_profit"] is True
        assert abs(pnl) < 10.0  # Must NOT be 2500 USD

    asyncio.run(_async_test())


def test_get_contract_size_helper():
    """Test _get_contract_size fallback and market lookup rules."""
    executor = OKXExecutor()
    state_mgr = MMS4StateManager()
    daemon = MMS4Daemon(executor=executor, state_manager=state_mgr)

    # 1. Fallbacks when exchange markets not loaded
    executor.exchange = None
    assert daemon._get_contract_size("BTC/USDC") == 0.0001
    assert daemon._get_contract_size("BTC/USDT") == 0.0001
    assert daemon._get_contract_size("BTC-USDC-SWAP") == 0.0001
    assert daemon._get_contract_size("ETH/USDC") == 0.001
    assert daemon._get_contract_size("ETH-USDT-SWAP") == 0.001
    assert daemon._get_contract_size("SOL/USDC") == 1.0

    # 2. Lookup via executor.exchange
    executor.exchange = MagicMock()
    executor.exchange.markets = {"SOL/USDC": {"contractSize": 0.1}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.1})
    assert daemon._get_contract_size("SOL/USDC") == 0.1
