# Execution plan — trade_sync warning spam (#124 follow-up)

Engine: om-auto-create-pr (steps: 3, --loop: no)
Branch: `fix/sync-log-spam` · Base: `main` @ ead1ce1 · Issue: #124 (follow-up to PR #126) · Role: Builder (SWARM)

## Goal

After the 0.48.15 deploy, `trade_sync` logged `Entry price for close ... inferred from PnL` ~108 times per minute (once per such close on every pass, ~half of all log lines). Log each condition once per process instead.

## Scope

1. `OKXTradeSyncWorker`: remember which close ids already produced the "entry price inferred" warning and which stale opening orders were already reported as dropped by the FIFO age cap; warn only the first time.
2. Test: two sync passes over the same data produce exactly one warning per condition.
3. Version 0.48.16 (`VERSION`, `src/mms4tk/__init__.py`, `CHANGELOG.md`).

## Non-goals

- No change to the sync logic, fees, schema, or API. Does not modify `.agents/plans/`.

## Risks

- The remembered ids live in memory only (one short string per affected close, bounded by history size); after a restart each is logged once again.

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` **including `slow`**; new test mutation-checked.

## Progress

PR: pending

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Fix

- [ ] 1.1 Warn once per close / stale leg; test, mutation-checked
- [ ] 1.2 Version 0.48.16 and changelog

### Phase 2: Validate

- [ ] 2.1 Full suite including slow: 0 failures; handshake generated and validated
