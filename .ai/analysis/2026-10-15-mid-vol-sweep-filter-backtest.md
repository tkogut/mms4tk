# Issue #134 Phase 2 — OOS backtest: mid-volatility sweep-rejection filter

**Verdict: do not enable `mid_vol_sweep_filter_enabled` for any symbol.** No symbol
clears the evidence bar Issue #134 set, once the metric is computed correctly — see
§2 for a methodology error in this run's own first pass, caught and corrected
before this verdict was written.

## 1. Setup

- Common OHLCV window across all 4 symbols (proxy instruments, same as prior
  session analyses): `2026-01-28` → `2026-10-15` (XAG/USDT-SWAP, listed 2025-04-09,
  is the binding constraint — BTC/ETH/XAU have longer history available but were
  truncated to match, so all four symbols are compared on the identical period).
  Split into 4 non-overlapping windows of ~65 days each, 40-candle warmup per window.
- `run_backtest()` (the real production engine classes, baseline `IndicatorParams`,
  `mid_vol_sweep_filter_enabled=False`) run once per symbol per window — a
  **single-pass** measurement, not a two-simulation diff: for every BASE_LONG/
  BASE_SHORT entry the baseline actually took, VR and the entry candle's sweep-
  rejection wick ratio were independently recomputed from the same OHLCV, and the
  trade tagged "would-reject" if `adaptive_vr_low <= VR <= adaptive_vr_high` (0.8–1.6)
  and the wick ratio was `< 20%`.

## 2. A methodology error caught before reporting

The first pass compared the **sum** of `pnl_pct` in the would-reject bucket against
zero, window by window — sums were negative in 11/16 symbol-windows, which looked
like a clean result. That metric is confounded: **44–77% of all entries fall into
the would-reject bucket** (this filter is not a narrow edge-case patch — VR sits in
the 0.8–1.6 band most of the time for this strategy, so requiring a wick there
affects the *majority* of signals, not a sliver). With that much more volume in the
reject bucket, its sum is negative more often than not simply because the whole
strategy's edge is close to zero in many windows (consistent with
`.ai/analysis/2026-10-14-sl-loss-attribution.md`'s own finding: "brak wykazanego
edge") — not because those specific trades are worse quality.

**Corrected metric: per-trade average PnL, reject bucket vs keep bucket, same
window.** This is the comparison that actually tests "does this filter remove bad
trades specifically," independent of bucket size.

## 3. Results (corrected metric)

| symbol | w1 avg reject% | w1 avg keep% | w2 reject% | w2 keep% | w3 reject% | w3 keep% | w4 reject% | w4 keep% | reject WORSE than keep |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | −0.178 | +0.078 | −0.003 | −0.052 | +0.080 | +0.135 | +0.066 | +0.007 | **2/4** |
| ETH | −0.107 | −0.279 | −0.098 | −0.201 | +0.071 | +0.146 | −0.036 | +0.242 | **2/4** |
| XAU | −0.170 | −0.110 | −0.135 | +0.126 | −0.039 | −0.081 | +0.112 | +0.002 | **2/4** |
| XAG | −0.179 | −0.132 | −0.106 | −0.098 | −0.171 | +0.201 | +0.208 | −0.124 | **3/4** (w2 margin razor-thin: −0.106 vs −0.098) |

No symbol reaches the required **3/4 windows, reject-bucket-worse** bar cleanly.
XAG's 3/4 includes a window (w2) where the difference (−0.106 vs −0.098) is well
within noise for ~70-trade buckets. In several windows (ETH w1/w2, XAU w2, XAG w4)
the *kept* trades were actually net worse than the *rejected* ones — the filter
would, in those windows, have thrown out the relatively better half.

## 4. Conclusion

- **The filter does not demonstrate it catches bad trades on this data.** This
  matches the pattern of both previously-tested filters in this file
  (`tma_trend_filter_enabled`, `high_vol_base_filter_enabled`) and the broader,
  already-documented finding that this strategy has no demonstrated edge at the
  current sample size (`.ai/analysis/2026-10-14-sl-loss-attribution.md` §1.4).
- **Scope correction:** this is not the narrow "patch a gap" change the issue was
  originally framed as — at 44-77% rejection, enabling it would be a near-universal
  tightening of the entry condition, not an edge-case fix. Any future re-test of
  this idea should account for that scale from the start.
- `mid_vol_sweep_filter_enabled` ships in this PR as dead-by-default infrastructure
  (consistent with the existing `*_filter_enabled` pattern) — **do not flip it to
  `True` in `system_settings` for any symbol** based on this evidence.

## 5. Limitations

- Proxy OHLCV (public swap instruments), not the exact traded EEA XPERP series —
  same documented caveat as every other backtest in this codebase.
- Single-pass would-reject tagging assumes the rest of the position's lifecycle
  (sizing, SCOUT_MODE, exits) is unaffected by the entry being blocked — a true
  two-simulation run would diverge further over time (a blocked entry changes
  subsequent state), but would also make "which trade corresponds to which" far
  harder to attribute; the single-pass method directly answers the question this
  issue actually asked ("are the entries this rule would reject bad entries") at the
  cost of not modeling second-order effects of fewer trades on SCOUT_MODE sizing.
- 4 non-overlapping windows, ~65 days / ~80-140 trades each — same sample-size
  caution as every other measurement in this project's `.ai/analysis/` history.
