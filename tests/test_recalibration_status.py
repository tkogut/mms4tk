"""PLAN-010 Phase 1: data-readiness tracking for the trend-filter recalibration
mechanism.

`find_duplicate_chains` / `count_clean_closed_trades` promote the connected-components
detection used ad hoc during the 2026-08-29 manual DB cleanup into tested, named
LoggerDB methods, so the "how much clean evidence exists for this symbol" question has
one authoritative answer instead of a throwaway script re-derived each time.
"""
import pytest

from mms4tk.logger_db import LoggerDB


@pytest.fixture
def db(tmp_path):
    return LoggerDB(db_path=str(tmp_path / "recal.db"))


def _insert(db, trade_id, ts, symbol="BTC/USDC", side="SHORT", entry=80000.0, exit_=79900.0, amount=2.0):
    pnl_pct = (entry - exit_) / entry if side == "SHORT" else (exit_ - entry) / entry
    db.upsert_closed_trade(
        trade_id=trade_id, timestamp=ts, symbol=symbol, side=side,
        entry_price=entry, exit_price=exit_, amount=amount,
        pnl_usd=pnl_pct * entry * amount, pnl_pct=pnl_pct, is_win=pnl_pct > 0,
    )


def test_find_duplicate_chains_groups_near_identical_bursts(db):
    """Same symbol/side, ~equal entry price, closed within seconds -> one chain."""
    _insert(db, "t1", "2026-08-16T09:00:00+00:00", entry=80000.0, exit_=79950.0)
    _insert(db, "t2", "2026-08-16T09:00:05+00:00", entry=80001.0, exit_=79800.0)
    _insert(db, "t3", "2026-08-16T09:00:12+00:00", entry=79999.5, exit_=79700.0)
    # Unrelated, well-separated trade -> not part of any chain.
    _insert(db, "solo", "2026-08-17T12:00:00+00:00", entry=81000.0, exit_=80500.0)

    chains = db.find_duplicate_chains(symbol="BTC/USDC")
    assert len(chains) == 1
    assert set(chains[0]) == {"t1", "t2", "t3"}


def test_find_duplicate_chains_respects_time_window(db):
    """Same price, but minutes apart -> genuinely separate trades, not a chain."""
    _insert(db, "a", "2026-08-16T09:00:00+00:00", entry=80000.0, exit_=79950.0)
    _insert(db, "b", "2026-08-16T09:10:00+00:00", entry=80000.0, exit_=79950.0)

    chains = db.find_duplicate_chains(symbol="BTC/USDC", time_window_seconds=60.0)
    assert chains == []


def test_find_duplicate_chains_respects_symbol_and_side(db):
    """Same timestamp/price but different side, or different (normalized) symbol,
    must not be linked."""
    _insert(db, "long1", "2026-08-16T09:00:00+00:00", side="LONG", entry=80000.0, exit_=80100.0)
    _insert(db, "short1", "2026-08-16T09:00:02+00:00", side="SHORT", entry=80000.0, exit_=79900.0)
    _insert(db, "eth1", "2026-08-16T09:00:00+00:00", symbol="ETH-USD_UM_XPERP-310404",
            side="SHORT", entry=80000.0, exit_=79900.0)

    chains = db.find_duplicate_chains()
    assert chains == []


def test_count_clean_closed_trades_excludes_degenerate_rows(db):
    """entry == exit (pre trade_sync-fix era) rows must not count as clean evidence."""
    _insert(db, "clean1", "2026-08-16T09:00:00+00:00", entry=80000.0, exit_=79800.0)
    _insert(db, "degenerate1", "2026-08-16T10:00:00+00:00", entry=80000.0, exit_=80000.0)

    assert db.count_clean_closed_trades("BTC/USDC") == 1


def test_count_clean_closed_trades_counts_a_duplicate_chain_once(db):
    """A 3-row duplicate-execution chain represents ONE real event -> contributes 1
    to the clean count, not 3 and not 0."""
    _insert(db, "t1", "2026-08-16T09:00:00+00:00", entry=80000.0, exit_=79950.0)
    _insert(db, "t2", "2026-08-16T09:00:05+00:00", entry=80001.0, exit_=79800.0)
    _insert(db, "t3", "2026-08-16T09:00:12+00:00", entry=79999.5, exit_=79700.0)
    _insert(db, "solo", "2026-08-17T12:00:00+00:00", entry=81000.0, exit_=80500.0)

    assert db.count_clean_closed_trades("BTC/USDC") == 2  # chain(=1) + solo(=1)


def test_count_clean_closed_trades_is_per_symbol(db):
    _insert(db, "btc1", "2026-08-16T09:00:00+00:00", symbol="BTC/USDC", entry=80000.0, exit_=79800.0)
    _insert(db, "eth1", "2026-08-16T09:00:00+00:00", symbol="ETH/USDC", entry=2400.0, exit_=2380.0)

    assert db.count_clean_closed_trades("BTC/USDC") == 1
    assert db.count_clean_closed_trades("ETH/USDC") == 1
    assert db.count_clean_closed_trades("SOL/USDC") == 0


def test_count_clean_closed_trades_matches_raw_okx_instid_rows(db):
    """Regression (found in code review, 2026-08-29): production `closed_trades.symbol`
    holds the raw OKX instId for every real trade_sync-authoritative row (confirmed live:
    130/130 rows), NEVER the clean pair a caller naturally passes. A plain string `==`
    match returns 0 for every real symbol in production — this must use normalize_symbol()
    like `delete_provisional_closed_trade` (PR #11) already does."""
    _insert(db, "real1", "2026-08-16T09:00:00+00:00", symbol="BTC-USD_UM_XPERP-310404",
            entry=80000.0, exit_=79800.0)
    _insert(db, "real2", "2026-08-17T09:00:00+00:00", symbol="BTC-USD_UM_XPERP-310404",
            entry=81000.0, exit_=80500.0)

    assert db.count_clean_closed_trades("BTC/USDC") == 2


def test_find_duplicate_chains_matches_raw_okx_instid_rows(db):
    _insert(db, "r1", "2026-08-16T09:00:00+00:00", symbol="BTC-USD_UM_XPERP-310404",
            entry=80000.0, exit_=79950.0)
    _insert(db, "r2", "2026-08-16T09:00:05+00:00", symbol="BTC-USD_UM_XPERP-310404",
            entry=80001.0, exit_=79800.0)

    chains = db.find_duplicate_chains(symbol="BTC/USDC")
    assert len(chains) == 1
    assert set(chains[0]) == {"r1", "r2"}


def test_count_clean_closed_trades_degenerate_row_does_not_bridge_a_false_chain(db):
    """A degenerate row (entry==exit) sitting in time/price range of two otherwise
    UNRELATED clean trades must not transitively chain them together once removed —
    exclude_trade_ids filters it out BEFORE chain detection, not after, precisely to
    prevent this."""
    # price_tolerance_pct=0.001 -> ~80 apart is the boundary near 80000. clean_a and
    # clean_b sit ~145 apart (outside tolerance of EACH OTHER directly); the
    # degenerate row sits ~75/70 from each (within tolerance of both) -- a bridge in
    # price only reachable by going through it.
    _insert(db, "clean_a", "2026-08-16T09:00:00+00:00", entry=80000.0, exit_=79000.0)
    _insert(db, "degenerate_bridge", "2026-08-16T09:00:05+00:00", entry=80075.0, exit_=80075.0)
    _insert(db, "clean_b", "2026-08-16T09:00:10+00:00", entry=80145.0, exit_=81000.0)

    # Without the fix, clean_a and clean_b would be seen as one chain via the bridge,
    # collapsing to 1 clean trade instead of 2.
    assert db.count_clean_closed_trades("BTC/USDC") == 2
