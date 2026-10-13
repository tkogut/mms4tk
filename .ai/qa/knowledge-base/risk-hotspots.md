# QA risk hotspots

## Test process

- **`pytest -m "not slow"` hides real regressions** — a backtest-behavior change (SCOUT expiring in `run_backtest`) turned `test_p1_addon_sl_gate_analysis` red, and a first pass with `-m "not slow"` missed it; always run the full `tests/` before approving. *Seen while testing PR #113 — independent review caught it.*
