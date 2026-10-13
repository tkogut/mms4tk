"""A2 (2026-09-10): KPIs computed from `data_collection_epoch_start`.

`LoggerDB.get_kpi_summary()` aggregated the FULL closed_trades history with no way
to restrict it, so every headline KPI (realized_net_pnl_usd, total_fees_usd,
max_drawdown, average_rr, net_win_rate_pct) was diluted by trades taken before the
P2/P3/PLAN-011 correctness fixes — the exact history the per-symbol epoch marker
exists to fence off. These tests cover the new `since=` parameter, the
`/api/v1/stats` `since_epoch` block built on it, and the backward-compatibility
guarantee that omitting `since` changes nothing.
"""

import os

import pytest
from fastapi.testclient import TestClient

import mms4tk.api as api_module
from mms4tk.logger_db import LoggerDB

EPOCH = "2026-08-30T00:00:00+00:00"


def _seed(db: LoggerDB) -> None:
    """Two pre-epoch trades (both big winners) and two post-epoch ones (one small
    win, one loss). Chosen so EVERY aggregate differs across the boundary: trade
    count, gross, fees, net, win rate and drawdown all move."""
    # --- before the epoch: the "untrustworthy" regime ---
    db.upsert_closed_trade(
        trade_id="old_1", timestamp="2026-08-20T10:00:00+00:00", symbol="BTC/USDC",
        side="LONG", entry_price=50000.0, exit_price=55000.0, amount=1.0,
        pnl_usd=100.0, pnl_pct=0.10, is_win=True, fee_usd=1.0, pnl_net_usd=99.0,
    )
    db.upsert_closed_trade(
        trade_id="old_2", timestamp="2026-08-25T10:00:00+00:00", symbol="ETH/USDC",
        side="SHORT", entry_price=3000.0, exit_price=2900.0, amount=1.0,
        pnl_usd=50.0, pnl_pct=0.03, is_win=True, fee_usd=0.5, pnl_net_usd=49.5,
    )
    # --- at/after the epoch: the current regime ---
    db.upsert_closed_trade(
        trade_id="new_1", timestamp="2026-08-30T09:00:00+00:00", symbol="BTC/USDC",
        side="LONG", entry_price=78000.0, exit_price=78500.0, amount=1.0,
        pnl_usd=4.0, pnl_pct=0.006, is_win=True, fee_usd=0.08, pnl_net_usd=3.92,
    )
    db.upsert_closed_trade(
        trade_id="new_2", timestamp="2026-09-01T09:00:00+00:00", symbol="ETH/USDC",
        side="SHORT", entry_price=2450.0, exit_price=2500.0, amount=1.0,
        pnl_usd=-10.0, pnl_pct=-0.02, is_win=False, fee_usd=0.05, pnl_net_usd=-10.05,
    )


@pytest.fixture()
def seeded_db(tmp_path) -> LoggerDB:
    db = LoggerDB(os.path.join(tmp_path, "epoch_kpi.db"))
    _seed(db)
    return db


# ── LoggerDB.get_kpi_summary(since=...) ──────────────────────────────────────

def test_get_kpi_summary_since_aggregates_only_post_epoch_trades(seeded_db):
    """The core A2 guarantee: `since` must filter BEFORE aggregation, so every
    derived figure — not just the trade count — describes the epoch only."""
    kpi = seeded_db.get_kpi_summary(since=EPOCH)

    assert kpi["total_trades"] == 2, "pre-epoch trades must not be counted"
    assert kpi["realized_gross_pnl_usd"] == pytest.approx(-6.0)   # 4.0 - 10.0
    assert kpi["total_fees_usd"] == pytest.approx(0.13)           # 0.08 + 0.05
    assert kpi["realized_net_pnl_usd"] == pytest.approx(-6.13)    # 3.92 - 10.05
    assert kpi["win_rate"] == pytest.approx(50.0)
    assert kpi["net_win_rate_pct"] == pytest.approx(50.0)

    # And it must genuinely differ from all-time, otherwise the test proves nothing.
    all_time = seeded_db.get_kpi_summary()
    assert all_time["total_trades"] == 4
    assert all_time["realized_net_pnl_usd"] == pytest.approx(142.37)
    assert kpi["realized_net_pnl_usd"] != all_time["realized_net_pnl_usd"]


def test_get_kpi_summary_since_restarts_the_drawdown_curve_at_the_epoch(seeded_db):
    """Drawdown inside the epoch is measured from an equity curve that STARTS at the
    boundary — an epoch-window KPI anchored to a pre-epoch equity peak would report
    the old regime's peak as the current regime's high-water mark."""
    epoch_kpi = seeded_db.get_kpi_summary(initial_equity=100.0, since=EPOCH)
    all_time = seeded_db.get_kpi_summary(initial_equity=100.0)

    # In-epoch: peak 104.0 (after +4.0), trough 94.0 (after -10.0) -> 10.0 USD.
    assert epoch_kpi["max_drawdown_usd"] == pytest.approx(10.0)
    assert epoch_kpi["max_drawdown_pct"] == pytest.approx(10.0 / 104.0 * 100.0, abs=0.01)
    # All-time: the same -10.0 leg, but off a 254.0 peak -> a much smaller %.
    assert all_time["max_drawdown_usd"] == pytest.approx(10.0)
    assert all_time["max_drawdown_pct"] < epoch_kpi["max_drawdown_pct"]


def test_get_kpi_summary_since_boundary_is_inclusive(seeded_db):
    """`timestamp >= since`, not `>`: a trade landing exactly on the epoch belongs to
    the new regime (the epoch marks where it BEGINS)."""
    on_boundary = seeded_db.get_kpi_summary(since="2026-08-30T09:00:00+00:00")
    assert on_boundary["total_trades"] == 2, "the trade at exactly `since` must be kept"

    just_after = seeded_db.get_kpi_summary(since="2026-08-30T09:00:01+00:00")
    assert just_after["total_trades"] == 1


def test_get_kpi_summary_since_tolerates_mixed_timestamp_formats(seeded_db):
    """Rows carry microseconds + offset; an operator-set epoch is usually
    minute-precision and may use a bare Z. Comparison is datetime-based, so the
    formats must not matter."""
    assert seeded_db.get_kpi_summary(since="2026-08-30T00:00:00Z")["total_trades"] == 2
    assert seeded_db.get_kpi_summary(since="2026-08-30T00:00:00")["total_trades"] == 2
    assert seeded_db.get_kpi_summary(since="2026-08-30T00:00:00.000000+00:00")["total_trades"] == 2


def test_get_kpi_summary_since_after_all_trades_returns_the_empty_shape(seeded_db):
    empty = seeded_db.get_kpi_summary(since="2027-01-01T00:00:00+00:00")
    assert empty["total_trades"] == 0
    assert empty["realized_net_pnl_usd"] == 0.0
    assert empty["average_rr"] == 0.0
    # Same keys as a populated summary — no caller can KeyError on the empty case.
    assert set(empty) <= set(seeded_db.get_kpi_summary())


# ── backward compatibility ───────────────────────────────────────────────────

def test_get_kpi_summary_without_since_is_byte_for_byte_unchanged(seeded_db):
    """Regression guard for every pre-A2 caller (api.py, trade_sync tests): omitting the parameter, and passing it as None explicitly,
    must both mean 'the whole history' and produce an identical dict."""
    implicit = seeded_db.get_kpi_summary()
    explicit_none = seeded_db.get_kpi_summary(since=None)
    assert implicit == explicit_none
    assert implicit["total_trades"] == 4

    # Positional `initial_equity` must keep working — `since` was appended, not
    # inserted ahead of it.
    assert seeded_db.get_kpi_summary(100.0) == seeded_db.get_kpi_summary(initial_equity=100.0)


# ── /api/v1/stats `since_epoch` block ────────────────────────────────────────

@pytest.fixture()
def api_with_db(seeded_db):
    """Point the API at the seeded DB and at a known active-symbol set, restoring
    both afterwards — `configured_symbols_store` is a module-level global shared
    with every other test in the session."""
    orig_db = api_module.logger_db
    orig_symbols = dict(api_module.configured_symbols_store)
    api_module.logger_db = seeded_db
    api_module.configured_symbols_store.clear()
    api_module.configured_symbols_store.update({
        "BTC/USDC": {"is_active": True, "allocation_usd": 50.0},
        "ETH/USDC": {"is_active": True, "allocation_usd": 50.0},
        "SOL/USDC": {"is_active": False, "allocation_usd": 0.0},
    })
    try:
        yield seeded_db
    finally:
        api_module.logger_db = orig_db
        api_module.configured_symbols_store.clear()
        api_module.configured_symbols_store.update(orig_symbols)


def test_stats_endpoint_reports_epoch_kpis_alongside_all_time(api_with_db):
    """End-to-end: with an epoch set, `/api/v1/stats` keeps its all-time top-level
    numbers (unchanged contract) and adds the epoch-restricted block."""
    api_with_db.set_setting("data_collection_epoch_start:BTC/USDC", EPOCH)
    api_with_db.set_setting("data_collection_epoch_start:ETH/USDC", "2026-09-01T00:00:00+00:00")

    with TestClient(api_module.app) as client:
        res = client.get("/api/v1/stats")
    assert res.status_code == 200
    data = res.json()

    # Top-level stays all-time — no existing dashboard number moves.
    assert data["total_trades"] == 4
    assert data["realized_net_pnl_usd"] == pytest.approx(142.37)

    block = data["since_epoch"]
    # EARLIEST epoch among ACTIVE symbols wins, so no active symbol's own window
    # gets silently truncated.
    assert block["epoch_start"] == EPOCH
    assert block["per_symbol_epochs"] == {
        "BTC/USDC": EPOCH,
        "ETH/USDC": "2026-09-01T00:00:00+00:00",
    }, "suspended symbols must not appear, and must not be able to win the min()"
    assert block["total_trades"] == 2
    assert block["realized_gross_pnl_usd"] == pytest.approx(-6.0)
    assert block["total_fees_usd"] == pytest.approx(0.13)
    assert block["realized_net_pnl_usd"] == pytest.approx(-6.13)
    assert block["net_win_rate_pct"] == pytest.approx(50.0)


def test_stats_since_epoch_equals_all_time_when_no_active_symbol_has_an_epoch(api_with_db):
    """With no epoch set anywhere, the block is present with `epoch_start: null` and
    mirrors the all-time figures — a stable shape beats a field that appears and
    disappears under the dashboard."""
    with TestClient(api_module.app) as client:
        res = client.get("/api/v1/stats")
    assert res.status_code == 200
    block = res.json()["since_epoch"]

    assert block["epoch_start"] is None
    assert block["per_symbol_epochs"] == {"BTC/USDC": None, "ETH/USDC": None}
    assert block["total_trades"] == 4
    assert block["realized_net_pnl_usd"] == pytest.approx(142.37)


def test_stats_pnl_alias_route_carries_the_same_block(api_with_db):
    """`/api/v1/stats` and `/api/v1/stats/pnl` are the same handler — the alias must
    not silently serve a stale shape."""
    api_with_db.set_setting("data_collection_epoch_start:BTC/USDC", EPOCH)
    with TestClient(api_module.app) as client:
        a = client.get("/api/v1/stats").json()
        b = client.get("/api/v1/stats/pnl").json()
    assert a["since_epoch"] == b["since_epoch"]
    assert a["since_epoch"]["epoch_start"] == EPOCH


def test_earliest_epoch_ignores_unparseable_values(api_with_db):
    """A malformed epoch string must never win the min() and quietly widen the
    window back over pre-fix history."""
    assert api_module._earliest_epoch({"A": "not-a-timestamp", "B": EPOCH}) == EPOCH
    assert api_module._earliest_epoch({"A": None, "B": None}) is None
    assert api_module._earliest_epoch({}) is None
