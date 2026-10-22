# Execution plan: historical replay of past entries under a frozen B0 baseline

Source doc: `.ai/specs/2026-10-22-historical-replay-b0-baseline.md`

## Goal

Build a read-only diagnostic script that replays real historical entries under a
frozen, simpler exit policy (B0: fixed SL, TP at the opposite TMA band, no
STALE/BE/addon/H4-gate machinery) and reports whether the entry signal shows
measurable edge, both on its own and against what actually happened — answering
the question a 2026-10-21 debate found the current protective-exit layer makes
unobservable. Phase 2 adds a second exit-policy variant (B0+adverse-brake) that
mirrors the live system's profit-side Giveback/Stall rule onto the loss side.

## Scope

In scope: a new script under `.ai/scripts/` (or wherever the 2026-10-14 analysis's
own tooling lives — confirmed in Step 1), reusing `TMAIndicator`, `Candle`,
`IndicatorParams` from `mms4tk.analytics` and `LoggerDB` (read-only) unchanged;
position reconstruction via the existing `cycle_logs`-based `entry_timestamp`
derivation, cross-checked against `okx_orders`; the B0 forward-walk simulator;
both pre-registered statistical tests; the markdown report + CSV/JSON sidecar.

Out of scope (per the spec's Non-goals): any production code change, any live
trading behavior change, the maker/post-only execution change (already shipped,
PR #141), re-litigating "should mms4tk resemble the simpler MasterMind-article
model," and settling the freeze-vs-no-freeze process question (surfaced as a
recommendation only).

## Risks

- Data coverage: `ohlcv_cache`/`entry_timestamp` reliability window is unverified
  until Step 1 runs — the spec requires disclosing the actual usable window and
  sample-size impact rather than assuming the ideal case.
- Statistical risk: even the full available sample may be underpowered (the
  2026-10-14 analysis found ~800-1600 closes needed for an effect this size) —
  the report must say so plainly, never overclaim a verdict.
- Methodology risk (pre-registration): the B0 rule and decision thresholds must
  be committed *before* Step 5/the full-dataset run, not derived after seeing
  results — Step 3 exists specifically to enforce this ordering.
- Addon-affected positions need a disclosed, non-blended comparison (base-size
  only on both sides, plus a scale-independent percentage view) or the
  comparison silently conflates two different notional sizes.

## Implementation Plan

### Phase 1: B0 core replay (spec Phase 1, Steps 1-6)

- [ ] 1.1 Verify data coverage (`ohlcv_cache` date range per symbol/timeframe; `entry_timestamp` grouping reliability window) — produces a findings note fixing the actual sample window.
- [ ] 1.2 Position reconstruction: extend/reuse `LoggerDB.get_closed_trades()`'s `cycle_logs` join; group legs by `(symbol, side, entry_timestamp)`; cross-check against `okx_orders`; flag addon-affected positions.
- [ ] 1.3 Pre-register the B0 rule (TP = opposite TMA band recomputed per candle; SL = `base_sl_pct` anchored to entry; no stale/BE/addon/gate; fee assumption) and the decision thresholds, committed before Step 1.5 runs on the full dataset.
- [ ] 1.4 Forward-walk B0 simulator: first-touch exit (TP/SL/censored) using candle high/low, documented same-candle tie-break rule.
- [ ] 1.5 Compute both pre-registered tests: B0-vs-zero, and B0-vs-actual paired per position.
- [ ] 1.6 Write the report (`.ai/analysis/{date}-b0-baseline-replay.md` + sidecar), including the Phase-1-owned freeze/forward-sample recommendation.

### Phase 2: B0+adverse-brake variant (spec Phase 2, Steps 7-9)

- [ ] 2.1 Implement the mirrored adverse-move exit rule as new script-local logic (never importing the live stateful `InactivityManager`), state the mirrored formula explicitly before coding it.
- [ ] 2.2 Extend the report to a three-way comparison (actual / B0 / B0+adverse-brake), re-running both pre-registered tests for the new variant.
- [ ] 2.3 Variant-specific recommendation (not duplicating Phase 1's general freeze recommendation).

### Phase 3: Validation and documentation

- [x] 3.1 Full validation gate — 782/782 pytest passed (754 baseline + 28 new)
- [x] 3.2 CHANGELOG/VERSION bump — 87c08c4, v0.48.25

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: B0 core replay (spec Phase 1, Steps 1-6)

- [x] 1.1 Verify data coverage — 194ee5f (real finding: ohlcv_cache empty, 77% entry_timestamp coverage)
- [x] 1.2 Position reconstruction — 194ee5f (+ fix: side-case normalization, notional derivation — 11318f8)
- [x] 1.3 Pre-register B0 rule + decision thresholds — 194ee5f
- [x] 1.4 Forward-walk B0 simulator — 194ee5f
- [x] 1.5 Compute both pre-registered tests — 194ee5f
- [x] 1.6 Write the report — 11318f8 (real run: n=219, both tests INCONCLUSIVE/underpowered, freeze recommendation included)

### Phase 2: B0+adverse-brake variant (spec Phase 2, Steps 7-9)

- [x] 2.1 Mirrored adverse-move exit rule — 194ee5f
- [x] 2.2 Three-way report comparison — 11318f8
- [x] 2.3 Variant-specific recommendation — 11318f8

### Phase 3: Validation and documentation

- [ ] 3.1 Full validation gate
- [ ] 3.2 CHANGELOG/VERSION bump
