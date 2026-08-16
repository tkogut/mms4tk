"""
Regression tests for the 2026-08-16 quantitative audit.

Each test pins a defect the audit found, named after it. They must fail against the
pre-remediation code and pass after. See docs/AUDIT_MMS4_2026-08-16.md.
"""

import asyncio
import math
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from mms4tk.analytics import Candle, IndicatorParams, MMS4SignalEngine, TMAIndicator
from mms4tk.daemon import MMS4Daemon
from mms4tk.inactivity_manager import MMS4InactivityManager, StaleAction
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor, normalize_symbol
from mms4tk.state_manager import MMS4State, MMS4StateManager


# ── helpers ─────────────────────────────────────────────────────────────

def mk(seq):
    return [
        Candle(timestamp=1_700_000_000_000 + i * 900_000, open=o, high=h, low=l, close=c, volume=1.0)
        for i, (o, h, l, c) in enumerate(seq)
    ]


def flat_candles(n=40, price=100.0, spread=0.4):
    return mk([(price, price + spread, price - spread, price)] * n)


class StubEngine:
    """Signal engine that returns whatever the test asks for."""

    def __init__(self, result=None):
        self.next = result

    def evaluate_signals(self, candles, state_manager, params=None, symbol=None):
        return self.next


def sizing_executor():
    """Executor double that never touches the network or credentials."""
    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=flat_candles(60))
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.execute_order = AsyncMock(return_value={"id": "ord_1", "status": "executed"})
    ex.reduce_position = AsyncMock(return_value={"id": "red_1", "status": "closed"})
    ex.calculate_order_amount = MagicMock(return_value=3.0)
    return ex


def position(symbol, side, contracts=4.0, entry=100.0, notional=21.0):
    return {
        "symbol": symbol,
        "side": side,
        "contracts": contracts,
        "entryPrice": entry,
        "notional": notional,
        "info": {"posSide": side, "avgPx": str(entry)},
    }


@pytest.fixture
def db(tmp_path):
    return LoggerDB(db_path=os.path.join(tmp_path, "audit.db"))


def make_daemon(db, executor, signal, symbol="ETH/USDC", state=MMS4State.BASE_MODE):
    sm = MMS4StateManager(initial_state=state, trading_symbol=symbol)
    d = MMS4Daemon(
        executor=executor,
        state_manager=sm,
        signal_engine=StubEngine(signal),
        logger_db=db,
        symbol=symbol,
        trade_amount_usd=10.0,
    )
    # Keep the inactivity engine out of the way unless a test targets it.
    d.inactivity_manager.set_symbol_config(
        symbol, {"impulse_negation_enabled": False, "stale_candles_threshold": 10**6}
    )
    return d, sm


# ── P0-1 / user request 2: active stop-loss reconciliation ──────────────

def test_scout_mode_entry_on_sl(db):
    """A position that vanishes from OKX after a >=2% adverse move enters SCOUT_MODE now."""
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    sig = SignalResult(signal_type="NONE", price=98.0, stop_loss=0.0, leverage=3.0)
    d, sm = make_daemon(db, ex, sig)

    # Cycle 1: a long position is open at 100 and gets snapshotted.
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE

    # Cycle 2: the position is gone and price sits 2% below entry -> stop-out.
    ex.fetch_positions = AsyncMock(return_value=[])
    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert sm.get_symbol_state("ETH/USDC") == MMS4State.SCOUT_MODE
    assert sm.leverage == 0.1
    assert res["reconciliation"] and res["reconciliation"][0]["is_stop_loss"] is True


def test_reconciliation_ignores_small_close(db):
    """A position closed near break-even must NOT be mistaken for a stop-loss."""
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    sig = SignalResult(signal_type="NONE", price=100.05, stop_loss=0.0, leverage=3.0)
    d, sm = make_daemon(db, ex, sig)

    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    ex.fetch_positions = AsyncMock(return_value=[])
    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE
    assert res["reconciliation"][0]["is_stop_loss"] is False


def test_reconciliation_survives_restart(db):
    """Snapshots live in SQLite, so a fresh daemon still detects the stop-out."""
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    sig = SignalResult(signal_type="NONE", price=97.0, stop_loss=0.0, leverage=3.0)
    d1, _ = make_daemon(db, ex, sig)
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "long", entry=100.0)])
    asyncio.run(d1.execute_symbol_cycle("ETH/USDC"))

    # Brand new daemon + state manager, same database.
    ex2 = sizing_executor()
    ex2.fetch_positions = AsyncMock(return_value=[])
    d2, sm2 = make_daemon(db, ex2, sig)
    asyncio.run(d2.execute_symbol_cycle("ETH/USDC"))

    assert sm2.get_symbol_state("ETH/USDC") == MMS4State.SCOUT_MODE


# ── P0-2: impulse negation must not liquidate the position ──────────────

def test_impulse_negation_no_loop():
    """One negation halves the position once; it never re-issues SPLIT_50 every cycle."""
    atr = 100.0
    m = MMS4InactivityManager()
    m.track_position("SYM", 10_000.0, 0.0, "long", atr)

    actions = [m.update_and_check("SYM", px, atr, True) for px in (10_020, 10_050, 10_030, 10_020)]
    assert actions[-1] == StaleAction.SPLIT_50

    later = [m.update_and_check("SYM", 10_015.0, atr, True) for _ in range(10)]
    assert later.count(StaleAction.SPLIT_50) == 0, "SPLIT_50 must not repeat"
    assert later.count(StaleAction.FORCE_CLOSE) <= 1, "at most one terminal close"
    assert m.get_tracker("SYM").is_split is True


def test_two_stage_ladder_reaches_force_close():
    """
    FORCE_CLOSE_BE must be reachable.

    Price is stagnant (deviation 0.3x ATR, under the 0.5x ATR threshold) but sits OUTSIDE
    the 0.1% break-even band, so the ladder — not BE_ESCAPE — owns the exit. Previously
    BE_ESCAPE was evaluated first and could never hand over to stage 2.
    """
    atr = 100.0
    m = MMS4InactivityManager()
    m.set_symbol_config("S", {"impulse_negation_enabled": False})
    m.track_position("S", 10_000.0, 0.0, "long", atr)

    emitted = []
    for _ in range(25):
        a = m.update_and_check("S", 10_030.0, atr, True)  # 30 < 0.5xATR, > 0.1% band (10)
        if a != StaleAction.HOLD:
            emitted.append(a)

    assert StaleAction.SPLIT_50 in emitted
    assert StaleAction.FORCE_CLOSE_BE in emitted


def test_be_escape_wins_inside_break_even_band():
    """
    Inside the BE band the position exits via BE_ESCAPE instead of the ladder.

    Both are full closes at ~entry, so this is an equivalent outcome — pinned here so the
    interaction between Rule II and the ladder stays intentional.
    """
    atr = 100.0
    m = MMS4InactivityManager()
    m.set_symbol_config("S", {"impulse_negation_enabled": False})
    m.track_position("S", 10_000.0, 0.0, "long", atr)

    emitted = []
    for _ in range(20):
        a = m.update_and_check("S", 10_005.0, atr, True)  # inside 0.1% band
        if a != StaleAction.HOLD:
            emitted.append(a)

    assert emitted[0] == StaleAction.SPLIT_50
    assert StaleAction.BE_ESCAPE in emitted
    # Terminal action fires once — the tracker latches `closing`.
    assert emitted.count(StaleAction.BE_ESCAPE) == 1


def test_stale_flag_resets_after_breakout():
    """Leaving the stagnation zone clears stale_detected."""
    atr = 100.0
    m = MMS4InactivityManager()
    m.set_symbol_config("S", {"impulse_negation_enabled": False})
    m.track_position("S", 10_000.0, 0.0, "long", atr)
    for _ in range(12):
        m.update_and_check("S", 10_010.0, atr, True)
    assert m.get_tracker("S").stale_detected is True

    m.update_and_check("S", 10_400.0, atr, True)  # +4x ATR breakout
    assert m.get_tracker("S").stale_detected is False


# ── P1-3: a zero price must not poison the tracker ──────────────────────

def test_zero_price_does_not_poison_tracker():
    atr = 100.0
    m = MMS4InactivityManager()
    m.track_position("S", 10_000.0, 0.0, "short", atr)

    assert m.update_and_check("S", 0.0, atr, True) == StaleAction.HOLD
    assert m.get_tracker("S").min_observed_price == 10_000.0
    # A profitable short must not read as a negated impulse.
    assert m.update_and_check("S", 9_990.0, atr, True) == StaleAction.HOLD


def test_signal_never_reports_zero_price():
    engine, sm = MMS4SignalEngine(), MMS4StateManager()
    res = engine.evaluate_signals(mk([(100, 101, 99, 100)]), sm)
    assert res.signal_type == "NONE"
    assert res.price == 100.0


# ── P1-4: base entries require the candle to close against the band ─────

def test_base_signal_requires_candle_direction():
    params = IndicatorParams()
    engine, sm = MMS4SignalEngine(), MMS4StateManager()
    pre = [(100, 100.4, 99.6, 100)] * 40
    upper = float(TMAIndicator.calculate(mk(pre), params)["upper_band"].iloc[-1])

    rising = pre + [(100, upper + 5, 99.5, upper + 4), (100, 100.2, 99.8, 100)]
    assert engine.evaluate_signals(mk(rising), sm).signal_type != "BASE_SHORT"

    falling = pre + [(100, upper + 5, 99.5, 99.0), (100, 100.2, 99.8, 100)]
    assert engine.evaluate_signals(mk(falling), sm).signal_type == "BASE_SHORT"


def test_flat_market_emits_no_signal():
    engine, sm = MMS4SignalEngine(), MMS4StateManager()
    assert engine.evaluate_signals(mk([(100, 100, 100, 100)] * 40), sm).signal_type == "NONE"


def test_outside_bar_is_ambiguous():
    params = IndicatorParams()
    engine, sm = MMS4SignalEngine(), MMS4StateManager()
    pre = [(100, 100.4, 99.6, 100)] * 40
    df = TMAIndicator.calculate(mk(pre), params)
    upper, lower = float(df["upper_band"].iloc[-1]), float(df["lower_band"].iloc[-1])

    both = pre + [(100, upper + 10, lower - 10, 99.0), (100, 100.2, 99.8, 100)]
    assert engine.evaluate_signals(mk(both), sm).signal_type == "NONE"


# ── P1-5 / user request 1: the addon ledger is persistent ───────────────

def test_addon_lock_survives_none_signal(db):
    """A cycle with no signal must not clear the addon guard for an open short."""
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "short", entry=100.0)])
    d, _ = make_daemon(db, ex, SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0))

    db.record_addon("ETH/USDC", "short", d._position_key(100.0))
    asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert db.get_addon_count("ETH/USDC", "short", d._position_key(100.0)) == 1


def test_addon_ledger_survives_restart(db):
    """The guard is stored in SQLite, so a restarted daemon still refuses a 2nd addon."""
    from mms4tk.analytics import SignalResult

    sig = SignalResult(signal_type="HIGH_RISK_ADD_SHORT", price=100.0, stop_loss=101.0, leverage=3.0)
    ex = sizing_executor()
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "short", entry=100.0, notional=7.0)])
    d1, _ = make_daemon(db, ex, sig)
    res1 = asyncio.run(d1.execute_symbol_cycle("ETH/USDC"))
    assert res1["outcome"] == "EXECUTED_HIGH_RISK_ADD_SHORT"

    ex2 = sizing_executor()
    ex2.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "short", entry=100.0, notional=7.0)])
    d2, _ = make_daemon(db, ex2, sig)  # fresh in-memory state, same DB
    res2 = asyncio.run(d2.execute_symbol_cycle("ETH/USDC"))

    assert res2["outcome"] == "SKIPPED_HIGH_RISK_ADD_SHORT"
    assert ex2.execute_order.await_count == 0


def test_addon_ledger_resets_for_new_position(db):
    """A different entry price is a different position and gets its own addon budget."""
    key_old, key_new = "100.00000000", "120.00000000"
    db.record_addon("ETH/USDC", "short", key_old)
    assert db.get_addon_count("ETH/USDC", "short", key_old) == 1
    assert db.get_addon_count("ETH/USDC", "short", key_new) == 0


# ── P1-6 / user request 3: is_allowed is honored, state is per-symbol ───

def test_daemon_honors_is_allowed(db):
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    ex.fetch_positions = AsyncMock(return_value=[])
    vetoed = SignalResult(
        signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0,
        is_allowed=False, reason="Signal blocked by directional_bias=SHORT_ONLY",
    )
    d, _ = make_daemon(db, ex, vetoed)
    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res["outcome"] == "BLOCKED_BASE_LONG"
    assert ex.execute_order.await_count == 0


def test_signals_use_per_symbol_state():
    """SCOUT_MODE on the primary symbol must not gate a different symbol."""
    sm = MMS4StateManager(trading_symbol="BTC/USDC")
    sm.set_symbol_state("BTC/USDC", MMS4State.SCOUT_MODE)
    engine = MMS4SignalEngine()
    candles = flat_candles(40)

    assert engine.evaluate_signals(candles, sm, symbol="BTC/USDC").leverage == 0.1
    assert engine.evaluate_signals(candles, sm, symbol="ETH/USDC").leverage == 3.0


# ── P1-7 / P1-8: allocation never exceeds the configured margin ─────────

@pytest.mark.parametrize(
    "symbol,price,ct_val",
    [
        ("BTC-USD_UM_XPERP", 95_000.0, 0.0001),
        ("DOGE-USDC-SWAP", 0.32, 1.0),
        ("ICP-USDC-SWAP", 8.5, 1.0),
    ],
)
def test_margin_never_exceeds_cap(symbol, price, ct_val):
    ex = OKXExecutor.__new__(OKXExecutor)  # no __init__ => no credentials touched
    ex.exchange = None

    base_target, addon_target = 21.0, 9.0
    total = 0.0
    for target in (base_target, addon_target):
        contracts = ex.calculate_order_amount(symbol, target, price)
        realized = contracts * price * ct_val
        assert realized <= target * 1.10, f"{symbol}: ${realized:.2f} overshoots ${target:.2f}"
        total += realized
    assert total <= (base_target + addon_target) * 1.10


def test_undersized_target_is_skipped_not_inflated():
    """SCOUT_MODE's tiny target must yield 'no trade', never an inflated one."""
    ex = OKXExecutor.__new__(OKXExecutor)
    ex.exchange = None
    assert ex.calculate_order_amount("BTC-USD_UM_XPERP", 0.70, 95_000.0) == 0.0


def test_daemon_skips_entry_when_size_does_not_fit(db):
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.calculate_order_amount = MagicMock(return_value=0.0)
    d, _ = make_daemon(db, ex, SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=0.1))
    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert res["outcome"] == "SKIPPED_BASE_LONG"
    assert ex.execute_order.await_count == 0


# ── P1-9: a partial reduction never becomes a full close ────────────────

@pytest.mark.parametrize("contracts", [1, 2, 3, 4, 5, 10])
def test_split_never_closes_whole_position(contracts):
    """
    reduce_position(0.5) either takes a genuine partial or reports 'not_divisible'.

    It must never close 100%: `max(1, floor(n*0.5))` used to turn a 1-contract split into
    a full liquidation, which is what let the impulse-negation loop drain positions.
    """
    amount = OKXExecutor.reduction_amount(contracts, 0.5)
    if amount is None:
        # Only a single contract is genuinely indivisible.
        assert contracts == 1
    else:
        assert 0 < amount < contracts, "a partial reduction must never close everything"
        # Integer contracts round DOWN: the error must always leave more of the position
        # on, never less. Closing 67% for a "50%" split would over-cut irreversibly.
        assert amount / contracts <= 0.5, "a 50% split must never close more than half"

    # A full close always closes everything.
    assert OKXExecutor.reduction_amount(contracts, 1.0) == float(contracts)


# ── P1-11: take-profit flip is atomic ───────────────────────────────────

def test_flip_aborts_when_close_fails(db):
    """If the close reports no_position and the position is still live, do not flip."""
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "short", entry=110.0)])
    ex.reduce_position = AsyncMock(return_value={"status": "no_position"})
    d, _ = make_daemon(db, ex, SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0))

    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res["outcome"].startswith("ERROR_")
    assert ex.execute_order.await_count == 0, "must not open an opposing leg"


def test_flip_executes_when_close_confirms(db):
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    ex.fetch_positions = AsyncMock(return_value=[position("ETH/USDC", "short", entry=110.0)])
    ex.calculate_order_amount = MagicMock(return_value=3.0)
    d, sm = make_daemon(db, ex, SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=3.0),
                        state=MMS4State.SCOUT_MODE)

    res = asyncio.run(d.execute_symbol_cycle("ETH/USDC"))
    assert res["outcome"] == "EXECUTED_TP_AND_FLIP_BASE_LONG"
    assert ex.execute_order.await_args.kwargs["side"] == "BUY"
    assert sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE


# ── P1-10: settings endpoint contract ───────────────────────────────────

def test_state_manager_exposes_settings_api():
    sm = MMS4StateManager(trading_symbol="BTC/USDC")
    sm.leverage = 42.0
    assert sm.leverage == 10.0, "override must clamp to 10x"
    sm.scout_mode = True
    assert sm.current_state == MMS4State.SCOUT_MODE
    assert sm.leverage == 0.1, "SCOUT_MODE beats a manual override"
    sm.scout_mode = False
    assert sm.current_state == MMS4State.BASE_MODE
    sm.save_state(reason="test")  # must not raise without a DB


# ── P1-12 / P2-28..31: metrics and KPI hygiene ──────────────────────────

def test_state_metrics_are_deduplicated(db):
    assert db.log_state_metrics("BASE_MODE", 3.0, 0.0, 0.0, 0) > 0
    assert db.log_state_metrics("BASE_MODE", 3.0, 0.0, 0.0, 0) == 0, "identical snapshot skipped"
    assert db.log_state_metrics("SCOUT_MODE", 0.1, 0.0, 0.0, 0) > 0, "real change is written"


def test_kpi_reports_both_drawdown_units(db):
    for i, pnl in enumerate([10.0, -4.0, 6.0, -20.0, 2.0]):
        db.upsert_closed_trade(
            trade_id=f"t{i}", timestamp=f"2026-08-16T0{i}:00:00+00:00", symbol="X/USDC",
            side="BUY", entry_price=100.0, exit_price=100.0 + pnl, amount=1.0,
            pnl_usd=pnl, pnl_pct=pnl / 100.0, is_win=pnl > 0, leverage=3.0,
        )
    kpi = db.get_kpi_summary(initial_equity=100.0)
    assert kpi["max_drawdown_usd"] == 20.0
    assert math.isclose(kpi["max_drawdown_pct"], 17.86, abs_tol=0.01)
    assert kpi["total_pnl_usd"] == -6.0
    assert kpi["total_trades"] == 5


def test_kpi_covers_full_history(db):
    for i in range(1200):
        db.upsert_closed_trade(
            trade_id=f"t{i}", timestamp=f"2026-08-16T{i//3600:02d}:{(i//60)%60:02d}:{i%60:02d}+00:00",
            symbol="X/USDC", side="BUY", entry_price=100.0, exit_price=101.0, amount=1.0,
            pnl_usd=1.0, pnl_pct=0.01, is_win=True, leverage=1.0,
        )
    assert db.get_kpi_summary()["total_trades"] == 1200, "KPI must not truncate at 1000"


# ── P2-33: one symbol normalizer ────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BTC/USD:USD-310404", "BTC"),
        ("BTC-USD_UM_XPERP-310404", "BTC"),
        ("BTC-USDC-SWAP", "BTC"),
        ("BTC/USDC", "BTC"),
        ("ICP/USD:USD-310711", "ICP"),
        ("", ""),
    ],
)
def test_symbol_normalization(raw, expected):
    assert normalize_symbol(raw) == expected


# ── Regressions found while remediating ─────────────────────────────────

def test_indivisible_split_does_not_book_a_stop_loss(db):
    """
    A SPLIT_50 that cannot be executed (1 contract) must leave everything intact.

    The position stays open, so reconciliation must not read it as a vanished position
    and book a phantom stop-loss — that would flip the system into SCOUT_MODE on a
    non-event.
    """
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    live = [position("ETH/USDC", "long", contracts=1.0, entry=100.0, notional=7.0)]
    ex.fetch_positions = AsyncMock(return_value=live)
    ex.reduce_position = AsyncMock(return_value={"status": "not_divisible", "contracts": 1.0})
    ex.calculate_order_amount = MagicMock(return_value=1.0)

    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="ETH/USDC")
    d = MMS4Daemon(
        executor=ex, state_manager=sm,
        signal_engine=StubEngine(SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0)),
        logger_db=db, symbol="ETH/USDC", trade_amount_usd=10.0,
    )
    d.inactivity_manager.set_symbol_config(
        "ETH/USDC", {"stale_candles_threshold": 2, "impulse_negation_enabled": False}
    )

    base_ts = 1_700_000_000_000
    for cycle in range(5):
        # Advance the candle series so the tracker actually counts candles.
        ex.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=base_ts + (cycle + i) * 900_000, open=100.0, high=100.5,
                   low=99.5, close=100.0, volume=1.0)
            for i in range(60)
        ])
        asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE
    assert [t for t in sm.trade_history if t.is_stop_loss] == [], "no phantom stop-loss"
    assert d.inactivity_manager.is_tracking("ETH/USDC"), "position stays supervised"


def test_partial_reduction_never_over_cuts():
    """Integer rounding must always leave MORE of the position on, never less."""
    for contracts in range(1, 21):
        amount = OKXExecutor.reduction_amount(float(contracts), 0.5)
        if amount is None:
            continue
        assert amount / contracts <= 0.5, f"{contracts} contracts: closed {amount} (>50%)"


def test_addon_clear_is_not_reissued_every_cycle(db):
    """The no-op ledger clear is guarded in memory rather than hitting SQLite each cycle."""
    from mms4tk.analytics import SignalResult

    ex = sizing_executor()
    ex.fetch_positions = AsyncMock(return_value=[])
    d, _ = make_daemon(db, ex, SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=3.0))

    calls = {"n": 0}
    real_clear = db.clear_addon_state

    def counting_clear(symbol, side=None):
        calls["n"] += 1
        return real_clear(symbol, side)

    db.clear_addon_state = counting_clear
    for _ in range(5):
        asyncio.run(d.execute_symbol_cycle("ETH/USDC"))

    assert calls["n"] <= 2, f"expected one clear per side, got {calls['n']}"
