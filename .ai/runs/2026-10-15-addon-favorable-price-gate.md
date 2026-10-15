# Execution plan — Favorable-Price Addon Gate (Issue #137)

Engine: om-auto-create-pr (steps: 5, --loop: no)
Branch: `feat/addon-favorable-price-gate` · Base: `main` · Role: Builder (SWARM)

## Goal
Close Issue #137: `HIGH_RISK_ADD` currently has no awareness of the open
position's entry price and can average into a loss (production incident,
2026-10-15, XAG/USDC). Backtest the hypothesis, then implement a default-off,
per-symbol gate (`addon_requires_favorable_price_enabled`) blocking an add that
would fire on the adverse side of the pinned `base_entry_price`.

## Scope
- `.ai/analysis/2026-10-15-addon-favorable-price-backtest.md`: OOS evidence
  (single-pass diagnostic on the real `run_backtest()` logic).
- `src/mms4tk/analytics.py`: new `IndicatorParams.addon_requires_favorable_price_enabled`.
- `src/mms4tk/backtest_engine.py`: gate the addon condition against `base_entry_price`.
- `src/mms4tk/daemon.py`: (a) wire the new flag into `_get_indicator_params`'s
  `IndicatorParams(...)` construction — and fix the same missing wiring for
  `mid_vol_sweep_filter_enabled` from #135, caught while doing this; (b) gate the
  `HIGH_RISK_ADD` execution branch using `self._base_entry_price(...)`, not
  `match_entry` (today's possibly-already-blended exchange average).
- Tests in both `tests/test_backtest_engine.py` and `tests/test_audit_remediation.py`.

## Non-goals
- Changing the Single Addon Guard, D1 exhaustion filter, or addon SL anchoring (P3 V2).
- Re-litigating the 2026-10-15 base entry (#134, closed) or the H4 gate's metals
  validation (separately closed).

## Risks
- `addon_requires_favorable_price_enabled` and `mid_vol_sweep_filter_enabled` both
  touch `_get_indicator_params`'s construction call — the latter was a genuine
  pre-existing gap (#135 shipped the field but never wired it into production
  config resolution), fixed here as a closely-related correction, not scope creep
  on its own commit.
- Backtest result (14/15 windows, p=0.0010) is strong but still a single-pass
  diagnostic, not a full before/after equity-curve simulation — documented as a
  limitation in the analysis doc.

## Implementation Plan

### Phase 1: OOS backtest (Issue #137's required evidence)
1.1 Single-pass diagnostic on `run_backtest()`, tag `addon_adverse`, compare
    per-trade average net PnL (the corrected #134 metric) across BTC/ETH/XAU/XAG,
    4 non-overlapping windows.
1.2 Write `.ai/analysis/2026-10-15-addon-favorable-price-backtest.md`.

### Phase 2: Production code + tests
2.1 `IndicatorParams.addon_requires_favorable_price_enabled` (default False).
2.2 `backtest_engine.py` addon-condition gate.
2.3 `daemon.py`: wire into `_get_indicator_params` (+ fix the `mid_vol_sweep_filter_enabled`
    wiring gap) and gate the `HIGH_RISK_ADD` execution branch via `_base_entry_price`.
2.4 Unit tests: `test_backtest_engine.py` (3) + `test_audit_remediation.py` (4).

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: OOS backtest

- [x] 1.1 Single-pass diagnostic, per-trade metric — 14/15 windows, p=0.0010
- [x] 1.2 Analysis doc

### Phase 2: Production code + tests

- [x] 2.1 IndicatorParams field
- [x] 2.2 backtest_engine.py gate
- [x] 2.3 daemon.py wiring + mid_vol_sweep_filter_enabled fix
- [x] 2.4 Unit tests — 744/744 full suite green
