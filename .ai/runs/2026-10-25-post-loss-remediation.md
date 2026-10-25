# Post-loss remediation: gate off post-only exits, fix production logging, epoch-bump tooling

## Goal

Stop the two most actionable, bug-class problems found while investigating a continued
small daily P&L bleed (-16.83 USD net/7d, -37.80/14d): a correctness risk in the
PR #141 post-only exit path, and a production logging bug that has silently dropped
every INFO-level log line (including the entire post-only code path's own logging)
since the container started using its current entrypoint. Also ship a small,
auditable script to bump `data_collection_epoch_start` instead of ad hoc SQL, so the
clean-trade-count regime marker used by PLAN-008/PLAN-010 can be reset after this
deploy.

These are bug-fix / safety / observability changes, explicitly exempt from the
forward-sample freeze recommended in `.ai/analysis/2026-10-22-b0-baseline-replay.md`
(that freeze targets further ENTRY/EXIT/FILTER rule changes, not fixing a correctness
bug in already-shipped code or restoring log visibility).

## Scope

- Gate `use_post_only` off at both `daemon.py` call sites added by PR #141
  (`StaleAction.SPLIT_50` and the impulse-negation `StaleAction.FORCE_CLOSE` branch).
  The `reduce_position`/`_attempt_post_only_reduce` machinery in `okx_executor.py`
  stays in place (dead code path, covered by its existing tests) so it can be
  re-enabled later once its partial-fill/cancel-race correctness bugs are fixed and
  the logging fix lets us observe it in production.
- Add `logging.basicConfig(...)` to `src/mms4tk/api.py` so INFO-level logs are not
  silently dropped under the real entrypoint (`uvicorn src.mms4tk.api:app`, which
  never executes `main.py`'s `basicConfig` call).
- Add Docker log rotation (`max-size`/`max-file`) for `antigravity_core` in
  `docker-compose.yml`, since turning INFO back on will multiply log volume and the
  current unbounded `json-file` driver has no cap.
- Add `scripts/bump_data_collection_epoch.py`: a small, tested, parameterized CLI
  that sets `data_collection_epoch_start:<SYMBOL>` via `LoggerDB`, replacing the
  undocumented direct-SQL pattern used in past epoch bumps.

## Non-goals

- No new per-position USD risk cap (owner confirmed XAU's $100 allocation_usd,
  raised 2026-10-21, was a deliberate decision — the existing `notional_cap_usd`
  + 10% tolerance guard on HIGH_RISK_ADD already covers addon stacking).
- No fix to the post-only partial-fill/cancel-race bugs in `_attempt_post_only_reduce`
  — the flag is gated off instead, which removes the exposure; fixing those bugs is
  only needed if/when the feature is turned back on.
- No change to `main.py`'s own `basicConfig` call or to the Dockerfile ENTRYPOINT.
- Running `scripts/bump_data_collection_epoch.py` against the production VPS DB
  happens as a manual post-deploy step after this PR merges and is redeployed — not
  part of this PR's own test/deploy flow (no CI access to the production DB).

## Risks

- Disabling post-only exits reverts STALE_SPLIT_50/STALE_FORCE_CLOSE to always
  paying the taker fee again — acceptable: Opus's independent review found the
  feature was worth at most ~$0.70 total since deploy, dwarfed by its correctness
  risk (full-size market fallback after a partial maker fill).
  risk (full-size market fallback after a partial maker fill).
- Turning INFO logging back on increases log volume; mitigated by the log-rotation
  change landing in the same PR.
- `docker-compose.yml` logging-driver change requires `antigravity_core` to be
  recreated (not just rebuilt) to take effect — documented in the summary comment.

## Implementation Plan

### Phase 1: Gate off post-only exits

- Set `use_post_only=False` at the `StaleAction.SPLIT_50` call site.
- Set `use_post_only=False` (was `stale_action == StaleAction.FORCE_CLOSE`) at the
  impulse-negation `FORCE_CLOSE` call site.
- Update/add a regression test asserting `reduce_position` is invoked without
  `use_post_only=True` from these two call sites.

### Phase 2: Fix production logging + add log rotation

- Add `logging.basicConfig(level=..., format=...)` in `src/mms4tk/api.py`, gated by
  an env var (`MMS4_LOG_LEVEL`, default `INFO`) so it can be turned down without a
  code change if volume is a problem.
- Add a `logging:` block (`json-file`, `max-size: "10m"`, `max-file: "5"`) to the
  `antigravity_core` service in `docker-compose.yml`.
- Add a test confirming the root logger ends up with a handler/level after importing
  `mms4tk.api` (can't easily test uvicorn's own startup, but can test the module-level
  side effect).

### Phase 3: Epoch-bump script

- Add `scripts/bump_data_collection_epoch.py`: CLI taking `--symbol` (repeatable)
  and an optional `--at` ISO timestamp (default: now, UTC), calling
  `LoggerDB.set_data_collection_epoch` (add this small helper to `LoggerDB` if it
  doesn't already exist as a named method — check first) for each symbol, printing
  the before/after value.
- Unit test using a disposable in-memory/temp-file `LoggerDB`.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Gate off post-only exits

- [x] 1.1 Set `use_post_only=False` at both daemon.py call sites + regression test — b646fe4

### Phase 2: Fix production logging + add log rotation

- [x] 2.1 Add `logging.basicConfig` to `api.py` (env-controlled level) + test — 134cf49
- [x] 2.2 Add Docker log rotation to `docker-compose.yml` — 134cf49

### Phase 3: Epoch-bump script

- [x] 3.1 Add `scripts/bump_data_collection_epoch.py` + unit test — b2afe22

Status: complete
