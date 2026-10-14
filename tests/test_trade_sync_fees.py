"""#124 (part 1): trade_sync must keep real OKX fees.

OKX order-history endpoints return only a short window (~100 orders) per call. The sync used to
recompute every close from that window alone, so once a close's OPENING order dropped out of it the
opening fee became 0 and the booked fee (and `pnl_net_usd`) was overwritten with a worse value.
These tests cover pagination, the persisted raw orders, and the "never lower a booked fee" rule.
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from mms4tk.logger_db import LoggerDB
from mms4tk.trade_sync import OKXTradeSyncWorker

INST = "XAU-USD_UM_XPERP-310502"  # ctVal 0.001 via the per-asset fallback


@pytest.fixture(autouse=True)
def _early_cutoff(monkeypatch):
    monkeypatch.setenv("MMS4_TRADE_SYNC_START_ISO", "2020-01-01T00:00:00+00:00")


def _order(ord_id, side, pos_side, px, sz, pnl, fee, ts, state="filled"):
    return {
        "ordId": str(ord_id), "state": state, "instId": INST, "side": side, "posSide": pos_side,
        "avgPx": str(px), "accFillSz": str(sz), "sz": str(sz), "pnl": str(pnl), "fee": str(fee),
        "lever": "3", "uTime": str(ts), "fillTime": str(ts),
    }


def _open(n, ts, fee=-0.02):
    return _order(f"open{n}", "buy", "long", 4000.0, 10, 0, fee, ts)


def _close(n, ts, pnl=0.5, fee=-0.02):
    return _order(f"close{n}", "sell", "long", 4005.0, 10, pnl, fee, ts)


def _worker(tmp_path, pages_fn):
    db = LoggerDB(os.path.join(tmp_path, "sync_fees.db"))
    executor = MagicMock()
    executor.has_credentials = True
    executor.initialize = AsyncMock()
    executor.exchange = MagicMock()
    executor.exchange.markets = None
    executor.exchange.private_get_trade_orders_history = AsyncMock(return_value={"data": []})
    executor.exchange.private_get_trade_orders_history_archive = AsyncMock(side_effect=pages_fn)
    return OKXTradeSyncWorker(executor=executor, logger_db=db, interval_seconds=10.0), db, executor


def _run(coro):
    return asyncio.run(coro)


# ── pagination ──────────────────────────────────────────────────────────────

def test_sync_walks_every_page_with_the_after_cursor(tmp_path):
    # newest first, like OKX: 125 open/close pairs = 250 orders -> pages of 100, 100, 50
    orders = []
    for n in range(125):
        t = 1_786_000_000_000 + n * 120_000
        orders[:0] = [_open(n, t), _close(n, t + 60_000)]
    calls = []

    async def pages(params):
        calls.append(dict(params))
        after = params.get("after")
        start = 0 if not after else next(i for i, o in enumerate(orders) if o["ordId"] == after) + 1
        return {"data": orders[start:start + int(params["limit"])]}

    worker, db, _ = _worker(tmp_path, pages)
    synced = _run(worker.sync_closed_trades())

    assert [c.get("after") for c in calls] == [None, orders[99]["ordId"], orders[199]["ordId"]]
    assert all(c["limit"] == "100" for c in calls)
    assert len(db.load_okx_orders()) == 250
    assert synced == 125
    trades = db.get_closed_trades(limit=None)
    assert len(trades) == 125
    # every close carries its opening fee: 0.02 open + 0.02 close
    assert all(t["fee_usd"] == pytest.approx(0.04) for t in trades)


def test_cursor_that_does_not_advance_cannot_loop_forever(tmp_path):
    page = [_close(n, 1_786_000_000_000 + n) for n in range(100)]
    calls = []

    async def pages(params):
        calls.append(params)
        return {"data": page}  # same full page every time

    worker, db, _ = _worker(tmp_path, pages)
    _run(worker.sync_closed_trades())
    assert len(calls) == 2  # second page adds no new ordId -> stop


def test_page_cap_bounds_the_walk(tmp_path, monkeypatch):
    monkeypatch.setenv("MMS4_TRADE_SYNC_MAX_PAGES", "3")
    counter = {"n": 0}
    calls = []

    async def pages(params):
        calls.append(params)
        base = counter["n"]
        counter["n"] += 100
        return {"data": [_close(base + i, 1_786_000_000_000 + base + i) for i in range(100)]}

    worker, _, _ = _worker(tmp_path, pages)
    _run(worker.sync_closed_trades())
    assert len(calls) == 3


# ── persisted orders keep the opening fee after the API window moves on ─────

def test_opening_order_seen_once_keeps_its_fee_when_it_leaves_the_api_window(tmp_path):
    state = {"data": [_open(1, 1_786_000_000_000)]}

    async def pages(params):
        return {"data": list(state["data"])}

    worker, db, _ = _worker(tmp_path, pages)
    assert _run(worker.sync_closed_trades()) == 0  # only an opening order so far

    # next pass: OKX no longer returns the opening order, only the close
    state["data"] = [_close(1, 1_786_000_060_000)]
    assert _run(worker.sync_closed_trades()) == 1

    trade = db.get_closed_trades(limit=None)[0]
    assert trade["fee_usd"] == pytest.approx(0.04), "opening fee must come from the stored order"
    assert trade["pnl_net_usd"] == pytest.approx(0.5 - 0.04)


def test_unchanged_closes_are_not_rewritten(tmp_path):
    data = [_close(1, 1_786_000_060_000), _open(1, 1_786_000_000_000)]

    async def pages(params):
        return {"data": list(data)}

    worker, _, _ = _worker(tmp_path, pages)
    assert _run(worker.sync_closed_trades()) == 1
    assert _run(worker.sync_closed_trades()) == 0


def test_okx_order_is_refreshed_when_its_state_changes(tmp_path):
    db = LoggerDB(os.path.join(tmp_path, "orders.db"))
    db.upsert_okx_order(_order("a1", "buy", "long", 4000, 10, 0, -0.02, 1_786_000_000_000, state="partially_filled"))
    db.upsert_okx_order(_order("a1", "buy", "long", 4000, 10, 0, -0.04, 1_786_000_000_500, state="filled"))
    stored = db.load_okx_orders()
    assert len(stored) == 1
    assert stored[0]["state"] == "filled" and stored[0]["fee"] == "-0.04"
    assert db.upsert_okx_order({"state": "filled"}) is False  # no id -> ignored


# ── a booked fee is never lowered ───────────────────────────────────────────

def _upsert(db, fee, pnl=0.5):
    db.upsert_closed_trade(
        trade_id="t1", timestamp="2026-10-10T10:00:00+00:00", symbol="XAU-USD_UM_XPERP-310502", side="LONG",
        entry_price=4000.0, exit_price=4005.0, amount=10.0, pnl_usd=pnl, pnl_pct=0.01,
        is_win=(pnl - fee) > 0, leverage=3.0, fee_usd=fee, pnl_net_usd=pnl - fee,
    )


def test_reupsert_with_a_lower_fee_keeps_the_booked_fee(tmp_path):
    db = LoggerDB(os.path.join(tmp_path, "guard.db"))
    _upsert(db, fee=0.10)            # full fee: gross 0.50, net 0.40
    _upsert(db, fee=0.05)            # degraded recompute (opening fee lost)
    t = db.get_closed_trades(limit=None)[0]
    assert t["fee_usd"] == pytest.approx(0.10)
    assert t["pnl_net_usd"] == pytest.approx(0.40)


def test_lower_fee_cannot_flip_a_net_loss_into_a_win(tmp_path):
    db = LoggerDB(os.path.join(tmp_path, "guard_win.db"))
    _upsert(db, fee=0.60, pnl=0.50)  # net -0.10 -> loss
    _upsert(db, fee=0.30, pnl=0.50)  # would look like net +0.20 -> win
    t = db.get_closed_trades(limit=None)[0]
    assert t["is_win"] in (0, False)
    assert t["pnl_net_usd"] == pytest.approx(-0.10)


def test_reupsert_with_a_higher_fee_is_applied(tmp_path):
    db = LoggerDB(os.path.join(tmp_path, "guard_up.db"))
    _upsert(db, fee=0.05)
    _upsert(db, fee=0.10)
    t = db.get_closed_trades(limit=None)[0]
    assert t["fee_usd"] == pytest.approx(0.10)
    assert t["pnl_net_usd"] == pytest.approx(0.40)


# ── review follow-ups ───────────────────────────────────────────────────────

def _serve(orders):
    """Pager over a newest-first list, honouring `limit` and the `after` ordId cursor."""
    async def pages(params):
        after = params.get("after")
        start = 0 if not after else next(i for i, o in enumerate(orders) if o["ordId"] == after) + 1
        return {"data": orders[start:start + int(params["limit"])]}
    return pages


def test_recon_row_is_cleaned_up_even_when_the_close_is_already_synced(tmp_path):
    data = [_close(1, 1_786_000_060_000), _open(1, 1_786_000_000_000)]

    async def pages(params):
        return {"data": list(data)}

    worker, db, _ = _worker(tmp_path, pages)
    _run(worker.sync_closed_trades())          # authoritative row booked first (dashboard poll)
    db.upsert_closed_trade(                    # daemon reconciliation writes its provisional row later
        trade_id="recon_XAU/USDC_long_1", timestamp="2026-10-10T10:00:30+00:00", symbol="XAU/USDC",
        side="LONG", entry_price=4000.0, exit_price=4004.0, amount=10.0, pnl_usd=0.4, pnl_pct=0.03,
        is_win=True, leverage=3.0, reason="STOP-LOSS",
    )
    assert db.has_provisional_closed_trades()

    _run(worker.sync_closed_trades())
    ids = [t["trade_id"] for t in db.get_closed_trades(limit=None)]
    assert ids == ["close1"], "the provisional duplicate must be removed on a later pass"
    assert not db.has_provisional_closed_trades()


def test_archive_walk_continues_when_the_recent_endpoint_already_returned_the_same_orders(tmp_path):
    orders = []
    for n in range(150):  # 300 orders, newest first
        t = 1_786_000_000_000 + n * 120_000
        orders[:0] = [_open(n, t), _close(n, t + 60_000)]
    worker, db, executor = _worker(tmp_path, _serve(orders))
    # the recent endpoint lists the newest 120 of them, the archive endpoint lists everything
    executor.exchange.private_get_trade_orders_history = AsyncMock(side_effect=_serve(orders[:120]))

    _run(worker.sync_closed_trades())
    assert len(db.load_okx_orders()) == 300, "archive page 1 overlaps the recent endpoint; the walk must go on"


def test_second_pass_stops_at_stored_orders_and_writes_nothing(tmp_path, monkeypatch):
    orders = []
    for n in range(125):
        t = 1_786_000_000_000 + n * 120_000
        orders[:0] = [_open(n, t), _close(n, t + 60_000)]
    worker, db, executor = _worker(tmp_path, _serve(orders))
    _run(worker.sync_closed_trades())
    first_calls = executor.exchange.private_get_trade_orders_history_archive.call_count
    assert first_calls == 3

    written = []
    real = db.upsert_okx_orders
    monkeypatch.setattr(db, "upsert_okx_orders", lambda items: written.append(len(items)) or real(items))
    executor.exchange.private_get_trade_orders_history_archive.reset_mock()
    _run(worker.sync_closed_trades())
    assert executor.exchange.private_get_trade_orders_history_archive.call_count == 1
    assert written == [0], "nothing new or changed -> nothing to store"


def test_kept_fee_keeps_net_win_flag_and_pnl_pct_consistent(tmp_path):
    state = {"data": [_close(1, 1_786_000_060_000, pnl=0.5), _open(1, 1_786_000_000_000)]}

    async def pages(params):
        return {"data": list(state["data"])}

    worker, db, _ = _worker(tmp_path, pages)
    _run(worker.sync_closed_trades())          # full fee 0.04, gross 0.5
    # the stored orders are lost and OKX now reports a different pnl for the close only
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("DELETE FROM okx_orders")
        conn.commit()
    state["data"] = [_close(1, 1_786_000_060_000, pnl=0.6)]
    _run(worker.sync_closed_trades())

    t = db.get_closed_trades(limit=None)[0]
    assert t["fee_usd"] == pytest.approx(0.04)
    assert t["pnl_net_usd"] == pytest.approx(0.6 - 0.04)
    margin = 4005.0 * 10 * 0.001 / 3
    assert t["pnl_pct"] == pytest.approx((0.6 - 0.04) / margin, rel=1e-3)


def test_stale_opening_order_is_dropped_by_the_fifo_age_cap(tmp_path, monkeypatch):
    data = [_close(1, 1_786_000_000_000 + 200 * 3_600_000), _open(1, 1_786_000_000_000)]

    async def pages(params):
        return {"data": list(data)}

    worker, db, _ = _worker(tmp_path, pages)
    _run(worker.sync_closed_trades())
    assert db.get_closed_trades(limit=None)[0]["fee_usd"] == pytest.approx(0.02)  # open older than 168 h

    monkeypatch.setenv("MMS4_TRADE_SYNC_OPEN_LEG_MAX_AGE_HOURS", "500")
    other = tmp_path / "b"
    other.mkdir()
    worker2, db2, _ = _worker(other, pages)
    _run(worker2.sync_closed_trades())
    assert db2.get_closed_trades(limit=None)[0]["fee_usd"] == pytest.approx(0.04)
