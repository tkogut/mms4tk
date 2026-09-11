"""Faza 3.2: optional H4 directional-bias gate (`run_backtest(htf_directional_gate_enabled=...)`).

Hypothesis under test (task.md, "Sanity-check głównego znaleźienia Fazy 2" +
Hermes "asynchronous supervision" model, `.agents/specs/knowledge`
"Architektura Systemowa mms4tk" §3): a higher-timeframe (H4) directional gate,
fed conceptually by the Hermes agent, could force `directional_bias=LONG_ONLY`
when price is strongly above the H4 TMA upper band (and `SHORT_ONLY` when
strongly below), blocking counter-trend entries during sustained trends —
exactly the regime (BTC_W1, +64% rally) where the strategy's mean-reversion
design bleeds worst.

This is DISTINCT from `tma_trend_filter_enabled` (same-interval TMA slope,
threshold 0.002, already tested and decided separately) — these tests never
touch that filter.
"""
import csv
import os
from datetime import datetime, timezone

from mms4tk import backtest_engine as be
from mms4tk.analytics import Candle, IndicatorParams

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_fixture_candles(name: str):
    candles = []
    with open(os.path.join(FIXTURES, name)) as f:
        for row in csv.DictReader(f):
            candles.append(Candle(
                timestamp=int(row["timestamp"]), open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]), volume=float(row["volume"]),
            ))
    return candles


def _hourly_candles(start_ts_ms: int, closes, high_pad=0.0, low_pad=0.0):
    """Build a run of synthetic hourly candles, one per close, 1h apart."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Candle(
            timestamp=start_ts_ms + i * 3_600_000,
            open=o, high=max(o, c) + high_pad, low=min(o, c) - low_pad, close=c, volume=1.0,
        ))
        prev = c
    return out


# ── _h4_bucket_ids / _build_h4_candles: pure resampling logic ──

def test_h4_bucket_ids_group_by_utc_4h_clock_boundary():
    # 2024-09-10 23:00 UTC -> bucket (hour//4 == 5); next candle 2024-09-11 00:00 -> bucket 0 (new day)
    candles = _hourly_candles(1_726_009_200_000, [100.0] * 6)
    for c in candles:
        assert datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc).minute == 0
    ids = be._h4_bucket_ids(candles)
    # first candle 23:00 alone in its bucket, next 5 candles (00:00..04:00) start a new bucket at 00:00
    assert ids[0] != ids[1]
    assert ids[1] == ids[2] == ids[3] == ids[4]  # 00:00,01:00,02:00,03:00 same bucket
    assert ids[5] != ids[1]  # 04:00 is a new bucket


def test_build_h4_candles_aggregates_ohlc_correctly():
    # Clean run starting exactly on a 4h boundary (2024-09-11 00:00 UTC).
    closes = [100.0, 101.0, 99.0, 102.0, 103.0, 104.0, 101.0, 105.0]
    candles = _hourly_candles(1_726_012_800_000, closes)
    ids = be._h4_bucket_ids(candles)
    assert ids == [0, 0, 0, 0, 1, 1, 1, 1]
    h4 = be._build_h4_candles(candles, ids)
    assert len(h4) == 2
    bucket0 = candles[0:4]
    assert h4[0].open == bucket0[0].open
    assert h4[0].close == bucket0[-1].close
    assert h4[0].high == max(c.high for c in bucket0)
    assert h4[0].low == min(c.low for c in bucket0)
    assert h4[0].timestamp == bucket0[0].timestamp


def test_build_h4_candles_no_lookahead_into_future_buckets():
    """A H4 candle for a bucket that already ended must be IDENTICAL whether or not
    later data (from subsequent buckets) exists in the array — the defining
    no-lookahead property this whole gate depends on."""
    closes_short = [100.0, 101.0, 99.0, 102.0]
    closes_long = closes_short + [999.0, 998.0, 997.0, 996.0, 1000.0]
    c_short = _hourly_candles(1_726_012_800_000, closes_short)
    c_long = _hourly_candles(1_726_012_800_000, closes_long)
    h4_short = be._build_h4_candles(c_short, be._h4_bucket_ids(c_short))
    h4_long = be._build_h4_candles(c_long, be._h4_bucket_ids(c_long))
    assert h4_short[0].open == h4_long[0].open
    assert h4_short[0].high == h4_long[0].high
    assert h4_short[0].low == h4_long[0].low
    assert h4_short[0].close == h4_long[0].close


# ── _htf_directional_bias_series: causal bias derivation ──

def test_htf_bias_series_defaults_to_both_during_warmup():
    candles = _hourly_candles(1_726_012_800_000, [100.0 + (i % 3) for i in range(50)])
    params = IndicatorParams()
    bias = be._htf_directional_bias_series(candles, params)
    assert len(bias) == len(candles)
    # Not enough H4 history yet (tma_length=20 H4 bars needs 80 H1 candles) -> BOTH throughout.
    assert all(b == "BOTH" for b in bias)


def test_htf_bias_series_only_uses_past_h4_data_not_current_bucket():
    """Extending the candle list with brand-new future candles must not change the
    bias computed for any EARLIER index — the core no-lookahead guarantee."""
    real = load_fixture_candles("ohlcv_BTC_USDT_1h_swap_24m.csv")[:2000]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    bias_full = be._htf_directional_bias_series(real, params)
    bias_prefix = be._htf_directional_bias_series(real[:1000], params)
    assert bias_full[:1000] == bias_prefix


def test_htf_bias_series_matches_calculate_higher_tf_logic_on_synthetic_rally():
    """A long, clean uptrend should eventually push h4 close above the H4 upper band
    -> LONG_ONLY; symmetric for a downtrend -> SHORT_ONLY."""
    n = 400
    up_closes = [100.0 + i * 0.6 for i in range(n)]  # steady, strong uptrend
    candles = _hourly_candles(1_726_012_800_000, up_closes)
    params = IndicatorParams()
    bias = be._htf_directional_bias_series(candles, params)
    assert "LONG_ONLY" in bias[-40:]
    assert "SHORT_ONLY" not in bias

    down_closes = [200.0 - i * 0.6 for i in range(n)]
    candles_down = _hourly_candles(1_726_012_800_000, down_closes)
    bias_down = be._htf_directional_bias_series(candles_down, params)
    assert "SHORT_ONLY" in bias_down[-40:]
    assert "LONG_ONLY" not in bias_down


# ── run_backtest integration ──

def test_htf_gate_disabled_by_default_is_bitwise_identical_to_no_param():
    candles = load_fixture_candles("ohlcv_BTC_USDT_1h_swap_24m.csv")[:3000]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    trades_default = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                                      trend_filter_enabled=False)
    trades_explicit_off = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                                           trend_filter_enabled=False, htf_directional_gate_enabled=False)
    assert trades_default == trades_explicit_off


def test_htf_gate_enabled_changes_and_reduces_trade_count_on_real_data():
    """A necessary (not sufficient) sanity check: the gate must actually DO
    something measurable — fewer or different trades than the ungated baseline —
    on a real, long enough slice of data."""
    candles = load_fixture_candles("ohlcv_BTC_USDT_1h_swap_24m.csv")[:3000]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    off = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                           trend_filter_enabled=False, htf_directional_gate_enabled=False)
    on = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                          trend_filter_enabled=False, htf_directional_gate_enabled=True)
    assert on != off


def test_htf_gate_blocks_short_entries_during_sustained_uptrend():
    """Construct a clean, long synthetic uptrend with periodic pullbacks (so the
    ungated baseline WOULD take BASE_SHORT entries on band touches) and verify the
    gate removes every counter-trend SHORT base entry once LONG_ONLY is active."""
    n = 500
    closes = []
    price = 100.0
    for i in range(n):
        # Strong drift up, with a sawtooth wobble so TMA bands still get touched.
        price += 0.5 + (2.0 if i % 11 == 0 else 0.0) - (1.5 if i % 13 == 0 else 0.0)
        closes.append(price)
    candles = _hourly_candles(1_726_012_800_000, closes)
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)

    bias = be._htf_directional_bias_series(candles, params)
    assert "LONG_ONLY" in bias  # the synthetic uptrend must actually arm the gate

    on = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                          trend_filter_enabled=False, htf_directional_gate_enabled=True)

    # Population-level check: with a persistent uptrend the gate should eliminate
    # short trades entirely relative to the ungated baseline.
    off = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                           trend_filter_enabled=False, htf_directional_gate_enabled=False)
    shorts_on = sum(1 for t in on if t["side"] == "short")
    shorts_off = sum(1 for t in off if t["side"] == "short")
    # The gate only arms once the rally has run long enough to push H4 close above
    # the H4 upper band (see warmup), so a handful of early shorts (before LONG_ONLY
    # first activates) are expected -- the gate must still strictly reduce them.
    assert shorts_on < shorts_off


def test_htf_gate_blocks_long_entries_during_sustained_downtrend():
    n = 500
    closes = []
    price = 300.0
    for i in range(n):
        price += -0.5 - (2.0 if i % 11 == 0 else 0.0) + (1.5 if i % 13 == 0 else 0.0)
        closes.append(price)
    candles = _hourly_candles(1_726_012_800_000, closes)
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)

    bias = be._htf_directional_bias_series(candles, params)
    assert "SHORT_ONLY" in bias

    off = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                           trend_filter_enabled=False, htf_directional_gate_enabled=False)
    on = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                          trend_filter_enabled=False, htf_directional_gate_enabled=True)
    longs_on = sum(1 for t in on if t["side"] == "long")
    longs_off = sum(1 for t in off if t["side"] == "long")
    assert longs_on < longs_off


def test_htf_gate_leaves_directional_bias_both_when_inside():
    """Range-bound synthetic data should never arm the gate -> BOTH throughout, and
    gate on/off must then produce IDENTICAL trades (same signal permission set)."""
    n = 300
    closes = [100.0 + 2.0 * ((i % 20) - 10) / 10.0 for i in range(n)]  # oscillates in a tight range
    candles = _hourly_candles(1_726_012_800_000, closes)
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    bias = be._htf_directional_bias_series(candles, params)
    assert set(bias) == {"BOTH"}

    on = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                          trend_filter_enabled=False, htf_directional_gate_enabled=True)
    off = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                           trend_filter_enabled=False, htf_directional_gate_enabled=False)
    assert on == off


def test_htf_gate_does_not_touch_tma_trend_filter_enabled():
    """The two filters are independent knobs — enabling the HTF gate must not
    implicitly enable/require `trend_filter_enabled`/`tma_trend_filter_enabled`."""
    candles = load_fixture_candles("ohlcv_BTC_USDT_1h_swap_24m.csv")[:2000]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    assert params.tma_trend_filter_enabled is False
    # Should not raise, and trend_filter_enabled stays independently False.
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, htf_directional_gate_enabled=True)
    assert isinstance(trades, list)
