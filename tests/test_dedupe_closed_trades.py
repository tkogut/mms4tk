"""Regression tests for the duplicate closed_trades bug found in Wykaz Transakcji.

Root cause: MMS4Daemon.reconcile_positions() writes an immediate, ESTIMATED closed-trade
row (trade_id="recon_{symbol}_{side}_<ts_ms>", exit price = the daemon's signal price at
detection time) so a stop-out is visible right away and drives the state machine without
waiting for OKXTradeSyncWorker. That worker later writes the AUTHORITATIVE row (real OKX
order id, real fill price) for the same real-world close. Because the two trade_id
schemes never match, upsert_closed_trade's ON CONFLICT(trade_id) never dedupes them, and
the same close appears twice with two different exit prices/pnl. Confirmed live in
production (BTC/USDC, 2026-08-28): entry=79668.8 recorded twice, exit 78335.3 vs 77942.0.

Fix: OKXTradeSyncWorker.sync_closed_trades() now deletes the matching provisional
`recon_*` row (matched by NORMALIZED symbol + side + entry price) right before writing
the authoritative one.
"""
import asyncio
import os

import pytest
from unittest.mock import AsyncMock, MagicMock

from mms4tk.logger_db import LoggerDB
from mms4tk.trade_sync import OKXTradeSyncWorker


@pytest.fixture
def db(tmp_path):
    return LoggerDB(db_path=str(tmp_path / "dedupe.db"))


def test_delete_provisional_matches_normalized_symbol(db):
    """reconcile_positions() writes the clean pair ("BTC/USDC"); the real OKX fill
    carries the raw instId ("BTC-USD_UM_XPERP-310404") — these must be recognized as
    the same instrument via normalize_symbol(), not a plain string match."""
    db.upsert_closed_trade(
        trade_id="recon_BTC/USDC_long_1787933006982",
        timestamp="2026-08-28T16:03:26.999444+00:00",
        symbol="BTC/USDC", side="LONG", entry_price=79668.8, exit_price=78335.3,
        amount=26.0, pnl_usd=-3.4671, pnl_pct=-0.0167, is_win=False,
    )

    removed = db.delete_provisional_closed_trade(
        symbol="BTC-USD_UM_XPERP-310404", side="LONG", entry_price=79668.8,
    )

    assert removed == 1
    assert db.get_closed_trades() == []


def test_delete_provisional_respects_price_tolerance(db):
    """A recon_ row for a DIFFERENT close (different entry price) must survive."""
    db.upsert_closed_trade(
        trade_id="recon_BTC/USDC_long_1", timestamp="2026-08-28T16:00:00+00:00",
        symbol="BTC/USDC", side="LONG", entry_price=79668.8, exit_price=78335.3,
        amount=26.0, pnl_usd=-3.0, pnl_pct=-0.01, is_win=False,
    )

    removed = db.delete_provisional_closed_trade(
        symbol="BTC-USD_UM_XPERP-310404", side="LONG", entry_price=50000.0,
    )

    assert removed == 0
    assert len(db.get_closed_trades()) == 1


def test_delete_provisional_respects_side(db):
    """A recon_ row for the OPPOSITE side at the same price must survive — this is a
    different position, not the same close."""
    db.upsert_closed_trade(
        trade_id="recon_BTC/USDC_short_1", timestamp="2026-08-28T16:00:00+00:00",
        symbol="BTC/USDC", side="SHORT", entry_price=79668.8, exit_price=78335.3,
        amount=26.0, pnl_usd=3.0, pnl_pct=0.01, is_win=True,
    )

    removed = db.delete_provisional_closed_trade(
        symbol="BTC-USD_UM_XPERP-310404", side="LONG", entry_price=79668.8,
    )

    assert removed == 0
    assert len(db.get_closed_trades()) == 1


def test_delete_provisional_never_touches_real_trade_sync_rows(db):
    """Only rows with the recon_ trade_id prefix are candidates — a genuine, authoritative
    trade_sync row (real numeric OKX order id) at the same price must never be deleted by
    this cleanup, even if it happens to match symbol/side/price (e.g. two legitimately
    separate closes at a similar price)."""
    db.upsert_closed_trade(
        trade_id="3873208213551452160", timestamp="2026-08-28T16:03:19.854000+00:00",
        symbol="BTC-USD_UM_XPERP-310404", side="LONG", entry_price=79668.8, exit_price=77942.0,
        amount=26.0, pnl_usd=-4.49, pnl_pct=-0.02, is_win=False,
    )

    removed = db.delete_provisional_closed_trade(
        symbol="BTC/USDC", side="LONG", entry_price=79668.8,
    )

    assert removed == 0
    assert len(db.get_closed_trades()) == 1


def test_sync_closed_trades_cleans_up_matching_provisional_row(db, monkeypatch):
    """End-to-end: a recon_ provisional row seeded before sync must be gone afterward,
    leaving only the authoritative trade_sync row — no duplicate in Wykaz Transakcji."""
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    # Provisional row written earlier by reconcile_positions().
    db.upsert_closed_trade(
        trade_id="recon_BTC/USDC_long_1787933006982",
        timestamp="2026-08-28T16:03:26.999444+00:00",
        symbol="BTC/USDC", side="LONG", entry_price=79668.8, exit_price=78335.3,
        amount=26.0, pnl_usd=-3.4671, pnl_pct=-0.0167, is_win=False,
    )

    mock_executor = MagicMock()
    mock_executor.has_credentials = True
    mock_executor.initialize = AsyncMock()
    mock_executor.exchange = MagicMock()
    mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={
        "data": [
            {
                # Opening fill — lets _consume_entry_price resolve the exact entry
                # price (79668.8) instead of falling back to _infer_entry_price's
                # pnl-derived approximation, keeping this test's entry price aligned
                # with the pre-seeded recon_ row's.
                "ordId": "open_leg_1",
                "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404",
                "side": "buy",
                "posSide": "long",
                "avgPx": "79668.8",
                "sz": "26.0",
                "pnl": "0",
                "uTime": "1787932000000",
            },
            {
                "ordId": "3873208213551452160",
                "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404",
                "side": "sell",
                "posSide": "long",
                "avgPx": "77942.0",
                "sz": "26.0",
                "pnl": "-4.48968",
                "uTime": "1787933000000",
            }
        ]
    })

    worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)
    synced = asyncio.run(worker.sync_closed_trades())

    assert synced == 1
    trades = db.get_closed_trades()
    assert len(trades) == 1
    assert trades[0]["trade_id"] == "3873208213551452160"
    assert trades[0]["exit_price"] == pytest.approx(77942.0)


def test_sync_carries_reason_from_provisional_row_onto_authoritative_row(db, monkeypatch):
    """The recon_ row's `reason` (set by reconcile_positions when it detects a
    stop-out) must survive onto the authoritative trade_sync row, not get dropped
    when the provisional row is deleted — otherwise get_closed_trades() falls back
    to its generic post-hoc pnl_pct guess and can mislabel a real stop-loss."""
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")

    db.upsert_closed_trade(
        trade_id="recon_BTC/USDC_long_1787933006982",
        timestamp="2026-08-28T16:03:26.999444+00:00",
        symbol="BTC/USDC", side="LONG", entry_price=79668.8, exit_price=78335.3,
        amount=26.0, pnl_usd=-3.4671, pnl_pct=-0.0167, is_win=False,
        reason="STOP-LOSS",
    )

    mock_executor = MagicMock()
    mock_executor.has_credentials = True
    mock_executor.initialize = AsyncMock()
    mock_executor.exchange = MagicMock()
    mock_executor.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={
        "data": [
            {
                "ordId": "open_leg_1", "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404", "side": "buy", "posSide": "long",
                "avgPx": "79668.8", "sz": "26.0", "pnl": "0", "uTime": "1787932000000",
            },
            {
                "ordId": "3873208213551452160", "state": "filled",
                "instId": "BTC-USD_UM_XPERP-310404", "side": "sell", "posSide": "long",
                "avgPx": "77942.0", "sz": "26.0", "pnl": "-4.48968", "uTime": "1787933000000",
            }
        ]
    })

    worker = OKXTradeSyncWorker(executor=mock_executor, logger_db=db, interval_seconds=10.0)
    synced = asyncio.run(worker.sync_closed_trades())

    assert synced == 1
    trades = db.get_closed_trades()
    assert len(trades) == 1
    assert trades[0]["trade_id"] == "3873208213551452160"
    assert trades[0]["reason"] == "STOP-LOSS"
