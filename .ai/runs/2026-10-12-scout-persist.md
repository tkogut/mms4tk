# Execution plan — SCOUT_MODE persisted per symbol (E1)

Engine: om-auto-create-pr (steps: 9, --loop: no)
Branch: `fix/scout-persist` · Base: `main` · Role: Builder (SWARM)

## Goal

SCOUT_MODE state survives a daemon restart **per symbol**, without leaking between symbols, and adding/editing a pair via the API no longer silently resets it to BASE_MODE.

## Context (verified in code on `main` @ 3092905)

Stage **E1** of a 5-stage fix for "SCOUT_MODE has no reliable per-symbol exit" (E2 `finalize_close`, E3 K=3 counter, E4 per-symbol `scout_override`, E5 24h `SCOUT_STALE` alert ship as separate PRs; they all depend on this one).

- `state_history` has no `symbol` column; the symbol only lives inside the free-text `reason` (`state_manager.py:162` writes `"[SYM] ..."`).
- `MMS4StateManager.__init__` (`state_manager.py:135-139`) and `set_logger_db` (`:210-216`) read only the **latest row** and assign it to the **primary** trading symbol. After a restart a non-primary symbol (e.g. XAG) silently returns to BASE_MODE without any take-profit, and if the last row belongs to XAG, the primary symbol (BTC) is wrongly put into SCOUT_MODE.
- `api.py:1949` unconditionally calls `set_symbol_state(sym, BASE_MODE, ...)` on every pair-configuration update, wiping an active SCOUT_MODE (even right after a real stop-out).
- `state_manager.save_state()` (`:347`, used by `api.py:1498`) writes the primary state with no symbol tag.

## Scope

1. `logger_db`: idempotent `symbol` column on `state_history` (same `PRAGMA table_info` + `ALTER TABLE` pattern as `closed_trades`), best-effort backfill from the `[SYM]` prefix in `reason`; `save_state(..., symbol=None)`; new `get_latest_symbol_states()` returning the latest row **per symbol**.
2. `state_manager`: rehydrate the whole `_symbol_states` map in `__init__` and `set_logger_db` through one shared helper; `_current_state` becomes the state of `trading_symbol` only; pass `symbol=` on every write, including `save_state()`.
3. `api.py:1949`: a pair-config update must not downgrade an existing SCOUT_MODE (unknown symbol stays BASE by default, so the call becomes a no-op for state).
4. Patch release: bump version in the 4 SSOT files + CHANGELOG.

## Non-goals

- No change to **when** SCOUT is entered or left (that is E2/E3). No `finalize_close`, no K counter, no expiry timer, no alerts. Per-symbol `scout_override`/HITL is limited to the minimal slice added in Phase 5 (optional `symbol` on two endpoints).
- No change to leverage/size-multiplier logic or to `backtest_engine.py` (persistence is not modeled there).
- No manual reset of XAG's current SCOUT state; it keeps its state through the deploy, which is the point.
- Does not modify `.agents/plans/` (Coordinator-owned).

## Risks

- **Production DB migration** (`/app/.agents/swarm/logs.db`): additive nullable column only; `CREATE`/`ALTER` guarded so it is idempotent and safe on re-run. Deploy under maintenance lock (`scripts/maintenance.py --enable` / `--disable`) after taking a DB backup — an ops step for the Coordinator, not performed by this PR.
- **Behavior change on first restart after deploy**: symbols whose latest SCOUT row is only recoverable from the legacy `[SYM]` prefix are restored via the backfill; rows with neither a symbol nor a prefix are treated as the primary symbol's (previous behavior), never applied to other symbols.
- Existing tests that assume "latest row → primary symbol" may need updating (`tests/test_state_persistence.py`).

## Validation

`validation.commands` from `.ai/agentic.config.json` (flake8/mypy are `|| true`; pytest is the real gate): `MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v`. Never run pytest against the production container.

## Progress

PR: #106

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Persistence layer (logger_db)

- [x] 1.1 Add idempotent `symbol` column to `state_history` with backfill from `[SYM]` reason prefix — 3ea0f63
- [x] 1.2 Add `symbol` argument to `save_state` and `get_latest_symbol_states()` — 3ea0f63

### Phase 2: State manager rehydration

- [x] 2.1 Rehydrate per-symbol states in `__init__` and `set_logger_db` via one shared helper — cc4e4d1
- [x] 2.2 Tag every state write with its symbol (`set_symbol_state`, `save_state`) — cc4e4d1
- [x] 2.3 Tests: restart keeps XAG in SCOUT and BTC in BASE, no cross-symbol leak, legacy rows — cc4e4d1

### Phase 3: API reset removal

- [x] 3.1 Stop `api.py` pair-config update from downgrading an active SCOUT_MODE — 9ef475b
- [x] 3.2 Test: pair-config update preserves SCOUT_MODE and leaves unknown symbols in BASE — 9ef475b

### Phase 4: Release

- [x] 4.1 Bump version in `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `CHANGELOG.md` — 9af3a06

### Phase 5: Per-symbol override (minimal slice of E4, added after review finding M1)

- [x] 5.1 Optional `symbol` on `/strategy/profile` and `/hitl/resolve`, passed to `relax_scout_mode(symbol)`; Hermes tool schemas expose it
- [x] 5.2 Tests: override/HITL with `symbol` relaxes only that symbol; omitted `symbol` keeps primary-only behavior
