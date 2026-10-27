"""Regression tests for the 2026-10-27 fix to the favorable-price addon gate
(Issue #137, `addon_requires_favorable_price_enabled`, `daemon.py` ~line 2280).

Live evidence (okx_orders base-fill vs add-fill reconciliation): of 19 addons
executed since the gate went live, 15 were net-underwater after realistic
round-trip fees, and one leaked even before fees from signal-vs-fill slippage.
Root cause: the gate compared the addon signal price against the pinned
`base_entry_price` anchor only, with no fee allowance.

Fix: prefer the position's actual live fill price (`match_entry`) over the
anchor when available (safe here because the Single Addon Guard above this
code path guarantees no addon has executed yet, so `match_entry` is still the
untouched base fill), and require the signal price to clear that reference by
at least `ADDON_FAVORABLE_FEE_MARGIN_PCT` (reuses
`state_manager.SCOUT_MIN_GROSS_PNL_PCT`, the same measured round-trip fee rate
used elsewhere in this codebase), not just be nominally on the favorable side.

Harness mirrors tests/test_max_margin_allocation.py.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mms4tk.daemon import MMS4Daemon, ADDON_FAVORABLE_FEE_MARGIN_PCT
from mms4tk.analytics import SignalResult
from mms4tk.logger_db import LoggerDB

pytestmark = pytest.mark.usefixtures("empty_symbols_store")

SYMBOL = "BTC/USDC"


def _make_daemon(tmp_path, *, base_entry_pinned: float, live_fill_price: float, addon_signal_price: float):
    """Builds a daemon with a BASE_MODE LONG position already open on `SYMBOL`,
    the favorable-price gate enabled, and a pending HIGH_RISK_ADD_LONG signal at
    `addon_signal_price`.

    `base_entry_pinned` seeds the persisted `base_entry_price` snapshot (the
    anchor); `live_fill_price` is the position's current exchange `entryPrice`/
    `avgPx` (`match_entry`) reported by `fetch_positions` this cycle — these
    differ to simulate the signal-vs-fill slippage scenario (case a); equal
    values simulate the normal no-slippage case (cases b/c/d).
    """
    executor = MagicMock()
    executor.initialize = AsyncMock()
    executor.close = AsyncMock()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.execute_order = AsyncMock(return_value={"id": "order-addon"})
    executor.fetch_positions = AsyncMock(return_value=[{
        "symbol": SYMBOL,
        "side": "long",
        "contracts": 1.0,
        "entryPrice": live_fill_price,
        "notional": 7.0,
        "info": {"posSide": "long", "avgPx": str(live_fill_price)},
    }])

    state_manager = MagicMock()
    state_manager.leverage = 1.0
    state_manager.current_state.value = "BASE_MODE"
    state_manager.is_high_risk_add_allowed.return_value = True

    signal_engine = MagicMock()
    signal_engine.evaluate_signals.return_value = SignalResult(
        signal_type="HIGH_RISK_ADD_LONG",
        price=addon_signal_price,
        stop_loss=0.0,
        leverage=1.0,
        is_allowed=True,
        reason="High Risk Add LONG signal",
    )

    logger_db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    # Pin base_entry_price (first-insert-wins — see save_position_snapshot's docstring).
    logger_db.save_position_snapshot(
        symbol=SYMBOL, side="long", contracts=1.0, entry_price=base_entry_pinned, last_price=base_entry_pinned,
    )
    logger_db.set_setting(f"addon_requires_favorable_price_enabled:{SYMBOL}", "true")

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_manager,
        signal_engine=signal_engine,
        logger_db=logger_db,
        symbol=SYMBOL,
        trade_amount_usd=10.0,
    )
    return daemon, executor


def test_margin_reused_from_state_manager_fee_constant():
    from mms4tk.state_manager import SCOUT_MIN_GROSS_PNL_PCT
    assert ADDON_FAVORABLE_FEE_MARGIN_PCT == SCOUT_MIN_GROSS_PNL_PCT


def test_a_favorable_vs_anchor_but_not_vs_fill_is_now_blocked(tmp_path, caplog):
    """Previously leaked: a signal price that clears the fee-covering margin
    against the stale $50,000 anchor (threshold $50,050.015) but NOT against the
    position's real live fill ($50,200, simulating slippage — threshold
    $50,250.20) is now blocked. Chosen so the anchor-vs-fill price switch alone
    determines the outcome, independent of the fee-margin change (both anchor
    and fill thresholds here apply the same margin)."""
    anchor = 50000.0
    fill = 50200.0
    signal_price = 50100.0
    assert signal_price > anchor * (1.0 + ADDON_FAVORABLE_FEE_MARGIN_PCT)  # clears the (stale) anchor threshold
    assert signal_price < fill * (1.0 + ADDON_FAVORABLE_FEE_MARGIN_PCT)  # does NOT clear the real fill threshold

    daemon, executor = _make_daemon(
        tmp_path, base_entry_pinned=anchor, live_fill_price=fill, addon_signal_price=signal_price,
    )
    with caplog.at_level("WARNING"):
        asyncio.run(daemon.execute_cycle())
    executor.execute_order.assert_not_called()
    assert "Favorable-Price Addon Gate" in caplog.text


def test_b_add_that_clears_fee_margin_is_allowed(tmp_path):
    threshold = 50000.0 * (1.0 + ADDON_FAVORABLE_FEE_MARGIN_PCT)
    daemon, executor = _make_daemon(
        tmp_path, base_entry_pinned=50000.0, live_fill_price=50000.0, addon_signal_price=threshold + 50.0,
    )
    asyncio.run(daemon.execute_cycle())
    executor.execute_order.assert_called_once()


def test_c_nominally_favorable_but_within_fee_margin_is_blocked(tmp_path, caplog):
    threshold = 50000.0 * (1.0 + ADDON_FAVORABLE_FEE_MARGIN_PCT)
    nominally_favorable_price = 50000.0 + (threshold - 50000.0) / 2.0  # > 50000, < threshold
    daemon, executor = _make_daemon(
        tmp_path, base_entry_pinned=50000.0, live_fill_price=50000.0, addon_signal_price=nominally_favorable_price,
    )
    with caplog.at_level("WARNING"):
        asyncio.run(daemon.execute_cycle())
    executor.execute_order.assert_not_called()
    assert "Favorable-Price Addon Gate" in caplog.text


def test_d_clearly_favorable_add_still_allowed(tmp_path):
    """Existing passing-case behavior preserved: a genuinely favorable add, well
    past the fee margin, is still allowed — the gate should not regress the
    14/15-window positive evidence behind it."""
    daemon, executor = _make_daemon(
        tmp_path, base_entry_pinned=50000.0, live_fill_price=50000.0, addon_signal_price=51000.0,
    )
    asyncio.run(daemon.execute_cycle())
    executor.execute_order.assert_called_once()
