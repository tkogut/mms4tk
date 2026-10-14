"""E3 of #107: a symbol leaves SCOUT_MODE after K consecutive CLEAN full closes.

Semantics (decisions 1-5 of #112): only FORCE_CLOSE / MIDLINE_INVALIDATION count; "clean" is
gross pnl_percent above the measured round-trip cost; a net-losing counting close and a
STOP_LOSS reset the run; FORCE_CLOSE_BE / BE_ESCAPE / CLOSED_EXTERNAL / partial closes are
neutral; a full take-profit still exits immediately.
"""
import asyncio
import os
import tempfile
import time

import pytest

from mms4tk import backtest_engine as be
from mms4tk.analytics import IndicatorParams
from mms4tk.logger_db import LoggerDB
from mms4tk.state_manager import (
    SCOUT_EXIT_K,
    SCOUT_MIN_GROSS_PNL_PCT,
    MMS4State,
    MMS4StateManager,
)

BTC, XAG = "BTC/USDC", "XAG/USDC"
CLEAN = SCOUT_MIN_GROSS_PNL_PCT * 2          # gross above the round-trip cost => net > 0
NET_LOSS = SCOUT_MIN_GROSS_PNL_PCT / 2       # a gross gain that does not cover the fees
LOSS = -0.005


def _scout_sm(db=None, symbol=XAG):
    sm = MMS4StateManager(logger_db=db, trading_symbol=BTC)
    sm.set_symbol_state(symbol, MMS4State.SCOUT_MODE, reason="test: prior stop")
    return sm


def _close(sm, reason, pct, symbol=XAG, sl_percent=0.0):
    return sm.finalize_close(
        symbol=symbol, side="short", reason=reason, entry_price=100.0,
        exit_price=100.0 * (1 - pct), amount=1.0, pnl=pct, pnl_percent=pct, sl_percent=sl_percent,
    )


def test_defaults_match_the_agreed_parameters():
    assert SCOUT_EXIT_K == 3
    assert SCOUT_MIN_GROSS_PNL_PCT == pytest.approx(be.ROUND_TRIP_FEE_PCT)


def test_k_clean_force_closes_in_a_row_exit_scout():
    sm = _scout_sm()
    for i in range(SCOUT_EXIT_K - 1):
        _close(sm, "FORCE_CLOSE", CLEAN)
        assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
        assert sm.get_scout_ok(XAG) == i + 1
    _close(sm, "FORCE_CLOSE", CLEAN)
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE
    assert sm.get_scout_ok(XAG) == 0


def test_exit_is_recorded_with_its_reason_in_state_history():
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        sm = _scout_sm(db)
        for _ in range(SCOUT_EXIT_K):
            _close(sm, "FORCE_CLOSE", CLEAN)
        latest = db.get_latest_symbol_states()[XAG]
        assert latest["state"] == "BASE_MODE"
        assert "SCOUT_EXPIRED_K" in latest["reason"]


def test_midline_invalidation_counts_like_force_close():
    sm = _scout_sm()
    _close(sm, "MIDLINE_INVALIDATION", CLEAN)
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "MIDLINE_INVALIDATION", CLEAN)
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE


@pytest.mark.parametrize("bad", [LOSS, NET_LOSS, 0.0])
def test_a_net_losing_counting_close_resets_the_run(bad):
    sm = _scout_sm()
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "FORCE_CLOSE", bad)
    assert sm.get_scout_ok(XAG) == 0
    _close(sm, "FORCE_CLOSE", CLEAN)
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
    assert sm.get_scout_ok(XAG) == 1


def test_a_stop_loss_resets_the_run():
    sm = _scout_sm()
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "STOP_LOSS", -0.021, sl_percent=0.021)
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
    assert sm.get_scout_ok(XAG) == 0


@pytest.mark.parametrize("neutral", [
    "FORCE_CLOSE_BE", "BE_ESCAPE", "CLOSED_EXTERNAL", "STALE_SPLIT_50", "MIDLINE_PARTIAL_TP",
])
def test_neutral_closes_neither_count_nor_reset(neutral):
    sm = _scout_sm()
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, neutral, CLEAN)       # even a profitable-looking neutral close adds nothing
    _close(sm, neutral, LOSS)        # and a losing one does not break the run
    assert sm.get_scout_ok(XAG) == 2
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
    _close(sm, "FORCE_CLOSE", CLEAN)
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE


@pytest.mark.parametrize("tp", ["TP_AND_FLIP", "D1_RANGE_TP", "MIDLINE_FULL_TP", "CLOSED_EXTERNAL_TP"])
def test_full_take_profit_still_exits_immediately_and_clears_the_counter(tp):
    sm = _scout_sm()
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, tp, CLEAN)
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE
    assert sm.get_scout_ok(XAG) == 0


def test_closes_in_base_mode_never_start_a_counter():
    sm = MMS4StateManager(trading_symbol=BTC)
    for _ in range(5):
        _close(sm, "FORCE_CLOSE", CLEAN)
    assert sm.get_scout_ok(XAG) == 0
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE


def test_re_entering_scout_starts_from_zero():
    sm = _scout_sm()
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "FORCE_CLOSE", CLEAN)
    sm.relax_scout_mode(XAG, respect_cooldown=False)  # operator override leaves SCOUT
    assert sm.get_scout_ok(XAG) == 0
    _close(sm, "STOP_LOSS", -0.021, sl_percent=0.021)  # BASE -> SCOUT again
    _close(sm, "FORCE_CLOSE", CLEAN)
    assert sm.get_scout_ok(XAG) == 1


def test_counter_is_per_symbol():
    sm = _scout_sm()
    sm.set_symbol_state(BTC, MMS4State.SCOUT_MODE, reason="test")
    _close(sm, "FORCE_CLOSE", CLEAN, symbol=XAG)
    _close(sm, "FORCE_CLOSE", CLEAN, symbol=XAG)
    _close(sm, "FORCE_CLOSE", CLEAN, symbol=BTC)
    assert sm.get_scout_ok(XAG) == 2 and sm.get_scout_ok(BTC) == 1
    _close(sm, "FORCE_CLOSE", CLEAN, symbol=XAG)
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE
    assert sm.get_symbol_state(BTC) == MMS4State.SCOUT_MODE


def test_xag_replay_since_its_2026_10_07_stop_is_one_of_three():
    """XAG's real closes after the 2026-10-07 stop (gross, from entry/exit prices): the run
    is broken by the -0.56% loss and by the +0.02% net-losing close, so today it counts 1/3."""
    sm = _scout_sm()
    for reason, pct in [
        ("FORCE_CLOSE", 0.0008),    # 10-07 16:00  +0.08%  (below the 0.10% cost)
        ("STALE_SPLIT_50", 0.0010),  # partial: neutral
        ("FORCE_CLOSE", 0.0020),    # 10-09 11:14  +0.20%  clean   -> 1
        ("FORCE_CLOSE", -0.0056),   # 10-10 10:00  -0.56%  loss    -> reset
        ("FORCE_CLOSE", 0.0002),    # 10-11 22:00  +0.02%  net loss
        ("FORCE_CLOSE", 0.0018),    # 10-12 03:30  +0.18%  clean   -> 1
    ]:
        _close(sm, reason, pct)
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
    assert sm.get_scout_ok(XAG) == 1


# ── persistence ────────────────────────────────────────────────────────────

def test_counter_survives_a_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        sm = _scout_sm(db)
        _close(sm, "FORCE_CLOSE", CLEAN)
        _close(sm, "FORCE_CLOSE", CLEAN)

        sm2 = MMS4StateManager(logger_db=LoggerDB(db_path=os.path.join(tmp, "l.db")), trading_symbol=BTC)
        assert sm2.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
        assert sm2.get_scout_ok(XAG) == 2
        _close(sm2, "FORCE_CLOSE", CLEAN)
        assert sm2.get_symbol_state(XAG) == MMS4State.BASE_MODE


def test_exit_removes_the_persisted_key_so_a_restart_cannot_resurrect_it():
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        sm = _scout_sm(db)
        _close(sm, "FORCE_CLOSE", CLEAN)
        assert db.get_setting(f"scout_ok:{XAG}") == "1"
        for _ in range(SCOUT_EXIT_K - 1):
            _close(sm, "FORCE_CLOSE", CLEAN)
        assert db.get_setting(f"scout_ok:{XAG}") is None


@pytest.mark.parametrize("garbage", ["abc", "-4", "999", ""])
def test_garbled_or_out_of_range_key_restores_zero(garbage):
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        _scout_sm(db)
        db.set_setting(f"scout_ok:{XAG}", garbage)
        sm2 = MMS4StateManager(logger_db=LoggerDB(db_path=os.path.join(tmp, "l.db")), trading_symbol=BTC)
        assert sm2.get_scout_ok(XAG) == 0
        assert sm2.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_a_stale_key_of_a_symbol_not_in_scout_is_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        db.set_setting(f"scout_ok:{XAG}", 2)  # XAG has no SCOUT state at all
        sm = MMS4StateManager(logger_db=LoggerDB(db_path=os.path.join(tmp, "l.db")), trading_symbol=BTC)
        assert sm.get_scout_ok(XAG) == 0


# ── safety ─────────────────────────────────────────────────────────────────

def test_a_counter_failure_never_breaks_a_close(monkeypatch):
    sm = _scout_sm()
    monkeypatch.setattr(sm, "_set_scout_ok", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    rec = _close(sm, "FORCE_CLOSE", CLEAN)      # must not raise
    assert rec in sm.trade_history
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_a_persistence_failure_keeps_the_in_memory_counter(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        sm = _scout_sm(db)
        monkeypatch.setattr(db, "set_setting", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
        _close(sm, "FORCE_CLOSE", CLEAN)
        assert sm.get_scout_ok(XAG) == 1


# ── backtest parity ────────────────────────────────────────────────────────

def test_backtest_feeds_gross_pnl_percent_into_the_same_counter(monkeypatch):
    """The backtest books through the same `finalize_close`, and must hand it the GROSS
    pnl_percent (fees are added to the trade dict separately) — the same quantity the daemon
    passes, otherwise the K-clean-closes rule would behave differently in backtest."""
    from tests.test_backtest_engine import load_fixture_candles

    seen = []
    real = MMS4StateManager.finalize_close

    def spy(self, symbol, side, reason, entry_price, exit_price, amount, pnl, pnl_percent, **kw):
        if not reason.endswith("PARTIAL_TP") and reason != "STALE_SPLIT_50":
            seen.append((reason, pnl_percent))
        return real(self, symbol, side, reason, entry_price, exit_price, amount, pnl, pnl_percent, **kw)

    monkeypatch.setattr(MMS4StateManager, "finalize_close", spy)
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False)

    # partial bookings (SPLIT_50 / midline TP) also appear in `trades` but do not close the
    # position and are neutral for the counter, so compare full closes only
    full = [t for t in trades if t["exit_reason"] not in ("STALE_SPLIT_50", "MIDLINE_PARTIAL_TP")]
    assert full and len(seen) == len(full)
    for (reason, pct), trade in zip(seen, full):
        assert reason == trade["exit_reason"]
        assert pct == pytest.approx(trade["pnl_pct"])          # gross, fees not subtracted
        assert trade["fee_pct"] == pytest.approx(be.ROUND_TRIP_FEE_PCT)


# ── review fixes: real fill only, NaN, ordering, env, daemon wiring ─────────

def test_an_estimated_pnl_is_neutral_it_neither_counts_nor_resets():
    sm = _scout_sm()
    _close(sm, "FORCE_CLOSE", CLEAN)
    sm.finalize_close(XAG, "short", "FORCE_CLOSE", 100.0, 99.0, 1.0, 1.0, CLEAN, pnl_is_estimate=True)
    sm.finalize_close(XAG, "short", "FORCE_CLOSE", 100.0, 101.0, 1.0, -1.0, LOSS, pnl_is_estimate=True)
    assert sm.get_scout_ok(XAG) == 1          # neither +1 (estimate) nor reset (estimated loss)
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_pnl_percent_is_neutral(bad):
    sm = _scout_sm()
    _close(sm, "FORCE_CLOSE", CLEAN)
    _close(sm, "FORCE_CLOSE", bad)
    assert sm.get_scout_ok(XAG) == 1


def test_counter_key_survives_when_persisting_the_exit_state_fails(monkeypatch):
    """The state is persisted BEFORE the counter is cleared: if saving it raises, the key must
    still be there so a restart sees a consistent (SCOUT + its counter) pair."""
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        sm = _scout_sm(db)
        _close(sm, "FORCE_CLOSE", CLEAN)
        assert db.get_setting(f"scout_ok:{XAG}") == "1"
        monkeypatch.setattr(db, "save_state", lambda **k: (_ for _ in ()).throw(RuntimeError("db down")))
        with pytest.raises(RuntimeError):
            sm.set_symbol_state(XAG, MMS4State.BASE_MODE, reason="x")
        assert db.get_setting(f"scout_ok:{XAG}") == "1"


def test_entering_scout_removes_a_stale_persisted_key():
    with tempfile.TemporaryDirectory() as tmp:
        db = LoggerDB(db_path=os.path.join(tmp, "l.db"))
        db.set_setting(f"scout_ok:{XAG}", 2)                       # left behind by a crash
        sm = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="stop")  # BASE -> SCOUT
        assert db.get_setting(f"scout_ok:{XAG}") is None


def test_invalid_env_values_fall_back_instead_of_crashing_at_import(monkeypatch):
    from mms4tk.state_manager import _env_number
    monkeypatch.setenv("MMS4_TEST_K", "three")
    assert _env_number("MMS4_TEST_K", 3, int) == 3
    monkeypatch.setenv("MMS4_TEST_K", "7")
    assert _env_number("MMS4_TEST_K", 3, int) == 7
    monkeypatch.delenv("MMS4_TEST_K")
    assert _env_number("MMS4_TEST_K", 3, int) == 3


# ── daemon wiring (real prices, SHORT, real fill vs estimate) ──────────────

from unittest.mock import AsyncMock, MagicMock

from mms4tk.daemon import MMS4Daemon
from mms4tk.okx_executor import OKXExecutor


@pytest.fixture
def daemon_sm():
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {XAG: {"contractSize": 0.01}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.01})
    sm = MMS4StateManager(trading_symbol=BTC)
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test")
    with tempfile.TemporaryDirectory() as tmp:
        daemon = MMS4Daemon(executor=executor, state_manager=sm,
                            logger_db=LoggerDB(db_path=os.path.join(tmp, "d.db")), symbol=BTC)
        yield daemon, sm, executor


def _book(daemon, fill, entry=61.25, exit_=61.14):
    return daemon._book_stale_close_if_executed(
        {"id": "1", "status": "closed"}, XAG, "short", entry, exit_, 10.0, "FORCE_CLOSE", fill=fill
    )


def test_daemon_counts_a_short_close_from_a_real_fill(daemon_sm):
    daemon, sm, _ = daemon_sm
    assert _book(daemon, fill={"exit_price": 61.14})       # short 61.25 -> 61.14 = +0.18% gross
    assert sm.get_scout_ok(XAG) == 1
    assert sm.trade_history[-1].pnl > 0


def test_daemon_does_not_count_a_close_without_a_real_fill(daemon_sm):
    daemon, sm, _ = daemon_sm
    assert _book(daemon, fill=None)                           # booked, but only as an estimate
    assert len(sm.trade_history) == 1 and sm.get_scout_ok(XAG) == 0


def test_daemon_real_fill_decides_not_the_candle_close(daemon_sm):
    """The candle says +0.18% but the market close filled at a loss: the REAL fill resets."""
    daemon, sm, _ = daemon_sm
    _book(daemon, fill={"exit_price": 61.14})
    assert sm.get_scout_ok(XAG) == 1
    daemon._book_stale_close_if_executed(
        {"id": "2"}, XAG, "short", 61.25, 61.14, 10.0, "FORCE_CLOSE", fill={"exit_price": 61.60}
    )
    assert sm.get_scout_ok(XAG) == 0


def test_daemon_three_real_fills_release_the_symbol(daemon_sm):
    daemon, sm, _ = daemon_sm
    for _ in range(3):
        _book(daemon, fill={"exit_price": 61.14})
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE


def test_fetch_close_fill_returns_the_order_fill(daemon_sm):
    daemon, _, executor = daemon_sm
    executor.fetch_last_closing_order = AsyncMock(return_value={"exit_price": 61.14, "pnl_usd": 0.1})
    fill = asyncio.run(daemon._fetch_close_fill(XAG, "short", 123, delay_s=0))
    assert fill["exit_price"] == 61.14
    assert executor.fetch_last_closing_order.call_args.kwargs["min_fill_time_ms"] == 123


def test_fetch_close_fill_retries_once_then_gives_up(daemon_sm):
    daemon, _, executor = daemon_sm
    executor.fetch_last_closing_order = AsyncMock(return_value=None)
    assert asyncio.run(daemon._fetch_close_fill(XAG, "short", 123, delay_s=0)) is None
    assert executor.fetch_last_closing_order.await_count == 2


def test_fetch_close_fill_swallows_errors(daemon_sm):
    daemon, _, executor = daemon_sm
    executor.fetch_last_closing_order = AsyncMock(side_effect=RuntimeError("okx down"))
    assert asyncio.run(daemon._fetch_close_fill(XAG, "short", 123, delay_s=0)) is None


def test_fetch_close_fill_is_bounded_by_a_timeout(daemon_sm):
    """A slow OKX must not stall the trading cycle: each attempt is capped."""
    daemon, _, executor = daemon_sm

    async def hang(*a, **k):
        await asyncio.sleep(30)

    executor.fetch_last_closing_order = hang
    started = time.monotonic()
    result = asyncio.run(daemon._fetch_close_fill(XAG, "short", 123, delay_s=0, timeout_s=0.05))
    assert result is None
    assert time.monotonic() - started < 5   # the 30 s hang was cut off by the per-attempt timeout


@pytest.mark.parametrize("active_entry,active_side,expected", [
    (61.25, "short", 61.25),   # same leg: live avgPx wins
    (61.25, "SHORT", 61.25),   # side case is irrelevant
    (61.25, "long", 60.0),     # opposite leg (hedge mode): fall back to the tracker's entry
    (0.0, "short", 60.0),      # no live price
    (61.25, None, 60.0),
])
def test_stale_entry_price_comes_from_the_same_leg_only(active_entry, active_side, expected):
    assert MMS4Daemon._stale_entry_price(active_entry, active_side, "short", 60.0) == expected
