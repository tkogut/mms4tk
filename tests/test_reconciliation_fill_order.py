import pytest
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.okx_executor import OKXExecutor


@pytest.fixture
def temp_db(tmp_path: Path) -> LoggerDB:
    db_file = tmp_path / "test_recon_fill.db"
    return LoggerDB(db_path=str(db_file))


def _make_daemon(temp_db: LoggerDB):
    executor = MagicMock()
    executor.has_credentials = True
    state_mgr = MagicMock(spec=MMS4StateManager)
    state_mgr.get_symbol_state.return_value = MagicMock(value="BASE_MODE")
    state_mgr.finalize_close = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol="BTC/USDC",
    )
    return daemon, state_mgr, executor


def test_reconcile_positions_with_real_okx_fill(temp_db: LoggerDB):
    """
    When OKXExecutor.fetch_last_closing_order returns an authoritative fill,
    reconcile_positions must use the exact exit price, exact PnL and fee from OKX,
    correctly classifying STOP-LOSS without ticker drift.
    """
    daemon, state_mgr, executor = _make_daemon(temp_db)

    # Saved snapshot: entry $77,630.00, 1 contract
    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=1.0,
        entry_price=77630.0,
        last_price=77630.0,
    )

    # Mock real fill order returned from OKX history
    executor.fetch_last_closing_order = AsyncMock(return_value={
        "order_id": "987654321",
        "exit_price": 76047.04,
        "pnl_usd": -4.27,
        "fee_usd": 0.21,
        "fill_time": 1789482919349,
        "is_real_fill": True,
    })

    # Call reconciliation with ticker price that has already bounced up ($76,582.90)
    events = daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=76582.90,
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["exit_price"] == 76047.04
    assert ev["is_real_fill"] is True
    assert ev["order_id"] == "987654321"
    # -2.03% move from entry 77630 is beyond -1.5% SL threshold -> MUST be STOP-LOSS
    assert ev["is_stop_loss"] is True

    # Check the close was booked via finalize_close as a STOP_LOSS with the exact pnl
    state_mgr.finalize_close.assert_called_once()
    kwargs = state_mgr.finalize_close.call_args[1]
    assert kwargs["reason"] == "STOP_LOSS"
    assert kwargs["pnl"] == pytest.approx(-4.27, abs=0.01)


def test_reconcile_positions_passes_snapshot_updated_at_as_min_fill_time(temp_db: LoggerDB):
    """
    daemon.reconcile_positions must pass the snapshot's last-confirmed-alive timestamp
    (`updated_at`) to fetch_last_closing_order as `min_fill_time_ms`, so a stale,
    unrelated earlier close of the same symbol/side can never be mistaken for the fill
    that just happened.
    """
    daemon, state_mgr, executor = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=1.0,
        entry_price=77630.0,
        last_price=77630.0,
    )
    snap = temp_db.get_position_snapshots("BTC/USDC")[0]
    expected_min_fill_time_ms = int(
        datetime.fromisoformat(str(snap["updated_at"])).timestamp() * 1000
    )

    executor.fetch_last_closing_order = AsyncMock(return_value={
        "order_id": "987654321",
        "exit_price": 76047.04,
        "pnl_usd": -4.27,
        "fee_usd": 0.21,
        "fill_time": expected_min_fill_time_ms + 1000,
        "is_real_fill": True,
    })

    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=76582.90,
    )

    executor.fetch_last_closing_order.assert_called_once()
    _, kwargs = executor.fetch_last_closing_order.call_args
    assert kwargs["min_fill_time_ms"] == pytest.approx(expected_min_fill_time_ms, abs=1)


def test_reconcile_positions_fallback_when_closing_order_none(temp_db: LoggerDB):
    """
    When OKXExecutor.fetch_last_closing_order returns None (API error / no credential),
    reconcile_positions must safely fall back to current_price with is_real_fill=False.
    """
    daemon, state_mgr, executor = _make_daemon(temp_db)

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=1.0,
        entry_price=77630.0,
        last_price=77630.0,
    )

    executor.fetch_last_closing_order = AsyncMock(return_value=None)

    events = daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=76047.04,
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["exit_price"] == 76047.04
    assert ev["is_real_fill"] is False
    assert ev["order_id"] is None
    assert ev["is_stop_loss"] is True


def test_okx_executor_fetch_last_closing_order_parsing():
    """
    Verify OKXExecutor.fetch_last_closing_order parses the OKX response format,
    filters by side/posSide, and extracts avgPx, pnl, fee.
    """
    executor = OKXExecutor()
    executor.has_credentials = True
    executor.exchange = MagicMock()

    raw_response = {
        "code": "0",
        "msg": "",
        "data": [
            {
                "ordId": "999001",
                "clOrdId": "",
                "instId": "BTC-USDC-SWAP",
                "posSide": "long",
                "side": "sell",
                "state": "filled",
                "avgPx": "76047.037",
                "pnl": "-4.274",
                "fee": "-0.2075",
                "fillTime": "1789482919349",
            },
            {
                "ordId": "999000",
                "clOrdId": "",
                "instId": "BTC-USDC-SWAP",
                "posSide": "long",
                "side": "buy",
                "state": "filled",
                "avgPx": "77630.0",
                "pnl": "0.0",
                "fee": "-0.21",
                "fillTime": "1789470000000",
            }
        ]
    }

    executor.exchange.private_get_trade_orders_history = AsyncMock(return_value=raw_response)

    result = asyncio.run(executor.fetch_last_closing_order("BTC/USDC", "long"))

    assert result is not None
    assert result["order_id"] == "999001"
    assert result["exit_price"] == pytest.approx(76047.037, abs=0.001)
    assert result["pnl_usd"] == pytest.approx(-4.274, abs=0.001)
    assert result["fee_usd"] == pytest.approx(0.2075, abs=0.001)
    assert result["fill_time"] == 1789482919349
    assert result["is_real_fill"] is True


def test_okx_executor_fetch_last_closing_order_rejects_stale_fill_before_min_time():
    """
    A closing order older than `min_fill_time_ms` must never be returned — it cannot be
    the fill that closed a position which was still confirmed open after that timestamp,
    only a stale, unrelated earlier trade on the same symbol/side sitting in the last 10
    order-history rows.
    """
    executor = OKXExecutor()
    executor.has_credentials = True
    executor.exchange = MagicMock()

    raw_response = {
        "code": "0",
        "msg": "",
        "data": [
            {
                "ordId": "999001",
                "instId": "BTC-USDC-SWAP",
                "posSide": "long",
                "side": "sell",
                "state": "filled",
                "avgPx": "76047.037",
                "pnl": "-4.274",
                "fee": "-0.2075",
                "fillTime": "1789482919349",  # stale — predates the position snapshot
            },
        ]
    }
    executor.exchange.private_get_trade_orders_history = AsyncMock(return_value=raw_response)

    # Snapshot was confirmed alive strictly after this stale order's fillTime.
    min_fill_time_ms = 1789482919349 + 1

    result = asyncio.run(
        executor.fetch_last_closing_order("BTC/USDC", "long", min_fill_time_ms=min_fill_time_ms)
    )

    assert result is None

    # Without the bound, the same stale order IS picked up — proves the bound is what
    # made the difference above, not some unrelated filter.
    result_unbounded = asyncio.run(executor.fetch_last_closing_order("BTC/USDC", "long"))
    assert result_unbounded is not None
    assert result_unbounded["order_id"] == "999001"
