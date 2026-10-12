# Execution plan — SCOUT_MODE exit after K clean closes, per symbol (E3 of #107)

Engine: om-auto-create-pr (steps: 9, --loop: no)
Branch: `feat/scout-exit-k` · Base: `main` @ b6cf5b2 (E1 #106 and E2 #111 merged) · Issue: #112 · Umbrella: #107 · Role: Builder (SWARM)

## Goal

A symbol leaves SCOUT_MODE after **K = 3 consecutive qualifying full closes** (counter per symbol, persisted, reset by a stop-out or a losing close), in addition to the existing exits (full take-profit, per-symbol `scout_override`). This is the first change that **deliberately** alters which closes leave SCOUT.

## Decisions (owner, 2026-10-12 — recommendations 1-5 of #112 accepted)

1. A losing counting close **resets** the counter ("K in a row"), it does not merely fail to increment it.
2. `BE_ESCAPE` / `FORCE_CLOSE_BE` do **not** count (and do not reset).
3. `MIDLINE_INVALIDATION` counts, by the same threshold rule as `FORCE_CLOSE`.
4. Threshold = measured round-trip cost: `pnl_percent > 0.0010003` (gross > cost ⇔ net > 0).
5. Stepped return to full size is out of scope.

## Semantics (single table, applied in `finalize_close`)

| Close | Effect on the counter of a symbol that is in SCOUT |
|---|---|
| Full TP (`TP_AND_FLIP`, `D1_RANGE_TP`, `MIDLINE_FULL_TP`, `CLOSED_EXTERNAL_TP`) | exits immediately (unchanged), counter cleared |
| `STOP_LOSS` | reset to 0 |
| `FORCE_CLOSE`, `MIDLINE_INVALIDATION` with `pnl_percent > SCOUT_MIN_GROSS_PNL_PCT` | +1; at K → `BASE_MODE`, reason `SCOUT_EXPIRED_K`, counter cleared |
| `FORCE_CLOSE`, `MIDLINE_INVALIDATION` otherwise (net loss) | reset to 0 |
| `FORCE_CLOSE_BE`, `BE_ESCAPE`, `CLOSED_EXTERNAL`, partials (`STALE_SPLIT_50`, `MIDLINE_PARTIAL_TP`) | neutral |
| Any entry into / exit from SCOUT | counter cleared |

Replaying XAG's real closes since its 2026-10-07 stop through this table gives 1/3 today (10-09 +0.20% counts; 10-10 -0.56% resets; 10-11 +0.02% is a net loss and resets; 10-12 +0.18% counts) — XAG would **not** be released yet; the next two clean closes would.

## Context (verified on `main` @ b6cf5b2)

- `MMS4StateManager.finalize_close` (`state_manager.py:~561`) is the only close entry point (E2); `backtest_engine` books through it too, so the counter gets backtest↔prod parity if it receives the same inputs (gross `pnl_percent`).
- `_restore_symbol_states_from_db` (`~180`) rebuilds per-symbol state (E1); `set_symbol_state` (`~226`) is the single state-change point.
- `LoggerDB.get_setting/set_setting/get_all_settings/delete_setting` (`logger_db.py:~1700-1745`) give a key-value store without a schema change.
- `ROUND_TRIP_FEE_PCT = 0.0010003` lives in `backtest_engine.py:88` (measured from production); `state_manager` must not import from the backtest, so the threshold gets its own constant.

## Scope

1. `state_manager.py`: `SCOUT_EXIT_K` (env `MMS4_SCOUT_EXIT_K`, default 3) and `SCOUT_MIN_GROSS_PNL_PCT` (env `MMS4_SCOUT_MIN_GROSS_PNL_PCT`, default 0.0010003); per-symbol counter `_scout_ok`; persistence under `system_settings` key `scout_ok:{symbol}`; restore on startup; clear on every SCOUT transition.
2. `finalize_close`: apply the semantics table after `record_trade`, inside the existing safety net (a counter failure must never break a close or the #99 restore).
3. Tests: semantics, XAG replay, per-symbol isolation, persistence/restart, backtest parity.
4. Docs: `state_manager.py` class docstring and `BACKWARD_COMPATIBILITY.md:42` (protected SCOUT→BASE contract extended), CHANGELOG, version 0.48.12.

## Non-goals

- No `SCOUT_STALE` alert / `scout_since` (E5), no stepped return to full size, no change to override/HITL (done in #106), no change to how closes are booked (E2) or to `closed_trades`.
- Does not touch `.agents/plans/` (Coordinator-owned). #108 / #109 are separate issues.

## Risks

- **A symbol can return to full size and 3x leverage without a take-profit** — the point of the change, and the reason for `risk-high`. Mitigations: K = 3, threshold above round-trip cost, stop and losing closes reset.
- **Existing exits must not regress** (the #99 class): full TP must still exit immediately, partial closes must stay neutral — covered by tests.
- **Restart behavior:** a missing/garbled `scout_ok:{symbol}` key restores 0 (safe: the symbol just needs K more closes).
- **Backtest parity:** `pnl_percent` handed to `finalize_close` must be gross in both worlds; checked by a parity test.

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` (never against the production container). Known unrelated failures on `main`: the two position-sizing tests (#109).

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Counter state

- [ ] 1.1 Add `SCOUT_EXIT_K` and `SCOUT_MIN_GROSS_PNL_PCT` constants with env overrides
- [ ] 1.2 Add the per-symbol counter with persistence under `scout_ok:{symbol}` and restore on startup
- [ ] 1.3 Clear the counter on every entry into or exit from SCOUT_MODE

### Phase 2: Exit rule

- [ ] 2.1 Apply the counting semantics table in `finalize_close`, inside the existing safety net
- [ ] 2.2 Exit to BASE_MODE with reason `SCOUT_EXPIRED_K` when the counter reaches K

### Phase 3: Tests

- [ ] 3.1 Tests: semantics per close type and the XAG replay (1/3 today)
- [ ] 3.2 Tests: per-symbol isolation, restart persistence, failure safety, existing exits unchanged
- [ ] 3.3 Test: backtest drives the same counter through `finalize_close` (parity)

### Phase 4: Docs and release

- [ ] 4.1 Update the SCOUT contract docstring and `BACKWARD_COMPATIBILITY.md:42`
- [ ] 4.2 Bump version to 0.48.12 in `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `CHANGELOG.md`
