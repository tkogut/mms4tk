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
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Point the whole test session at a throwaway SQLite file BEFORE any mms4tk
# module is imported. logger_db resolves DEFAULT_DB_PATH at import time, and
# mms4tk.api builds a module-level LoggerDB() singleton from it — so without
# this, tests write live trading settings straight into the production SSOT
# (.agents/swarm/logs.db). Verified in practice: a test-suite run left
# tp_mode/tp_auto_regime rows tagged 'test_cleanup' in the real database, which
# on the VPS would silently drop a running DYNAMIC_MIDLINE regime to CLASSIC.
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "MMS4_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="mms4tk-tests-"), "logs.db")
)

import pytest

from mms4tk.okx_executor import OKXExecutor, _generate_mock_candles


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
) -> List[Any]:
    # Reuses the existing generator from okx_executor.py — exactly what the real
    # method already returns in its except branch after a failed live request, so
    # tests expecting "some" sensible candles keep the same effective data shape,
    # just without the slow and unreliable network round-trip in front of it.
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
    from mms4tk.api import logger_db as api_logger_db, daemon as api_daemon
    
    default_env = {
        "MMS4_ENGINE_MODE": "CLASSIC",
        "MMS4_ADAPTIVE_VR_HIGH": "1.6",
        "MMS4_ADAPTIVE_VR_LOW": "0.8",
        "MMS4_TIME_LOCK_START": "13:30",
        "MMS4_TIME_LOCK_END": "14:30",
    }
    
    # Snapshot before test
    for k, v in default_env.items():
        os.environ[k] = v
    if api_logger_db:
        api_logger_db.set_settings_bulk({
            "engine_mode": "CLASSIC",
            "adaptive_vr_high": "1.6",
            "adaptive_vr_low": "0.8",
            "time_lock_start": "13:30",
            "time_lock_end": "14:30",
            "tp_mode": "CLASSIC",
            "tp_auto_regime": "false",
        }, updated_by="test_isolation_init")
    api_daemon.total_max_portfolio_margin_usd = 0.0

    yield

    # Restore after test
    for k, v in default_env.items():
        os.environ[k] = v
    if api_logger_db:
        api_logger_db.set_settings_bulk({
            "engine_mode": "CLASSIC",
            "adaptive_vr_high": "1.6",
            "adaptive_vr_low": "0.8",
            "time_lock_start": "13:30",
            "time_lock_end": "14:30",
            "tp_mode": "CLASSIC",
            "tp_auto_regime": "false",
        }, updated_by="test_isolation_cleanup")
    api_daemon.total_max_portfolio_margin_usd = 0.0

