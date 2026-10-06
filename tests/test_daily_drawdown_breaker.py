"""
Tests for issue #95: an account-wide daily USD drawdown circuit breaker.
Disabled by default (`daily_drawdown_limit_usd` unset/0) — must be a no-op unless
explicitly configured.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor, is_maintenance_locked, set_maintenance_lock
from mms4tk.state_manager import MMS4State, MMS4StateManager


def _daemon(temp_db: LoggerDB, limit_usd: float = 0.0) -> MMS4Daemon:
    executor = OKXExecutor()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.fetch_positions = AsyncMock(return_value=[])
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    d = MMS4Daemon(executor=executor, state_manager=state_mgr, logger_db=temp_db, symbol="BTC/USDC")
    d.daily_drawdown_limit_usd = limit_usd
    return d


def _seed_closed_trade(db: LoggerDB, pnl_net_usd: float, when: datetime | None = None) -> None:
    ts = (when or datetime.now(timezone.utc)).isoformat()
    db.upsert_closed_trade(
        trade_id=f"t_{ts}_{pnl_net_usd}", timestamp=ts, symbol="BTC/USDC", side="LONG",
        entry_price=100.0, exit_price=99.0, amount=1.0, pnl_usd=pnl_net_usd, pnl_pct=-0.01,
        is_win=pnl_net_usd > 0, leverage=3.0, fee_usd=0.0, pnl_net_usd=pnl_net_usd,
    )


@pytest.fixture(autouse=True)
def _clear_maintenance_lock():
    set_maintenance_lock(False)
    yield
    set_maintenance_lock(False)


def test_disabled_by_default_is_a_noop(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, -10_000.0)  # huge loss, would trip any real threshold
    daemon = _daemon(db, limit_usd=0.0)

    daemon._check_daily_drawdown("BTC/USDC")

    assert not is_maintenance_locked()


def test_breach_engages_maintenance_lock(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, -60.0)
    _seed_closed_trade(db, -50.0)  # today's total: -110
    daemon = _daemon(db, limit_usd=100.0)

    daemon._check_daily_drawdown("BTC/USDC")

    assert is_maintenance_locked()
    trades = db.get_closed_trades()
    halts = [t for t in trades]  # sanity: closed_trades unaffected
    assert len(halts) == 2


def test_under_threshold_does_not_engage_lock(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, -40.0)  # under the -100 limit
    daemon = _daemon(db, limit_usd=100.0)

    daemon._check_daily_drawdown("BTC/USDC")

    assert not is_maintenance_locked()


def test_profitable_day_does_not_engage_lock(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, 5.0)
    daemon = _daemon(db, limit_usd=100.0)

    daemon._check_daily_drawdown("BTC/USDC")

    assert not is_maintenance_locked()


def test_yesterdays_losses_do_not_count_today(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    yesterday = datetime.now(timezone.utc).replace(hour=12) - __import__("datetime").timedelta(days=1)
    _seed_closed_trade(db, -500.0, when=yesterday)
    daemon = _daemon(db, limit_usd=100.0)

    daemon._check_daily_drawdown("BTC/USDC")

    assert not is_maintenance_locked()


def test_logs_a_cycle_event_on_breach(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, -150.0)
    daemon = _daemon(db, limit_usd=100.0)

    daemon._check_daily_drawdown("BTC/USDC")

    import sqlite3
    with sqlite3.connect(str(tmp_path / "logs.db")) as conn:
        row = conn.execute(
            "SELECT outcome, details FROM cycle_logs WHERE outcome = 'DAILY_DRAWDOWN_HALT'"
        ).fetchone()
    assert row is not None
    assert "150" in row[1]


def test_does_not_relog_or_rebreach_same_day(tmp_path: Path):
    """Once halted today, repeated calls must not spam logs or re-trigger the lock write."""
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, -150.0)
    daemon = _daemon(db, limit_usd=100.0)

    daemon._check_daily_drawdown("BTC/USDC")
    daemon._check_daily_drawdown("BTC/USDC")
    daemon._check_daily_drawdown("BTC/USDC")

    import sqlite3
    with sqlite3.connect(str(tmp_path / "logs.db")) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM cycle_logs WHERE outcome = 'DAILY_DRAWDOWN_HALT'"
        ).fetchone()[0]
    assert count == 1


def test_already_maintenance_locked_skips_check(tmp_path: Path):
    """If maintenance mode is already engaged for an unrelated reason (e.g. manual
    killall), the daily breaker must not try to re-derive/overwrite the lock reason."""
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, -150.0)
    daemon = _daemon(db, limit_usd=100.0)
    set_maintenance_lock(True, reason="Emergency killall")

    daemon._check_daily_drawdown("BTC/USDC")  # must not raise, must not touch cycle_logs

    import sqlite3
    with sqlite3.connect(str(tmp_path / "logs.db")) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM cycle_logs WHERE outcome = 'DAILY_DRAWDOWN_HALT'"
        ).fetchone()[0]
    assert count == 0


def test_get_todays_realized_pnl_usd_sums_only_today(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    _seed_closed_trade(db, 10.0)
    _seed_closed_trade(db, -3.0)
    yesterday = datetime.now(timezone.utc).replace(hour=12) - __import__("datetime").timedelta(days=1)
    _seed_closed_trade(db, -999.0, when=yesterday)

    assert db.get_todays_realized_pnl_usd() == pytest.approx(7.0)


def test_get_todays_realized_pnl_usd_empty_db_returns_zero(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    assert db.get_todays_realized_pnl_usd() == 0.0
