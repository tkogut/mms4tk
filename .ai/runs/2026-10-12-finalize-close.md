# Execution plan — `finalize_close`: one place that books every position close (E2 of #107)

Engine: om-auto-create-pr (steps: 11, --loop: no)
Branch: `feat/finalize-close` · Base: `main` @ 23efcae (E1/#106 merged) · Issue: #110 · Umbrella: #107 · Role: Builder (SWARM)

## Goal

Every way a position can disappear is booked through ONE function, `MMS4StateManager.finalize_close(...)`, driven by ONE reason→flags table shared by the daemon and the backtest. **No change to which closes enter or leave SCOUT_MODE** (that is E3). A future close path can no longer skip the state machine.

## Context (verified on `main` @ 23efcae)

| Close path | Where (`daemon.py`) | Books via | Flags today |
|---|---|---|---|
| Reconciliation (stop / external close) | `~661` | `record_trade` | `is_stop_loss=is_stop_out`, `is_take_profit=(not is_stop_out and pnl>0)` |
| Midline partial TP | `~1482` | `record_trade` | TP + `is_partial` |
| TP + flip on opposite TMA band | `~1821`, explicit `on_take_profit_hit` `~1839` | `record_trade` + direct hook | TP |
| `STALE_SPLIT_50` | `~1572-1641` | **nothing** | backtest books it as partial TP |
| `STALE_FORCE_CLOSE` / `FORCE_CLOSE_BE` / `BE_ESCAPE` | `~1642-1660` | **nothing** | backtest: neither SL nor TP |

- `backtest_engine.py:171-207` (`_trade_record_flags`) holds a second copy of the reason→flags table; pinned by `tests/test_backtest_engine.py:337`.
- `state_manager.record_trade` (`~460-515`) appends to `_trade_history`, adds to `_realized_pnl` / `_current_equity`, then calls the SL/TP hooks and `log_state_metrics`.
- **Equity double-count check (issue #110 open question) — resolved:** `set_equity()` is never called outside `state_manager.py`, so `_current_equity` is a local accumulator, not the exchange balance; `_trade_history` feeds only `trade_count` in `state_logs` and `total_trades` in the status payload. Booking the two missing paths changes those metrics, never a risk or sizing decision. `closed_trades` is written elsewhere (`logger_db.upsert_closed_trade`, `trade_sync`); `finalize_close` does not touch it.
- `reduce_position` returns the raw exchange order (no PnL), so stale-path PnL has to be estimated the way the midline path already does it (`daemon.py:~1464-1466`: `pnl_pct * entry * contracts * ct_size`).
- #99 invariant: the SCOUT→BASE restore on a full TP must fire even if bookkeeping raises (`daemon.py:~1834-1839`). It must survive the refactor.

## Scope

1. `state_manager.py`: `CLOSE_REASON_FLAGS` (single source of truth) + `finalize_close(symbol, side, reason, entry_price, exit_price, amount, pnl, pnl_percent, sl_percent=0.0, trade_id=None)`; an unknown reason raises.
2. `backtest_engine.py`: `_trade_record_flags` reads the shared table; closes go through `finalize_close`.
3. `daemon.py`: all five paths call `finalize_close` (the two missing ones are added with estimated PnL).
4. A guard test: nothing in `daemon.py` calls `record_trade` / `on_take_profit_hit` / `on_stop_loss_hit` directly.
5. Patch release 0.48.11 + CHANGELOG.

## Non-goals

- No change to SCOUT entry/exit rules: `FORCE_CLOSE*`/`BE_ESCAPE` stay "neither SL nor TP", `STALE_SPLIT_50` stays a partial TP (SCOUT preserved). Those are E3.
- No K=3 counter, no expiry, no alert (E3/E5); no per-symbol override work (done in #106).
- No change to `closed_trades` writes, DB schema, sizing, or leverage.
- Does not touch `.agents/plans/` (Coordinator-owned). #108 / #109 are separate issues.

## Risks

- **Parity backtest↔prod** (project rule): both must read the same table; the existing parity tests must pass unchanged.
- **Metrics change on two paths:** `trade_count` / `realized_pnl` / the `state_logs` equity accumulator now include stale closes (documented in CHANGELOG; no decision depends on them).
- **Estimated PnL** for stale closes is derived from the current price, like the midline path; it is a ledger estimate, not the fill.
- Variable scope in the stale block: `existing_contracts` is only bound inside the midline branch, so the stale path must read contracts from `active_pos` explicitly.

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` (never against the production container). Known unrelated failures on `main`: the two position-sizing tests (#109).

## Progress

PR: #111

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Shared table and `finalize_close`

- [x] 1.1 Add `CLOSE_REASON_FLAGS` to `state_manager.py` with values identical to the backtest table — 5199a93
- [x] 1.2 Add `MMS4StateManager.finalize_close(...)`; unknown reason raises — 5199a93
- [x] 1.3 Tests: table values, `finalize_close` flags, FORCE_CLOSE on profit keeps SCOUT, SL enters SCOUT, full TP exits it — 5199a93

### Phase 2: Backtest on the shared table

- [x] 2.1 `backtest_engine` reads the shared table and books closes via `finalize_close` — b025863
- [x] 2.2 Keep `_trade_record_flags` as a thin delegate so `tests/test_backtest_engine.py:337` and the parity tests pass unchanged — b025863

### Phase 3: Daemon paths

- [x] 3.1 Reconciliation, midline and TP+flip call `finalize_close`, keeping the #99 unconditional SCOUT restore — 31cb456
- [x] 3.2 `STALE_SPLIT_50` and `FORCE_CLOSE*` / `BE_ESCAPE` call `finalize_close` with estimated PnL — 31cb456
- [x] 3.3 Guard test: `daemon.py` has no direct `record_trade` / `on_take_profit_hit` / `on_stop_loss_hit` calls — 31cb456

### Phase 4: Release

- [x] 4.1 Bump version to 0.48.11 in `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `CHANGELOG.md` — 9e68d6f
