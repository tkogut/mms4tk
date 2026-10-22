# Execution plan: maker (post-only) execution for STALE_SPLIT_50/FORCE_CLOSE exits

Source doc: `.ai/specs/briefs/2026-10-22-maker-post-only-stale-exits.md`

## Goal

Stop paying OKX taker fees on the two stagnation-ladder exits (STALE_SPLIT_50 and the
impulse-negation STALE_FORCE_CLOSE) whose thin gross edge the fee gap now exceeds
(confirmed net-negative in the 2026-10-15–21 sample: −5.55 USD net on +1.30 USD gross),
by routing them through a passive maker (post-only) limit order first, with a bounded
timeout and an unconditional fallback to today's guaranteed market order if it doesn't
fill in time.

## Scope

In scope: `OKXExecutor.reduce_position()` (`src/mms4tk/okx_executor.py`) gets an opt-in
post-only path; the two call sites in `daemon.py` behind `StaleAction.SPLIT_50` and
`StaleAction.FORCE_CLOSE` (impulse-negation full cut — **not** `FORCE_CLOSE_BE`, which is
the grace-period-expired close and is out of scope per the brief) opt into it.

Out of scope (brief's Non-goals, confirmed against the actual code during triage):
- `FORCE_CLOSE_BE` and `BE_ESCAPE` share the exact same `daemon.py` branch
  (`elif stale_action in (StaleAction.FORCE_CLOSE_BE, StaleAction.BE_ESCAPE,
  StaleAction.FORCE_CLOSE):`) as the in-scope `FORCE_CLOSE` — the branch must
  discriminate on which `StaleAction` fired, not treat the whole branch as in-scope.
- `MIDLINE_PARTIAL_TP` (line ~1664) and `TAKE_PROFIT_*` (line ~2033) also call the same
  shared `reduce_position()` — they must keep today's exact market-order behavior by
  simply never passing the new flag (default `False`).
- Changing the stagnation-ladder trigger logic, SL/TP order types, or entry-signal logic.

## Risks

- Fill-rate risk on a genuinely fast move: mitigated by the bounded timeout + guaranteed
  market fallback (the whole point of the brief's "explicitly rejected: pure post-only").
- Shared-function blast radius: `reduce_position()` is called by 4 distinct reasons;
  the new parameter must default to `False` so the 2 out-of-scope callers are provably
  unaffected — covered by a regression test, not just code review.
- Real-money order-type change: validated with mocked-exchange unit tests (fill path,
  timeout-then-fallback path, cancel-failure-still-falls-back path), not live trading,
  per the brief's risk signal.

## Implementation Plan

### Phase 1: Post-only execution path + wiring

- [ ] 1.1 Add `_attempt_post_only_reduce()` helper and `use_post_only`/`post_only_timeout_seconds` params to `OKXExecutor.reduce_position()` (`src/mms4tk/okx_executor.py`) — places a passive (non-crossing) limit order with `postOnly: True`, polls `fetch_order` until filled or timeout, cancels and returns `None` on timeout so the caller's existing market-order path runs unchanged.
- [ ] 1.2 Wire `use_post_only=True` into the `StaleAction.SPLIT_50` call site in `daemon.py` (~line 1802).
- [ ] 1.3 Wire `use_post_only=True` into the `StaleAction.FORCE_CLOSE` case only, inside the shared `elif stale_action in (FORCE_CLOSE_BE, BE_ESCAPE, FORCE_CLOSE):` branch (~line 1876) — `FORCE_CLOSE_BE`/`BE_ESCAPE` must NOT receive the flag.
- [ ] 1.4 Unit tests (new file, mocked `self.exchange` per the `test_okx_executor_fetch_candles.py` convention): maker fill before timeout; maker timeout → cancel → market fallback; cancel-call itself fails → still falls back to market (never raises up); regression — every existing caller (no `use_post_only` kwarg) still issues a plain market order, byte-for-byte unchanged `params`.

### Phase 2: Validation and documentation

- [ ] 2.1 Run the full validation gate (flake8, mypy, pytest, build).
- [ ] 2.2 CHANGELOG.md entry + VERSION/`__init__.py` bump, following this repo's established emoji-section style.

## Progress

PR: #141

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Post-only execution path + wiring

- [x] 1.1 Add `_attempt_post_only_reduce()` helper and params to `reduce_position()` — 68eb9d8
- [x] 1.2 Wire SPLIT_50 call site — 5ce223c
- [x] 1.3 Wire FORCE_CLOSE-only branch (guard against FORCE_CLOSE_BE/BE_ESCAPE) — 5ce223c
- [x] 1.4 Unit tests (fill / timeout-fallback / cancel-failure-fallback / regression) — c6794d2

### Phase 2: Validation and documentation

- [x] 2.1 Full validation gate — 753/753 pytest passed, 0.48.24
- [x] 2.2 CHANGELOG/VERSION bump — 4f25f97
