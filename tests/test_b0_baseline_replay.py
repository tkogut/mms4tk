"""Unit tests for `scripts/b0_baseline_replay.py`'s pure, testable core: the
forward-walk B0 simulator, the statistics helpers, and the decision-threshold
classifier. Convention follows `tests/test_structural_sl_analysis.py` (import
the script as a module via `importlib`, test pure functions directly) since
`scripts/` is not a package. No DB or network access in any test here -- the
simulator is exercised against synthetic candle series only, per the spec's
testability requirement (TP-first, SL-first, same-candle tie-break,
never-touched/censored)."""
import importlib.util
import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "b0_baseline_replay.py")

_spec = importlib.util.spec_from_file_location("b0_baseline_replay", SCRIPT_PATH)
b0 = importlib.util.module_from_spec(_spec)
# The module must be registered in sys.modules BEFORE exec_module runs: its
# @dataclass-decorated classes resolve type hints via
# sys.modules[cls.__module__], which is None otherwise on Python 3.12.
sys.modules[_spec.name] = b0
_spec.loader.exec_module(b0)

Candle = b0.Candle
IndicatorParams = b0.IndicatorParams


def _flat_candles(n, price, start_ts=0, step_ms=3_600_000, wiggle=0.3):
    """n candles oscillating gently around `price` -- gives the rolling TMA/ATR
    windows a non-zero ATR (a perfectly flat seed makes ATR=0, collapsing the
    band onto the TMA itself and making every subsequent candle falsely "touch"
    it immediately), while keeping the TMA itself close to `price`."""
    candles = []
    for i in range(n):
        delta = wiggle if i % 2 == 0 else -wiggle
        o = price + delta
        c = price - delta
        candles.append(Candle(
            timestamp=start_ts + i * step_ms,
            open=o, high=max(o, c) + 0.05, low=min(o, c) - 0.05, close=c, volume=1.0,
        ))
    return candles


# ── simulate_exit: TP-first, SL-first, tie-break, censored ──────────────────

def test_simulate_exit_long_hits_tp_first():
    """A long position whose price rallies cleanly through the upper band
    before ever threatening the stop must score TP, not SL."""
    seed = _flat_candles(40, 100.0)
    # A strong, steady rally after the seed -- should push price through the
    # upper band well before the 2% stop (entry*0.98) is ever challenged.
    rally = [
        Candle(timestamp=seed[-1].timestamp + (i + 1) * 3_600_000,
               open=100.0 + i * 2, high=100.0 + i * 2 + 1, low=100.0 + i * 2 - 0.1,
               close=100.0 + i * 2, volume=1.0)
        for i in range(30)
    ]
    candles = seed + rally
    result = b0.simulate_exit("long", 100.0, candles, sl_pct=0.02)
    assert result.exit_type == "TP"
    assert result.pct_return is not None and result.pct_return > 0


def test_simulate_exit_short_hits_sl_first():
    """A short position whose price rallies against it must score SL, not TP,
    when the stop is touched before the (far) opposite band."""
    seed = _flat_candles(40, 100.0)
    rally_against = [
        Candle(timestamp=seed[-1].timestamp + (i + 1) * 3_600_000,
               open=100.0 + i * 1.0, high=100.0 + i * 1.0 + 0.5, low=100.0 + i * 1.0 - 0.1,
               close=100.0 + i * 1.0, volume=1.0)
        for i in range(5)
    ]
    candles = seed + rally_against
    result = b0.simulate_exit("short", 100.0, candles, sl_pct=0.02)
    assert result.exit_type == "SL"
    assert result.exit_price == pytest.approx(102.0)  # entry * (1 + 0.02)
    assert result.pct_return is not None and result.pct_return < 0


def test_simulate_exit_same_candle_tie_break_is_conservative():
    """When a single candle's range touches both the SL level and the TP band,
    B0 must score it as the SL (the pre-registered conservative tie-break),
    never the TP."""
    seed = _flat_candles(40, 100.0)
    # One huge-range candle that spans both the entry-anchored 2% SL below and
    # a level far enough above to plausibly cross the (tight, low-volatility)
    # upper band computed from the flat seed.
    wild = Candle(timestamp=seed[-1].timestamp + 3_600_000, open=100.0, high=130.0, low=90.0, close=100.0, volume=1.0)
    result = b0.simulate_exit("long", 100.0, seed + [wild], sl_pct=0.02)
    assert result.exit_type == "SL"


def test_simulate_exit_never_touched_is_censored():
    """A price path that never reaches either the SL or the opposite band
    before the series ends must be reported as CENSORED, not scored as a loss
    or a win -- scoring it either way would bias the aggregate metric."""
    seed = _flat_candles(40, 100.0)
    forward = _flat_candles(20, 100.05, start_ts=seed[-1].timestamp + 3_600_000, wiggle=0.05)
    result = b0.simulate_exit("long", 100.0, seed + forward, sl_pct=0.02)
    assert result.exit_type == "CENSORED"
    assert result.net_usd == 0.0
    assert result.pct_return is None


def test_simulate_exit_no_candles_is_no_data():
    result = b0.simulate_exit("long", 100.0, [], sl_pct=0.02)
    assert result.exit_type == "NO_DATA"


def test_simulate_exit_entry_index_skips_unseeded_pre_entry_candles():
    """Regression: without an explicit entry_index, walking from index 0 of a
    series that ALSO carries pre-entry seed history would evaluate the
    rolling TMA/ATR bands before they're seeded (NaN) for that whole
    stretch -- SL detection still works (it doesn't depend on the band), but
    TP detection is silently skipped there, one-sidedly biasing the simulator
    toward missing/delaying early TP touches. This test's pre-entry section
    contains a clean round-trip rally (up through what would be a TP band
    computed from the early, still-calm TMA, then back to baseline) that must
    NOT be scored, because it happens entirely before `entry_index` -- the
    position only actually "exists" once price is back at the calm baseline."""
    seed = _flat_candles(40, 100.0)
    # Round-trip: rallies up (would cross a TP band computed from the calm
    # seed's TMA/ATR), then returns to baseline before the real entry point --
    # realistic in shape (a touch-and-reject), isolating entry_index as the
    # only thing that can make this leak into the result.
    up = [
        Candle(timestamp=seed[-1].timestamp + (i + 1) * 3_600_000,
               open=100.0 + i * 2, high=100.0 + i * 2 + 1, low=100.0 + i * 2 - 0.1,
               close=100.0 + i * 2, volume=1.0)
        for i in range(8)
    ]
    down = [
        Candle(timestamp=up[-1].timestamp + (i + 1) * 3_600_000,
               open=up[-1].close - i * 2, high=up[-1].close - i * 2 + 0.1, low=up[-1].close - i * 2 - 1,
               close=up[-1].close - i * 2, volume=1.0)
        for i in range(8)
    ]
    pre_entry = seed + up + down
    entry_index = len(pre_entry)  # "entry" is placed right after the round trip, back near baseline
    entry_price = pre_entry[-1].close
    # Nothing happens post-entry -> must be CENSORED, not a TP carried over
    # from the pre-entry round trip.
    post_entry_flat = _flat_candles(10, entry_price, start_ts=pre_entry[-1].timestamp + 3_600_000, wiggle=0.05)
    candles = pre_entry + post_entry_flat

    result = b0.simulate_exit("long", entry_price, candles, sl_pct=0.02, entry_index=entry_index)
    assert result.exit_type == "CENSORED", (
        f"entry_index was not respected -- the pre-entry round trip leaked into the result (got {result.exit_type})"
    )


def test_simulate_exit_adverse_brake_fires_before_hard_sl():
    """Phase 2: the mirrored adverse-brake rule must be able to exit a position
    BEFORE the hard SL would, once the adverse excursion threshold is reached
    and then stalls for the configured number of candles."""
    seed = _flat_candles(40, 100.0)
    # Drift down to roughly -1 ATR (well short of the 2% hard SL on a
    # higher-priced-but-low-ATR seed), then go flat for several candles --
    # should trip the brake rather than ride all the way to the SL.
    drift = [
        Candle(timestamp=seed[-1].timestamp + (i + 1) * 3_600_000,
               open=100.0 - i * 0.05, high=100.0 - i * 0.05 + 0.02, low=100.0 - i * 0.05 - 0.1,
               close=100.0 - i * 0.05, volume=1.0)
        for i in range(10)
    ]
    # Genuinely flat (no wiggle) so it never registers a new adverse extreme --
    # using the oscillating helper here would intermittently dip below
    # drift[-1].low and reset the stall counter.
    stall = [
        Candle(timestamp=drift[-1].timestamp + (i + 1) * 3_600_000,
               open=drift[-1].low, high=drift[-1].low + 0.01, low=drift[-1].low, close=drift[-1].low, volume=1.0)
        for i in range(6)
    ]
    candles = seed + drift + stall
    brake_rule = {"min_adverse_atr": 0.6, "stall_candles": 4}
    result = b0.simulate_exit("long", 100.0, candles, sl_pct=0.20, adverse_brake=brake_rule)  # SL set wide so only the brake can fire
    assert result.exit_type == "BRAKE"


# ── apply_notional_and_fees ──────────────────────────────────────────────────

def test_apply_notional_and_fees_scales_and_deducts():
    raw = b0.ExitSimResult("TP", 110.0, 5, 0.0, pct_return=0.10)
    scaled = b0.apply_notional_and_fees(raw, notional_usd=1000.0, fee_pct_round_trip=0.001)
    assert scaled.net_usd == pytest.approx(1000.0 * 0.10 - 1000.0 * 0.001)


def test_apply_notional_and_fees_passthrough_for_censored():
    raw = b0.ExitSimResult("CENSORED", None, None, 0.0, pct_return=None)
    scaled = b0.apply_notional_and_fees(raw, notional_usd=1000.0, fee_pct_round_trip=0.001)
    assert scaled.net_usd == 0.0
    assert scaled.exit_type == "CENSORED"


# ── Statistics helpers ───────────────────────────────────────────────────────

def test_mean_and_ci_basic():
    stats = b0.mean_and_ci([1.0, 2.0, 3.0, 4.0, 5.0])
    assert stats["n"] == 5
    assert stats["mean"] == pytest.approx(3.0)
    assert stats["ci_lo"] < 3.0 < stats["ci_hi"]


def test_mean_and_ci_single_value_degenerates_safely():
    stats = b0.mean_and_ci([2.0])
    assert stats["n"] == 1
    assert stats["se"] == 0.0


def test_sign_test_all_positive_is_significant():
    result = b0.sign_test([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    assert result["positive"] == 10
    assert result["negative"] == 0
    assert result["p_value"] < 0.01


def test_sign_test_balanced_is_not_significant():
    result = b0.sign_test([1.0, -1.0, 2.0, -2.0, 3.0, -3.0])
    assert result["p_value"] == pytest.approx(1.0)


def test_sign_test_empty_is_neutral():
    result = b0.sign_test([])
    assert result["n"] == 0
    assert result["p_value"] == 1.0


# ── classify_verdict: pre-registered decision thresholds ─────────────────────

def test_classify_verdict_underpowered_below_min_n():
    stats = {"mean": 5.0, "ci_lo": 1.0, "ci_hi": 9.0}
    verdict = b0.classify_verdict(stats, n=50)
    assert "INCONCLUSIVE" in verdict
    assert "underpowered" in verdict


def test_classify_verdict_ci_includes_zero():
    stats = {"mean": 0.05, "ci_lo": -0.5, "ci_hi": 0.6}
    verdict = b0.classify_verdict(stats, n=900)
    assert "CI includes zero" in verdict


def test_classify_verdict_below_practical_significance():
    stats = {"mean": 0.05, "ci_lo": 0.01, "ci_hi": 0.09}  # excludes zero, but tiny
    verdict = b0.classify_verdict(stats, n=900)
    assert "practical" in verdict.lower()


def test_classify_verdict_edge_detected_positive():
    stats = {"mean": 5.0, "ci_lo": 1.0, "ci_hi": 9.0}
    verdict = b0.classify_verdict(stats, n=900)
    assert verdict == "EDGE DETECTED (positive)"


def test_classify_verdict_edge_detected_negative():
    stats = {"mean": -5.0, "ci_lo": -9.0, "ci_hi": -1.0}
    verdict = b0.classify_verdict(stats, n=900)
    assert verdict == "EDGE DETECTED (negative)"


# ── Pre-registration sanity: the rule and thresholds are actually committed ──

def test_b0_rule_is_fully_specified():
    assert b0.B0_RULE["tp"] == "opposite_tma_band"
    assert b0.B0_RULE["sl_pct"] == pytest.approx(0.02)
    assert b0.B0_RULE["same_candle_tie_break"] == "sl"


def test_decision_thresholds_are_committed():
    assert b0.DECISION_THRESHOLDS["min_n_for_verdict"] == 800
    assert 0 < b0.DECISION_THRESHOLDS["edge_vs_zero_alpha"] < 1


# ── Position reconstruction: against a throwaway in-memory db, never the
#    live-mounted production file ────────────────────────────────────────────

@pytest.fixture
def fixture_db(tmp_path):
    """A disposable sqlite file with the minimal real schema this script reads
    (closed_trades, position_addons, okx_orders) -- a known, hand-built case
    covering a split position (2 legs, 1 entry) and an addon-affected position,
    mirroring the documented 2026-10-15 XAG addon incident's shape."""
    db_path = tmp_path / "fixture.db"
    conn = __import__("sqlite3").connect(str(db_path))
    conn.execute("""CREATE TABLE closed_trades (
        trade_id TEXT, timestamp TEXT, symbol TEXT, side TEXT, entry_price REAL,
        exit_price REAL, pnl_usd REAL, pnl_net_usd REAL, fee_usd REAL, entry_timestamp TEXT
    )""")
    conn.execute("""CREATE TABLE position_addons (
        symbol TEXT, side TEXT, addon_count INTEGER, position_key TEXT, updated_at TEXT
    )""")
    conn.execute("""CREATE TABLE okx_orders (
        ord_id TEXT PRIMARY KEY, inst_id TEXT, side TEXT, pos_side TEXT, fill_time INTEGER,
        fee REAL, pnl REAL, updated_at TEXT, raw_json TEXT
    )""")
    conn.execute("""CREATE TABLE ohlcv_cache (
        symbol TEXT, timeframe TEXT, timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL
    )""")
    conn.execute("""CREATE TABLE cycle_logs (
        timestamp TEXT, symbol TEXT, signal_type TEXT, state TEXT, leverage REAL, price REAL, outcome TEXT, details TEXT
    )""")
    # A split position: two legs, one shared entry_timestamp -> one Position.
    conn.execute(
        "INSERT INTO closed_trades VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("t1", "2026-09-20T10:05:00+00:00", "XAG-USD_UM_XPERP-310509", "SHORT", 56.63, 56.90, -0.20, -0.25, 0.05, "2026-09-20T10:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO closed_trades VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("t2", "2026-09-20T14:00:00+00:00", "XAG-USD_UM_XPERP-310509", "SHORT", 56.63, 57.78, -1.00, -1.05, 0.05, "2026-09-20T10:00:00+00:00"),
    )
    # An unrelated, non-addon, single-leg position.
    conn.execute(
        "INSERT INTO closed_trades VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("t3", "2026-09-21T08:00:00+00:00", "BTC-USD_UM_XPERP-310404", "LONG", 80000.0, 81000.0, 10.0, 9.9, 0.1, "2026-09-21T06:00:00+00:00"),
    )
    # A leg with no entry_timestamp (pre-09-18-style unreliable row) -- must be excluded.
    conn.execute(
        "INSERT INTO closed_trades VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("t4", "2026-08-01T08:00:00+00:00", "ETH-USD_UM_XPERP-310404", "LONG", 3000.0, 3010.0, 1.0, 0.9, 0.1, None),
    )
    # position_addons row matching the XAG split position's pinned entry (56.63).
    conn.execute(
        "INSERT INTO position_addons VALUES (?,?,?,?,?)",
        ("XAG-USD_UM_XPERP-310509", "short", 1, "56.63000000", "2026-09-20T12:00:00+00:00"),
    )
    # okx_orders fills for the XAG split position: 2 reduceOnly fills, matching its 2 legs.
    for i, ts in enumerate([1789898700000, 1789912800000]):  # 2026-09-20 10:05:00 and 14:00:00 UTC
        conn.execute(
            "INSERT INTO okx_orders VALUES (?,?,?,?,?,?,?,?,?)",
            (f"ord{i}", "XAG-USD_UM_XPERP-310509", "buy", "short", ts, 0.05, -0.2, "2026-09-20T00:00:00+00:00", "{}"),
        )
    conn.commit()
    conn.close()
    return str(db_path)


def test_load_positions_groups_split_legs_into_one_position(fixture_db):
    conn = b0.connect_readonly(fixture_db)
    try:
        positions = b0.load_positions(conn)
    finally:
        conn.close()

    by_symbol = {p.symbol: p for p in positions}
    assert "XAG-USD_UM_XPERP-310509" in by_symbol
    xag = by_symbol["XAG-USD_UM_XPERP-310509"]
    assert xag.leg_count == 2
    assert xag.actual_net_usd == pytest.approx(-0.25 + -1.05)
    assert xag.is_addon_affected is True


def test_load_positions_normalizes_side_to_lowercase(fixture_db):
    """Regression: closed_trades.side is stored UPPERCASE ("LONG"/"SHORT") in
    production, but every downstream comparison in this script (simulate_exit,
    the okx_orders cross-check) uses lowercase "long"/"short" to match
    okx_orders.pos_side. The first full run against real data silently flipped
    every SL/TP direction before this normalization was added -- this test
    pins the fix."""
    conn = b0.connect_readonly(fixture_db)
    try:
        positions = b0.load_positions(conn)
    finally:
        conn.close()
    for p in positions:
        assert p.side in ("long", "short"), f"{p.symbol} side was not normalized: {p.side!r}"


def test_load_positions_derives_realistic_notional_from_real_pnl(fixture_db):
    """Regression: notional must come from the position's own realized
    pnl_usd/pct-move, never from entry_price alone (treating `entry_price * 1`
    as "notional" produced a nonsensical ~$56,000 notional for a position that
    actually traded a few tens of dollars, before this fix)."""
    conn = b0.connect_readonly(fixture_db)
    try:
        positions = b0.load_positions(conn)
    finally:
        conn.close()
    xag = next(p for p in positions if p.symbol == "XAG-USD_UM_XPERP-310509")
    # Derived from the bigger leg (t2: pnl_usd=-1.00, entry=56.63, exit=57.78, short).
    assert xag.notional_usd == pytest.approx(49.24, abs=0.1)
    assert xag.notional_usd < 1000  # sanity bound: nowhere near entry_price-as-notional (~$56)
    assert xag.total_fees_usd == pytest.approx(0.05 + 0.05)


def test_load_positions_excludes_rows_without_entry_timestamp(fixture_db):
    conn = b0.connect_readonly(fixture_db)
    try:
        positions = b0.load_positions(conn)
    finally:
        conn.close()
    symbols = {p.symbol for p in positions}
    assert "ETH-USD_UM_XPERP-310404" not in symbols  # the null-entry_timestamp row must be excluded, not guessed


def test_load_positions_non_addon_position_flagged_correctly(fixture_db):
    conn = b0.connect_readonly(fixture_db)
    try:
        positions = b0.load_positions(conn)
    finally:
        conn.close()
    btc = next(p for p in positions if p.symbol == "BTC-USD_UM_XPERP-310404")
    assert btc.is_addon_affected is False
    assert btc.leg_count == 1


def test_cross_check_okx_fill_count_matches_leg_count(fixture_db):
    conn = b0.connect_readonly(fixture_db)
    try:
        positions = b0.load_positions(conn)
        xag = next(p for p in positions if p.symbol == "XAG-USD_UM_XPERP-310509")
        fill_count = b0.cross_check_okx_fill_count(conn, xag)
    finally:
        conn.close()
    assert fill_count == xag.leg_count == 2


def test_connect_readonly_rejects_writes(fixture_db):
    """The whole point of mode=ro: a write attempt must fail, not silently
    succeed against the (in production) live-mounted database."""
    conn = b0.connect_readonly(fixture_db)
    try:
        with pytest.raises(__import__("sqlite3").OperationalError):
            conn.execute("INSERT INTO closed_trades (trade_id) VALUES ('should_fail')")
    finally:
        conn.close()


def test_check_data_coverage_reports_empty_ohlcv_cache(fixture_db):
    conn = b0.connect_readonly(fixture_db)
    try:
        coverage = b0.check_data_coverage(conn)
    finally:
        conn.close()
    assert coverage["ohlcv_cache_rows"] == 0
    assert coverage["closed_trades"]["n"] == 4
    assert coverage["closed_trades_entry_timestamp_coverage"]["with_entry_timestamp"] == 3
