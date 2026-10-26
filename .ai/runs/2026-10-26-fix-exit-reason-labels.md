# Fix exit-reason mislabeling in get_closed_trades (TP_AND_FLIP + margin-based SL fallback)

## Goal

Fix two real classification bugs in `LoggerDB.get_closed_trades()` found during an
adversarial audit of the SL-vs-TP dollar asymmetry (`.ai/runs/2026-10-25-post-loss-remediation.md`'s
follow-up investigation, 2026-10-26): (1) `EXECUTED_TP_AND_FLIP_*` outcomes have no
mapping in the `cycle_logs` matcher, so real band take-profits silently fall through
to the PnL-threshold fallback and mostly land as "BREAK-EVEN" instead of
"TAKE-PROFIT"; (2) that same fallback's STOP-LOSS branch thresholds on `pnl_pct`,
which is margin-based (leveraged), not the real price move — at 3x leverage a
~-0.5% price move already crosses the `<= -0.015` threshold, several times more
sensitive than the real ~2% stop-loss distance. Together these distort every
exit-reason-grouped P&L report this project has produced this session (independently
re-derived, this run doesn't trust the audit's numbers blindly — see Phase 1 for the
re-verification against the actual code and a fresh DB query).

This is a pure reporting/classification bug fix — it does not touch any trading
decision, order placement, or risk parameter. Exempt from the forward-sample freeze
(`.ai/analysis/2026-10-22-b0-baseline-replay.md`) on the same basis as #143.

## Scope

- `src/mms4tk/logger_db.py::get_closed_trades()`: add an `elif out.startswith("EXECUTED_TP_AND_FLIP")`
  branch mapping to `"TAKE-PROFIT"` (daemon.py sets
  `outcome = f"EXECUTED_TP_AND_FLIP_{signal.signal_type}"`, e.g.
  `EXECUTED_TP_AND_FLIP_BASE_SHORT` — never matched today since the only existing
  checks are exact `out == "STOP-LOSS"` / `out == "FORCE_CLOSE"`).
- Same function's PnL-threshold fallback: replace the `pnl_pct <= -0.015` (margin-based)
  STOP-LOSS check with a price-move-based check using `entry_price`/`exit_price`
  already present on every `closed_trades` row — mirroring the real stop-loss
  reconciliation logic's own threshold (`daemon.py`'s `is_stop_out` check: adverse
  move `>= base_sl_pct * 0.75`, i.e. ~1.5% of price with the default 2% base SL).
- `scripts/reclassify_exit_reason_labels.py`: a new one-time migration, following the
  exact pattern of the existing `scripts/reclassify_split_50_reason.py` /
  `scripts/reclassify_break_even_reason.py` (clear the cached `reason` on affected
  rows, then call the real, now-fixed `get_closed_trades()` to recompute and persist
  it — no reimplementation of the matching logic in the script).
- Unit tests for both classifier fixes and the new migration script.

## Non-goals

- No change to any trading decision, order, SL/TP placement, or risk parameter.
- No change to the `cycle_logs`-based matcher's existing signal_type branches
  (STALE_FORCE_CLOSE, STALE_SPLIT_50, STALE_FORCE_CLOSE_BE, STALE_BE_ESCAPE,
  MIDLINE_PARTIAL_TP) — those are confirmed correct by the audit.
- Does not re-run the migration against the production VPS database as part of this
  PR — that happens as a manual post-deploy step (same pattern as the epoch-bump
  script in #143), with a mandatory backup first.
- Does not re-litigate the structural TP/SL R:R asymmetry itself (ATR-based SL vs
  fixed %, the winner-only stale-exit layer) — that's a separate, much larger
  decision requiring its own backtest, explicitly out of scope here.

## Risks

- The migration script writes to `closed_trades.reason` — mitigated by the existing
  dry-run-by-default / `--apply`-to-write pattern plus the backup reminder, matching
  the two prior reclassify scripts exactly.
- A trade whose fallback classification changes (e.g. from "STOP-LOSS" to
  "BREAK-EVEN" because the real price move was under 1.5%) changes historical
  reporting, not any stored PnL figure — `pnl_usd`/`pnl_net_usd` are untouched.

## Implementation Plan

### Phase 1: Fix the classifier in `get_closed_trades()`

- Add the missing `EXECUTED_TP_AND_FLIP_*` → `"TAKE-PROFIT"` mapping.
- Replace the margin-based `pnl_pct <= -0.015` STOP-LOSS fallback check with a
  price-move-based one derived from `entry_price`/`exit_price`/`side`.
- Unit tests: one exercising the new TP_AND_FLIP mapping end-to-end (via a synthetic
  `cycle_logs` row), one exercising the corrected fallback threshold (a case that
  used to misclassify under the margin-based check but now classifies correctly
  under the price-move-based one, and a case right at the boundary).

### Phase 2: Add the relabeling migration script

- `scripts/reclassify_exit_reason_labels.py`, modeled on
  `scripts/reclassify_split_50_reason.py`: dry-run by default, `--apply` to clear +
  reclassify. Targets rows whose cached `reason` could plausibly change under the
  Phase 1 fix — any row currently "BREAK-EVEN" or "STOP-LOSS" (the two fallback
  outcomes affected) is a candidate; the script reports before/after per row so the
  operator can see exactly what changed before trusting it.
- Unit test using the same `importlib`-based pattern as
  `tests/test_reclassify_split_50_reason.py`.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Fix the classifier in `get_closed_trades()`

- [x] 1.1 Add `EXECUTED_TP_AND_FLIP_*` mapping + price-move-based STOP-LOSS fallback + tests — 5902d7f

### Phase 2: Add the relabeling migration script

- [x] 2.1 Add `scripts/reclassify_exit_reason_labels.py` + unit test — 8c3d841

Status: complete
