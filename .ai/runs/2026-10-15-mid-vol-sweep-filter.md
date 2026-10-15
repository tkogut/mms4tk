# Execution plan — mid-volatility sweep-rejection filter (Issue #134)

Engine: om-auto-create-pr (steps: 4, --loop: no)
Branch: `feat/mid-vol-sweep-filter` · Base: `main` · Role: Builder (SWARM)

## Goal
Close the identified gap in `check_sweep_rejection()` coverage (0.8 ≤ VR ≤ 1.6, no
wick requirement) behind a new, default-off, per-symbol flag
(`mid_vol_sweep_filter_enabled`), then run the OOS backtest Issue #134 requires
(primary metric: PnL of the REJECTED trades, not overall PnL) before recommending
whether to enable it for any symbol.

## Scope
- `src/mms4tk/analytics.py`: new `IndicatorParams.mid_vol_sweep_filter_enabled`
  field + the BASE_SHORT/BASE_LONG gate, mirroring the existing compression/
  high-vol filter pattern exactly (20% non-extreme threshold).
- Unit tests mirroring the existing VR-filter test pattern.
- A backtest/report cycle (not shipped as production code) measuring the filter's
  effect per symbol (BTC/ETH/XAU/XAG) across >=4 non-overlapping OOS windows.

## Non-goals
- Two-candle confirmation (Filter B) — explicitly out of scope per the Opus
  consultation recorded on #134 (reward/cost asymmetry hits BTC/ETH hardest).
- Enabling the already-rejected `high_vol_base_filter_enabled` (VR > 1.6, 40%
  threshold) — not being re-litigated.
- Flipping `mid_vol_sweep_filter_enabled` to `True` in production `system_settings`
  without the owner reviewing the backtest report this run produces.

## Risks
- Backtest sample-size: fewer, more selective trades widen the standard error: a
  null/mixed result must be read as "no", not as grounds to ship live per symbol.
- OKX public history-candles depth differs per instrument (XAU/XAG listed
  2025-04-09; BTC/ETH much longer) — windows are sized to what's actually available
  for all four symbols, not assumed.

## Implementation Plan

### Phase 1: Production code + tests
1.1 Add `mid_vol_sweep_filter_enabled` to `IndicatorParams`, default `False`.
1.2 Add the BASE_SHORT/BASE_LONG gate (20% sweep-rejection bar) for
    `adaptive_vr_low <= VR <= adaptive_vr_high`.
1.3 Unit tests: blocked/allowed/disabled-by-default, both sides.

### Phase 2: OOS backtest report (Issue #134's required evidence)
2.1 Fetch sufficient 1H OHLCV history per symbol (OKX public `history-candles`,
    documented proxy instruments) and split into >=4 non-overlapping windows.
2.2 Run `run_backtest()` per symbol per window with the filter OFF (baseline) and
    ON, diff the trade sets to find the REJECTED trades, and report their PnL sign
    per window per symbol (primary metric per #134).
2.3 Write the report into `.ai/analysis/` and post the verdict to Issue #134 —
    recommend enabling (per symbol) only where the bar is cleared.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Production code + tests

- [x] 1.1 mid_vol_sweep_filter_enabled field — d33ef12
- [x] 1.2 BASE_SHORT/BASE_LONG gate — d33ef12
- [x] 1.3 Unit tests — d33ef12

### Phase 2: OOS backtest report

- [x] 2.1 Fetch OHLCV, split windows — common window 2026-01-28..2026-10-15, 4x ~65d
- [x] 2.2 Run baseline backtest, tag would-reject trades — caught and fixed a
      sum-vs-average methodology error along the way (see analysis doc §2)
- [x] 2.3 Report + Issue #134 verdict — `.ai/analysis/2026-10-15-mid-vol-sweep-filter-backtest.md`: **no symbol clears the bar; do not enable for any symbol**
