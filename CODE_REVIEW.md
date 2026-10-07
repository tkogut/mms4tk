# Code Review Guide — mms4tk

Auto-applied by `om-code-review`/`om-auto-review-pr` when present. Derived from this repository's actual stack and the conventions observed in its history — not a generic checklist.

## Stack

Python 3.12, FastAPI (`src/mms4tk/api.py`), SQLite via raw `sqlite3` (`src/mms4tk/logger_db.py`), pytest. No ORM — all queries are hand-written SQL strings; review them for correctness directly, there is no schema layer to lean on.

## Review priorities, in order

1. **Correctness of money/PnL math.** This codebase has shipped multiple real bugs here (contractSize scaling in `trade_sync.py`, PnL misclassification in `get_closed_trades()`'s fallback classifier). Any change touching `pnl_usd`, `pnl_pct`, `pnl_net_usd`, or contract-size math must be checked against `_get_contract_size()`'s per-symbol table (`daemon.py`) and the real OKX `contractSize` lookup it falls back from.
2. **Exit-path and risk-sizing contracts.** Changes to `inactivity_manager.py`, `state_manager.py`, or the SL/TP sections of `daemon.py` must be checked against:
   - Does this change the outcome distribution for a position opened under the *previous* behavior? If yes, it needs a `data_collection_epoch_start` bump for the affected symbol(s) (see `BACKWARD_COMPATIBILITY.md`). If it's pure observability (new logging, new API field), it must not bump the epoch.
   - Does it interact with `SCOUT_MODE`? A reduced-risk-after-loss symbol must have a working path back to `BASE_MODE` — the #99 incident (`reconcile_positions` hardcoding `is_take_profit=False`) shipped silently for weeks because this wasn't checked.
   - Does the addon leg (`HIGH_RISK_ADD`) still correctly carry no independent stop-loss (P3 V2 — one trigger, anchored to `base_entry_price`)?
3. **Security.** Never log, print, or commit secrets (API keys, tokens, `.env` contents) — mask with `sed`/`grep -v` per `CLAUDE.md` R-SEC-01. A leaked-secret finding in this repo's history required a full `git filter-repo` rewrite; treat any new leak as equally serious even if assessed as low-risk by the owner.
4. **New signal logic requires evidence, not intuition.** The "Profitability Workstream" is formally closed (`DECISIONS.md` §1): no new filter/threshold/parameter sweep tested on the existing 24-month backtest fixtures (`tests/fixtures/*_24m*`, `*_max*`) without a qualitatively new evidence source (real production fills, a new independent dataset, a live regime change). Flag any PR that re-tests an already-rejected hypothesis without new evidence — check `DECISIONS.md` §5's rejected-hypotheses table first.

## Repo-specific checks

- **CRLF files.** Several core files (`analytics.py`, `logger_db.py` among others) use CRLF line endings. A programmatic edit (not the `Edit` tool) that reads/writes with Python's default text mode will silently convert the whole file to LF, producing a multi-thousand-line diff that obscures the real change. Check `file <path>` before and after any non-`Edit`-tool modification to such a file.
- **Pre-commit role hook.** `.git-hooks/pre-commit` blocks the default `coordinator` role from committing anything outside `.md`/`.txt`/`.json`/`SDLC`/`specs/` paths. A commit touching `src/*` or `tests/*` needs `SWARM_ROLE=builder` set, or it will be rejected locally before ever reaching CI — don't mistake this for a CI failure.
- **Test suite pollution.** Running the full `pytest tests/` suite writes real values into `.agents/swarm/settings.json`/`symbols.json` and creates real `.agents/swarm/logs.db`/`logs.db-shm`/`logs.db-wal` files. Clean up with `git checkout -- .agents/swarm/settings.json symbols.json && rm -f .agents/swarm/logs.db .agents/swarm/logs.db-shm .agents/swarm/logs.db-wal` before committing — **never** `rm -f .agents/swarm/logs.db*`, that glob also matches the committed `logs.db.bak_*` backup files and deletes real repo history (hit live during this PR's own independent review). A PR that accidentally includes these changes should be flagged.
- **Known pre-existing flaky tests** (not introduced by a reviewed PR unless the PR touches their subject directly): `test_max_margin_allocation.py::test_daemon_70_30_capital_split_calculation`, `test_position_sizing.py::test_daemon_position_sizing_calculation`, `test_recalibration_history_lists_most_recent_first`.
- **`closed_trades.reason` classification.** `get_closed_trades()`'s cycle_logs-matching fallback (`logger_db.py`) is easy to get subtly wrong — a loose time-window match can misattribute one event's cycle_logs row to a different, unrelated closing event on the same symbol (the exact bug fixed in #91/#93). Any change to this matching logic needs a regression test reproducing a same-symbol, closely-timed, different-event scenario.
- **Hermes tool schemas.** `src/mms4tk/hermes_tools.py` defines the tool surface the external Hermes ops assistant calls. A schema change here is a breaking change for Hermes, not just an internal refactor — see `BACKWARD_COMPATIBILITY.md`.

## Severity guidance

Map findings to this repo's existing `priority-*`/`risk-*` label taxonomy (already in use on every issue/PR since #67): `risk-high` for anything touching live order placement or SL/TP trigger math; `risk-medium` for risk-management logic that doesn't touch execution directly (e.g. SCOUT_MODE state transitions); `risk-low` for pure observability/logging/classification fixes. `priority-extreme` is reserved for a non-functional emergency path (the precedent: #67, `emergency_killall` silently not closing positions).
