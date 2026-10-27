# Execution plan: BTC timeframe default-mismatch + addon favorable-price fee leak

## Goal

Fix two confirmed, independent production bugs with a single small PR:
1. `src/mms4tk/api.py`'s `/api/v1/symbols/update` endpoint silently resets any
   field the caller omits (e.g. `timeframe`) to its Pydantic schema default,
   because `AddSymbolRequest.timeframe` defaults to `"15m"` while the real
   intended default (symbols.json / `load_symbols_config()`'s `defaults` dict)
   is `"1h"`. This is why BTC/USDC's `symbol_configurations.timeframe` was
   found at `15m` in production on 2026-10-27 while ETH/XAU/XAG correctly show
   `1h`, same day a bulk `allocation_usd` update touched all 4 symbols
   (2026-10-21).
2. `src/mms4tk/daemon.py`'s `addon_requires_favorable_price_enabled` gate
   (~line 2280) compares the incoming addon signal price against the
   persisted `base_entry_price` anchor with no fee buffer and without
   preferring the position's actual live fill price. Live evidence: 15/19
   executed addons since the gate went live were net-underwater after
   realistic round-trip fees; one leaked even before fees due to
   signal-vs-fill slippage.

## Scope

- `src/mms4tk/api.py`: partial-merge fix for `/api/v1/symbols/update` (preserve
  existing stored value for any field the caller's request didn't explicitly
  set, via `model_fields_set`), restricted to the `/update` route only (the
  `/add` route keeps full-schema-default behavior for brand-new symbols,
  unchanged, already guarded by the existing 409-on-existing-symbol check).
  Normalize the remaining `"15m"` literal defaults in this file to `"1h"` for
  consistency with `symbols.json`/`load_symbols_config()`'s `defaults` dict.
- `scripts/fix_btc_timeframe.py`: new one-time correction script, dry-run by
  default, `--apply` to write, following `scripts/bump_data_collection_epoch.py`'s
  exact CLI/doc pattern. Only ever reports/fixes BTC/USDC; never touches other
  symbols. Not run with `--apply` against production by this run.
- `src/mms4tk/daemon.py`: tighten the favorable-price addon gate to (a) prefer
  the position's actual live fill price (`match_entry`, today's OKX avgPx —
  safe here because the Single Addon Guard above this code path guarantees no
  addon has executed yet, so `match_entry` is still the base fill, not an
  averaged price) over the persisted `base_entry_price` anchor when available,
  and (b) require the signal price to clear that reference by at least the
  existing measured round-trip fee constant (reusing
  `state_manager.SCOUT_MIN_GROSS_PNL_PCT`, 0.0010003 — already imported
  pattern in this file, same value as `backtest_engine.ROUND_TRIP_FEE_PCT`;
  `daemon.py` follows the same "must not import from backtest_engine"
  convention already documented in `state_manager.py`).
- Tests: `tests/test_api.py` (or nearest existing symbols-config test module)
  for the partial-merge behavior and the normalized defaults; a new
  `tests/test_fix_btc_timeframe.py` for the script; `tests/test_daemon.py`
  (or nearest existing addon-gate test module) for the 4 gate regression
  cases.
- VERSION / `src/mms4tk/__init__.py` / `CHANGELOG.md` bump, following the
  exact pattern of PRs #143/#144.

## Non-goals

- No change to entry-signal logic, exit logic, SL/TP, leverage, or the
  stale/giveback layer.
- No change to the `/add` endpoint's full-replace-with-defaults behavior.
- No `--apply` run against the production database by this run — dry-run /
  local test DB only. The operator runs `--apply` on the VPS themselves after
  reviewing the merged PR.
- No VPS deploy.

## Risks

- Partial-merge changes `/update` semantics: a caller that relied on omitting
  a field to reset it to the schema default will now instead keep the
  existing stored value. Mitigated: the endpoint's own docstring already
  states `/update`'s contract is "the dashboard always echoes the full
  current config back" — i.e. omission was never an intentional reset
  mechanism, it was the bug. `model_fields_set` correctly distinguishes
  "field present in the request body" from "field defaulted by Pydantic"
  regardless of whether the provided value equals the default.
- Addon gate: preferring live fill price over the persisted anchor could, in
  principle, change blocking decisions for well-behaved cases. Mitigated by:
  restricting this preference to a path already guaranteed addons_done == 0
  (so `match_entry` is still the untouched base fill), keeping the anchor as
  fallback when no live price is available, and regression tests that nail
  down both the previously-leaking case and the previously-passing case.

## Implementation Plan

### Phase 1: BTC timeframe default-mismatch fix + correction script

- Partial-merge fix in `add_or_update_trading_symbol` for the `/update` route.
- Normalize remaining `"15m"` default literals to `"1h"` in `api.py`.
- Regression tests for both.
- `scripts/fix_btc_timeframe.py` one-time correction script + tests.

### Phase 2: Addon favorable-price gate fee/fill-price fix

- Prefer live fill price + fee-covering margin in the gate at
  `daemon.py` ~line 2280.
- Regression tests: (a) favorable-vs-anchor but not vs-fill now blocked,
  (b) clears fee margin still allowed, (c) within fee margin blocked,
  (d) existing clearly-favorable passing case preserved.

### Phase 3: Version bump, changelog, validation gate

- Bump VERSION / `__init__.py` / CHANGELOG.md per PR #143/#144 convention.
- Run the full validation gate.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: BTC timeframe default-mismatch fix + correction script

- [x] 1.1 Partial-merge fix for `/update` + consistent `1h` defaults + regression tests — c49fc6d
- [x] 1.2 One-time BTC timeframe correction script + tests — c49fc6d

### Phase 2: Addon favorable-price gate fee/fill-price fix

- [x] 2.1 Fee-covering-margin + live-fill-price gate fix + regression tests — 512ba3f

### Phase 3: Version bump, changelog, validation gate

- [x] 3.1 VERSION/__init__.py/CHANGELOG bump + full validation gate — d37169c (flake8/mypy/build unavailable in this env, configured non-fatal `|| true`; pytest 814 passed, 0 failed)

### Phase 4: Advisor review follow-up (pre-finalize self-review)

- [x] 4.1 Blocker: merge must also cover the 10 `system_settings`-only fields, not just the 5 SQLite columns — the `set_settings_bulk` write previously used `req.X` directly, which would have silently disabled the live `addon_requires_favorable_price_enabled` gate (and others) on the next unrelated `/update` after a restart. Fixed via `_trend_filter_fields(...)`-sourced merge; regression test added and verified to fail against the pre-fix code — bc48e09
- [x] 4.2 Test quality: addon-gate case (a) didn't actually isolate the fill-price-vs-anchor change (the fee margin alone already blocked it). Rewrote with values that isolate the switch, added `caplog` assertions on cases (a)/(c) to confirm the block reason. Verified both fail against the pre-fix anchor-only gate and pass with the fix — cce98ba
- [x] 4.3 Deploy-note accuracy: documented the apply/restart ordering hazard (script writes SQLite only; a stale in-memory `configured_symbols_store` would clobber it back on the next `/update` for any symbol) in both the script's docstring/runtime output and the PR body; softened the CHANGELOG's "config/bugfix-only" framing to call out the epoch-bump need; PR body discloses the `SCOUT_MIN_GROSS_PNL_PCT` env-override coupling, the ~0.10% vs ~0.15% fee-rate note, and the now-diverged `backtest_engine.py` addon logic — e050a49
