"""
Regression test for the 2026-09-17 production incident: get_closed_trades()
derived `reason`/`entry_timestamp` from a per-row cycle_logs lookup on every call
with nothing ever cached (closed_trades had no such columns), so GET
/api/v1/history/trades — polled every 5s by the dashboard — re-ran an unindexed
scan of cycle_logs for every trade, forever. Confirmed live via strace on the
production process (100% CPU, ~20k pread64/s against logs.db).

Fix: `reason`/`entry_timestamp` columns on `closed_trades`, populated once and
read back on every later call instead of re-querying cycle_logs.
"""
import os
import sqlite3
from datetime import datetime, timezone

from mms4tk.logger_db import LoggerDB


def test_get_closed_trades_caches_reason_and_entry_timestamp(tmp_path):
    db_file = os.path.join(tmp_path, "test_logs.db")
    db = LoggerDB(db_path=db_file)

    trade_ts = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
    db.upsert_closed_trade(
        trade_id="trade_cache_1",
        timestamp=trade_ts.isoformat(),
        symbol="BTC/USDC",
        side="LONG",
        entry_price=60000.0,
        exit_price=60500.0,
        amount=1.0,
        pnl_usd=5.0,
        pnl_pct=0.01,
        is_win=True,
        leverage=1.0,
    )

    # A matching cycle_logs row the reason/entry_timestamp lookups should find on
    # the FIRST call only.
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO cycle_logs (timestamp, symbol, signal_type, state, leverage, price, outcome, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_ts.isoformat(), "BTC/USDC", "STOP-LOSS", "BASE_MODE", 1.0, 60500.0, "STOP-LOSS", ""),
        )
        conn.commit()

    # 1st call: cache miss — must compute reason/entry_timestamp from cycle_logs
    # and persist them onto the closed_trades row.
    trades = db.get_closed_trades()
    assert len(trades) == 1
    assert trades[0]["reason"] == "STOP-LOSS"

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT reason, entry_timestamp FROM closed_trades WHERE trade_id = ?",
            ("trade_cache_1",),
        ).fetchone()
    assert row["reason"] == "STOP-LOSS", "reason must be persisted onto closed_trades, not just returned"

    # Remove the cycle_logs row: if get_closed_trades() were still re-querying it
    # on every call (the bug), the reason would now disappear/change. A correctly
    # cached implementation must return the same value regardless.
    with sqlite3.connect(db_file) as conn:
        conn.execute("DELETE FROM cycle_logs")
        conn.commit()

    trades_again = db.get_closed_trades()
    assert trades_again[0]["reason"] == "STOP-LOSS", (
        "2nd call must read the cached reason, not re-derive it from cycle_logs "
        "(which is now empty) — this is the exact N+1 regression from 2026-09-17"
    )


def test_get_closed_trades_caches_a_not_found_entry_timestamp(tmp_path):
    """
    Regression test for the follow-up bug in the first cut of this fix: gating the
    entry_timestamp lookup on `not entry_timestamp` made a genuine "no matching
    EXECUTED_BASE row" (legitimately None) indistinguishable from "never looked
    up" — so every trade with no match re-ran the expensive lookup on EVERY call,
    forever. Confirmed live via py-spy: 100% CPU stuck in exactly this query.

    Proof: a trade with NO matching cycle_logs row on the first call (entry_timestamp
    stays None) must NOT re-run the lookup on a second call, even after a row that
    WOULD now match is inserted — an un-cached implementation would pick it up and
    flip entry_timestamp to non-None, which is the bug.
    """
    db_file = os.path.join(tmp_path, "test_logs.db")
    db = LoggerDB(db_path=db_file)

    trade_ts = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
    db.upsert_closed_trade(
        trade_id="trade_cache_2",
        timestamp=trade_ts.isoformat(),
        symbol="ETH/USDC",
        side="LONG",
        entry_price=2500.0,
        exit_price=2550.0,
        amount=1.0,
        pnl_usd=5.0,
        pnl_pct=0.01,
        is_win=True,
        leverage=1.0,
    )

    # 1st call: no matching cycle_logs row at all yet — entry_timestamp genuinely
    # not found, reason falls back to the pnl-based heuristic. Both must still be
    # persisted (`reason` is never left empty by that fallback).
    trades = db.get_closed_trades()
    assert trades[0]["entry_timestamp"] is None
    assert trades[0]["reason"]

    # Now insert a row that WOULD satisfy the entry_timestamp query if it ran again.
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO cycle_logs (timestamp, symbol, signal_type, state, leverage, price, outcome, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_ts.isoformat(), "ETH/USDC", "EXECUTED_BASE", "BASE_MODE", 1.0, 2500.0, "OPENED", ""),
        )
        conn.commit()

    trades_again = db.get_closed_trades()
    assert trades_again[0]["entry_timestamp"] is None, (
        "2nd call must NOT re-run the entry_timestamp lookup just because a genuine "
        "'not found' was cached as NULL — this is the exact regression that kept "
        "production pegged at 100% CPU after the first (incomplete) fix"
    )
