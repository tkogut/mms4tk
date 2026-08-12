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


def test_trade_sync_worker(tmp_path):
    import asyncio
    
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
