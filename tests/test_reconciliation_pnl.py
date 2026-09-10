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


def test_reconciliation_v2_addon_position_classified_by_base_entry_price(temp_db: LoggerDB):
    """
    P3 V2: the SL is anchored to base_entry_price (pinned at the first snapshot),
    not avgPx — regardless of whether the position later got an addon. Simulates
    the audit's own canonical shape (SHORT base@100, later averaged down to
    avgPx=99.70 by an addon) but under V2: the real trigger is base_entry_price *
    1.02 = 102.0, not something derived from the addon's own fill. A close at
    exactly that trigger must be a stop-out, AND `sl_percent` (what
    state_manager.on_stop_loss_hit's >=2% SCOUT_MODE/Hermes-H4 gate sees) must
    come out at ~2% — fixing state_manager.py:344 as a side effect, since under
    the OLD (addon_sl_pct-anchored) model this gate never fired for
    addon-carrying stop-outs. `pnl_percent` (accounting) stays avgPx-based and is
    a DIFFERENT, larger number — the two are intentionally not the same.
    """
    daemon, state_mgr = _make_daemon(temp_db)

    # First snapshot (base-only): base_entry_price self-seeds to 100.0.
    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="short", contracts=26.0, entry_price=100.0, last_price=100.0,
    )
    # A later cycle's snapshot after an addon averaged the position down to 99.70 —
    # base_entry_price must NOT move.
    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="short", contracts=37.0, entry_price=99.70, last_price=99.70,
    )
    temp_db.record_addon("BTC/USDC", "short")  # Single Addon Guard bookkeeping only

    daemon.reconcile_positions(
        target_symbol="BTC/USDC", live_positions=[], current_price=102.0,
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    assert kwargs["is_stop_loss"] is True
    assert kwargs["sl_percent"] == pytest.approx(0.02, abs=1e-6)
    # Accounting PnL is still avgPx-relative and clearly different from sl_percent.
    assert kwargs["pnl_percent"] == pytest.approx((99.70 - 102.0) / 99.70, abs=1e-6)


def test_reconciliation_v2_addon_position_far_from_trigger_is_not_stop_out(temp_db: LoggerDB):
    """The same addon-carrying position, closed near avgPx (nowhere near the
    base_entry_price-anchored trigger) — e.g. a take-profit flip whose snapshot
    wasn't cleared in time — must NOT be misclassified as a stop-out."""
    daemon, state_mgr = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="short", contracts=26.0, entry_price=100.0, last_price=100.0,
    )
    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="short", contracts=37.0, entry_price=99.70, last_price=99.70,
    )
    temp_db.record_addon("BTC/USDC", "short")

    daemon.reconcile_positions(
        target_symbol="BTC/USDC", live_positions=[], current_price=99.50,
    )

    state_mgr.record_trade.assert_called_once()
    assert state_mgr.record_trade.call_args[1]["is_stop_loss"] is False


def test_reconciliation_v2_addon_position_classified_by_base_entry_price_long(temp_db: LoggerDB):
    """LONG mirror of the canonical SHORT test above — all existing V2 addon
    tests were SHORT-only (independent review, round 3); this closes that gap."""
    daemon, state_mgr = _make_daemon(temp_db)

    # base opened at 100.0; an addon later averaged the live position UP to 100.30
    # (LONG addon fills higher, pyramiding into a favorable move) — base_entry_price
    # must stay pinned at 100.0.
    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="long", contracts=26.0, entry_price=100.0, last_price=100.0,
    )
    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="long", contracts=37.0, entry_price=100.30, last_price=100.30,
    )
    temp_db.record_addon("BTC/USDC", "long")

    daemon.reconcile_positions(
        target_symbol="BTC/USDC", live_positions=[], current_price=98.0,  # 100.0 * 0.98
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    assert kwargs["is_stop_loss"] is True
    assert kwargs["sl_percent"] == pytest.approx(0.02, abs=1e-6)
    assert kwargs["pnl_percent"] == pytest.approx((98.0 - 100.30) / 100.30, abs=1e-6)


def test_reconciliation_without_addon_uses_base_threshold(temp_db: LoggerDB):
    """Regression guard: a position with no addon at all — base_entry_price ==
    avgPx (never averaged), so this is unchanged from pre-V2 behavior. A loss
    comfortably past base_sl_pct*0.75 (1.5%) is a stop-out."""
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


def test_reconciliation_v2_base_entry_price_survives_addon_averaging(temp_db: LoggerDB):
    """Direct persistence check: base_entry_price is set on the position's FIRST
    snapshot and never changes afterwards, even as entry_price (avgPx) moves —
    this is the SSOT save_position_snapshot's docstring promises."""
    temp_db.save_position_snapshot(
        symbol="ETH/USDC", side="long", contracts=14.0, entry_price=2500.0, last_price=2500.0,
    )
    temp_db.save_position_snapshot(
        symbol="ETH/USDC", side="long", contracts=20.0, entry_price=2510.0, last_price=2510.0,
    )
    temp_db.save_position_snapshot(
        symbol="ETH/USDC", side="long", contracts=20.0, entry_price=2495.0, last_price=2495.0,
    )

    snaps = {s["side"]: s for s in temp_db.get_position_snapshots("ETH/USDC")}
    assert snaps["long"]["base_entry_price"] == pytest.approx(2500.0)
    assert snaps["long"]["entry_price"] == pytest.approx(2495.0)  # avgPx did move


def test_reconciliation_v2_migration_heals_zero_base_entry_price(temp_db: LoggerDB):
    """A row written before the base_entry_price column existed (or otherwise
    left at the schema default 0.0) must self-heal from the NEXT snapshot write,
    rather than staying pinned at a meaningless 0.0 forever."""
    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="short", contracts=10.0, entry_price=100.0, last_price=100.0,
    )
    # Simulate a pre-migration remnant directly.
    import sqlite3
    with sqlite3.connect(temp_db.db_path) as conn:
        conn.execute(
            "UPDATE position_snapshots SET base_entry_price = 0.0 WHERE symbol=? AND side=?",
            ("BTC/USDC", "short"),
        )
        conn.commit()

    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="short", contracts=10.0, entry_price=98.0, last_price=98.0,
    )
    snap = next(s for s in temp_db.get_position_snapshots("BTC/USDC") if s["side"] == "short")
    assert snap["base_entry_price"] == pytest.approx(98.0)


def test_reconciliation_v2_daemon_fallback_when_snapshot_missing_base_entry_price(temp_db: LoggerDB, monkeypatch):
    """Defense in depth: even if the snapshot dict genuinely has no usable
    base_entry_price (0.0), reconcile_positions must fall back to entry_price
    (avgPx) rather than crash or divide by zero — same as pre-V2 behavior."""
    daemon, state_mgr = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC", side="long", contracts=10.0, entry_price=100.0, last_price=100.0,
    )

    original = temp_db.get_position_snapshots

    def _zeroed_base_entry_price(symbol):
        rows = original(symbol)
        for r in rows:
            r["base_entry_price"] = 0.0
        return rows

    monkeypatch.setattr(temp_db, "get_position_snapshots", _zeroed_base_entry_price)

    daemon.reconcile_positions(
        target_symbol="BTC/USDC", live_positions=[], current_price=97.9,  # 2.1% loss
    )
    assert state_mgr.record_trade.call_args[1]["is_stop_loss"] is True


def test_update_addon_sl_trigger_price_syncs_ledger_without_touching_count(temp_db: LoggerDB):
    """
    Direct unit test of logger_db's set_addon_sl_trigger_price/get_addon_sl_trigger_price
    plus daemon._update_addon_sl_trigger_price, independent of any caller.

    P3 V2 (2026-09-10, independent review round 3): this mechanism (the addon
    composite-trigger ledger from P3 rounds 1-2) is superseded — the Self-Healing
    SL Guard no longer calls _update_addon_sl_trigger_price, and reconcile_positions
    no longer reads this value at all (both now use base_entry_price
    unconditionally, see reconcile_positions/the Self-Healing Guard). The method
    itself still works correctly (verified here) and is left in place for schema/
    backward-compat safety on rows written before V2, but nothing in the live
    code path calls it anymore.
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
