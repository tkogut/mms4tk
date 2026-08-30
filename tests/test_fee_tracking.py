import os
import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from mms4tk.logger_db import LoggerDB
from mms4tk.trade_sync import OKXTradeSyncWorker
import mms4tk.api as api_module


def test_schema_auto_migration(tmp_path):
    """Test that existing SQLite databases with legacy closed_trades schema are auto-migrated with fee_usd and pnl_net_usd."""
    db_file = os.path.join(tmp_path, "legacy_logs.db")
    
    # 1. Create legacy table schema without fee_usd and pnl_net_usd
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                amount REAL NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                is_win INTEGER NOT NULL,
                leverage REAL NOT NULL DEFAULT 1.0
            )
        """)
        cursor.execute("""
            INSERT INTO closed_trades 
                (trade_id, timestamp, symbol, side, entry_price, exit_price, amount, pnl_usd, pnl_pct, is_win, leverage)
            VALUES 
                ('legacy_1', '2026-08-10T10:00:00Z', 'BTC/USDC', 'LONG', 50000.0, 51000.0, 1.0, 100.0, 0.02, 1, 1.0)
        """)
        conn.commit()

    # 2. Open DB with LoggerDB, which runs _init_db() migration
    db = LoggerDB(db_path=db_file)

    # 3. Check table columns
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(closed_trades)")
        cols = {row["name"]: row for row in cursor.fetchall()}
        assert "fee_usd" in cols
        assert "pnl_net_usd" in cols

    # 4. Verify existing legacy trade returns defaults
    trades = db.get_closed_trades()
    assert len(trades) == 1
    assert trades[0]["trade_id"] == "legacy_1"
    assert trades[0]["fee_usd"] == 0.0
    assert trades[0]["pnl_net_usd"] == 0.0

    # 5. Verify upserting a new trade with fee and net PnL persists correctly
    db.upsert_closed_trade(
        trade_id="new_trade_2",
        timestamp="2026-08-11T12:00:00Z",
        symbol="BTC/USDC",
        side="LONG",
        entry_price=51000.0,
        exit_price=52000.0,
        amount=1.0,
        pnl_usd=100.0,
        pnl_pct=0.02,
        is_win=True,
        leverage=1.0,
        fee_usd=1.25,
        pnl_net_usd=98.75
    )

    trades = db.get_closed_trades()
    assert len(trades) == 2
    t2 = next(t for t in trades if t["trade_id"] == "new_trade_2")
    assert t2["fee_usd"] == 1.25
    assert t2["pnl_net_usd"] == 98.75

    recent = db.get_recent_closed_trades(limit=10)
    assert len(recent) == 2


def test_fifo_fee_matching_full_roundtrip(tmp_path, monkeypatch):
    """Test that FIFO trade synchronization calculates full round-trip fees (open fee + close fee) and net PnL."""
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    db_file = os.path.join(tmp_path, "test_fifo.db")
    db = LoggerDB(db_path=db_file)

    mock_executor = MagicMock()
    mock_executor.has_credentials = True
    mock_executor.initialize = AsyncMock()
    mock_executor.exchange = MagicMock()
    mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={
        "data": [
            {
                "ordId": "ord_open_1",
                "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404",
                "side": "buy",
                "posSide": "long",
                "avgPx": "50000.0",
                "sz": "1.0",
                "pnl": "0.0",
                "fee": "-0.05",
                "uTime": "1786525300000"
            },
            {
                "ordId": "ord_close_1",
                "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404",
                "side": "sell",
                "posSide": "long",
                "avgPx": "50500.0",
                "sz": "1.0",
                "pnl": "10.0",
                "fee": "-0.05",
                "uTime": "1786525400000"
            }
        ]
    })

    import asyncio
    worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)
    synced = asyncio.run(worker.sync_closed_trades())
    assert synced == 1

    trades = db.get_closed_trades()
    assert len(trades) == 1
    t = trades[0]
    assert t["trade_id"] == "ord_close_1"
    assert t["entry_price"] == 50000.0
    assert t["exit_price"] == 50500.0
    assert t["pnl_usd"] == 10.0
    assert t["fee_usd"] == pytest.approx(0.10)
    assert t["pnl_net_usd"] == pytest.approx(9.90)
    assert t["is_win"] == 1


def test_partial_close_split_50_fee_consumption(tmp_path, monkeypatch):
    """Test partial close (SPLIT_50) properly consumes pro-rata opening fees."""
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    db_file = os.path.join(tmp_path, "test_split50.db")
    db = LoggerDB(db_path=db_file)

    mock_executor = MagicMock()
    mock_executor.has_credentials = True
    mock_executor.initialize = AsyncMock()
    mock_executor.exchange = MagicMock()
    mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={
        "data": [
            {
                "ordId": "ord_open_short",
                "state": "filled",
                "instId": "ETH-USD_UM_XPERP-310404",
                "side": "sell",
                "posSide": "short",
                "avgPx": "3000.0",
                "sz": "2.0",
                "pnl": "0.0",
                "fee": "-0.20",  # 0.10 fee per unit
                "uTime": "1786525300000"
            },
            {
                "ordId": "ord_close_split_1",
                "state": "filled",
                "instId": "ETH-USD_UM_XPERP-310404",
                "side": "buy",
                "posSide": "short",
                "avgPx": "2950.0",
                "sz": "1.0",
                "pnl": "10.0",
                "fee": "-0.06",
                "uTime": "1786525400000"
            },
            {
                "ordId": "ord_close_split_2",
                "state": "filled",
                "instId": "ETH-USD_UM_XPERP-310404",
                "side": "buy",
                "posSide": "short",
                "avgPx": "2900.0",
                "sz": "1.0",
                "pnl": "20.0",
                "fee": "-0.05",
                "uTime": "1786525500000"
            }
        ]
    })

    import asyncio
    worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)
    synced = asyncio.run(worker.sync_closed_trades())
    assert synced == 2

    trades = db.get_closed_trades()
    assert len(trades) == 2

    t1 = next(t for t in trades if t["trade_id"] == "ord_close_split_1")
    assert t1["amount"] == 1.0
    assert t1["pnl_usd"] == 10.0
    # Open fee for 1.0 unit = 0.10 + Close fee = 0.06 => 0.16
    assert t1["fee_usd"] == pytest.approx(0.16)
    assert t1["pnl_net_usd"] == pytest.approx(9.84)
    assert t1["is_win"] == 1

    t2 = next(t for t in trades if t["trade_id"] == "ord_close_split_2")
    assert t2["amount"] == 1.0
    assert t2["pnl_usd"] == 20.0
    # Open fee for remaining 1.0 unit = 0.10 + Close fee = 0.05 => 0.15
    assert t2["fee_usd"] == pytest.approx(0.15)
    assert t2["pnl_net_usd"] == pytest.approx(19.85)
    assert t2["is_win"] == 1


def test_net_win_loss_classification(tmp_path, monkeypatch):
    """Test that a trade with gross profit but negative net PnL is classified as LOSS."""
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    db_file = os.path.join(tmp_path, "test_net_loss.db")
    db = LoggerDB(db_path=db_file)

    mock_executor = MagicMock()
    mock_executor.has_credentials = True
    mock_executor.initialize = AsyncMock()
    mock_executor.exchange = MagicMock()
    mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={
        "data": [
            {
                "ordId": "ord_open_long",
                "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404",
                "side": "buy",
                "posSide": "long",
                "avgPx": "60000.0",
                "sz": "1.0",
                "pnl": "0.0",
                "fee": "-0.07",
                "uTime": "1786525300000"
            },
            {
                "ordId": "ord_close_fee_loss",
                "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404",
                "side": "sell",
                "posSide": "long",
                "avgPx": "60004.0",
                "sz": "1.0",
                "pnl": "0.04",  # Gross profit +$0.04
                "fee": "-0.07",  # Close fee $0.07, Total fee $0.14 -> Net PnL -$0.10
                "uTime": "1786525400000"
            }
        ]
    })

    import asyncio
    worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)
    synced = asyncio.run(worker.sync_closed_trades())
    assert synced == 1

    trades = db.get_closed_trades()
    assert len(trades) == 1
    t = trades[0]
    assert t["pnl_usd"] == pytest.approx(0.04)
    assert t["fee_usd"] == pytest.approx(0.14)
    assert t["pnl_net_usd"] == pytest.approx(-0.10)
    assert t["is_win"] == 0  # LOSS


def test_api_stats_aggregation_fees_and_net_pnl(tmp_path, monkeypatch):
    """Test KPI summary and GET /api/v1/stats/pnl endpoint fee and net PnL aggregations."""
    monkeypatch.setenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
    db_file = os.path.join(tmp_path, "test_api_stats.db")
    db = LoggerDB(db_path=db_file)

    # 1. Insert 3 trades: 1 win, 1 gross win / net loss, 1 gross loss
    db.upsert_closed_trade(
        trade_id="t1",
        timestamp="2026-08-15T10:00:00Z",
        symbol="BTC/USDC",
        side="LONG",
        entry_price=50000.0,
        exit_price=51000.0,
        amount=1.0,
        pnl_usd=10.0,
        pnl_pct=0.10,
        is_win=True,
        fee_usd=0.50,
        pnl_net_usd=9.50
    )
    db.upsert_closed_trade(
        trade_id="t2",
        timestamp="2026-08-15T11:00:00Z",
        symbol="BTC/USDC",
        side="LONG",
        entry_price=50000.0,
        exit_price=50004.0,
        amount=1.0,
        pnl_usd=0.04,
        pnl_pct=0.0004,
        is_win=False,  # Net loss
        fee_usd=0.14,
        pnl_net_usd=-0.10
    )
    db.upsert_closed_trade(
        trade_id="t3",
        timestamp="2026-08-15T12:00:00Z",
        symbol="ETH/USDC",
        side="SHORT",
        entry_price=3000.0,
        exit_price=3050.0,
        amount=1.0,
        pnl_usd=-5.0,
        pnl_pct=-0.05,
        is_win=False,
        fee_usd=0.30,
        pnl_net_usd=-5.30
    )

    kpi = db.get_kpi_summary()
    assert kpi["total_trades"] == 3
    assert kpi["realized_gross_pnl_usd"] == pytest.approx(5.04)
    assert kpi["total_fees_usd"] == pytest.approx(0.94)
    assert kpi["realized_net_pnl_usd"] == pytest.approx(4.10)
    assert kpi["win_rate"] == pytest.approx(66.7, abs=0.1)  # 2 of 3 gross wins
    assert kpi["net_win_rate_pct"] == pytest.approx(33.3, abs=0.1)  # 1 of 3 net wins

    # 2. Test FastAPI endpoints
    orig_db = api_module.logger_db
    api_module.logger_db = db
    try:
        with TestClient(api_module.app) as client:
            headers = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}
            
            # GET /api/v1/stats/pnl
            res = client.get("/api/v1/stats/pnl", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["realized_gross_pnl_usd"] == pytest.approx(5.04)
            assert data["total_fees_usd"] == pytest.approx(0.94)
            assert data["realized_net_pnl_usd"] == pytest.approx(4.10)
            assert data["net_win_rate_pct"] == pytest.approx(33.3, abs=0.1)

            # GET /api/v1/trades/history
            res_hist = client.get("/api/v1/trades/history", headers=headers)
            assert res_hist.status_code == 200
            hist = res_hist.json()
            assert len(hist) == 3
            assert any(item["fee_usd"] == 0.50 and item["pnl_net_usd"] == 9.50 for item in hist)

            # GET /api/v1/history/trades
            res_hist2 = client.get("/api/v1/history/trades", headers=headers)
            assert res_hist2.status_code == 200
            assert len(res_hist2.json()) == 3
    finally:
        api_module.logger_db = orig_db
