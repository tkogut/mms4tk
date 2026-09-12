"""
Global pytest fixtures for the MMS4 test suite.

Session-wide policy: no test ever makes a real network call to the OKX REST API.
OKXExecutor.__init__ always builds a real ccxt.okx(...) client regardless of whether
OKX credentials are configured, and several of its methods make real network calls even
in that "Mock Mode" (see the docstrings in okx_executor.py). In an environment without
reliable network access those calls hang until ccxt's own retry/timeout chain is
exhausted, stalling every subsequent test one after another.

This fixture patches the CLASS mms4tk.okx_executor.OKXExecutor, not any single
instance, because OKXExecutor() is built in several independent places across the
suite: the module-level shared singleton in api.py (constructed before this fixture can
possibly run) AND fresh, directly-constructed instances inside individual test files
(test_daemon.py, test_multi_symbol.py, and others). Patching only one instance does not
protect the rest. A class-level patch protects all of them — including instances created
BEFORE the fixture runs, because attribute lookup on an instance falls through to the
class when there is no instance-level override.
"""

# P3 conftest hygiene (2026-09-10): confirmed live risk — running this suite via
# `docker exec antigravity_core pytest ...` on the production container would have
# every test import `mms4tk.api`'s module-level `logger_db`/`daemon` singletons
# pointed at the REAL production DB (`LoggerDB()`'s default resolves
# `/app/.agents/swarm/logs.db` when `MMS4_DB_PATH` isn't already overridden), and
# `_isolate_system_settings` below unconditionally writes 5 global settings and
# calls `set_maintenance_lock(False)` there. See `.agents/MEMORY.md` for the
# operational rule (don't run pytest that way at all) — this is the belt-and-
# braces code fix the fixture's own prior docstring flagged as "a separate,
# larger piece of work, not in scope" for the earlier P6.1 review: force BOTH
# the DB path AND the maintenance-lock file locations to an isolated per-session
# temp directory, unconditionally, regardless of which environment variables or
# real files already exist on the machine running pytest. This MUST run before
# any `mms4tk.*` module is imported anywhere in the test session (DEFAULT_DB_PATH
# is resolved once, at `logger_db` module import time) — hence it is the very
# first thing this file does, ahead of even the stdlib imports below.
import atexit as _atexit
import os as _os
import shutil as _shutil
import tempfile as _tempfile

_TEST_STATE_DIR = _tempfile.mkdtemp(prefix="mms4tk_pytest_")
_os.environ["MMS4_DB_PATH"] = _os.path.join(_TEST_STATE_DIR, "test_logs.db")
_atexit.register(_shutil.rmtree, _TEST_STATE_DIR, ignore_errors=True)

import time
from typing import Any, Dict, List, Optional

import pytest

from mms4tk import okx_executor as _okx_executor_module
from mms4tk.okx_executor import OKXExecutor, _generate_mock_candles

# Redirect every maintenance-lock path this process will ever check/write to a
# single file under the isolated temp dir above — `set_maintenance_lock`/
# `is_maintenance_locked` iterate this exact list, so replacing its contents
# (not just appending) is what keeps them off `/app/.agents/swarm/MAINTENANCE_LOCK`
# and every other real location a production deploy might use.
_okx_executor_module.MAINTENANCE_LOCK_PATHS[:] = [
    _os.path.join(_TEST_STATE_DIR, "MAINTENANCE_LOCK")
]


# ---------------------------------------------------------------------------
# Fakes. Each is assigned on the CLASS, so each MUST take `self` as its first
# parameter — Python binds it automatically through the descriptor protocol when
# called via an instance (e.g. `some_executor.fetch_candles(...)`). Omitting
# `self` here raises TypeError only on the first real call of that method in
# some test, which is an easy silent mistake to introduce.
# ---------------------------------------------------------------------------

async def _fake_initialize(self) -> None:
    return None


async def _fake_close(self) -> None:
    return None


async def _fake_verify_symbol_with_okx(self, symbol: str, *args, **kwargs) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "okx_verified": True,
        "autotrade_permitted": True,
        "okx_status": "AUTOTRADE_READY",
        "last_price": 100.0,
        "reason": "stubbed by tests/conftest.py — no live OKX call",
    }


async def _fake_fetch_candles(
    self,
    symbol: Optional[str] = None,
    timeframe: str = "1m",
    limit: int = 100,
    allow_mock_fallback: bool = True,
) -> List[Any]:
    # Reuses the existing generator from okx_executor.py — exactly what the real
    # method already returns in its except branch after a failed live request, so
    # tests expecting "some" sensible candles keep the same effective data shape,
    # just without the slow and unreliable network round-trip in front of it.
    # `allow_mock_fallback` is accepted (not exercised) purely for signature parity
    # with the real method (okx_executor.py) — this fake never fails, so it never
    # needs to raise regardless of the flag's value.
    return _generate_mock_candles(count=limit)


async def _fake_discover_eea_xperp_symbol(self, symbol: str = "BTC") -> Optional[str]:
    return None


async def _fake_execute_order(
    self,
    symbol: str,
    side: str,
    amount: float,
    stop_loss_pct: float = 0.02,
    leverage: float = 1.0,
    price: Optional[float] = None,
) -> Dict[str, Any]:
    # Same shape as the real method's built-in "no credentials" branch — same keys,
    # same "executed" status and mock flag — so callers in daemon.py reading
    # .get("id")/.get("status") behave exactly as they do today without OKX keys.
    return {
        "id": f"mock_order_test_{int(time.time() * 1000)}",
        "symbol": symbol,
        "side": side.lower(),
        "amount": amount,
        "leverage": leverage,
        "stop_loss_pct": stop_loss_pct,
        "status": "executed",
        "mock": True,
    }


async def _fake_reduce_position(
    self,
    symbol: str,
    side: str,
    reduction_pct: float = 0.50,
    reason: str = "STALE_SPLIT",
) -> Dict[str, Any]:
    # Mirrors what the real method already returns in this environment:
    # fetch_positions() is credentials-gated and already returns [] without OKX keys,
    # so matching_contracts is always 0 and the real reduce_position falls through to
    # exactly this "no_position" result. All this fake actually removes is the one
    # unconditional discover_eea_xperp_symbol() network call preceding that logic.
    # daemon.py::_close_succeeded() treats "no_position" as False — correctly, since
    # no position was in fact closed.
    return {"status": "no_position", "symbol": symbol, "side": side.lower()}


_PATCHED_METHODS = {
    "initialize": _fake_initialize,
    "close": _fake_close,
    "verify_symbol_with_okx": _fake_verify_symbol_with_okx,
    "fetch_candles": _fake_fetch_candles,
    "discover_eea_xperp_symbol": _fake_discover_eea_xperp_symbol,
    "execute_order": _fake_execute_order,
    "reduce_position": _fake_reduce_position,
}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_real_okx_network: opt this test out of the autouse "
        "_no_real_okx_network fixture (tests/conftest.py). Use ONLY for a deliberate "
        "live integration test — such a test makes real network calls to OKX and may "
        "hang or fail in environments without network access.",
    )


@pytest.fixture(autouse=True)
def _no_real_okx_network(request, monkeypatch):
    """
    Session-wide default: stop every OKXExecutor instance from making real network
    calls to OKX, by patching mms4tk.okx_executor.OKXExecutor at class level.
    Applied automatically to every test.

    Escape hatch: mark a test @pytest.mark.allow_real_okx_network to skip this
    patching for a deliberate live integration test.
    """
    if request.node.get_closest_marker("allow_real_okx_network"):
        return
    for name, fake in _PATCHED_METHODS.items():
        monkeypatch.setattr(OKXExecutor, name, fake)


@pytest.fixture(autouse=True)
def _isolate_system_settings():
    """Ensure clean default environment and database settings for every test."""
    import os
    from mms4tk.api import logger_db as api_logger_db, daemon as api_daemon, configured_symbols_store
    from mms4tk.okx_executor import set_maintenance_lock
    
    default_env = {
        "MMS4_ENGINE_MODE": "CLASSIC",
        "MMS4_ADAPTIVE_VR_HIGH": "1.6",
        "MMS4_ADAPTIVE_VR_LOW": "0.8",
        "MMS4_TIME_LOCK_START": "13:30",
        "MMS4_TIME_LOCK_END": "14:30",
        "MMS4_MAINTENANCE_MODE": "false",
    }
    
    # Snapshot before test
    for k, v in default_env.items():
        os.environ[k] = v
    syms_snapshot = {k: dict(v) if isinstance(v, dict) else v for k, v in configured_symbols_store.items()}
    set_maintenance_lock(False)
    # Audit P6.1 (2026-09-08): full-table snapshot, in ADDITION to the 5 fixed keys
    # below — a safety net for system_settings keys this fixture has no fixed list
    # for, including per-symbol-qualified ones (e.g.
    # "stale_candles_threshold:BTC/USDC"). Without this, a test writing such a key
    # pollutes the real DB (which docker-compose mounts into the live daemon) for
    # every test/process that opens this same db file afterward — found by
    # independent review while this same ticket's own new tests did exactly that.
    #
    # NOT covered by this snapshot/restore, by design, pre-dating this fixture's
    # extension (commit 3a9c688): the 5 GLOBAL keys below
    # (engine_mode/adaptive_vr_high/adaptive_vr_low/time_lock_start/time_lock_end)
    # are unconditionally re-forced to these canonical values both before AND after
    # every test, regardless of what the snapshot contained — so a test that
    # legitimately changes one of these 5 (e.g. to exercise CLASSIC mode) does NOT
    # leak it forward, but conversely the snapshot/restore logic below can never
    # protect a value set for these 5 specific keys either; the DB row for each is
    # always whatever this fixture last wrote. Harmless now: this DB is the
    # isolated per-session temp one this file forces at import time (see the top
    # of this module) — the "real, currently-live effect on the shared repo-root
    # logs.db (docker-mounted)" a second review flagged here (2026-09-10) no
    # longer applies to a normal test run; it only would if something else in the
    # process overrode MMS4_DB_PATH again after this file's own override.
    settings_snapshot = dict(api_logger_db.get_all_settings()) if api_logger_db else {}
    if api_logger_db:
        api_logger_db.set_settings_bulk({
            "engine_mode": "CLASSIC",
            "adaptive_vr_high": "1.6",
            "adaptive_vr_low": "0.8",
            "time_lock_start": "13:30",
            "time_lock_end": "14:30",
        }, updated_by="test_isolation_init")
    api_daemon.total_max_portfolio_margin_usd = 0.0

    yield

    # Restore after test
    for k, v in default_env.items():
        os.environ[k] = v
    set_maintenance_lock(False)
    configured_symbols_store.clear()
    configured_symbols_store.update({k: dict(v) if isinstance(v, dict) else v for k, v in syms_snapshot.items()})
    if api_logger_db:
        # General restore first: undo anything this test wrote, known or not.
        after_settings = api_logger_db.get_all_settings()
        for key in after_settings.keys() - settings_snapshot.keys():
            api_logger_db.delete_setting(key)
        for key, val in settings_snapshot.items():
            if after_settings.get(key) != val:
                api_logger_db.set_setting(key, val, updated_by="test_isolation_cleanup")
        # Then re-force the 5 fixed keys to canonical defaults regardless of what the
        # snapshot happened to contain — guarantees the NEXT test starts from the same
        # known state this fixture has always guaranteed for these specific keys.
        api_logger_db.set_settings_bulk({
            "engine_mode": "CLASSIC",
            "adaptive_vr_high": "1.6",
            "adaptive_vr_low": "0.8",
            "time_lock_start": "13:30",
            "time_lock_end": "14:30",
        }, updated_by="test_isolation_cleanup")
    api_daemon.total_max_portfolio_margin_usd = 0.0

