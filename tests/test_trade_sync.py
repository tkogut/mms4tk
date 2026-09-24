import pytest
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock

from mms4tk.logger_db import LoggerDB
from mms4tk.trade_sync import OKXTradeSyncWorker


def test_logger_db_closed_trades_and_kpi(tmp_path):
    db_file = os.path.join(tmp_path, "test_logs.db")
    db = LoggerDB(db_path=db_file)

    # Insert sample closed trades
    db.upsert_closed_trade(
        trade_id="trade_1",
        timestamp="2026-08-12T10:00:00Z",
        symbol="BTC/USD:USD-310404",
        side="SHORT",
        entry_price=63700.0,
        exit_price=63500.0,
        amount=1.0,
        pnl_usd=2.0,
        pnl_pct=0.03,
        is_win=True,
        leverage=1.0
    )

    db.upsert_closed_trade(
        trade_id="trade_2",
        timestamp="2026-08-12T10:15:00Z",
        symbol="BTC/USD:USD-310404",
        side="SHORT",
        entry_price=63500.0,
        exit_price=63800.0,
        amount=1.0,
        pnl_usd=-3.0,
        pnl_pct=-0.04,
        is_win=False,
        leverage=1.0
    )

    trades = db.get_closed_trades()
    assert len(trades) == 2

    kpi = db.get_kpi_summary()
    assert kpi["total_trades"] == 2
    assert kpi["win_rate"] == 50.0
    assert kpi["profit_factor"] == 0.67
    assert kpi["max_drawdown"] == 3.0
    assert kpi["total_pnl_usd"] == -1.0


def test_trade_sync_worker(tmp_path, monkeypatch):
    import asyncio

    # The production default cutoff (MMS4_TRADE_SYNC_START_ISO) post-dates this fixture's
    # fills, which would filter them all out. Pin an early cutoff so the test exercises
    # the sync logic rather than the baseline filter.
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    async def _async_test():
        db_file = os.path.join(tmp_path, "test_logs.db")
        db = LoggerDB(db_path=db_file)

        mock_executor = MagicMock()
        mock_executor.has_credentials = True
        mock_executor.initialize = AsyncMock()
        mock_executor.exchange = MagicMock()
        mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={
            "data": [
                {
                    "ordId": "ord_100",
                    "state": "filled",
                    "instId": "BTC-USD_UM_XPERP-310404",
                    "side": "buy",
                    "posSide": "short",
                    "avgPx": "63723.7",
                    "sz": "1.0",
                    "pnl": "1.5",
                    "uTime": "1786525312271"
                }
            ]
        })

        worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)
        synced = await worker.sync_closed_trades()

        assert synced == 1
        trades = db.get_closed_trades()
        assert len(trades) == 1
        assert trades[0]["trade_id"] == "ord_100"
        assert trades[0]["pnl_usd"] == 1.5

    asyncio.run(_async_test())


def test_trade_sync_worker_pnl_pct_scales_by_contract_size(tmp_path, monkeypatch):
    """
    Regression test: OKX reports `size` in contracts, not coins. A close-only fill
    (no matching open leg, so entry is inferred from PnL) with no ctVal scaling
    inflates notional/margin by 1/ctVal, driving pnl_pct toward zero and making a
    real ~2% stop-loss get misclassified downstream as "BREAK-EVEN" instead of
    "STOP-LOSS" (LoggerDB.get_closed_trades' pnl_pct <= -0.015 fallback never trips).
    """
    import asyncio

    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    async def _async_test():
        db_file = os.path.join(tmp_path, "test_logs.db")
        db = LoggerDB(db_path=db_file)

        mock_executor = MagicMock()
        mock_executor.has_credentials = True
        mock_executor.initialize = AsyncMock()
        mock_executor.exchange = MagicMock()
        # No markets loaded -> _get_contract_size falls back to the per-asset
        # default instead of MagicMock's auto-`__float__` (which returns 1.0 and
        # would silently defeat this test's ctVal scaling).
        mock_executor.exchange.markets = None
        # BTC SHORT stop-out: 19 contracts (ctVal=0.0001 -> ~0.0019 BTC, ~$150
        # notional at this price), lever 3x, a ~2% adverse move -> ~$3 loss.
        mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={
            "data": [
                {
                    "ordId": "ord_sl_1",
                    "state": "filled",
                    "instId": "BTC-USD_UM_XPERP-310404",
                    "side": "buy",
                    "posSide": "short",
                    "avgPx": "78984.6",
                    "sz": "19",
                    "pnl": "-3.0",
                    "lever": "3",
                    "uTime": "1786525312271"
                }
            ]
        })

        worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)
        synced = await worker.sync_closed_trades()

        assert synced == 1
        trades = db.get_closed_trades()
        assert len(trades) == 1
        pnl_pct = trades[0]["pnl_pct"]
        # Correctly scaled: notional ~$150, margin ~$50, pnl_pct ~ -0.06.
        # Buggy (unscaled) calc gives notional ~$1.5M, pnl_pct ~ -0.000006.
        assert pnl_pct <= -0.015, f"pnl_pct={pnl_pct} would misclassify as BREAK-EVEN, not STOP-LOSS"

    asyncio.run(_async_test())


def test_trade_sync_worker_skips_overlapping_calls(tmp_path, monkeypatch):
    """
    Regression test for the resource-leak fixed alongside 0d528d1: api.py's
    GET /api/v1/history/trades fires sync_closed_trades() via asyncio.create_task
    on every poll (every 5s from the frontend) without waiting for the previous call
    to finish. Without a concurrency guard, overlapping calls each drive the shared
    OKXExecutor's initialize()/exchange concurrently — the actual cause of the
    "Unclosed client session" warnings and cascading OKX RequestTimeouts observed on
    unrelated endpoints (balance/positions) in production. A second call arriving
    while one is still in flight must be skipped (return 0), not executed.
    """
    import asyncio
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    async def _async_test():
        db_file = os.path.join(tmp_path, "test_logs.db")
        db = LoggerDB(db_path=db_file)

        init_call_count = 0

        async def slow_initialize():
            nonlocal init_call_count
            init_call_count += 1
            await asyncio.sleep(0.05)

        mock_executor = MagicMock()
        mock_executor.has_credentials = True
        mock_executor.initialize = slow_initialize
        mock_executor.exchange = MagicMock()
        mock_executor.exchange.private_get_trade_orders_history = AsyncMock(return_value={"data": []})
        mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={"data": []})

        worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)

        # Two overlapping calls, as api.py's polling endpoint would produce if fired
        # faster than a single round-trip against OKX completes.
        results = await asyncio.gather(
            worker.sync_closed_trades(),
            worker.sync_closed_trades(),
        )

        assert init_call_count == 1, "overlapping call must be skipped, not re-run initialize()"
        assert results.count(0) >= 1, "the skipped overlapping call must return 0"

    asyncio.run(_async_test())
