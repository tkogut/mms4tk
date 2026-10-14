# Execution plan — trade_sync keeps real fees: pagination, persisted OKX orders, no fee downgrade (#124, part 1)

Engine: om-auto-create-pr (steps: 8, --loop: no)
Branch: `fix/trade-sync-fees` · Base: `main` @ 4049bc9 · Issue: #124 (part 1 of 2) · Role: Builder (SWARM)

## Goal

`closed_trades.fee_usd` / `pnl_net_usd` stop degrading: the sync no longer depends on the ~100 most recent OKX orders, raw OKX orders are kept in the database, and a re-sync can never lower an already booked fee.

## Scope (part 1 — the source; the one-off history repair is a separate PR)

1. **Pagination** in `OKXTradeSyncWorker._sync_closed_trades_locked`: loop `orders-history` and `orders-history-archive` with `limit=100` and the `after` ordId cursor until a short page, a page with no new ordIds, a page older than `MMS4_TRADE_SYNC_START_ISO`, or a page cap.
2. **Persist raw orders**: new additive table `okx_orders` (`ord_id` PK, instrument/side/posSide/state/price/size/fee/pnl/fill time, full `raw_json`) with `upsert_okx_order` / `load_okx_orders`; every fetched order is stored, never deleted; closed trades are built from the stored set (fetched orders override stored ones by ordId), so a close keeps its opening leg after the API window moves on.
3. **No fee downgrade** in `upsert_closed_trade`: on conflict `fee_usd = MAX(existing, new)`; `pnl_net_usd` / `is_win` follow the kept fee.
4. Log a WARNING when an entry price had to be inferred (opening leg missing even after 1+2).

## Non-goals

- The one-off repair of existing rows (dry-run + backup) — part 2 of #124, its own PR.
- No change to trading logic, strategy, the API contract, or existing columns; `SOL/ICP/XRP` `ctVal` fallback stays for part 2.
- Does not modify `.agents/plans/`.

## Risks

- OKX `after` pagination semantics are verified only against mocks here; the loop is bounded (page cap, "no new ordIds" stop) so a mis-behaving cursor cannot spin.
- Rows already degraded are not repaired by this PR; the `MAX` rule only prevents further loss.
- DB schema change is additive (`CREATE TABLE IF NOT EXISTS`); bounded storage (one row per order, ~100 orders/week).

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` **including `slow`**; new tests mutation-checked; version 0.48.15 in 3 files.

## Progress

PR: #126

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Storage

- [x] 1.1 `logger_db`: `okx_orders` table, `upsert_okx_order`, `load_okx_orders` — 42832c4
- [x] 1.2 `logger_db.upsert_closed_trade`: `fee_usd = MAX(existing, new)`, net and is_win follow it — 42832c4

### Phase 2: Sync

- [x] 2.1 `trade_sync`: paginated, bounded fetch of both endpoints — 42832c4
- [x] 2.2 `trade_sync`: persist fetched orders, build closed trades from the stored set; warn on inferred entry — 42832c4

### Phase 3: Tests, docs, release

- [x] 3.1 Tests (pagination, window slide keeps the opening fee, no fee downgrade, loop guards), mutation-checked — 42832c4
- [x] 3.2 `BACKWARD_COMPATIBILITY.md` note, version 0.48.15 (`VERSION`, `src/mms4tk/__init__.py`, `CHANGELOG.md`) — 81d7e5c

### Phase 4: Validate

- [x] 4.1 Full suite including slow: 0 failures; handshake generated and validated — 81d7e5c
