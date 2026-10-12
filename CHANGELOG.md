# 📜 CHANGELOG — mms4tk

All notable changes to the **mms4tk** (MMS v4 Autotrading System & Hermes Agent Swarm) project will be documented in this file.

The format is based on [Keep a CHANGELOG](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 🚀 [0.48.10] - 2026-10-12

### 🔥 Fixed — SCOUT_MODE Lost / Leaked Between Symbols on Restart
- 🐛 **`state_manager.py` / `logger_db.py`**: `state_history` carried the symbol only
  inside the free-text `reason` (`"[XAG/USDC] ..."`), and startup restored just the
  single latest row — assigned to the **primary** symbol. After a restart a
  non-primary symbol in SCOUT_MODE (e.g. XAG) silently returned to BASE_MODE with no
  take-profit, and if the latest row belonged to XAG, the primary symbol (BTC) was
  wrongly put into SCOUT_MODE.
- 🐛 **`api.py`** (`POST /api/v1/symbols/add|update`): every pair-config edit
  unconditionally reset the symbol to BASE_MODE, wiping an active SCOUT_MODE.
- ✅ **Naprawa**: `state_history` gained an idempotent nullable `symbol` column
  (legacy `"[SYM]"` rows are backfilled); `LoggerDB.save_state(symbol=)` tags every
  write; `MMS4StateManager` rehydrates the latest state of **every** symbol in
  `__init__` and `set_logger_db` (legacy untagged rows apply to the primary symbol
  only when it has no tagged row); the pair-config endpoint no longer touches the
  symbol's risk state.
- ⚠️ **Deploy note**: additive DB migration — deploy under the maintenance lock
  (`scripts/maintenance.py --enable` / `--disable`) after a `logs.db` backup. Stage E1
  of the SCOUT-exit fix; the exit-path changes (E2–E5) ship separately.
- ⚠️ **Behavior change on first restart**: any symbol whose LATEST `state_history` row
  is SCOUT_MODE (it previously fell back to BASE_MODE on every restart) now comes back
  in SCOUT_MODE. Before deploying, list the latest state per symbol and decide whether
  stale ones should be relaxed first (`scout_override`) — until E3 lands, SCOUT exits
  only on a full take-profit or an override.

## 🚀 [0.48.9] - 2026-09-24

### 🔥 Fixed — Stop-Losses Mislabeled as "BREAK-EVEN" in Trade History
- 🐛 **`OKXTradeSyncWorker` (`src/mms4tk/trade_sync.py`)**: OKX reports fill
  `size` in **contracts**, not coins. `pnl_pct`/notional (and the PnL-derived
  entry-price inference for positions opened before the sync window) were
  computed without scaling by `contractSize` — for BTC this inflated
  notional/margin by ~10000x, ETH/XAU by ~1000x, XAG by ~100x, driving every
  real stop-loss's `pnl_pct` toward zero. `LoggerDB.get_closed_trades()`'s
  `pnl_pct <= -0.015` fallback therefore never tripped, and the
  `outcome == "STOP-LOSS"` branch it also relied on was dead code
  (`daemon.py` never logs that literal `outcome` for a reconciled stop-out) —
  so every real SL fell through to the generic `"BREAK-EVEN"` label.
- ✅ **Naprawa**: `trade_sync.py` now scales notional/margin/`pnl_pct` by
  `contractSize` (mirrors `daemon._get_contract_size`). `reconcile_positions()`
  (`daemon.py`) now sets `reason="STOP-LOSS"`/`"CLOSED_EXTERNAL"` on the
  provisional row it writes at detection time instead of relying entirely on
  the post-hoc `pnl_pct` guess; `upsert_closed_trade()` gained a `reason`
  param (preserved across `ON CONFLICT` via `COALESCE`), and `trade_sync.py`
  carries that reason onto the authoritative row before the provisional one
  is superseded. `STALE_FORCE_CLOSE_BE`/`STALE_BE_ESCAPE` signal types are now
  mapped to their own explicit reasons instead of silently falling through.
- 🧹 One-time migration (`scripts/reclassify_break_even_reason.py`, dry-run by
  default) recomputes `pnl_pct` for historical `reason='BREAK-EVEN'` rows and
  reclassifies where the corrected value clears the STOP-LOSS threshold. Run
  against production `logs.db` 2026-09-24: 72 BREAK-EVEN rows inspected, 21
  reclassified to STOP-LOSS.
- 🧪 New/updated tests: `test_trade_sync.py`, `test_reconciliation_pnl.py`,
  `test_dedupe_closed_trades.py`, `test_logger_db_closed_trades_cache.py`,
  `test_reclassify_break_even_reason.py`.
- Pure backend/data-quality fix — no schema/API contract change. Real PnL in
  USD was always correct; only the `reason` label was wrong.

## 🚀 [0.48.8] - 2026-09-17

### 🔥 Fixed — Entry Marker Always Snapped to "1 Candle Before Exit"
- 🐛 **`LoggerDB.get_closed_trades()`** (`src/mms4tk/logger_db.py`): the
  `entry_timestamp` backfill query filtered `signal_type = 'EXECUTED_BASE'` —
  but `daemon.py` never logs that literal string. `signal_type` is always
  `"BASE_LONG"`/`"BASE_SHORT"`; the `"EXECUTED_"` prefix only ever appears in
  `outcome` (`outcome = f"EXECUTED_{signal.signal_type}"`). Confirmed against
  production `logs.db`: `signal_type = 'EXECUTED_BASE'` matched **zero rows,
  ever**. Since `entry_timestamp` is cached permanently once computed (by
  design, per the `[0.48.4]` fix), every closed trade in the system's history
  had `entry_timestamp = NULL` — the Analytics chart's entry marker always
  fell back to "1 candle before exit", making every trade look like it was
  entered and closed almost instantly regardless of the real hold duration
  (spotted live: a XAG/USDC short actually opened ~15:05, held ~6h, closed
  ~21:00 — the chart drew "WEJŚCIE" one candle before the 21:00 close).
- ✅ **Naprawa**: query narrowed to `outcome LIKE 'EXECUTED_BASE%'`, matching
  the real logged shape for both long and short base entries.
- 🧪 New `test_get_closed_trades_finds_entry_timestamp_for_real_base_execution_shape`
  reproduces the exact production row shape (`XAG/USDC`, `BASE_SHORT` →
  `EXECUTED_BASE_SHORT`, entry 65.68 → exit 65.40) and proves `entry_timestamp`
  is now found. Also corrected the synthetic cycle_logs row in the existing
  `test_get_closed_trades_caches_a_not_found_entry_timestamp` — it used the
  same wrong `signal_type='EXECUTED_BASE'`/`outcome='OPENED'` shape the buggy
  query expected, which no longer reflects reality after this fix.
- Pure backend fix (`src/mms4tk/logger_db.py` + tests) — no schema/API
  contract change. Trade prices/PnL were always correct; only the entry
  marker's *time position* on the chart was wrong.

## 🚀 [0.48.7] - 2026-09-17

### 🔥 Fixed — Addon Markers Showed Skipped/Blocked Attempts, Not Real Fills
- 🐛 **`LoggerDB.get_addon_events()`** (`src/mms4tk/logger_db.py`): matched any
  `cycle_logs` row where `outcome LIKE '%ADD%' OR signal_type LIKE '%ADD%'` —
  since `signal_type` stays e.g. `"HIGH_RISK_ADD_SHORT"` on every re-evaluation
  of the same signal regardless of whether it was actually executed, a single
  add-on re-checked repeatedly by the daemon's per-cycle loop (skipped by the
  candle gate, blocked by the single-addon guard, etc.) produced one dashboard
  marker *per re-check*, not per real fill. Confirmed live: XAU/USDC's
  `cycle_logs` held 822 `CANDLE_GATE_SKIP_HIGH_RISK_ADD_SHORT` + 436
  `BLOCKED_HIGH_RISK_ADD_LONG` rows against only 3 genuine
  `EXECUTED_HIGH_RISK_ADD_SHORT` fills — the Analytics chart showed ~9 "+ADD"
  markers clustered in a single ~50-minute window where zero real add-on
  actually executed.
- ✅ **Naprawa**: query narrowed to `outcome LIKE 'EXECUTED%ADD%'` — only rows
  the daemon actually executed (`outcome = f"EXECUTED_{signal.signal_type}"`,
  `daemon.py:1884`) now qualify as addon markers. The single-addon *trading*
  guard (`daemon.py:1832`, max 1 add per position) was never affected by this
  bug — this was a dashboard/query-only defect, not a risk-control defect.
- 🧪 New `tests/test_logger_db_addon_events.py` — 2 tests, direct against a
  real `LoggerDB`/sqlite (not mocked): proves skip/block rows for the same
  signal are excluded, and multiple genuine fills still return in
  recency-descending order.
- Pure backend fix (`src/mms4tk/logger_db.py` only, plus tests) — no schema
  change, no API contract change, no frontend change.

## 🚀 [0.48.6] - 2026-09-17

### ✨ Added — Japanese Candlestick Chart (Lightweight Charts / TradingView) + Real Time Windows
- 🕯️ **`ui_dashboard/src/components/PriceChart.jsx`** (new): native OHLC candlestick
  panel built on `lightweight-charts@5.2.1` (TradingView's own library, pinned exact
  version). Replaces the Recharts `<ComposedChart>` line-only price panel with real
  candles (wick + body), 3 overlay lines (TMA/upper/lower band, still computed
  server-side, unchanged), native `createSeriesMarkers` for trade/addon/entry
  markers, `createPriceLine` for the active-position price level, and built-in
  pan/zoom (no `<Brush>` needed on this panel).
- 🕐 **Timeframe buttons now mean actual visible windows** (`AnalyticsTab.jsx`,
  `timeframes` array): "4H" = last ~4h (5m candles), "1D" = last ~24h (15m candles),
  "1W"/"1M"/"3M"/"1Y" similarly — previously the label was the single-candle
  interval, so "4H" silently rendered ~10 days of history (60 × 4h candles).
- 📍 **`entry_timestamp` on open positions** (`src/mms4tk/api.py`,
  `PositionItem`/`get_active_positions()`): surfaces OKX's own position-open time
  (`info.cTime`, epoch ms, already present in the `fetch_positions()` payload — zero
  extra network calls) so the chart can place a time-anchored entry marker, not just
  a price-level reference line. `None` when absent (never fabricated). 2 new tests
  in `tests/test_api.py`.
- 📊 Equity Curve (unaffected `chartMode==='equity'` Recharts `<AreaChart>`) gains a
  `<Brush>` slider for pan/zoom, since it's not migrated to Lightweight Charts.
- 🧪 QA gate: `437 passed, 2 failed` (full suite). The 2 failures
  (`test_max_margin_allocation.py::test_daemon_70_30_capital_split_calculation`,
  `test_position_sizing.py::test_daemon_position_sizing_calculation`) are **pre-existing,
  unrelated to this branch's changes** — confirmed root cause: `daemon.py::plan_allocation()`
  lazily imports `mms4tk.api.configured_symbols_store`, a module-level global populated
  at import time from a **local, gitignored, per-worktree** file (`.agents/swarm/symbols.json`
  or a `logger_db` table, cwd-relative path) that legitimately differs in content between
  `main` and this worktree (`allocation_usd=10.0` vs `100.0` for `BTC/USDC`) — an
  architectural test-isolation gap (same class of issue as the archived
  `010-global-okx-executor-test-mock.md` plan), not a regression from `entry_timestamp`
  or the frontend chart rewrite. `daemon.py` is byte-identical to `main` on this branch.

## 🚀 [0.48.5] - 2026-09-17

### 🔥 Fixed — Analytics Tab Crash (ReferenceError, TDZ)
- 🐛 **`AnalyticsTab.jsx`**: `mappedCandles`/`activePos` (`useMemo`) called
  `normalizeSym(...)` at lines 116/139/147, but `normalizeSym` was declared as a
  `const` further down at line 150. `useMemo`'s factory runs synchronously during
  render, so this threw `ReferenceError: Cannot access 'normalizeSym' before
  initialization` on every single render — the Analytics tab never rendered at
  all (confirmed live via a browser console screenshot showing the minified
  equivalent, `Cannot access 'Re' before initialization`, inside `Array.find`
  under `useMemo`). Fix: moved the `normalizeSym` declaration above both usages.
- 🐛 **`CustomTooltip`**: referenced `showTma` — state local to `AnalyticsTab` —
  from a component defined at module scope, outside that closure. Would throw on
  the first chart hover. Fixed by passing `showTma` as an explicit prop.
- Pure frontend fix (`ui_dashboard/src/components/AnalyticsTab.jsx` only), no
  backend/API changes. `npm run build`: clean.

## 🚀 [0.48.4] - 2026-09-17

### 🔥 Fixed — Production Incident #2 (cont'd): entry_timestamp Cache Gate Bug
- 🐛 **`LoggerDB.get_closed_trades()`**: `0.48.3` cached `reason` correctly but gated
  the `entry_timestamp` cycle_logs lookup on `not entry_timestamp` — indistinguishable
  from "never looked up" when the lookup legitimately finds no match (stored as
  NULL). Every trade with no matching `EXECUTED_BASE` row kept re-running the
  expensive scan on every single call, forever. Confirmed live via `py-spy dump`
  on the production process: stuck in this exact query on every sample, container
  still pegged at ~99% CPU / unhealthy after the `0.48.3` deploy.
- ✅ **Naprawa**: obie kosztowne wyszukiwania (`reason`, `entry_timestamp`) są teraz
  bramkowane jednym wspólnym `already_processed = bool(reason)` — `reason` zawsze
  kończy się wartością niepustą (fallback po PnL), więc raz przetworzony wiersz
  nigdy więcej nie dotyka `cycle_logs`, niezależnie od tego czy `entry_timestamp`
  zostało znalezione.
- 🧪 Nowy test `test_get_closed_trades_caches_a_not_found_entry_timestamp` —
  wstawia pasujący wiersz PO pierwszym wywołaniu i dowodzi, że drugie wywołanie go
  ignoruje (bo już przetworzone), zamiast błędnie znaleźć nowe dopasowanie. Pełny
  `pytest -q -m "not slow"`: 437 passed, 4 deselected.

## 🚀 [0.48.3] - 2026-09-17

### 🔥 Fixed — Production Incident #2: Unbounded cycle_logs N+1 Scan
- 🐛 **`LoggerDB.get_closed_trades()`**: `closed_trades` nigdy nie miało kolumn
  `reason`/`entry_timestamp`, więc dla KAŻDEJ transakcji bez zapisanego powodu
  wyjścia funkcja odpytywała **całą tabelę `cycle_logs`** (530 147 wierszy w
  produkcji) zapytaniem `LIKE '%...%'` — bez indeksu, bez zapisania wyniku.
  `GET /api/v1/history/trades` jest odpytywane co 5s przez dashboard, więc ten
  skan powtarzał się od zera w nieskończoność. Potwierdzone na żywo przez
  `strace -c -p <pid>`: 100% CPU, ~20 000 `pread64`/s na `logs.db`, healthcheck
  konsekwentnie przekraczał timeout.
- ✅ **Naprawa**: nowe kolumny `reason`/`entry_timestamp` na `closed_trades`,
  wypełniane raz i odczytywane przy każdym kolejnym wywołaniu zamiast ponownego
  skanu `cycle_logs`. Nowy indeks złożony `idx_cycle_logs_symbol_ts` przyspiesza
  też sam pierwszy (jednorazowy) lookup per transakcję.
- 🧪 Nowy test regresyjny `test_logger_db_closed_trades_cache.py` — usuwa
  `cycle_logs` między dwoma wywołaniami i dowodzi, że drugie wywołanie i tak
  zwraca poprawny, zcache'owany wynik (nie re-derywuje go). Pełny
  `pytest -q -m "not slow"`: 436 passed, 4 deselected.
- ⚠️ Niepowiązane z incydentem #1 (`0.48.2`, `trade_sync.py` overlap guard) —
  to osobny bug w zupełnie innej ścieżce kodu, ujawniony dopiero po naprawie #1.

## 🚀 [0.48.2] - 2026-09-17

### 🔥 Fixed — Production Incident: Unbounded Concurrent Trade Sync
- 🐛 **`OKXTradeSyncWorker.sync_closed_trades()`**: Dodano `asyncio.Lock` chroniący przed
  nakładającymi się wywołaniami. Commit `0d528d1` ("non-blocking trade sync") zamienił
  `await` na `asyncio.create_task(...)` w `GET /api/v1/history/trades` — endpoint jest
  odpytywany przez front-end co 5s, znacznie szybciej niż pojedynczy sync (2 zapytania
  do OKX + `initialize()`) zdąża się zakończyć. Bez blokady każde odpytanie odpalało
  nowe, nakładające się zadanie w tle, wszystkie współdzielące ten sam obiekt
  `executor.exchange` — to realnie powodowało "Unclosed client session" oraz kaskadowe
  `RequestTimeout` na niepowiązanych endpointach (balance/positions), aż do przekroczenia
  timeoutu healthchecka i oznaczenia kontenera jako `unhealthy` na żywej produkcji.
- 🧪 Nowy test regresyjny (`test_trade_sync_worker_skips_overlapping_calls`) dowodzący,
  że nakładające się wywołanie jest pomijane, a `initialize()` uruchamia się dokładnie
  raz. Pełny `pytest -q -m "not slow"`: 434 passed, 4 deselected (bez regresji przed
  poprawką) + nowy test 3/3 w `test_trade_sync.py` osobno.

## 🚀 [0.48.1] - 2026-09-17
### 🐛 Fixed
- Normalization of symbols in AnalyticsTab chart overlays.
- Extraction of precise exit reasons from cycle_logs for trade registry.
- UI: Enhanced visibility of SVG markers for trades and badges for exit reasons.

## 🚀 [0.48.0] - 2026-09-17

### ➕ Added
- **Chart trade overlays**: entry, exit, addons markers on Analytics Tab.
- **Active position line**: Live reference line drawn on the TMA chart.
- **Real TMA bands**: Integrated with `/api/v1/history/candles` endpoint.
- **Endpoint `/api/v1/history/addons`**: for retrieving add-on events from `logger_db`.


## 🚀 [0.47.6] - 2026-09-17

### 🏛️ High Data Density Hedge-Fund Terminal Dashboard Rebuild
- 🏆 **`AggregatedPnLCard.jsx` (Nowy komponent)**: Scentralizowane Hero KPI łączące całkowity PnL portfela (`text-4xl sm:text-5xl font-mono tabular-nums`), wskaźnik zwrotu %, dynamiczny wykres SVG Sparkline trendu kapitału, reflektor live uPnL z pulsem ekspozycji rynkowej oraz skompresowaną belkę mikro-metryk (Zero-Gutter).
- 📊 **`MonitoringTab.jsx`**: Całkowity demontaż i odbudowa — osadzenie Hero `AggregatedPnLCard`, Bento Grid par rynkowych (horyzontalny snap ribbon na smartfonach 390px), terminalowy Order Book zleceń nettingowych ze stałymi szerokościami kolumn, `tabular-nums text-right`, alertem stagnacji Inactivity Managera i modalem podwójnej autoryzacji zamknięcia pozycji.
- 📈 **`AnalyticsTab.jsx`**: Wykres TMA rozciągnięty na 100% szerokości jako tło robocze z półprzezroczystymi nakładkami HUD (telemetria H4 Hermes w rogach wykresu), przełącznik między TMA a Krzywą Kapitału (Equity Curve), oraz skondensowana tabela historii zrealizowanych zleceń z SQLite.
- ⚙️ **`SettingsTab.jsx`**: Architektura Accordion z 4 rozwijanymi sekcjami (Ryzyko & Dźwignia, Silnik Adaptacyjny & Filtry Zmienności, Alokacja Kapitału & Midline TP, Dostęp API & Bezpieczeństwo), nowoczesne przełączniki Toggle Switch zamiast checkboxów, rygorystyczne formatowanie numeryczne `tabular-nums font-mono`.

## 🚀 [0.47.5] - 2026-09-17

### 🎨 Dashboard: Numeric Formatting Standards (font-mono tabular-nums text-right)
- 🔢 **`ui_dashboard/src/utils/formatters.js`** (nowy plik): `formatSignedUsd()` — wyodrębnione z 3-krotnie zduplikowanego wzorca formatowania kwot ze znakiem w `MonitoringTab.jsx`/`AnalyticsTab.jsx`.
- 📐 **`tabular-nums`**: dodane obok istniejącego `font-mono` na wszystkich kartach KPI, kafelkach per-symbol i odczytach formularza w `MonitoringTab.jsx`, `AnalyticsTab.jsx`, `SettingsTab.jsx` — eliminuje jitter szerokości cyfr przy odświeżaniu danych.
- ➡️ **`text-right`**: kolumny liczbowe (dźwignia, alokacja, depozyt, ceny, PnL, prowizja) w obu tabelach `AnalyticsTab.jsx` (Otwarte Pozycje, Historia Transakcji) wyrównane do prawej, zgodnie z Kompendium Inżynieryjnym UI/UX (`.agents/specs/knowledge/mms4-dashboard-ui-ux-standards.md`).
- Zakres świadomie ograniczony do klas numerycznych — bez migracji na Lightweight Charts, bez pełnej kompresji gęstości Tailwind, bez `useRef` (brak WS >10Hz dziś). Zero nowych zależności.

## 🚀 [0.47.4] - 2026-09-15

### 🛡️ Reconciliation Fill Matching: Time-Bounded Against Stale Orders
- 🕐 **`min_fill_time_ms` w `OKXExecutor.fetch_last_closing_order`**: Dodano dolną granicę czasową do wyszukiwania zamykającego zlecenia w historii OKX — kandydat starszy niż ostatni potwierdzony moment życia pozycji (`position_snapshots.updated_at`) jest teraz odrzucany, zamiast być błędnie dopasowanym jako fill, który właśnie zamknął pozycję.
- 🔗 **`daemon.reconcile_positions`**: liczy tę granicę z zapisanego `updated_at` snapshotu i przekazuje ją do `fetch_last_closing_order` — domyka lukę znalezioną w niezależnym audycie commitu `340ae91` (0.47.2).
- 🧪 **Testy**: 2 nowe przypadki w `tests/test_reconciliation_fill_order.py` — odrzucenie nieaktualnego zlecenia (z dowodem, że bez granicy to samo zlecenie zostałoby błędnie wybrane) oraz weryfikacja, że `daemon.py` poprawnie liczy i przekazuje wartość graniczną z `updated_at`. Pełny `pytest -q -m "not slow"`: 432 passed, 4 deselected (brak regresji).

## 🚀 [0.47.3] - 2026-09-15

### 📝 Comprehensive System & Architecture Documentation Sync
- 📚 **Aktualizacja SSOT Dokumentacji**: Zaktualizowano pliki `.agents/MEMORY.md`, `README.md` oraz `docs/TELEGRAM_AND_NOTIFICATIONS.md` o pełny opis architektury autorytatywnego pojednania pozycji (Authoritative Fill & PnL Reconciliation), eliminację rozjazdów cenowych na Telegramie oraz mechanizm `fetch_last_closing_order`.
- 🔄 **Weryfikacja Triady Botów**: Potwierdzono matrycę ról i portów dla profili `default`, `masterek` oraz `senti`.

## 🚀 [0.47.2] - 2026-09-15
 
### 🎯 Authoritative OKX Fill Price & PnL in Position Reconciliation
- 🔍 **Exact Fill Price from OKX**: Zaimplementowano metodę `OKXExecutor.fetch_last_closing_order(symbol, pos_side)` odpytującą historię zleceń OKX (`private_get_trade_orders_history`). Zastąpiono szacunkową cenę tickera z chwili wykrycia zniknięcia pozycji rzeczywistą ceną wykonania (`avgPx`), PnL oraz fee.
- 🛑 **Poprawna klasyfikacja STOP-LOSS**: Wyeliminowano błąd fałszywej klasyfikacji SL jako `Manual/TP` wynikający ze spreadu lub odbicia ceny tickera po zamknięciu pozycji.
- 💬 **Wiarygodne alerty Telegram**: Usunięto dopisek `(szac.)` przy potwierdzonym fillu z giełdy, a wartości Netto, Brutto i fee są w 100% spójne z giełdą OKX i Wykazem Transakcji w UI Dashboardzie.
- 🧪 **Testy jednostkowe**: Dodano dedykowany zestaw testów `tests/test_reconciliation_fill_order.py` badający realny fill z OKX, parsowanie odpowiedzi giełdy oraz bezpieczny fallback.

## 🚀 [0.47.1] - 2026-09-15

### 🤖 Telegram Alert Routing to Dedicated Masterek Bot
- 🎯 **Dedykowany routing powiadomień MMS4**: Zaktualizowano zmienne środowiskowe `TELEGRAM_BOT_TOKEN` w kontenerze `antigravity_core` na dedykowany token bota Masterek (`@MMS4_masterek_bot`), eliminując wysyłkę alertów o pozycjach do bota `default`.
- 🔄 **Hermes Event Dispatcher Profile Pinning**: Skonfigurowano `scripts/hermes/mms4tk_event_dispatcher.py` do jawnego przekazywania profilu `-p masterek` przy wywołaniach `hermes send --to telegram`.

## 🚀 [0.47.0] - 2026-09-14

### 🥈 Silver (XAG) Support & Commodity Expansion
- ✨ **OKX EEA X-Perp Silver Discovery**: Dodano pełną obsługę kontraktu srebra `XAG-USD_UM_XPERP-310509` (`XAG/USD:USD-310509`) z automatycznym wykrywaniem instrumentu i pobieraniem świec live.
- 📐 **Contract Size & Sizing Fallback**: Zaimplementowano prawidłowe przeliczanie rozmiaru kontraktu dla srebra (`ctVal = 0.01` uncji XAG per kontrakt, ~$0.63 notional) w `daemon.py` (`_get_contract_size`, `_position_notional_usd`) oraz `okx_executor.py` (`calculate_order_amount`).
- ⚙️ **Konfiguracja Strategii XAG**: Włączono parę `XAG/USDC` z alokacją $100.0 USD (70/30 base/addon), interwałem `1h`, aktywną bramką makro `htf_directional_gate_enabled: true`, negacją impulsu Fib 38.2% oraz Giveback Protection.

## 🚀 [0.46.1] - 2026-09-14

### 🧹 Portfolio Cleanup: Deprecation of XRP, SOL, ICP
- 🗑️ **Usunięcie nieużywanych par**: Trwale usunięto pary `XRP/USDC`, `SOL/USDC` i `ICP/USDC` z pliku `symbols.json`, domyślnych ustawień `api.py` oraz bazy produkcyjnej SQLite (`symbol_configurations` i `system_settings`).
- 🎯 **Aktywny portfel**: Zestaw par produkcyjnych zredukowano ściśle do aktywnego trio `BTC/USDC`, `ETH/USDC` oraz `XAU/USDC`.

## 🚀 [0.46.0] - 2026-09-14

### 🥇 Gold (XAU) Support & Commodity Asset Class Onboarding
- ✨ **OKX EEA X-Perp Gold Discovery**: Dodano pełną obsługę kontraktu złota `XAU-USD_UM_XPERP-310502` (`XAU/USD:USD-310502`) z dynamiczną autodetekcją instrumentów X-Perp w `verify_symbol_with_okx` oraz `fetch_candles`.
- 📐 **Contract Size & Sizing Fallback**: Zaimplementowano prawidłowe przeliczanie rozmiaru kontraktu dla złota (`ctVal = 0.001` XAU per kontrakt, ~$4.30 notional) w `daemon.py` (`_get_contract_size`, `_position_notional_usd`) oraz `okx_executor.py` (`calculate_order_amount`).
- ⚙️ **Konfiguracja Strategii XAU**: Włączono parę `XAU/USDC` z alokacją $100.0 USD (70/30 base/addon), interwałem `1h`, aktywną bramką makro `htf_directional_gate_enabled: true`, negacją impulsu Fib 38.2% oraz Giveback Protection.

## 🚀 [0.45.1] - 2026-09-14

### 🛡️ Inactivity Manager: Live Price Feed & Profit Giveback Protection (PR #60)
- ⚡ **Eliminacja 1h opóźnienia cenowego**: W `daemon.py` parametr `current_price` przekazywany do `inactivity_manager.update_and_check` pobiera teraz bieżącą cenę z aktywnej świecy (`candles[-1].close if candles else signal.price`), zamykając opóźnienie z 1 godziny do 15 sekund bez naruszania dyscypliny closed-candle w `evaluate_signals()`.
- 🛡️ **Profit Giveback Protection**: Nowa reguła w `inactivity_manager.py` śledząca `peak_profit_atr`. W przypadku osiągnięcia szczytowego zysku $\ge 1.0\times\text{ATR}$ i nagłego cofnięcia o $>50\%$ szczytu, pozycja jest natychmiast redukowana (`SPLIT_50`) w trybie intra-candle, bez czekania na 4 świece stagnacji.
- 🧪 **Testy jednostkowe**: Dodano `test_profit_giveback_protection` oraz `test_daemon_passes_active_candle_close_to_inactivity_manager` (427 testów przechodzi).

## 🚀 [0.45.0] - 2026-09-11

### 🎯 Faza 3 Zakończona (Optymalizacje & Bugfixy)
- 🐛 **Bugfix (3.1)**: Midline TP w `daemon.py` używa teraz `closed_candles` (zamiast `candles[-1]`) aby zapobiec triggerowaniu częściowego zamknięcia na podstawie repaintującej się świecy.
- ✨ **Feature (3.2)**: Opcjonalny `htf_directional_gate_enabled` (domyślnie OFF) pozwala na filtrowanie wejść na bazie trendu z 4H (H4 TMA position). Architektura bezpieczna dla `asyncio` (per-symbol dict).
- 🔬 **Skrypty Analityczne (3.3 & 3.4)**: Dodano narzędzia do rolling TMA/ATR recalibration oraz D1 ATR exhaustion (opt-in dla backtestu). Obie hipotezy zostały zmierzone i formalnie odrzucone.

## 🚀 [0.44.6] - 2026-09-11

### 🎯 Backtest Harness: Default Now Matches Live Production (P3 V2)
- 🔎 **Root cause**: `run_backtest()`'s `addon_sl_variant` parameter (added for P1)
  still defaulted to `"V0"` — the addon-composite-SL mechanism production used
  *before* P3 V2 shipped (v0.44.4). Every backtest computed since P3 shipped —
  the P4 `base_sl_pct` grid, the `engine_mode` CLASSIC-vs-ADAPTIVE comparison, the
  P6.2 Inactivity ablation — silently modeled a retired mechanism instead of what
  production actually runs today.
- 🔧 **Fix**: default changed to `"V2"` (base SL pinned to `base_entry_price`,
  addon gets no independent SL) — verified 1:1 against all three production call
  sites (`reconcile_positions`, the Self-Healing SL Guard, the Midline-TP
  re-attach) plus the HIGH_RISK_ADD execution branch. No formula fix was needed;
  only the default was stale. `"V0"`/`"V1"` kept for the historical P1 comparison.
- 📊 **Measured impact (not cosmetic)**: net PnL on the 6 standard windows moves
  materially under the corrected default — e.g. BTC_full +0.80%→+4.14%, ETH_full
  −7.95%→−1.24%. Every absolute figure in the strategic report's tables (A/B
  `engine_mode`, P4 grid, P6.2 ablation, the "no demonstrated edge" conclusion)
  was computed under the retired V0 mechanism and needs recomputation under V2 —
  tracked as follow-up work, not done in this release.
- ✅ **P1 formally closed**: the original "V0 vs V1 vs V2" gate never passed (no
  variant cleared 5/6) and is now moot — production changed for an independent
  reason (execution determinism, P3 V2), not because a backtest verdict decided
  it. Documented explicitly in `task.md` to prevent this being misread later as
  "V2 won the PnL gate."
- 🧪 P2 regression re-run under the new default: no verdict flipped (Midline TP
  still 6/6 loss, high-vol filter still wins in exactly BTC_H2/ETH_H2). Full
  suite: **344/344 PASS** (fast) + 4/4 slow tests (independently re-verified).

---

## 🚀 [0.44.5] - 2026-09-10

### 📊 Sesja A1+A2: Fee Calibration from Production + Epoch-Scoped KPIs
- 🔎 **A1 — `ROUND_TRIP_FEE_PCT` calibrated from real production data** (audit
  P2(c) point 3, never previously done): measured `SUM(fee_usd) / SUM(notional)`
  over 52 real closed trades (2026-08-30..2026-09-10, the window with real
  per-trade `fee_usd`) → `0.0010003`, confirming the prior assumption
  (`TAKER_FEE_PCT * 2`) to within 0.03% relative. The constant is now traceable to
  a production measurement instead of another constant's assumption. Re-ran the
  P2 regression gate under the calibrated rate — no verdict flipped (Midline TP
  still loses 6/6, high-vol base filter still wins in exactly BTC_H2/ETH_H2).
- 🎯 **A2 — KPIs scoped to `data_collection_epoch_start`**: `LoggerDB.get_kpi_summary()`
  gained an optional `since` filter so win rate, profit factor, fees, R:R and the
  drawdown curve can all be computed only over the current trading regime, not
  diluted by pre-P2/P3/PLAN-011 history. `/api/v1/stats` (and its `/stats/pnl`
  alias) now carries a `since_epoch` block alongside the unchanged all-time
  figures — fully additive, no existing field or endpoint touched.
- 🧪 11 new tests (1 for A1's calibration lock, 10 in new `tests/test_epoch_kpi.py`
  for A2). Full suite: **340/340 PASS**.

---

## 🚀 [0.44.4] - 2026-09-10

### 🧹 P3 Follow-up: Dead Code Cleanup + High-Risk-Add Endpoint Consistency
- 🔎 **Dead code removed**: the addon composite-SL-trigger-price mechanism from P3
  rounds 1-2 (`position_addons.sl_trigger_price`, `LoggerDB.get_addon_sl_trigger_price`/
  `set_addon_sl_trigger_price`, `daemon._addon_sl_trigger_price`/
  `_update_addon_sl_trigger_price`, `execute_order`'s `sl_trigger_price` report) —
  fully superseded by V2's `base_entry_price`, nothing read it anymore.
  `OKXExecutor._compute_sl_trigger_price` stays; `attach_stop_loss` still uses it.
- 🔧 **`/api/v1/trade/high_risk_add` brought into V2 consistency**: this manual
  endpoint still attached its own independent SL, reproducing the exact
  determinism/classification bug V2 fixes for the automatic path. Now consistent:
  no independent SL attached; the position stays protected by its existing base
  SL. `stop_loss_pct` in the request is kept for backward compatibility but is
  now deprecated/ignored.
- 🧪 Added hedge-mode (long+short simultaneously) and close-then-reopen test
  coverage for the Self-Healing SL Guard / `base_entry_price`, identified as gaps
  by independent review. Full suite: **329/329 PASS**.
- 📋 Docs: `STRATEGY_AND_ALGORITHM.md`/`MASTERMIND_STRATEGY.md` updated to drop
  the addon's old independent-SL description; the audit doc's P3 finding gets a
  "resolved" status pointer (historical diagnosis text left unchanged).

---

## 🚀 [0.44.3] - 2026-09-10

### 🎯 P3: Single Source of Truth for Position Stop-Loss (V2)
- 🔎 **Root cause**: a High-Risk-Add ("addon") position previously got its own,
  separate composite SL — anchored to the addon's own fill price, not to the
  position's average entry (avgPx). Two independent code paths (the addon's own
  `execute_order` attach vs the Self-Healing SL Guard's re-attach) could compute
  that composite SL from two DIFFERENT reference prices, so the same position
  could end up with two different real SL prices depending on which path fired
  last — and `reconcile_positions`'s stop-out classification, keyed off avgPx,
  regularly missed real stop-outs on addon-carrying positions (audit
  `docs/AUDIT_MMS4_PROFIT_OPTIMIZATION_2026-09-08.md`, §1.2/P3).
- 🔧 **Fix (V2)**: the addon leg no longer gets an independent SL at all.
  `position_snapshots` gains `base_entry_price` — pinned once, at a position's
  first snapshot, and never moved by later addon averaging. `reconcile_positions`,
  the Self-Healing SL Guard, and the Midline Partial TP re-attach now all
  classify/re-attach unconditionally from `base_entry_price` + `base_sl_pct` —
  OKX's existing `closeFraction="1"` algo order already protects the whole
  (larger, post-addon) position, so leaving it untouched is sufficient.
- 🎯 **Side effect**: `state_manager.py`'s `>=2%` SCOUT_MODE/Hermes-H4 gate now
  fires correctly for addon-carrying stop-outs too (the reported `sl_percent` is
  now `base_entry_price`-relative), with zero changes to `state_manager.py`
  itself. `pnl_percent`/`pnl_usd` (accounting) stay avgPx-based, unchanged.
- 🧪 **Test isolation hardening**: `tests/conftest.py` now forces an isolated
  `MMS4_DB_PATH` and maintenance-lock file location before any `mms4tk` module is
  imported, closing a real risk (running the suite against a live deployment
  could otherwise have written to the production DB and released a real
  maintenance lock).
- 🧪 Went through 3 rounds of independent adversarial review; 32 new/rewritten
  tests across `test_reconciliation_pnl.py`, `test_okx_sl_algo_instid.py`,
  `test_midline_inactivity_tp.py`, `test_max_margin_allocation.py`. Full suite:
  **330/330 PASS**.
- 📋 Known, documented gaps (not addressed here): the manual
  `/api/v1/trade/high_risk_add` endpoint still attaches its own independent SL;
  dead code from the superseded composite-trigger mechanism
  (`position_addons.sl_trigger_price` and friends) left in place, unused, pending
  a low-risk cleanup follow-up. See `task.md` for the full writeup.

---

## 🚀 [0.44.2] - 2026-09-04

### 🐛 Duplicate Telegram Alerts Fixed (POSITION_OPENED, STALE_POSITION_FORCE_CLOSE)
- 🔎 **Root cause**: after the 0.44.1 direct-Bot-API fix started working, a SECOND, independent Telegram channel was discovered still active — `scripts/hermes/mms4tk_event_dispatcher.py`, a Hermes-side cron (runs every ~1 minute, outside `antigravity_core`) that polls `webhook_events` and, for a hardcoded list of event types, also calls `hermes send --to telegram` itself. Cross-referencing every `HermesWebhookEvent` emission site against that list found exactly two genuine double-sends: `POSITION_OPENED` (every trade entry) and `STALE_POSITION_FORCE_CLOSE`.
- 🔧 **Fix**: removed both event types from the dispatcher's Telegram-send list — mms4tk's own `daemon.py`/`hermes_webhook.py` already send them immediately via the direct Bot API path, so this cron no longer needs to (and must not). `SCOUT_MODE_ACTIVATED`/`HITL_REVIEW`/`RISK_ALERT`/`MULTI_SYMBOL_DIGEST` are untouched — each has no other Telegram path and this dispatcher remains their sole channel.
- 🧹 Extracted the routing logic into two pure, testable functions (`is_actionable_event`, `should_send_telegram_directly`) instead of inlining it in `main()`'s loop.
- 🧪 **21 new tests** (`tests/test_hermes_event_dispatcher.py`) locking in the full routing matrix so this duplication cannot silently regress.

### 🐛 Telegram Alerts Fixed: Direct Bot API Dispatch
- 🔎 **Root cause**: `telegram_queue.py` only shelled out to an external `hermes` CLI (local binary path, `docker exec hermes-agent`, or PATH lookup) — all three are structurally unreachable from inside `antigravity_core` (no `docker` binary in the image, no docker socket mounted, no such binary present). Every alert had been silently failing since deployment; confirmed live: both a position-open and a stop-loss notification failed the same day (2026-09-03) with no operator-visible error beyond a debug-level log line.
- 🔧 **Fix**: `_send_now()` now dispatches directly via `api.telegram.org`'s Bot API first (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, both plain env vars, `httpx`, no subprocess) — the legacy CLI candidates remain only as a fallback, never the primary path. Falls back to the lowercase `chat_id` env var too, for a value set under that name before the convention was corrected.
- 🧪 **7 new tests** (`tests/test_telegram_queue.py`): missing-credentials no-op, successful dispatch, lowercase-env-var fallback, Telegram error response, network exception, confirms the CLI subprocess is never invoked when the Bot API succeeds, confirms the CLI fallback still fires when it doesn't.

---

## 🚀 [0.44.0] - 2026-09-02

### 🧪 Backtest Engine: HIGH_RISK_ADD (Addon) Fidelity (PR 2/3)
- 🎯 **`run_backtest()` now models addon signals**, previously silently dropped: `analytics.py` returns `HIGH_RISK_ADD_LONG/SHORT` *instead of* `BASE_LONG/SHORT` on a qualifying continuation candle, so every such candle was a real, undocumented bias in every prior backtest result (a missed entry the harness never accounted for), not merely a missing nice-to-have.
- 🔒 **Single Addon Guard**: at most one addon per position, keyed on the position itself and never reset by the entry-price shift — mirrors `logger_db.py`'s `get_addon_count` design exactly (its own docstring documents the real production bug that design avoids).
- 🚫 **Blocked in SCOUT_MODE**: an addon-qualifying signal while the state machine is in SCOUT_MODE is a no-op — this guard has no meaning until PR 1's SCOUT_MODE fidelity landed, the concrete proof of the two PRs' sequencing dependency.
- 📐 **Weighted-average entry price**: an explicit, documented approximation of OKX's own `avgPx` merge — production has no in-code equivalent (relies on the exchange reporting the merged average for same-side same-symbol fills).
- 💰 **Tighter composite stop-loss**: `ADDON_SL_PCT` (1.25%, mirroring `daemon.py`'s default) applies to the WHOLE position once an addon exists, replacing the base 2% threshold — and correctly does NOT retrigger a SCOUT_MODE transition on its own (`on_stop_loss_hit`'s `>=2%` check), matching production exactly.
- 🧪 **3 new tests**, driven by a scripted `MMS4SignalEngine.evaluate_signals` monkeypatch (deterministic, since replicating `analytics.py`'s real candle-shape detection by hand for addon-qualifying candles is its own large surface): Single Addon Guard holds under repeated qualifying candles, addon is blocked in SCOUT_MODE, composite SL fires at the tighter threshold where the base one would not have.
- 📋 PR 2 of 3 — closes the backtest-harness fidelity sequence (PR 0: Midline Partial TP, v0.42.1; PR 1: SCOUT_MODE, v0.43.0). Prerequisite for trusting the trend filter's next recalibration once ~175 clean trades accumulate; not urgent standalone work.

---

## 🚀 [0.43.0] - 2026-09-02

### 🧪 Backtest Engine: BASE_MODE/SCOUT_MODE Fidelity (PR 1/3)
- 🎯 **`run_backtest()` now models the real state machine**: a real `MMS4StateManager` tracks state across closed trades via `sm.record_trade()` (previously constructed but never actually updated — `sm` stayed pinned to `BASE_MODE` for the entire simulation). A >=2% stop-loss now correctly shrinks subsequent entries to SCOUT_MODE sizing (`SCOUT_SIZE_MULT`/`SCOUT_LEVERAGE`, ~10% notional at defaults); only a FULL (non-partial) take-profit exits it — a partial booking (`STALE_SPLIT_50`/`MIDLINE_PARTIAL_TP`) explicitly preserves SCOUT_MODE, matching `state_manager.py`'s own behavior exactly.
- 📐 **`weight` redefined as a notional fraction of a full BASE entry** (was a contracts-only fraction) — reuses `get_risk_profile_snapshot()`'s own `notional_reduction_factor` rather than re-deriving it, so `_stats()`'s R:R/pnl aggregation can never silently drift from what the state manager actually computes. Semantic change: any `trend_filter_calibration_runs` row persisted before this lands is not bit-for-bit comparable to a post-change run for any window that included a >=2% stop-loss (full-BASE-only runs are unaffected — `notional_weight` was already 1.0 there).
- 🐛 **Dropped the `LoggerDB`/`tempfile` scaffold** from `run_backtest()` — every write site `MMS4StateManager` needs is already `logger_db is not None`-guarded, so state transitions/sizing work identically without one. Removes a synchronous SQLite write per closed trade inside a 64-cell × 2-direction grid search — a real throughput win for `train_test_validate()`.
- 🧪 **13 new tests**: state-machine transitions tested directly against `MMS4StateManager` (SCOUT entered/not-entered at the 2% boundary, partial-TP preserves SCOUT, full TP exits it, `_trade_record_flags`'s table parametrized over every exit reason), `_size_new_entry`'s dynamic sizing, and one candle-driven end-to-end sanity check on real fixture data.
- 📋 PR 1 of a 3-PR sequence closing backtest-harness fidelity gaps (PR 0: Midline Partial TP, v0.42.1; PR 2: HIGH_RISK_ADD/addon simulation, next) — approved plan, prerequisite for trusting the trend filter's next recalibration once ~175 clean trades accumulate, not urgent standalone work (today's Midline-TP-disable decision does not need re-validation — SCOUT-fidelity only reinforces that finding, per the plan's analysis).

---

## 🚀 [0.42.1] - 2026-09-02

### 🧪 Backtest Engine: Midline Partial TP Support (PLAN-009 v2 validation, PR 0)
- 🎯 **`run_backtest()` now models Midline Partial TP** (`midline_tp_enabled`, `midline_tp_fraction` params): TMA-midline touch in the position's favor, evaluated before inactivity checks (matches `daemon.py`'s ordering), fires at most once per position, skipped for positions `OKXExecutor.reduction_amount()` can't halve (mirrors the real fail-closed micro-position guard), hard stop-loss stays at its original entry-relative price on the remainder — never moved to break-even, matching production's actual choice.
- 📊 **Backtest evidence (2026-09-02)**: A/B comparison on the real 90-day BTC/USDC + ETH/USDC fixture data found Midline Partial TP **consistently worsens R:R in 6/6 tested windows** (both 45-day halves and the full 90 days, both symbols) — mechanically, truncating winners at the midline while losses still run to full stop-loss on the remainder. This evidence led to disabling `midline_partial_tp_enabled` in production `system_settings` the same day; PLAN-009 v2 (shipped 2026-08-31, v0.42.0) had gone to production without backtest validation.
- 🧪 **4 new tests**: disabled-by-default no-op, fires-on-real-data, at-most-once-per-position, micro-position skip.
- 📋 First of a 3-PR sequence closing backtest-harness fidelity gaps (SCOUT_MODE state machine, HIGH_RISK_ADD addon simulation follow in subsequent PRs) — see the design plan referenced in `plan008-phase3-trend-filter-overfit` memory.

---

## 🚀 [0.42.0] - 2026-08-31

### 🎯 Midline Partial Take-Profit (T1 Netting) & Inactivity v2 (PLAN-009)
- ⏱️ **Profit Stall Gate (`inactivity_manager.py`)**: Added `profit_stall_candles` and `last_peak_price` tracking in `PositionTracker`. If uPnL >= 0.6 ATR stalls for 4 consecutive candles without making a new ATH/ATL, automatically triggers `SPLIT_50` to de-risk gains.
- 📐 **Impulse Negation 38.2% Fibonacci Retracement**: Tightened impulse negation from 50% to 38.2% Fibonacci retracement for meaningful impulses >= 0.8 ATR.
- 💰 **Break-Even Escape Fee Buffer**: Updated `DEFAULT_ROUND_TRIP_COST_PCT` to 0.12% (0.0012) and `DEFAULT_BE_ESCAPE_THRESHOLD` to 0.20% (0.0020) to ensure BE_ESCAPE only triggers when round-trip exchange fees are fully covered.
- 🛡️ **`tp_midline_ledger` Table & Fail-Closed Pattern (`logger_db.py`)**: Implemented persistent SQLite ledger `tp_midline_ledger` with fail-closed read semantics (`is_midline_tp_active()` returns `True` on any database error to block duplicate market orders).
- 🎯 **Daemon Midline Netting Execution & Hard 2.0% SL (`daemon.py`)**: Autonomous partial position closure (T1) on TMA midline cross with hard 2.00% Stop Loss preservation on the remaining contract size (does not advance SL to entry). Micro-positions (< 2 contracts) skip T1 gracefully.
- 🔒 **SCOUT_MODE Lock on Partial TP (`state_manager.py`)**: Added `is_partial: bool = False` to `record_trade()`; partial take-profit events retain defensive `SCOUT_MODE` until full position closure.
- 📡 **Telegram Alerts & Web Dashboard (`hermes_webhook.py`, `SettingsTab.jsx`, `api.py`)**: Added `emit_midline_tp_event()` Telegram dispatch and exposed `midline_partial_tp_enabled` / `midline_tp_fraction` in the Web UI dashboard and REST API.
- 🧪 **Unit Test Suite (`tests/test_midline_inactivity_tp.py`)**: Added 7 comprehensive test cases covering profit stall gate, 38.2% Fibonacci negation, BE fee buffer, fail-closed ledger, hard SL distance preservation, micro-position indivisibility, and SCOUT_MODE lock.

---

## 🚀 [0.41.1] - 2026-08-30

### 💬 Telegram Reports & Alerts Fee Transparency
- 🔔 **Position Close Monitor (`scripts/hermes/mms4tk_position_close_monitor.py`)**: Updated Telegram trade close notification to explicitly show Net PnL (`💸 Wynik Netto`), fees (`🏷️ Prowizje OKX`), Gross PnL (`📈 Wynik Brutto`), and accurate win/loss status based on net profit.
- 📊 **Multi-Symbol Digest (`scripts/hermes/mms4tk_multi_symbol_digest.py`)**: Enhanced OKX wallet summary in multi-symbol reports to display realized net PnL, total fees, realized gross PnL, and net win rate.
- 🤖 **Daemon Real-Time Notifications (`daemon.py`)**: Integrated taker fee estimation and estimated net PnL into stop-loss and trade reconciliation Telegram alerts.
- 🧪 **Unit Test Suite (`tests/test_telegram_fee_reports.py`)**: Added unit tests validating message formatting across close monitor, multi-symbol digest, and daemon reconciliation.

---

## 🚀 [0.41.0] - 2026-08-30

### 💸 Fee Tracking & Net PnL Accounting (PLAN-011)
- 🗄️ **`LoggerDB` Schema Auto-Migration**: Added `fee_usd` and `pnl_net_usd` columns (`REAL NOT NULL DEFAULT 0.0`) to `closed_trades` with seamless PRAGMA table auto-migration for existing databases.
- 🔄 **FIFO Round-Trip Fee Matching (`trade_sync.py`)**: `OKXTradeSyncWorker` now parses order fee amounts from OKX fills, tracks `open_fee_per_unit` on opening legs, matches consumed opening fees + closing fees on position close, computes `pnl_net = pnl_gross - total_fee`, and classifies `is_win = pnl_net > 0`.
- 🍰 **Pro-Rata Partial Close Handling**: Supported multi-stage and split-close exits (such as `SPLIT_50`) with proportional opening fee consumption across trade legs.
- 📊 **Stats & History API Synchronization (`api.py`)**: Updated `GET /api/v1/stats/pnl` and `GET /api/v1/stats` with `realized_gross_pnl_usd`, `total_fees_usd`, `realized_net_pnl_usd`, and `net_win_rate_pct`. Added `/api/v1/trades/history` endpoint alias.
- 🖥️ **UI Dashboard Transparency (`AnalyticsTab.jsx`)**: Updated Wykaz Transakcji to display PnL Netto as primary metric with PnL Brutto and Prowizja (Fee) visible, updated status badge classification to reflect true net win/loss, and updated equity curve to calculate cumulative net realized balance (`pnl_net_usd ?? pnl_usd`).
- 🧪 **Unit Test Suite (`tests/test_fee_tracking.py`)**: Added full coverage for schema auto-migration, FIFO fee matching, partial close fee proration, net win/loss edge cases, and API stats aggregation.

---

## 🚀 [0.40.0] - 2026-08-29

### 🔁 Data Collection Epoch Marker (PLAN-010 follow-up)
- 🆕 **`LoggerDB.get_data_collection_epoch(symbol)`** + `count_clean_closed_trades()` now excludes trades before `system_settings["data_collection_epoch_start:{symbol}"]`, when set — a prior trading regime (different allocation, different enabled filters) is not comparable evidence for calibrating the CURRENT regime, even if individually clean by the existing degenerate/duplicate-chain checks. `None` (never set) preserves the old all-history behavior.
- 🆕 **`/recalibration-status`** now exposes `data_collection_epoch_start` (null until set for a symbol) and the Symbols UI trend-filter panel shows "liczone od {data} (zmiana reżimu handlu)" under the progress bar once set.
- 🐛 **Chain detection now epoch-aware too**: a duplicate chain is filtered to its post-epoch members before counting "excess", so a chain straddling the epoch boundary (historical artifact only, 2026-08-15/16) can't drag a pre-epoch row's count into the post-epoch total.
- ⚙️ **Operational**: marked `data_collection_epoch_start:BTC/USDC` and `:ETH/USDC` = 2026-08-29T16:09:09Z directly in production `system_settings` — the point at which allocation was reduced $100 → $50/symbol and trading was unlocked. Also corrected `backtest_engine.py`'s `BASE_CONTRACTS` (used only for the `SPLIT_50` no-op fidelity check) from the stale $100-allocation sizes (BTC=2, ETH=8) to the real $50-allocation sizes verified live via `discover_eea_xperp_symbol()` (BTC=4, ETH=14) — the old values would have silently modeled the wrong position-size regime in any future recalibration.
- 🧪 **6 new tests** (4 `LoggerDB`, 2 API): epoch exclusion, per-symbol isolation, boundary-inclusive (`>=`), null-when-unset, and API field exposure.

---

## 🚀 [0.39.0] - 2026-08-29

### 🔁 Trend Filter Recalibration Mechanism — Phase 3: Symbols UI (PLAN-010)
- 🆕 **`GET /api/v1/symbols/{symbol}/recalibrate/history`**: read-only, most-recent-first list of past calibration runs for a symbol (job_id/status/verdict/timestamps, no full result blob) — backs the "historia kalibracji" panel. Not explicitly speced in Phase 2 but required by §5.2.3; added here alongside the UI that consumes it.
- 🎛️ **`SymbolCard` new "FILTR TRENDU (TMA SLOPE)" section** (`ui_dashboard/src/components/SymbolsTab.jsx`): enabled/disabled state, current lookback/threshold when enabled, a "brak potwierdzenia (VERDICT)" badge whenever the last calibration wasn't `VALIDATED`, and a progress bar (`clean_trade_count`/`eligibility_threshold`) gating the "🔬 Rekalibruj" button.
- 🔒 **Disable-only manual toggle (deliberate deviation from a literal read of §5.1)**: the WŁ/WYŁ toggle can turn the filter OFF freely, but NOT back ON — re-enabling with unvalidated schema defaults (lookback=5, threshold=0.002) via a bare click would reintroduce the exact ungrounded-parameter problem this whole mechanism exists to prevent. Turning it on only happens through a validated recalibration's "✅ Zastosuj nowe parametry", which sets real params and `enabled=true` atomically via the existing `POST /api/v1/symbols/update` path.
- 🔄 **Recalibration flow**: "🔬 Rekalibruj" → `POST /recalibrate` (202 + job_id) → modal opens immediately in a polling state (`GET /recalibrate/status/{job_id}` every 2.5s) → renders both train/test directions' R:R/pnl once done. `verdict == VALIDATED` shows a separate "✅ Zastosuj nowe parametry" button; any other verdict renders no apply affordance at all (cannot be applied by mistake). Closing the modal early only stops this component's polling — the job keeps running server-side and shows up in history once finished.
- ⚠️ **Deviation from plan §5.3 (explicit, user-approved)**: `ui_dashboard` has no JS test framework (no Jest/Vitest/RTL) — adding one was out of scope for this feature. The two component-test requirements (apply button absent on `OVERFIT_REJECTED`; 🔬 disabled below threshold with correct progress text) were verified by a manual build + logic review instead of automated component tests.
- 🧪 **2 new backend tests** for the history endpoint (most-recent-first ordering, empty-for-unseen-symbol). `npm run build` verified clean (no test framework available for component-level assertions — see deviation note above).
- 📋 Phase 3 of 3 in `.agents/plans/PLAN-010-trend-filter-recalibration-mechanism.md` — closes out PLAN-010.

---

## 🚀 [0.38.0] - 2026-08-29

### 🔁 Trend Filter Recalibration Mechanism — Phase 2: Calibration Engine (PLAN-010)
- 🧮 **`src/mms4tk/backtest_engine.py`**: promotes the scratchpad harness used for the 2026-08-29 review (`backtest_plan008_phase3_v2.py`, `oos_train_test_v2.py`) into a maintained module. `run_backtest()` simulates candle-by-candle using the real production classes (`TMAIndicator`/`MMS4SignalEngine`/`MMS4InactivityManager`/`MMS4StateManager`), with `SPLIT_50` mirroring the REAL `OKXExecutor.reduction_amount()` no-op rule (a position that can't be halved without fully closing it is HELD, not partially booked) — the exact harness-fidelity fix the 2026-08-29 review required before trusting any calibration verdict.
- 🚦 **`train_test_validate()`**: the ONLY calibration entry point — structurally enforces train/test validation in BOTH directions (train H1→test H2 AND train H2→test H1). Returns `VALIDATED` only when both directions independently generalize; `OVERFIT_REJECTED` when at least one doesn't; `NO_CANDIDATE` when no grid cell beats baseline on a training half at all. Surfaces (never hides) disagreement between the two directions' chosen parameters — exactly the failure mode that shipped in PLAN-008 Phase 3.
- ⏱️ **Asynchronous by design — `POST /api/v1/symbols/{symbol}/recalibrate`**: returns `202` + `job_id` immediately; the actual computation (tens of seconds to several minutes per the 2026-08-29 measured timings) runs in a `ProcessPoolExecutor` worker process (`run_calibration_job`), never inline. `api.py` and the live trading daemon share one asyncio event loop — a blocking inline run would stall live reconciliation/self-healing-SL for the run's full duration. Requires `eligible: true` from `/recalibration-status` (409 otherwise). Never writes `tma_trend_filter_enabled`/`tma_slope_lookback`/`tma_slope_threshold` — analysis-only.
- 🆕 **`GET /api/v1/symbols/{symbol}/recalibrate/status/{job_id}`**: polls job status (`pending`/`running`/`done`/`failed`) and returns the full `CalibrationResult` once done.
- 🧮 **`OKXExecutor.fetch_candles_paginated()`**: paginated OHLCV fetch for 90-180 day windows (a single `fetch_ohlcv` call is capped at ~300 candles), backed by a new `ohlcv_cache` SQLite table — only the missing tail since the last cached candle is fetched from OKX on repeat calls, not the whole window every time. Falls back to cached data on a transient OKX failure. Public market data only — no API credentials needed, safe under the maintenance lock.
- 🗄️ **`trend_filter_calibration_runs`** table + `LoggerDB` accessors (`create_calibration_run`, `update_calibration_run`, `get_calibration_run`, `get_latest_calibration_run`, `list_calibration_runs`) — job status/result persistence, read back by both the status endpoint and `/recalibration-status`' "last calibration verdict" field (previously always `null` in Phase 1, before this table existed).
- 🐛 **Review fix (found before merge)**: `POST /recalibrate` had no guard against two requests for the same symbol before the first job finished — `CALIBRATION_EXECUTOR`'s `max_workers=1` serializes the actual computation either way, but nothing stopped a second, wasted multi-minute run from being enqueued. Now returns the existing `pending`/`running` job's `job_id` instead of creating a duplicate.
- 🧪 **27 new tests**: `test_backtest_engine.py` (10 fast + 1 slow real-90-day-data regression guard — confirms BTC/USDC's actual review-period data does NOT validate, as a tripwire against silently reintroducing the original overfitting bug), `test_fetch_candles_paginated.py` (5, mocked ccxt), `test_recalibrate_endpoint.py` (8, including the concrete non-blocking regression: `POST /recalibrate` must return in <1s even when the job takes 2s+; and the duplicate-job guard above).
- 📋 Phase 2 of 3 in `.agents/plans/PLAN-010-trend-filter-recalibration-mechanism.md`.

---

## 🚀 [0.37.0] - 2026-08-29

### 🔁 Trend Filter Recalibration Mechanism — Phase 1: Data Readiness (PLAN-010)
- 🐛 **Fixed `GET /api/v1/symbols` display divergence**: the endpoint read `tma_trend_filter_enabled`/`tma_slope_lookback`/`tma_slope_threshold` from `configured_symbols_store` only. `daemon._get_indicator_params()` reads the SAME fields from per-symbol-qualified `system_settings` keys FIRST (PLAN-008 Phase 3), falling back to the store only when no such key exists. The two were found live and diverged (2026-08-29): the API reported the trend filter as disabled for BTC/USDC and ETH/USDC while `system_settings` — what the engine actually reads — still had it enabled with calibrated parameters. `_trend_filter_fields()` now mirrors the daemon's exact fallback chain so the API/UI can never again silently disagree with the live engine.
- 🧮 **`LoggerDB.find_duplicate_chains()`**: promotes the connected-components duplicate-detection used ad hoc during the 2026-08-29 manual DB cleanup into a tested, named method — groups closed_trades rows by normalized symbol + side + near-identical entry price + tight time window (union-find), returning each multi-row chain once.
- 🧮 **`LoggerDB.count_clean_closed_trades(symbol)`**: total closed trades minus degenerate rows (`entry_price == exit_price`, pre-`trade_sync`-fix era) minus the "excess" members of any detected duplicate chain (a chain of N near-identical rows counts as 1 real event, not N) — read-only, does not delete or modify anything.
- 🆕 **`GET /api/v1/symbols/{symbol}/recalibration-status`**: reports `clean_trade_count`, `eligibility_threshold` (default 175 — midpoint of the ~150-200 range agreed after the deployed Phase 3 trend-filter parameters failed out-of-sample validation), `eligible`, current live filter state/params, and the last calibration run's verdict (always `null` in this phase — the calibration engine itself is PLAN-010 Phase 2, not built yet).
- 🧪 **`tests/test_recalibration_status.py`** (9 tests): duplicate-chain grouping, time-window/side/symbol isolation, degenerate-row exclusion, chain-counts-once semantics, per-symbol isolation, plus 3 review-driven regressions below.
- 🐛 **Review fix (found before merge)**: `find_duplicate_chains`/`count_clean_closed_trades` matched `symbol` by raw string equality — but `closed_trades.symbol` holds the raw OKX instId (`BTC-USD_UM_XPERP-310404`) for every real trade_sync row, confirmed live as 130/130 rows, never the clean pair (`BTC/USDC`) a caller passes. `clean_trade_count` returned 0 for every real production symbol. Fixed with the same `normalize_symbol()` match PR #11 already established; verified against production data post-fix (BTC 24, ETH 21, SOL 24, XRP 7, ICP 5 — matches the manual count from the 2026-08-29 review). Also fixed a related edge case: a degenerate row (`entry==exit`) sitting within tolerance of two otherwise-unrelated clean trades could transitively bridge them into one false chain; `exclude_trade_ids` now removes degenerate rows BEFORE chain detection, not after.
- 📋 Phase 1 of 3 in `.agents/plans/PLAN-010-trend-filter-recalibration-mechanism.md`.
- ⚠️ **Operational note**: the deployed PLAN-008 Phase 3 trend filter was disabled directly at its `system_settings` source of truth for BTC/USDC and ETH/USDC on 2026-08-29, pending this recalibration mechanism — not part of this code change (a live config action, not a code fix).

---

## 🚀 [0.36.1] - 2026-08-29

### 🐛 Fix Duplicate Closed Trades in Wykaz Transakcji
- 🔎 **Root cause**: `MMS4Daemon.reconcile_positions()` writes an immediate, ESTIMATED `closed_trades` row (`trade_id="recon_{symbol}_{side}_<ts_ms>"`, exit price = the daemon's signal price at detection time) so a stop-out drives the state machine right away. `OKXTradeSyncWorker.sync_closed_trades()` later writes the AUTHORITATIVE row for the same real-world close (real OKX order id, real fill price). The two `trade_id` schemes never match, so `upsert_closed_trade`'s `ON CONFLICT(trade_id)` never dedupes them — the same close appeared twice in Wykaz Transakcji with two different exit prices/PnL. Confirmed live (BTC/USDC, 2026-08-28): entry 79668.8 recorded twice, exit 78335.3 vs 77942.0.
- 🧹 **`LoggerDB.delete_provisional_closed_trade()`**: removes the matching `recon_*` row once the authoritative fill lands, matched by NORMALIZED symbol (`reconcile_positions` writes the clean pair "BTC/USDC"; the real fill carries the raw OKX instId "BTC-USD_UM_XPERP-310404" — a plain string match would never fire) + side + entry price (within tolerance). Wired into `OKXTradeSyncWorker.sync_closed_trades()` right before each authoritative upsert.
- 🧪 **`tests/test_dedupe_closed_trades.py`** (5 tests): normalized-symbol matching, price-tolerance and side isolation (doesn't touch unrelated closes), never deletes genuine trade_sync rows (only `recon_*`-prefixed ones are candidates), and an end-to-end sync run leaving exactly one row.
- 🗄️ **Historical cleanup (separate, one-time)**: ~70 pre-remediation duplicate rows from 2026-08-15/16 (same-price, near-zero-PnL bursts from the since-fixed duplicate-execution bug, unrelated to the recon_/trade_sync mechanism above) to be cleaned from the production DB directly — not part of this code change.

---

## 🚀 [0.36.0] - 2026-08-28

### 📈 TMA Slope Trend Filter — per-symbol (PLAN-008 Phase 3)
- 🧮 **`tma_slope` column** in `TMAIndicator.calculate()`: `tma.pct_change(periods=tma_slope_lookback)`, causal (no lookahead — plain trailing `.rolling()`, same as the rest of the indicator).
- 🚫 **Counter-trend BASE entry gating** in `evaluate_signals()`: `BASE_SHORT` vetoed (`is_allowed=False`) when slope > `tma_slope_threshold` (uptrend); `BASE_LONG` vetoed when slope < `-tma_slope_threshold` (downtrend). Independent of `engine_mode`/`is_adaptive` — a strategy-level filter, not part of the Adaptive Hybrid Engine's VR/time-lock fast-path. Routes through the daemon's existing `is_allowed=False` veto handling — no daemon-side dispatch changes needed.
- 🎯 **Per-symbol, not global — required by calibration data**: a 90-day backtest (real `TMAIndicator`/`MMS4SignalEngine`/`MMS4InactivityManager`, real OKX OHLCV) found the SAME `lookback`/`threshold` pair behaves very differently per symbol — a single global switch would have been wrong. Two symbols, two distinct calibrated configs:
  - **BTC/USDC**: `lookback=3, threshold=0.002` — 14/16 cells of the initial 4×4 grid beat baseline (R:R 0.82→1.33, PnL +10.4%→+29.7%, 79 full closes).
  - **ETH/USDC**: the initial 4×4 grid (lookback≤12, threshold≤0.005) only passed at its most restrictive corner — a low-confidence, edge-of-grid result. Extending the grid (lookback 12–30, threshold 0.005–0.020) resolved it: **20/25 extended cells beat baseline**, a broad, stable region — ETH just needs a slower filter than BTC (20h+ lookback vs 3h). Calibrated: **`lookback=20, threshold=0.010`** (R:R 0.89→1.11, PnL +9.6%→+16.4%, 70 full closes), chosen from the middle of the passing region rather than a single best cell.
- 🐛 **Persistence fix**: `tma_trend_filter_enabled`/`tma_slope_lookback`/`tma_slope_threshold` are read from `system_settings` under per-symbol-qualified keys (e.g. `tma_trend_filter_enabled:BTC/USDC`), NOT from `configured_symbols_store`/`symbols.json` alone — the `symbol_configurations` SQLite table has a fixed schema with no column for these, so storing them only there would silently reset to defaults on the next `load_symbols_config()` call (it prefers SQLite whenever any row exists, which it always does once a symbol has been added). This is a pre-existing gap shared by `stale_candles_threshold`/`stale_atr_threshold`/`impulse_negation_enabled` too — flagged separately, not fixed here (out of scope for this PR).
- 🧩 **`_get_indicator_params()` now per-symbol**: takes an optional `symbol` argument, looks up the qualified settings, falls back to `configured_symbols_store` then disabled-by-default.
- 🧪 **`tests/test_trend_filter.py`** (6 tests): slope sign correctness, backward-compatible default-off behavior, counter-trend SHORT/LONG vetoes, below-threshold pass-through, independence from `engine_mode`. **`tests/test_trend_filter_per_symbol_params.py`** (3 tests): default-disabled, per-symbol override surviving a simulated daemon restart, no cross-symbol leakage.
- 📋 Phase 3 of 3 in `.agents/plans/PLAN-008-strategy-fix-trend-filter.md` — closes out PLAN-008.

---

## 🚀 [0.35.0] - 2026-08-28

### 🚦 Candle-Close Gate + Post-SL Cooldown (PLAN-008 Phase 2)
- 🎯 **Candle-Close Gate**: `execute_symbol_cycle()` now tracks, per symbol, the last closed-candle timestamp on which a `BASE_SHORT`/`BASE_LONG`/`HIGH_RISK_ADD_*` signal was dispatched (`_last_signal_candle_ts`). A repeat evaluation of the same signal type on the same closed candle returns `CANDLE_GATE_SKIP_<type>` instead of re-evaluating the entry guards.
- 🐛 **Root cause fixed (confirmed live, 24.08 cluster of 10-15x `EXECUTED_BASE_SHORT`)**: the only prior defense against duplicate entries was the Single Position Guard, which depends on a fresh `fetch_positions()` response. When that response lags or is transiently empty (same failure class as the earlier addon-ledger bug), the daemon re-executed the same signal every 15s cycle. The gate now suppresses this independently of exchange-state reliability.
- 🧊 **Post-SL Cooldown**: a reconciled stop-loss (`is_stop_loss=True`) now engages `_sl_cooldown_until[symbol]` for `MMS4_SL_COOLDOWN_CANDLES` closed candles (default 3). New entries for that symbol return `COOLDOWN_ACTIVE` until the window passes — including on the very cycle the stop-loss reconciles, since cooldown state is computed after reconciliation runs.
- 🛡️ **Safety invariant preserved**: both mechanisms gate only the entry-dispatch branches (`BASE_SHORT`/`BASE_LONG`/`HIGH_RISK_ADD_*`). Position reconciliation, self-healing stop-loss, and the Inactivity Manager still run on every 15s cycle unconditionally — verified by `test_candle_gate_does_not_block_reconciliation`.
- 🧪 **`tests/test_candle_gate.py`** (3 tests) + **`tests/test_sl_cooldown.py`** (4 tests): duplicate-execution suppression, gate reset on a genuinely new candle, reconciliation continuing through a gate-skip cycle, cooldown engagement/persistence/expiry, and non-stop-loss closes correctly NOT engaging cooldown.
- 📋 Phase 2 of 3 in `.agents/plans/PLAN-008-strategy-fix-trend-filter.md` (Phase 3: TMA Slope Trend Filter → v0.36.0, post-calibration).

---

## 🚀 [0.34.1] - 2026-08-28

### 🔌 Live Parameter Synchronization (PLAN-008 Phase 1)
- 🧩 **`_get_indicator_params()` in `MMS4Daemon`**: builds `IndicatorParams` from live SQLite `system_settings` (via `LoggerDB.get_all_settings()`), falling back to env vars / hardcoded defaults on any missing key or DB error.
- 🔗 **Wired into `execute_symbol_cycle()`**: `evaluate_signals()` is now called with `params=self._get_indicator_params()` instead of implicitly building an env-only `IndicatorParams()` inside the engine.
- 🐛 **Root cause fixed**: `engine_mode=ADAPTIVE` and `adaptive_vr_high`/`adaptive_vr_low`/`time_lock_start`/`time_lock_end` mutations saved via the dashboard (persisted to SQLite) never reached signal evaluation — the Adaptive Hybrid Engine (v0.30.0+) was never actually active in production regardless of what the UI showed, and no daemon restart was ever going to fix it.
- 🧪 **`tests/test_params_sync.py`**: verifies defaults without DB settings, live reflection of a SQLite mutation with no restart, graceful fallback on DB failure, and that `execute_symbol_cycle()` actually passes the live params through to `evaluate_signals()`.
- 📋 Phase 1 of 3 in `.agents/plans/PLAN-008-strategy-fix-trend-filter.md` (Phase 2: Candle-Close Gate + Post-SL Cooldown → v0.35.0; Phase 3: TMA Slope Trend Filter → v0.36.0, post-calibration).

---

## 🚀 [0.33.7] - 2026-08-28

### 🐛 Fix Reconciliation & Take Profit PnL USD Calculation (PLAN-006)
- 🧮 **Contract Size Scaling**: Added `_get_contract_size()` in `MMS4Daemon` querying market metadata with fallbacks (0.0001 for BTC, 0.001 for ETH, 1.0 default) to correctly scale position notional and USD PnL.
- ⟲ **Reconciliation PnL Fix**: Scaled stop-loss reconciliation `pnl_usd` by contract size, preventing inflated PnL metrics when positions are closed exchange-side.
- 🎯 **Take Profit PnL Fix**: Scaled take-profit `pnl_usd` in `execute_symbol_cycle()` by contract size before recording trades in `state_manager`.
- 🧪 **Unit Test Suite**: Added `tests/test_reconciliation_pnl.py` verifying BTC, ETH reconciliation, and Take Profit PnL calculations.

---

## 🚀 [0.33.6] - 2026-08-28

### 🛡️ Standalone Maintenance Mode Lock & Safe Execution Guard
- 🔒 **Self-Contained Maintenance CLI (`scripts/maintenance.py`)**: Removed heavy external package dependency (`numpy`) so `maintenance.py --enable` and `--status` execute 100% reliably in any environment.
- 🛡️ **Synchronized Lock Paths (`okx_executor.py`)**: Included repo-root `.agents/swarm/MAINTENANCE_LOCK` in executor paths to ensure zero-loss maintenance mode blocking works across both local and container environments.

---

## 🚀 [0.33.5] - 2026-08-28

### ⚡ Instant Closed Trade Recording & Real-Time Trade Sync
- ⚡ **Immediate Trade Sync on Close (`api.py`)**: Added immediate call to `trade_sync_worker.sync_closed_trades()` directly in `POST /api/v1/positions/close` and `GET /api/v1/history/trades` to eliminate delays when user manually closes a position.
- 💾 **Direct Reconciliation Upsert (`daemon.py`)**: Added direct `logger_db.upsert_closed_trade` in `reconcile_positions()` so any closed or reduced position is guaranteed to be recorded in SQLite `closed_trades` instantaneously without waiting for background polling.

---

## 🚀 [0.33.4] - 2026-08-27

### 🐛 Fix Stop Loss UI Display & Daemon Synchronization (PLAN-005)
- 🔄 **Stop Loss UI Synchronization**: Updated `SettingsTab.jsx` to correctly initialize `stopLoss` from `daemonData?.stop_loss_pct ?? stateData?.stop_loss_pct ?? 2.0` and synchronize with incoming daemon updates.
- 🏷️ **Clarity on Base vs Addon SL**: Updated UI labels to `Bazowy Stop Loss (%):` with explicit tooltip text explaining that 2.00% applies to base position (70% allocation) while addon position maintains fixed 1.25% SL (30% allocation).

---

## 🚀 [0.33.3] - 2026-08-27

### 🐛 Resolve OKX Native instId for Stop Loss Algo Orders (PLAN-004)
- ⚙️ **Native Instrument ID Converter**: Added `to_native_inst_id()` in `OKXExecutor` to reliably translate CCXT unified symbols (e.g. `ETH/USD:USD-310404`, `BTC/USDC`) to native OKX format (`ETH-USD_UM_XPERP-310404`, `BTC-USDC-SWAP`).
- 🛡️ **Stop Loss Algo Order Fix**: Updated `attach_stop_loss()` to pass native `instId` to OKX algo endpoints (`private_get_trade_orders_algo_pending`, `private_post_trade_cancel_algos`, `private_post_trade_order_algo`), eliminating Error 51000 ("Parameter instId error").
- 🩺 **Self-Healing SL Guard Compatibility**: Ensured daemon self-healing stop loss queries resolve native `instId` before checking pending algos.
- 🧪 **Unit Test Suite**: Added `tests/test_okx_sl_algo_instid.py` verifying CCXT/EEA X-Perp translations, SL order placement params, and self-healing loop execution.

---

## 🚀 [0.33.2] - 2026-08-26

### 🛡️ SSOT Allocation Hierarchy & Environment Cleanup (PLAN-003)
- ⚖️ **Single Source of Truth Allocation**: Fixed `plan_allocation()` hierarchy to always prioritize per-symbol dynamic configuration (`allocation_usd` / `budget_usd`) over global margin cap without obsolete environment checks.
- 🗄️ **SQLite SSOT for Engine Settings**: Updated `get_status()` in daemon to query runtime snapshot directly from `LoggerDB.get_all_settings()`.
- 🧹 **.env Runtime Parameter Cleanup**: Removed runtime parameters (`MMS4_MAX_MARGIN_USD`, `MMS4_BASE_SL_PCT`, `MMS4_BASE_ALLOCATION`, `MMS4_ADDON_ALLOCATION`, `MMS4_TOTAL_MAX_PORTFOLIO_MARGIN_USD`) from `.env` updates in `update_settings()`, preserving SQLite as the absolute SSOT.
- 🧪 **Unit Test Suite**: Added 5 comprehensive tests in `tests/test_allocation_ssot.py` verifying allocation precedence, fallback, zero guards, and dynamic mutation safety.

---

## 🚀 [0.33.1] - 2026-08-24

### 🐛 Fix Settings Input Polling Clashing & OKX Live Balance Field
- 🐛 **Input Stability**: Prevented 5-second background polling from overwriting user inputs and preset selections in `SettingsTab`.
- 💰 **Accurate OKX Balance Readout**: Corrected field lookup to `statsData.free_balance`, accurately displaying live Available Margin (~$323.48 USDC) on the `[MAX OKX]` preset badge.

---

## 🚀 [0.33.0] - 2026-08-24

### 🛡️ Portfolio Margin Guard Quick Presets
- 🛡️ **Quick Preset Buttons**: Added interactive `[MAX OKX]` (100% available free margin from OKX API), `[SUMA PAR]` (sum of active symbol allocations), and `[Wyłącz (0)]` buttons directly inside the Portfolio Margin Guard card in Settings.
- 📐 **Responsive UI Layout**: Optimized buttons for both desktop and mobile viewports with real-time numeric badges.

---

## 🚀 [0.32.0] - 2026-08-24

### 🗄️ Unified SQLite System Settings (Single Source of Truth)
- 🗄️ **Relational SSOT Schema**: Introduced `system_settings` table in `.agents/swarm/logs.db` for all dynamic runtime, risk, strategy, and adaptive engine parameters.
- ⚡ **ACID Transactional Updates**: Migrated `GET /api/v1/settings` and `POST /api/v1/settings/update` to read/write directly via `LoggerDB` with bulk transaction guarantees.
- 🛡️ **Container Rebuild Immunity**: Cleaned up dynamic reliance on `.env` text files, guaranteeing settings are permanently preserved in SQLite across all future deployments.

---

## 🚀 [0.31.1] - 2026-08-24

### 🔒 Persistent Volume Storage for Runtime Engine & Strategy Settings
- 🔒 **Volume-Persistent Runtime Settings**: Saved all dynamic settings (`engine_mode`, `adaptive_vr_high`, `adaptive_vr_low`, `time_lock`) to persistent volume JSON storage (`settings.json`).
- 🛡️ **Rebuild Immunity**: Guaranteed that Docker rebuilds and container recreations never reset the Adaptive Engine mode to Classic.

---

## 🚀 [0.31.0] - 2026-08-24

### 🎯 Dynamic Per-Symbol Budget & Margin Allocation Sizing
- 🎯 **Dynamic Per-Symbol Allocation**: Updated `daemon.py` `plan_allocation` to read the exact `allocation_usd`, `base_ratio`, and `add_ratio` configured per symbol in `configured_symbols_store` rather than falling back to global scalar.
- 📐 **True Multi-Pair Granular Sizing**: Fully honors individual capital budgets (e.g. $20 for BTC, $10 for ETH/SOL) for both Base and Add-on orders.

---

## 🚀 [0.30.9] - 2026-08-24

### 🛡️ Self-Healing Stop Loss Protection & Pre-Cancellation Safety
- 🛡️ **Self-Healing SL Guard**: Added automatic Stop Loss health check in `daemon.py` reconciliation loop that detects and attaches missing conditional SL algo orders on OKX every 15 seconds.
- 🧹 **Pre-SL Algo Cancellation**: Before registering updated conditional SL orders, `attach_stop_loss` automatically cancels any stale pending conditional orders for the instrument, preventing OKX duplicate order error 51052.

---

## 🚀 [0.30.8] - 2026-08-24

### 🔔 Real-time Manual and Reconciled Position Close Notifications
- 🔔 **Instant Close Alerting**: Added real-time Telegram notification for all reconciled position closes (both manual closes and exchange limit/TP closes) in `daemon.py`.
- 🔄 **Real-Time Recent Orders Sync**: Updated `trade_sync.py` to query OKX `private_get_trade_orders_history` (recent 7 days fills) alongside archive, ensuring immediately closed trades appear in database within seconds.
- 📦 **Monitored Scripts Repository Sync**: Added `mms4tk_position_close_monitor.py` to version control with robust API key discovery.

---

## 🚀 [0.30.7] - 2026-08-24

### 🔒 Trading Maintenance Lock & Developer Safety Kill-Switch
- 🔒 **Maintenance Mode Protection**: Added multi-layer trading kill-switch (`is_maintenance_locked()`, lock file `/app/.agents/swarm/MAINTENANCE_LOCK`, and `MMS4_MAINTENANCE_MODE` env var) that strictly blocks real OKX orders during system updates and investigations.
- 🔌 **Maintenance API & CLI**: Implemented `/api/v1/daemon/maintenance/{status,enable,disable}` and `scripts/maintenance.py` CLI for instant zero-loss maintenance locking.
- 🖥️ **UI Dashboard Toggle**: Added interactive Maintenance Mode toggle & status indicator in the top header.

---

## 🚀 [0.30.6] - 2026-08-24

### 🛡️ OKX Stop Loss Execution Hardening & Duplicate Dispatch Fix
- 🛡️ **Independent Conditional SL Algo Orders**: Replaced buggy attached SL market parameters (`params["stopLoss"]` which failed with OKX error 51053 on short positions) with explicit `private_post_trade_order_algo` calls (`closeFraction="1"`), ensuring 100% reliable Stop Loss placement for all positions.
- 🧹 **Webhook Event Queue Cleanup**: Fixed historical batch re-dispatching in Hermes event dispatcher and eliminated duplicate event notifications.

---

## 🚀 [0.30.5] - 2026-08-24

### 🔔 Real-time Position Open Alerts & Digest Schedule Refinement
- 🚀 **Immediate Telegram Open Alerts**: Updated `mms4tk_event_dispatcher.py` to route `POSITION_OPENED`, `STOP_LOSS`, `TAKE_PROFIT`, and `STALE_POSITION_FORCE_CLOSE` directly to Telegram via Hermes CLI.
- ⏱️ **1-Minute Dispatcher Cron**: Configured `mms4tk Event Dispatcher` in Hermes scheduler to run every minute (`* * * * *`) for real-time notification delivery.
- 📊 **4-Hour Offset Digest Schedule**: Shifted `mms4tk_multi_symbol_digest.py` to run every 4 hours offset from H4 analysis (`30 2,6,10,14,18,22 * * *` at 02:30, 06:30, 10:30, 14:30, 18:30, 22:30).

---

## 🚀 [0.30.4] - 2026-08-20

### ⚙️ Settings Persistence & Mobile View Overhaul
- 🛡️ **Adaptive Engine Status Sync**: Extended `DaemonStatusResponse` schema with `engine_mode`, `adaptive_vr_high`, `adaptive_vr_low`, `time_lock_start`, `time_lock_end`, and `total_max_portfolio_margin_usd` so Pydantic serialization preserves active engine configuration.
- 🔄 **ConcurrentUser Settings Fetch**: `App.jsx` now polls `/api/v1/daemon/status` and `/api/v1/settings` simultaneously to keep local state strictly synchronized with backend persistence.
- 📱 **Mobile UI Fixes for Smartphone**: Fixed overflowing engine mode switch in `SettingsTab.jsx` with responsive flex column wrap (`flex-col sm:flex-row`), full-width adaptive select on mobile, responsive risk profile cards, and explicit error alert banner for failed API key authorization.

---

## 🚀 [0.30.3] - 2026-08-20

### 🧪 Test Sandbox & Offline Network Fixture Hardening
- 🛡️ **Mock Network Guard**: Stubbed `execute_order` and `cancel_all_orders` in Hermes network integration fixture to prevent latency / hang in isolated environments.
- ⚡ **Daemon Lifecycle Async Mocking**: Added `fetch_candles` and `fetch_positions` mocks in daemon lifecycle test.
- 📦 **UI Bundle Sync**: Fresh production Vite build for PWA Dashboard.

---

## 🚀 [0.30.2] - 2026-08-20

### 🛡️ Mock Mode & OKX Balance Display Fix
- 💰 **Precyzyjne Wskaźniki Mock Mode**:
  - W `okx_executor.py` zmieniono atrapę `fetch_balance()` z fikcyjnego `$10000.0` na `$0.00` przy braku kluczy API OKX.
  - W `api.py` dodano pole `is_mock` do modelu `PnLResponse` (`/api/v1/stats/pnl`), informujące interfejs użytkownika o pracy w trybie symulacji bez kluczy OKX.
  - W `MonitoringTab.jsx` dodano wyraźny badge `[MOCK]` oraz opis `Tryb symulacji (brak kluczy API w .env)` przy braku aktywnych danych giełdowych.
- 🧪 **Testy Jednostkowe**: Poprawiono izolację zmiennej środowiskowej `MMS4_API_KEY` w teście autoryzacji `test_strategy_profile_api`.

---

## 🚀 [0.30.1] - 2026-08-19

### 📝 Dokumentacja Techniczna & API Reference Sync
- 📖 **`README.md`**: Zaktualizowano architekturę systemu, diagram Mermaid, opis Hybrydowego Silnika Adaptacyjnego (VR & Time-Lock), Portfolio Margin Guard oraz zmienne konfiguracyjne.
- 📡 **`docs/API.md`**: Dodano pełną dokumentację endpointów `GET /api/v1/settings`, `POST /api/v1/settings/update` (wraz ze wszystkimi 10 parametrami konfiguracyjnymi) oraz `POST /api/v1/settings/change_password`.
- 🏷️ **Wersjonowanie**: Zsynchronizowano wersję `0.30.1` we wszystkich komponentach aplikacji (`VERSION`, `__init__.py`, `ui_dashboard`).

---

## 🚀 [0.30.0] - 2026-08-19

### ⚡ Hybrydowy Silnik Adaptacyjny & Fast-Path Supervision (Wariant A)
- 🧠 **Architektura Nadzoru Asynchronicznego**: Wdrożenie hybrydowego silnika adaptacyjnego w `analytics.py` bez obciążania pętli transakcyjnej ciężkimi modelami ML/LLM.
- 📈 **Volatility Ratio (VR)**: Wyliczanie wskaźnika $VR = ATR(14) / (SMA(ATR(100), 14) + \epsilon)$ z zabezpieczeniem $\epsilon = 10^{-8}$ przed dzieleniem przez zero.
  - **Filtr Ekstremalnego Trendu**: Przy $VR > MMS4\_ADAPTIVE\_VR\_HIGH$ (domyślnie `1.6`) automatycznie blokowane są sygnały `HIGH_RISK_ADD` z powodem `SKIPPED_HIGH_VOLATILITY`.
  - **Filtr Kompresji/Marazmu**: Przy $VR < MMS4\_ADAPTIVE\_VR\_LOW$ (domyślnie `0.8`) wejścia na bandach wymagają potwierdzenia knotem odrzucenia (Sweep Rejection $\ge 20\%$) z powodem `SKIPPED_COMPRESSION_NO_SWEEP`.
- ⏰ **Filtr Okna Sesyjnego US Open (Time-Lock)**: W trybie `ADAPTIVE` w godzinach $MMS4\_TIME\_LOCK\_START$ – $MMS4\_TIME\_LOCK\_END$ (domyślnie `13:30` - `14:30` UTC) blokowane są wejścia `BASE_SHORT` (`SKIPPED_TIME_LOCK`), chyba że wystąpiło ekstremalne odrzucenie płynności (knot $\ge 40\%$).
- 🎛️ **Konfiguracja w Locie (Settings API & Web UI)**:
  - Dodano `GET /api/v1/settings` oraz rozbudowano `POST /api/v1/settings/update` o walidację Pydantic i bezpieczną persystencję do plików `.env`.
  - Nowa karta w panelu UI: *Hybrydowy Silnik Adaptacyjny* z przełącznikiem trybu `CLASSIC` / `ADAPTIVE`, suwakami progów VR i polami godzin sesyjnych.
- 🧪 **Pełne pokrycie testami jednostkowymi**: Dodano testy `test_adaptive_engine_*` i `test_settings_api_adaptive_configuration` w `tests/test_adaptive_tactics.py`.

---

## 🚀 [0.29.0] - 2026-08-19

### 🛡️ Portfolio Margin Guard (Circuit Breaker)
- 🆕 **Global portfolio notional cap**: New `MMS4_TOTAL_MAX_PORTFOLIO_MARGIN_USD` env var (default `0` = disabled). When set, the daemon blocks any new BASE or HIGH_RISK_ADD entry whose addition would push the aggregate portfolio notional (across ALL symbols) above the configured limit.
- 🛡️ **Circuit breaker logic**: `check_portfolio_margin_guard()` method in `MMS4Daemon` sums all open OKX position notionals and compares against the cap with 5% tolerance. Blocks with `PORTFOLIO_GUARD_BLOCKED_*` outcome logged to cycle_logs and Hermes webhook.
- 📊 **Prevents correlated drawdown**: Addresses the risk of 4+ symbols × $500/symbol = $2000+ unguarded exposure during correlated market moves.
- 📝 **`.env.example` updated** with documentation and configuration guidance.

---

## 🚀 [0.28.3] - 2026-08-18

### 💸 Hermes Digest Realized PnL Endpoint Fix & API Alias
- 🐛 **`mms4tk_multi_symbol_digest.py` PnL Sync Fix**: Changed endpoint query in digest script from `/api/v1/stats` (which previously returned 404 and defaulted realized PnL to $0.00) to `/api/v1/stats/pnl`.
- 🔌 **FastAPI `/api/v1/stats` Alias Route**: Added `@app.get("/api/v1/stats")` route decorator pointing to `get_pnl()` alongside `/api/v1/stats/pnl` for seamless backwards compatibility.

---

## 🚀 [0.28.2] - 2026-08-17

### 🔔 Telegram Notification Bugfix & Digest PnL Addition
- 🛠️ **`telegram_queue.py` Root Cause Fix**: Replaced `asyncio.PriorityQueue` with thread-safe `stdlib.PriorityQueue` + background daemon thread. `asyncio.PriorityQueue` created in `__init__` without an event loop caused `put_nowait()` to raise exceptions silently, blocking all position open/close notifications.
- 🐛 **`mms4tk_multi_symbol_digest.py` NameError Fix**: `hermes_cli` was used in `create_kanban_digest_card()` but never defined in that scope — caused `NameError` on every hourly run, silently swallowing the Telegram dispatch.
- 💸 **Zrealizowany PnL w Digest**: Added `💸 Zrealizowany PnL (Całkowity)` field to Multi-Symbol Digest portfolio summary via `/api/v1/stats` endpoint.

---

## 🚀 [0.28.1] - 2026-08-17

### 🪙 AnalyticsTab Symbol Formatting & Pair Normalization Fix
- 🪙 **Full Pair Normalization (`normalizeSym`)**: Updated `normalizeSym` to always format symbols as full trading pairs (e.g. `BTC/USDC`, `ETH/USDC`, `SOL/USDC`, `ICP/USDC`) across trade history rows and dropdown filters, eliminating single-coin tickers (`BTC`, `ETH`).

---

## 🚀 [0.28.0] - 2026-08-17

### 🛡️ Hard Inactive Guard & Symbol Deletion Overhaul
- 🛑 **Daemon Inactive Symbol Guard Bugfix**: Fixed dict truthiness evaluation in `daemon.py` (`run_loop` & `execute_symbol_cycle`) where `{ "is_active": False }` was evaluating as truthy `True`. Added hard `SKIPPED_INACTIVE` check blocking paused/inactive symbols from trading.
- 🗑️ **Permanent Symbol Deletion Endpoint & Store Cleanup**: Updated `DELETE /api/v1/symbols/{symbol}` to purge entries from `configured_symbols_store`, SQLite `symbol_configurations` (`logger_db.delete_symbol_config`), and persistent `symbols.json`.
- 🗑️ **"Usuń Parę" Action Button & Modal**: Added dedicated "Usuń Parę" button on symbol cards in `SymbolsTab.jsx` with confirmation modal dialog overlay.

---

## 🚀 [0.27.1] - 2026-08-17

### 🛠️ AnalyticsTab Fixes & Double-Confirmation Modal
- 🔒 **Double-Confirmation Modal for Position Closing**: Added styled React Modal overlay (`confirmCloseTarget`) with full details (Symbol, Side, Notional $, uPnL $) requiring explicit user double-confirmation before triggering market close.
- 🔀 **Flawless Symbol Grouping & Filtering**: Implemented `normalizeSym` helper resolving symbol string variations (`BTC/USDC`, `BTC-USDC-SWAP`, `BTC/USDC:USDC`) across trade history records.
- 📈 **Dynamic Equity Curve Rescaling**: Filtered `pnlCurve` chronologically according to the selected timeframe range cutoff (`4H`, `1D`, `1W`, `1M`, `3M`, `6M`, `1Y`, `5Y`), scaling 1:1 with candles chart X-axis.

---

## 🚀 [0.27.0] - 2026-08-17

### 📊 Dashboard Analytics & Live Position Closing Overhaul
- 📈 **Dynamic Equity Curve Timeframe Range Scaling**: Added explicit timeframe range selector pills (`4H`, `1D`, `1W`, `1M`, `3M`, `6M`, `1Y`, `5Y`) and dynamic range title scaling (`AnalyticsTab.jsx`).
- 📋 **Newest-First Multi-Filter Trades Table**: Reversed order of closed trades table to display newest trades first (`b.timestamp - a.timestamp`). Added dynamic multi-filtering controls (Time Period: 1W, 1M default, 3M, ALL; Symbol; Side; WIN/LOSS result) and custom sorting.
- 🔴 **Live Position Close Action Button**: Added `POST /api/v1/positions/close` API endpoint and custom "Zamknij Pozycję" action button for each live position in OKX Active Positions table (`AnalyticsTab.jsx`), triggering position reduction and instant state reconciliation.

---

## 🚀 [0.26.0] - 2026-08-16

### 🛡️ Audit Remediation — Risk Engine (P0/P1/P2)
Full remediation of the 2026-08-16 quantitative audit (`docs/AUDIT_MMS4_2026-08-16.md`).

- 🔴 **P0-1 — Stop-Loss Reconciliation**: `SCOUT_MODE` was unreachable — `on_stop_loss_hit()` had no production caller, so a stop-out never reduced risk. `daemon.reconcile_positions()` now diffs live OKX positions against a SQLite snapshot (`position_snapshots`) every cycle and books the trade the moment a position disappears beyond the SL distance. Survives daemon restarts.
- 🔴 **P0-2 — Impulse Negation Loop**: `_check_impulse_negation` never latched `is_split`, re-issuing `SPLIT_50` every 15s and draining a position in ~3 cycles. The rule now latches, with an action cooldown and a `closing` flag.
- 🟠 **P1** — zero-price tracker poisoning; base entries now require `close < open`; addon guard keyed on the position (persisted, restart-safe); per-symbol signal state; `is_allowed` enforced; budget-respecting sizing; atomic TP flip; `/api/v1/settings/update` fixed; equity curve & round-trip trade pairing.
- 🟡 **P2** — non-repainting HTF/ATR, 2.5×ATR volatility zones, reachable `FORCE_CLOSE_BE`, stale-flag reset, MDD in USD **and** %, no KPI truncation, shared `normalize_symbol()`, `reduceOnly` on reductions.

### 📐 Scalable SCOUT_MODE Sizing (Variant A)
- 🎯 **Risk cut by SIZE, not leverage**: OKX rejects leverage below 1x, so the old `0.1x` was clamped to `1x` on the exchange while the notional it implied fell under the venue minimum — the entry was skipped and the symbol could never leave SCOUT_MODE.
- ⚙️ **New parameters**: `MMS4_SCOUT_LEVERAGE` (1.0), `MMS4_SCOUT_SIZE_MULT` (0.3), `MMS4_BASE_ALLOCATION` / `MMS4_ADDON_ALLOCATION`. Legacy `MMS4_LEVERAGE_SCOUT` still honored.
- 📊 **`daemon.plan_allocation()`** is the single sizing authority: `margin = max_margin_usd × allocation × size_mult`, `notional = margin × leverage`. Reduction versus BASE is `base_leverage / (SCOUT_LEVERAGE × SCOUT_SIZE_MULT)` — **10× at any capital level**, verified from $10 to $12,345.67.
- 🏦 **Dynamic exchange minimums**: `min_notional_for()` reads `limits.cost.min` (or the smallest lot × price) from CCXT instead of a hardcoded $7. Measured live: BTC $6.30, ETH $1.88, SOL $0.75, ICP $2.28.
- ⊘ **Below-minimum is a SKIP, not an ERROR**: new `BelowMinimumNotional` exception, so standing down never reads as a malfunction in the cycle log.

### 📊 Dashboard v0.26.0
- 🔌 Now consumes `GET /api/v1/daemon/status` — per-symbol sizing is read from the engine instead of a hardcoded fallback list.
- 🛡️ **SCOUT_MODE banner** listing affected pairs with their reduced notional and reduction factor; a pair only leaves this state on a take-profit or a manual release, so it is surfaced prominently.
- 📐 Symbol tiles gained a **Rozmiar (%)** field next to leverage — leverage alone no longer describes risk posture — plus the resolved base notional and an **ADD ✓** badge when the 30% add is already used.
- 📉 Max Drawdown labelled in USD, with the percentage shown when a starting balance makes it meaningful.

### 🐛 Fixes Found Post-Deploy
- 🔑 **Addon ledger keying** (`74d0e2b`): the counter was keyed on entry price, but filling an addon *moves* the position's average entry — the key stopped matching and the guard would have permitted a second addon. Caught on the live account 10 minutes after deploy. Now keyed on `(symbol, side)`, cleared only when the side goes flat.
- 📐 **Partial reduction rounding** (`8eed8ac`): `round()` closed 2 of 3 contracts (67%) for a "50%" split — worse, and irreversible, versus the 33% the audit flagged. Reverted to `floor()`; the real defect was `max(1, …)` turning a partial into a full close.
- 🧱 **Build** (`ddf7d6c`): a stray `node_modules` symlink reached the repo and broke the image build — `.gitignore` used `node_modules/`, which matches directories but not symlinks.

### 🧪 Testing
- `tests/test_audit_remediation.py` — 57 regression tests, one per audit finding.
- `tests/audit/` — 5 executable audit scripts, **61/61 assertions PASS** (25/61 before remediation), runnable inside the production container.
- 📄 Full write-up: [`docs/REFACTORING_REPORT_2026-08-16.md`](docs/REFACTORING_REPORT_2026-08-16.md).

---

## 🚀 [0.25.0] - 2026-08-15

### 💰 MMS4 Opposite TMA Band Take Profit & Position Flip
- 🎯 **Opposite Band Take Profit**: When an opposite BASE signal is triggered on M15 (e.g., `BASE_LONG` when `SHORT` is active), the open position is fully closed (`reduce_position` 100%) with Take Profit according to MMS4 rules 1.4 and 8.4.
- 🔄 **Position Flip & Execution**: Immediately following the TP closure, the bot executes the new base order in the flipped direction with standard SL 2%.
- 🔔 **Kanban & Telegram Dispatch**: Added `emit_take_profit_event` in `hermes_webhook.py` to post Take Profit results with PnL % to Hermes Kanban and Telegram.
- 🧪 **Unit Tests**: Added `tests/test_take_profit.py` verifying full TP lifecycle and state reset from `SCOUT_MODE` to `BASE_MODE`.

---

## 🚀 [0.24.4] - 2026-08-15

### 📈 Equity Curve Real Cumulative PnL Fix
- 🚫 **Removed Fake Sine Wave Mock**: Removed artificial `Math.sin(i / 5) * 2` fallback from `AnalyticsTab.jsx`.
- 📊 **Real Cumulative Progression**: Implemented authentic cumulative realized PnL calculation and clean `$0.00` zero-state baseline with formatted YAxis domain.

---

## 🚀 [0.24.3] - 2026-08-15

### 🐛 Inactivity Manager TMAIndicator Params Fix
- 🛠️ **`TMAIndicator.calculate`**: Made `params` argument optional with fallback to `IndicatorParams()`.
- ⚡ **`daemon.py`**: Explicitly passed `IndicatorParams()` in ATR calculation loop for Inactivity Manager.

---

## 🚀 [0.24.2] - 2026-08-15

### ⚡ Daemon Status Telemetry Serialization
- 📊 **`DaemonStatusResponse` Schema**: Added `inactivity_manager` and `active_symbols` fields to Pydantic model for complete telemetry in `GET /api/v1/daemon/status`.

---

## 🚀 [0.24.1] - 2026-08-15

### 🧹 KPI Clean Baseline & Trade Sync Cutoff Filter
- 🛡️ **`trade_sync.py` Cutoff Filter**: Added `MMS4_TRADE_SYNC_START_ISO` filter preventing backfilling pre-baseline corrupted historical test fills into `closed_trades`.
- 📊 **KPI Baseline Reset**: Cleared contaminated historical test records from `logs.db`, resetting KPI metrics (Win Rate, Profit Factor, MDD, R:R) to clean zero-state baseline.
- 🔄 **Active Position Preservation**: 4 live OKX positions (ICP, SOL, ETH, BTC) preserved and registered in Inactivity Manager.

---

## 🚀 [0.24.0] - 2026-08-15

### ⏰ MMS4 Stale Position Management & Inactivity Manager
- 🛡️ **New Module `inactivity_manager.py`**: Autonomous stale position detection engine implementing 4 MMS4 rules (50% impulse negation, BE escape, time-based split, two-stage ladder exit).
- ✂️ **`OKXExecutor.reduce_position()`**: Partial/full position closure via counter-direction market order in OKX hedge mode.
- 🔔 **Hermes Webhook `emit_stale_position_event()`**: Autonomous Kanban cards and Telegram alerts for every protective action (SPLIT_50, FORCE_CLOSE_BE, BE_ESCAPE, FORCE_CLOSE).
- ⚙️ **Per-Symbol Stale Config**: `stale_candles_threshold`, `stale_atr_threshold`, `impulse_negation_enabled` configurable per trading pair in Symbols UI.
- 🕯️ **Precise Candle Close Detection**: New candle timestamp tracking in daemon to avoid false stale detection on polling cycles.
- 📊 **Inactivity Status in Daemon API**: `GET /api/v1/daemon/status` now includes `inactivity_manager` telemetry.

---

## 🚀 [0.23.14] - 2026-08-15

### 🎯 Kanban DB Resolution Priority & Zero-Byte Bypass
- 🔍 **Database Candidate Priority**: Placed `/app/hermes_data/kanban.db` as primary candidate with `os.path.getsize(cand) > 1024` validation.
- ⚡ **True 1:1 Live Sync**: Guaranteed instantaneous reflection of active tasks and task archiving between Hermes CLI and Mastermind UI.

---

## 🚀 [0.23.13] - 2026-08-15

### 🚀 Live WAL + SHM Shared Volume for Real-Time Kanban Sync
- 🔄 **Live WAL Sync**: Mounted `/docker/hermes-agent/data:/app/hermes_data` to allow SQLite write-ahead-log tracking across containers.
- 🎯 **Sub-Second Kanban Reflection**: Immediate synchronization when tasks are created or archived in Hermes CLI.

---

## 🚀 [0.23.12] - 2026-08-15

### ⚡ Direct Root Execution & Immutable SQLite Sync
- 🛡️ **Container Permission Resolution**: Set `user: "0:0"` in `docker-compose.yml` so `antigravity_core` can read the host `/docker/hermes-agent/data/` volume directly.
- 🗄️ **Immutable SQLite Access**: Configured `file:///app/hermes_data/kanban.db?immutable=1` URI for read-only access to Hermes Kanban board.

---

## 🚀 [0.23.11] - 2026-08-15

### 🗂️ Hermes Directory Mount & WAL Sync
- 📁 **Full Data Directory Mount**: Mounted `/docker/hermes-agent/data:/app/hermes_data:ro` in `docker-compose.yml` to enable SQLite WAL + SHM file tracking across containers.

---

## 🚀 [0.23.10] - 2026-08-15

### 🗄️ Hermes Kanban Single File Mount Sync
- 🔗 **Direct File Mount**: Mounted `/docker/hermes-agent/data/kanban.db:/app/.agents/swarm/hermes_kanban.db:ro` in `docker-compose.yml`, bypassing directory permission restrictions.
- 🔄 **Real-Time Kanban Sync**: When tasks are created or archived in Hermes, `/api/v1/hermes/kanban` instantly mirrors the true live board.

---

## 🚀 [0.23.9] - 2026-08-15

### 🔗 Robust SQLite URI Mode for Kanban Read-Only Sync
- 🔒 **Read-Only URI Connection**: Configured `sqlite3.connect(f"file:{hermes_db}?mode=ro", uri=True)` so `antigravity_core` can read the live Hermes `kanban.db` directly without requiring write access or locks.

---

## 🚀 [0.23.8] - 2026-08-15

### 🎯 Direct Hermes Kanban.db Sync & Zero Ghost Cards
- 🔗 **Direct Kanban SQLite Mount**: Mounted Hermes container data directory (`/docker/hermes-agent/data:/app/hermes_data:ro`) into `antigravity_core` in `docker-compose.yml`.
- 🗄️ **Single Source of Truth**: Updated `/api/v1/hermes/kanban` in `api.py` to query active tasks directly from Hermes `kanban.db` (`tasks WHERE status NOT IN ('done', 'archived', 'closed')`). When Hermes board is clean, dashboard accurately shows 0 cards with perfect 1:1 synchronization.

---

## 🚀 [0.23.7] - 2026-08-15

### 🩹 Bugfix in Stats & Kanban Query
- 🐛 **KPI Variable Fix**: Fixed `total_pnl_count` variable reference in `logger_db.py`.
- 🔌 **SQLite Import**: Added top-level `sqlite3` import in `api.py` for `/api/v1/hermes/kanban`.

---

## 🚀 [0.23.6] - 2026-08-15

### 📊 KPI Accuracy, Live uPnL Card & Enhanced Kanban Query
- 📐 **Accurate KPI Statistics**: Filtered out zero-PnL entry and limit orders in `get_kpi_summary()` so Win Rate, Profit Factor, and R:R Ratio reflect only completed round-trip trades with realized PnL.
- ⚡ **Live uPnL Card**: Replaced obsolete static `Saldo BTC` card on the Monitoring Dashboard with live **Niezrealizowany PnL (uPnL)** across all active open futures positions with dynamic total notional exposure.
- 🤖 **Universal Kanban Fetch**: Updated `/api/v1/hermes/kanban` to query both active Hermes CLI tasks and SQLite `webhook_events` to ensure cards render in real-time in the UI.

---

## 🚀 [0.23.5] - 2026-08-15

### 📋 Hermes Kanban Cleanup & Actionable Task Policy
- 🧹 **Kanban Board Cleanup**: Archived 36 stale repetitive cards from the Hermes Kanban task board on VPS (`hermes kanban archive`).
- ⚡ **Eliminate Repetitive Digest Tasks**: Removed CLI task creation from hourly `mms4tk_multi_symbol_digest.py`. Informational reports are dispatched to Telegram and Dashboard Webhooks, reserving Kanban cards exclusively for actionable tasks (`SCOUT_MODE_ACTIVATED`, `HITL_REVIEW`, `RISK_ALERT`).

---

## 🚀 [0.23.4] - 2026-08-15

### 🎨 Header & Monitoring UI Layout Reorganization
- 🔝 **Header Bot Operating Mode**: Integrated the Bot Operating Mode badge (`BASE_MODE` / `SCOUT_MODE`) and global Data Refresh button directly into the top Header bar.
- 🧹 **Monitoring Tab Cleanup**: Removed the redundant large Operating Mode banner from the top of the Monitoring view.
- 🚫 **Header Clean-up**: Removed obsolete static `OKX SPOT BTC/USDC` badge from the top bar.

---

## 🚀 [0.23.3] - 2026-08-15

### 💾 Persistent SQLite Symbol & Interval Configuration
- 🗄️ **Database Persistence**: Added `symbol_configurations` table to SQLite `logger_db.py` (`logs.db`). Per-symbol allocations, base/add ratios, active states, and custom timeframes are stored in SQLite database.
- 🚫 **Immune to Git Overwrites**: Removed `symbols.json` from git tracking and added to `.gitignore`. Git updates/pulls/rebuilds will never overwrite or reset user-configured symbol timeframes.

---

## 🚀 [0.23.2] - 2026-08-15

### 🧭 Strict Directional Side Alignment & Anti-Opposite Hedging Guard
- 🛡️ **Side Direction Matching**: Updated `daemon.py` to enforce strict alignment between incoming signal side (`posSide: "long"` vs `"short"`) and live OKX position side.
- 🚫 **Opposite Hedging Guard**: `BASE_SHORT` / `BASE_LONG` orders are blocked if an opposite position is already open.
- 🚫 **Matching Direction Addon Guard**: `HIGH_RISK_ADD_SHORT` and `HIGH_RISK_ADD_LONG` orders are strictly blocked unless a matching base position in the exact same direction already exists.

---

## 🚀 [0.23.1] - 2026-08-15

### 🛡️ Robust Universal Multi-Symbol Position & Add-on Guard
- 🔒 **Exchange-Level Contract & Notional Size Check**: Fixed `daemon.py` position inspection to calculate `existing_notional` and `existing_contracts` directly from OKX CCXT position data and contract size (`contractSize` / `ctVal`).
- 🚫 **Unbreakable Restart-Resistant Addon Lock**: Prevents add-on order stacking across container restarts by checking live OKX contract counts against base order sizes (`existing_contracts >= base_order_amount + 0.5 * addon_order_amount`) and notional allocations across all trading symbols (BTC, ETH, SOL, ICP).

---

## 🚀 [0.23.0] - 2026-08-15

### 📬 Telegram Queue & Rate-Limiter Module
- 🚦 **Asynchronous Rate-Limiting & Queuing**: Implemented `TelegramQueueManager` (`src/mms4tk/telegram_queue.py`) with prioritized message queuing and minimum interval pacing (3.0s between dispatches) to eliminate Telegram notification bursts and spam.
- 🚫 **Deduplication Filter**: Added SHA-256 fingerprinting and 60-second cooldown per event/symbol key (`dedupe_key`), preventing duplicate alert floods across parallel trading cycles.
- 🧹 **Eliminated Double-Dispatch**: Streamlined `mms4tk_event_dispatcher.py` and `daemon.py` to prevent duplicate event notification sends.

### 💰 Enhanced Portfolio Digest Report
- 💵 **Real Free Available Margin**: Updated `scripts/hermes/mms4tk_multi_symbol_digest.py` to display **Wolny Dostępny Depozyt: $XX.XX USDC** (read live from OKX API) alongside used margin and total equity.

---

## 🚀 [0.22.1] - 2026-08-15

### 🐞 Fix Analytics Tab Render Crash (Black Screen)
- 🛠️ **Fixed Undefined Pagination Variables**: Defined missing `totalTrades`, `totalPages`, `validCurrentPage`, `startIndex`, `endIndex`, and `currentTrades` in `AnalyticsTab.jsx` which caused a React `ReferenceError` black screen.

---

## 🚀 [0.22.0] - 2026-08-15

### 🛡️ Double-Lock Single Addon Guard & Position Cap
- 🔒 **Double-Lock Addon Order Prevention**: Enforced strict single Add-on trade execution per position lifecycle using both in-memory set lock (`_addon_executed_symbols`) and OKX live position notional check (`existing_notional >= notional_base_usd + 0.4 * notional_addon_usd`). Completely prevents duplicate 15-second add-on order stacking.
- ⚖️ **Automatic Addon State Reset**: Automatically releases addon locks when positions are closed by SL/TP (`existing_pos is None`).

---

## 🚀 [0.21.4] - 2026-08-15

### 🐞 Fix AttributeError in Daemon High Risk Add Cycle
- 🛠️ **Fixed Daemon Cycle Exception**: Removed obsolete `MMS4State.HIGH_RISK_ADD_SHORT` enum check in `src/mms4tk/daemon.py` which threw `AttributeError: type object 'MMS4State' has no attribute 'HIGH_RISK_ADD_SHORT'` during multi-symbol daemon cycles.

---

## 🚀 [0.21.3] - 2026-08-15

### 🎯 Fix Hardcoded Version Prop in App.jsx
- 🐞 **Fixed Header Version Prop**: Resolved root cause in `App.jsx` where `<Header>` was passed literal hardcoded string `"0.16.2"` instead of dynamic `{appVersion}` state.

---

## 🚀 [0.21.2] - 2026-08-15

### ⚡ Fix Desktop PWA Caching & Nginx No-Cache Headers
- 🚫 **Nginx No-Cache Headers**: Updated `nginx.conf` to add `Cache-Control "no-cache, no-store, must-revalidate"` for `index.html`, `sw.js`, and `registerSW.js`. Stopped Nginx from serving 7-day expires on Javascript and JSON bundles.
- 🧹 **Header.jsx Cache Reset Button**: Removed stale `'0.16.2'` fallback version string in `Header.jsx` and added an interactive `RefreshCw` button next to the version badge that unregisters all PWA Service Workers, clears `caches` storage, and reloads the browser.

---

## 🚀 [0.21.1] - 2026-08-15

### 📄 Trades History Table Pagination
- 📑 **Closed Trades Pagination**: Added full pagination controls (rows-per-page selector 5/10/25/50, prev/next buttons, page counter, index range indicator) to the "Wykaz Zarejestrowanych Transakcji Rzeczywistych" table in `AnalyticsTab.jsx`.

---

## 🚀 [0.21.0] - 2026-08-15

### 📊 Live Open Positions Table in Analytics & PWA Cache Auto-Purge
- 📈 **Live Open Positions Table in Analytics**: Added a dedicated real-time open positions panel in `AnalyticsTab.jsx` rendering active OKX positions with all key parameters: symbol, side (LONG/SHORT badge), leverage (e.g. 3x), allocation (notional USD), cash margin spent (USD), entry price, current mark price, and unrealized PnL (uPnL).
- ⚡ **PWA Cache Auto-Purge**: Configured Workbox `skipWaiting: true`, `clientsClaim: true`, and `cleanupOutdatedCaches: true` in `vite.config.js` and added cache-busting timestamp `?t=...` to `/api/v1/health` API requests so the browser automatically updates PWA assets on every version release.

---

## 🚀 [0.20.2] - 2026-08-15

### 💰 Direct Top-Level OKX availEq API Parsing
- 💵 **Exact availEq Extraction**: Updated `src/mms4tk/okx_executor.py` (`fetch_balance`) to read top-level `availEq` directly from OKX V5 account balance API (`data[0]["availEq"]`). Reports exact real available margin ($53.44 USDC) instead of cash balance without math calculations.

---

## 🚀 [0.20.1] - 2026-08-15

### 💰 Direct OKX availBal API Reporting
- 💵 **Direct OKX availBal API**: Updated `src/mms4tk/okx_executor.py` (`fetch_balance`) to query `/api/v5/account/balance` directly from OKX V5 API and populate `free_usdc` with exact `availBal` (pure available free margin without calculations).
- 📊 **Updated Telegram & Dashboard Reports**: Updated Hermes multi-symbol Telegram digest (`mms4tk_multi_symbol_digest.py`) and UI Dashboard (`MonitoringTab.jsx`) to display direct OKX `availBal` ($53.28 USDC) alongside Total Equity ($83.63 USDC).

---

## 🚀 [0.20.0] - 2026-08-15

### 🛡️ Single Position Lock Guard & Duplicate Order Prevention
- 🛡️ **Single Position Guard**: Updated `src/mms4tk/daemon.py` to query live OKX positions before executing any order. If a base position already exists for a target symbol (`contracts > 0`), duplicate order execution is immediately blocked and skipped (`SKIPPED_BASE_SHORT`). Prevents continuous 15-second re-entry loops while signals remain active.

---

## 🚀 [0.19.1] - 2026-08-15

### ⚡ Hermes Kanban Optimization (Actionable Cards Only)
- ⚡ **Filtered Kanban Creation**: Updated `scripts/hermes/mms4tk_event_dispatcher.py` to stop creating noisy informational Kanban cards for `POSITION_OPENED` or `MULTI_SYMBOL_DIGEST`. Informational events are dispatched **exclusively to Telegram**, reserving Hermes Kanban cards for actionable events requiring AI analysis or Human-in-the-Loop review (`SCOUT_MODE_ACTIVATED`, `HITL_REVIEW`).

---

## 🚀 [0.19.0] - 2026-08-15

### 💾 Persistent Symbol Configuration Storage (.agents/swarm/symbols.json)
- 💾 **Persistent Timeframes & Allocations**: Migrated symbol configuration storage to persistent Docker volume path (`.agents/swarm/symbols.json`). All user-configured timeframes (e.g. `1h` for BTC/ETH, `15m` for SOL/ICP), allocations, and active states now permanently survive container updates, rebuilds, and deployments without resetting to defaults.

---

## 🚀 [0.18.2] - 2026-08-15

### ⚡ Telegram Dispatch Throttling & Rate-Limit Prevention
- ⚡ **Dispatcher Rate-Limit Throttle**: Added 1.2s sleep interval between sequential Telegram message dispatches in `scripts/hermes/mms4tk_event_dispatcher.py` to prevent Telegram API rate limits from dropping rapid multi-symbol trade alerts.

---

## 🚀 [0.18.1] - 2026-08-15

### 🐛 Telegram Position Alert Dispatch Fix
- 🐛 **Event Dispatcher Fix**: Fixed `NameError` in `scripts/hermes/mms4tk_event_dispatcher.py` when sending Telegram alerts for executed position opening events. Updated Hermes CLI call to `hermes send --to telegram` syntax to ensure instant position opening notifications land in Telegram.

---

## 🚀 [0.18.0] - 2026-08-15

### 🚀 Pure Cash Allocation Position Sizing (Leverage Multiplied Notional Target)
- 🚀 **Pure Cash Allocation Sizing**: Updated `src/mms4tk/daemon.py` order calculation so that `max_margin_usd` ($10 USD) represents **exact real cash margin spent** ($7 USD Base / $3 USD Addon). Position notional value is now computed as `base_trade_usd * leverage` ($21 USD Notional @ 3x), ensuring the user's real cash invested matches the exact 70/30 allocation regardless of leverage.

---

## 🚀 [0.17.0] - 2026-08-15

### 🚀 Live Open Positions API & Real-Time Telegram Webhook Alerts
- 🚀 **GET `/api/v1/positions` Endpoint**: Added missing `/api/v1/positions` REST endpoint in `src/mms4tk/api.py` querying OKX live open positions (`executor.fetch_positions()`). Resolves Hermes hourly digest showing 0 open positions.
- 💬 **Real-time Telegram Webhook Notifications**: Added automatic `HermesWebhookEvent` emission in `src/mms4tk/daemon.py` on order execution (`POSITION_OPENED`), delivering instant Telegram notifications when positions open.

---

## 🐛 [0.16.2] - 2026-08-14

### 🐛 OKX Futures / EEA Contract Lot Precision Fix
- 🐛 **Contract Amount Recalculation**: Fixed `InvalidOrder: okx amount must be greater than minimum amount precision of 1` error in `execute_order` (`okx_executor.py`). When symbols are mapped to OKX Futures/EEA Swap contracts (`ETH/USD:USD-310404`, `SOL/USD:USD-310404`), fractional coin quantities (e.g. `0.0037 ETH`) are now dynamically converted to integer contract quantities (e.g. `1 contract`), enabling order execution.

---

## 🚀 [0.16.1] - 2026-08-14

### 🚀 30m Timeframe Support & Independent Simulation Audit
- 🚀 **30m Timeframe Selector**: Added `30m` option to per-symbol timeframe dropdown in Web Dashboard (`SymbolsTab.jsx`) and backend symbol management.
- 🧪 **Independent Quantitative Backtest Audit**: Created `scratch/independent_strategy_simulator.py` and saved comparative audit report to `docs/mms4_independent_simulation_audit.md` demonstrating `1h` (67.5% Win Rate) and `15m` superiority over 1m/5m/30m.

---

## 🚀 [0.16.0] - 2026-08-14

### 🚀 Mastermind MMS v4 Strategy Timeframe & Signal Overhaul
- 🚀 **Per-Symbol Timeframe Selection**: Added per-symbol `timeframe` configuration (`15m` default, `1h`, `5m`, `1m`) across backend, `symbols.json`, `MMS4Daemon` loop, and React UI (`SymbolsTab.jsx`, `MonitoringTab.jsx`).
- 🎯 **TMA Outer Band Penetration Trigger**: Refined signal evaluation in `src/mms4tk/analytics.py` for `BASE_LONG` and `BASE_SHORT` to trigger reliably upon outer TMA ATR band penetration on swing timeframes.
- 📊 **Audit & Post-Implementation Documentation**: Created `/home/admin_tk/projects/mms4tk/docs/mms4_strategy_audit_report.md` and `/home/admin_tk/projects/mms4tk/docs/mms4_post_implementation_report.md`.

---

## 🌐 [0.15.8] - 2026-08-13

### 🌐 Resilient Container API URL Resolution
- 🌐 **Dynamic Base URL Probing**: Added `get_api_base_url()` to `scripts/hermes/mms4tk_multi_symbol_digest.py` probing `http://antigravity_core:8000`, `http://172.17.0.1:8501`, and `http://localhost:8501`, ensuring the script reaches `mms4tk` REST API reliably whether run inside `hermes-agent` Docker container, on VPS host, or inside core container.

---

## 💬 [0.15.7] - 2026-08-13

### 💬 Hermes Telegram Notification Integration
- 💬 **Hermes Telegram Notify**: Added fallback `hermes notify --channel telegram` dispatch inside `scripts/hermes/mms4tk_multi_symbol_digest.py` via native CLI or `docker exec hermes-agent` to deliver hourly Multi-Symbol Digest reports directly to the user's Telegram chat.

---

## 🔖 [0.15.6] - 2026-08-13

### 🔖 Dynamic Version Resolution & Webhook Fix Verification
- 🔖 **Dynamic Script Versioning**: Updated `scripts/hermes/mms4tk_multi_symbol_digest.py` to dynamically fetch the live application version from `/api/v1/health`, resolving version string mismatches.

---

## 🔔 [0.15.4] - 2026-08-13

### 🔔 Custom Webhook Event Logging REST Endpoint
- 🔔 **POST /api/v1/hermes/webhook Endpoint**: Added endpoint in `src/mms4tk/api.py` allowing custom script events (like hourly Multi-Symbol Digest cards) to post directly to `webhook_events` DB table for instant display on Dashboard Kanban & System Status.

---

## 📋 [0.15.3] - 2026-08-13

### 📋 Direct Webhook Integration for Digest Cards
- 📋 **Direct Webhook Posting**: Updated `scripts/hermes/mms4tk_multi_symbol_digest.py` to post hourly digest cards directly to `POST /api/v1/hermes/webhook`, ensuring digest reports render on Dashboard Kanban and Webhook events even when CLI binary paths differ.

---

## 📈 [0.15.2] - 2026-08-13

### 📜 Hermes Multi-Symbol Digest Script Implementation
- 📜 **Unified Multi-Symbol Digest Script**: Added `scripts/hermes/mms4tk_multi_symbol_digest.py` executable script for Hermes agent cron (`0 * * * *`). Automatically fetches active symbols and open positions, formats structured markdown report, and posts to Hermes Kanban & Telegram.

---

## 📈 [0.15.1] - 2026-08-13

### 📈 Live Open Positions Endpoint & Hermes Positions Digest Integration
- 📈 **GET /api/v1/positions Endpoint**: Added REST endpoint in `src/mms4tk/api.py` to fetch live open positions across all active trading symbols from OKX (side, contracts, entry price, mark price, notional USD, unrealized PnL, leverage, liquidation price).
- 🤖 **Hermes Open Positions OpenAPI Schema**: Added `mms4_get_positions` tool definition to `.agents/swarm/openapi.json`, allowing Hermes digest reports to render live open position tables with uPnL %.

---

## 🤖 [0.15.0] - 2026-08-13

### 🤖 Hermes Swarm Agent Multi-Symbol Integration & OpenAPI Schema
- 🤖 **OpenAPI Schema Integration**: Added `mms4_get_symbols` tool definition to `.agents/swarm/openapi.json` allowing Hermes agent to dynamically discover all configured trading symbols, allocation USD amounts, Base/Add ratios, and live states.
- 📐 **Cron Strategy Architecture**: Formulated Unified Multi-Symbol Digest & Event-Driven Alerting architecture for Hermes agent swarm (`hermes_multi_symbol_plan.md`).

---

## 🌍 [0.14.4] - 2026-08-13

### 🌐 Universal Contract Amount Precision & JSON Import Fix
- 🌍 **Universal Contract Precision**: Expanded contract pattern matching (`:`, `-`, `XPERP`, `SWAP`, `PERP`, `FUTURES`, or any non-spot symbol) in `calculate_order_amount` in `src/mms4tk/okx_executor.py` so integer contract amounts are guaranteed across ALL present and future pairs (`BTC`, `SOL`, `ICP`, `ETH`, `AVAX`, etc.).
- 📦 **API JSON Import Fix**: Added `import json` at top of `src/mms4tk/api.py` to fix `Failed to save symbols.json: name 'json' is not defined` when adding dynamic symbols.

---

## 🩹 [0.14.3] - 2026-08-13

### 🔢 OKX Contract Order Amount Integer Precision Enforcement
- 🔢 **Contract Integer Amount Precision**: Updated `calculate_order_amount` in `src/mms4tk/okx_executor.py` to enforce integer contract amounts (`contracts = math.ceil(raw_contracts)` and `max(contracts, 1)`) for OKX contract/futures markets. Fixes `InvalidOrder: okx amount must be greater than minimum amount precision of 1` on pairs like `SOL/USD:USD-310404`.

---

## 🩹 [0.14.2] - 2026-08-13

### 🐛 Import Resolution Fix for API Version Resolver
- ⚡ **Fixed Import & NameError**: Added `get_app_version()` resolver in `src/mms4tk/api.py` to safely resolve `APP_VERSION` from `VERSION` file or `mms4tk` package with fallback, eliminating `NameError: name '__version__' is not defined`.
- 🩺 **Health Endpoint Synchronized**: Updated `/api/v1/health` to return dynamic `APP_VERSION` string.

---

## 🎯 [0.14.1] - 2026-08-13

### 🎯 Core FastAPI & Docker Volume Version Synchronization Fix
- ⚡ **FastAPI Version Synchronization**: Fixed `app = FastAPI(..., version=__version__)` constructor in `src/mms4tk/api.py` and unit test assertions, replacing the old `0.1.0` string.
- 🐳 **Docker Volume Alignment**: Mounted `./VERSION:/app/VERSION:ro` into the `antigravity_core` service in `docker-compose.yml` to guarantee identical container versioning.

---

## 💵 [0.14.0] - 2026-08-13

### 💵 Per-Symbol Trade Allocation (USD) & Base/Add Ratio Customization
- 💵 **Individual Pair Allocation Controls**: Added per-symbol `allocation_usd` (default $10.00 USD) and `base_ratio` / `add_ratio` (default 70% / 30%) settings to `SymbolsTab.jsx` and backend store (`symbols.json`).
- ⚡ **Dynamic API Health Version Fix**: Fixed `/api/v1/health` endpoint in `src/mms4tk/api.py` to return the real `__version__` string (e.g. `0.14.0`), removing hardcoded `"0.1.0"`.
- 📊 **Monitoring Grid Allocation Display**: Updated `MonitoringTab.jsx` to render individual USD allocation amounts and Base/Add percentage ratios for each active symbol card.

---

## 🔄 [0.13.2] - 2026-08-13

### 🐛 Dynamic Header Version & Active-Only Symbol Filtering in Monitoring Grid
- ⚡ **Dynamic Live Version Fetching**: Updated `App.jsx` and `Header.jsx` to fetch live system version from `/api/v1/health` (rendering dynamic `v0.13.2`), eliminating static fallback `0.13.0`.
- ⏸️ **Active-Only Symbol Grid Filtering**: Updated `MonitoringTab.jsx` to filter `data.symbols` for `is_active !== false`. Paused symbols in the `Symbols` tab are now correctly hidden from the active trading grid on the `Monitoring` tab.

---

## 🟢 [0.13.1] - 2026-08-13

### 🟢 Simplified Ticker Verification via Live OKX Price Confirmation
- 🟢 **Live OKX Price Confirmation**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to confirm pairs directly via live OKX `fetch_ticker` price fetching. If OKX returns a valid live price, the pair is confirmed and marked **🟢 AUTOTRADE_READY** with its live price display, reflecting unlocked user API permissions.

---

## 🔑 [0.13.0] - 2026-08-12

### 🔑 OKX API Key Trade Fee Permission Probe (`GET /api/v5/account/trade-fee`)
- 🔑 **Authenticated Trade Fee Probe**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to query `privateGetAccountTradeFee` directly for the user's specific API Key. This asks OKX's private authenticated API for exact trading permissions and fee schedules per pair, correctly setting Red LED badges 🔴 for unpermitted pairs.

---

## 🎯 [0.12.2] - 2026-08-12

### 🎯 Account-Specific Permitted Instrument Whitelist Check (`GET /api/v5/account/instruments`)
- 🎯 **Exact User Account Whitelist Verification**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to query `privateGetAccountInstruments` for the exact list of trading instruments enabled on the User's OKX API Key/subaccount. Pairs not in the user's permitted instrument list (e.g. `AVAX/USDC`) are marked `autotrade_permitted: False` with Red LED badge 🔴.

---

## 🛠️ [0.12.1] - 2026-08-12

### 🛠️ Multi-Mode (`cross`, `cash`, `isolated`) OKX Account Permission Verification
- 🛠️ **Spot/Swap Account Probing Fix**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to probe `privateGetAccountMaxSize` across `cash` (Spot), `cross` (Cross Margin/Swap), and `isolated` (Isolated Margin) modes. This fixes false negative errors for Spot pairs like `ETH/USDC`, `BTC/USDC`, `SOL/USDC` while correctly restricting unauthorized pairs (`AVAX/USDC`).

---

## 🤖 [0.12.0] - 2026-08-12

### 🤖 Autotrading Account Permission Verification & Badge Differentiation
- 🤖 **Private Autotrading Permission Check**: Differentiated public ticker availability from private autotrading execution permissions on the User's OKX API Key (`privateGetAccountLeverageInfo`).
- 🔴 **Red LED for Blocked Autotrading**: `SymbolsTab.jsx` explicitly displays **`🔴 HANDEL AUTOMATYCZNY: BLOKOWANY`** when a pair (e.g. `AVAX/USDC`) lacks autotrading execution permissions on the user's OKX API Key, while showing **`🟢 HANDEL AUTOMATYCZNY: GOTOWY`** for permitted pairs (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`).

---

## 💚 [0.11.1] - 2026-08-12

### 💚 Multi-Candidate OKX Ticker Verification Restoration
- 💚 **Resilient Ticker Candidate Probing**: Restored multi-candidate ticker verification (`BTC/USDC`, `BTC-USDC`, `BTC/USDC:USDC`, `BTC-USDC-SWAP`) across Spot and Perpetual Swap OKX markets, restoring Green LED status for standard pairs (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`).

---

## 🔐 [0.11.0] - 2026-08-12

### 🔐 User OKX API Key Account Permission Verification
- 🔑 **Account API Key Permission Check**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to run `privateGetAccountMaxSize` using the User's authenticated OKX credentials. If a pair is available globally on OKX but not permitted on the user's API Key / Account, it returns `UNSUPPORTED_ON_USER_API_KEY`.
- 🔴 **Red LED Account Badge**: `SymbolsTab.jsx` renders **Red LED Dot** + **`Status API OKX: BRAK UPRAWNIEŃ W KLUCZU API`** for pairs restricted by user account limits.

---

## 🎯 [0.10.1] - 2026-08-12

### 🎯 Strict OKX Live REST API Verification Fix
- 🔴 **Eliminated Mock Fallback in Verification**: Fixed `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` so that verification performs strict live OKX API `fetch_ticker` tests without falling back to mock candles, ensuring unsupported pairs (e.g. `AVAX/USDC`) correctly render the Red LED status light.

---

## 🔴🟢 [0.10.0] - 2026-08-12

### 🔴🟢 OKX API LED Status Light & Live Pair Verification
- 🟢 **Pulsing Green / Red LED Status Lights**: Added live API LED indicators (Pulsing Green for verified OKX pairs with live price, Red for invalid/unsupported tickers) to every trading pair card in `SymbolsTab.jsx`.
- 🔍 **One-Time OKX REST API Verification**: Integrated live OKX ticker verification endpoint (`/api/v1/symbols/verify`) and modal `Sprawdź OKX` button to test and confirm pair availability prior to activation.

---

## ⏸️ [0.9.4] - 2026-08-12

### ⏸️ Paused Symbol UI Persistence & OKX Instrument Mapping Clarification
- ⏸️ **UI Paused Symbol State**: Refactored symbol store in `src/mms4tk/api.py` so deactivated pairs remain visible in `SymbolsTab.jsx` with the **`[WSTRZYMANA]`** status badge instead of disappearing from the manager view.
- 🔗 **Automatic OKX Ticker Resolution**: OKX Executor automatically formats standard trading pairs (`ETH/USDC` -> `ETH-USDC` / `ETH-USDC-SWAP`) without manual ID lookup.

---

## 📈 [0.9.3] - 2026-08-12

### 📈 Dynamic Symbol Titles & Labels in Analytics Price Chart
- 📈 **Dynamic Chart Titles**: Updated `AnalyticsTab.jsx` price chart title and tooltips to adapt dynamically to the currently selected symbol (e.g. `Ścieżka Ceny ETH/USDC & Pasma TMA`, `Cena ETH`), removing all hardcoded BTC text.

---

## 🔐 [0.9.2] - 2026-08-12

### 🔐 Auth Header Fix for UI Symbol Operations & Icon Cleanup
- 🔐 **Web Dashboard Auth Bypass Fix**: Updated `verify_api_key` in `src/mms4tk/api.py` to allow requests originating from the Basic-Auth protected UI dashboard without triggering unwanted browser-native login popups when adding/toggling trading pairs.
- 🎨 **Symbol Form Auto-Formatting & Icon Cleanup**: Removed duplicate `+` icon from "Dodaj Nową Parę" button and auto-appends `/USDC` suffix (e.g. `SOL` -> `SOL/USDC`).

---

## 📊 [0.9.1] - 2026-08-12

### 📊 Monitoring Tab Multi-Symbol Redesign & Zero-Trade KPI Fix
- 📊 **Multi-Symbol Active Grid**: Redesigned `MonitoringTab.jsx` layout to render a live grid of all active trading pairs (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`), displaying individual operating mode (`BASE_MODE`/`SCOUT_MODE`), leverage, and capital allocation.
- 🎯 **Zero-Trade KPI Guard**: Fixed KPI calculations for Win Rate %, Profit Factor, Max Drawdown, and R:R Ratio when 0 trades exist in SQLite `logs.db`, cleanly displaying `0.0%` / `0.00` and `"Oczekiwanie na pierwsze transakcje z OKX"`.

---

## 🎛️ [0.9.0] - 2026-08-12

### 🎛️ User-Controlled Multi-Symbol UI Manager & Hermes Multi-Pair Tools
- 🎛️ **User Active Pair Toggle Switch**: Integrated interactive per-symbol Active/Paused toggles and `➕ Dodaj Nową Parę` modal in `SymbolsTab.jsx`, allowing users to activate or pause trading pairs dynamically.
- 📊 **Per-Symbol Chart Switcher**: Added symbol selection dropdown (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`) in `AnalyticsTab.jsx` to view technical charts for any active pair.
- 🤖 **Hermes Agent Multi-Pair Tools**: Updated `src/mms4tk/hermes_tools.py` with multi-symbol status inspection and per-pair emergency actions.

---

## ⚡ [0.8.1] - 2026-08-12

### ⚡ Multi-Symbol Async Execution Loop (Phase 2)
- ⚡ **Parallel Multi-Symbol Loop**: Upgraded `MMS4Daemon` in `src/mms4tk/daemon.py` with `execute_multi_symbol_cycle()` running parallel candle fetching (`asyncio.gather`) and signal evaluation across all active trading symbols (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`).
- 📊 **Per-Symbol Audit Logging**: Extended `LoggerDB` cycle logging to record individual cycle results per active trading symbol.

---

## 🔀 [0.8.0] - 2026-08-12

### 🚀 Multi-Symbol Trading Backend & State Isolation
- 🔀 **Per-Symbol State Isolation**: Extended `MMS4StateManager` to track operating states (`BASE_MODE` / `SCOUT_MODE`), leverage, and Stop-Loss triggers per active trading symbol independently.
- ⚡ **Multi-Symbol Asynchronous Execution**: Upgraded `MMS4Daemon` to execute parallel candle fetching, indicator calculation, and order evaluation across multiple configured active symbols (e.g. `BTC/USDC`, `ETH/USDC`, `SOL/USDC`).
- 🌐 **Multi-Symbol REST API Endpoints**: Added `GET /api/v1/symbols`, `POST /api/v1/symbols/add`, `POST /api/v1/symbols/pause`, and `DELETE /api/v1/symbols/{symbol}` endpoints.
- 🛡️ **Global Portfolio Risk Governor**: Implemented multi-symbol capital allocation split and circuit breaker.

---

## 🐛 [0.7.1] - 2026-08-12

### 🛠️ React UI Chart Scaling, Risk Profile Brackets & Password Management
- 📈 **Synchronized Timeframe Scaling**: Added 4H, 1D, 1W, 1M, 3M, 6M, 1Y, 5Y timeframes with synchronized date X-axis scaling across Price & Equity charts.
- 📜 **Closed Trades History Table**: Restored real SQLite `logs.db` trade history audit table below analytics charts.
- ⚡ **Risk Profile Brackets**: Strictly coupled leverage brackets (CONSERVATIVE 1x-2x, MODERATE 2x-5x, AGGRESSIVE 5x-10x) to active strategy risk profile.
- 🔑 **In-App Password Manager**: Added HTTP BasicAuth password update feature in Settings card updating `.env` configuration.

---

## ⚛️ [0.7.0] - 2026-08-12

### 🚀 React 18 + Vite + TailwindCSS UI Migration
- ⚛️ **React Single-Page Application**: Replaced Streamlit UI with high-performance React 18, Vite, and TailwindCSS dashboard.
- 📲 **Native PWA Integration**: Integrated `vite-plugin-pwa` with native `beforeinstallprompt` handling, Service Worker caching, and custom app icons.
- 📊 **Recharts & Lucide Icons**: Added smooth interactive chart components for TMA indicators, balance progression, and trading signals.
- 🐳 **Nginx Production Container**: Optimized multi-stage Docker build for ultra-fast load times and static caching.

---

## 📝 [0.6.7] - 2026-08-12

### 📝 Comprehensive System & Technical Documentation Refresh
- 📝 **Updated System Architecture Diagrams**: Refreshed `README.md` to reflect Traefik BasicAuth HTTPS reverse proxy, Streamlit Web Dashboard v0.6.7, In-App Password Manager, Mobile PWA, and Scalable Plotly Analytics.
- 📡 **REST API Reference Refresh**: Synchronized `docs/API.md` and `docs/DEPLOYMENT_GUIDE.md` with active `0.6.7` deployment parameters and `{SHA}` htpasswd basicauth specs.

---

## 📋 [0.6.6] - 2026-08-12

### 🐛 Dashboard Kanban Cards & Completed Status Filter
- 📋 **Live Kanban Status Filtering**: Updated `fetch_kanban_cards()` to query active Hermes CLI card statuses (filtering out `done`, `closed`, and `archived` cards).
- 🧹 **Undispatched Webhook Events Filter**: Filtered `webhook_events` to only display pending undispatched events (`dispatched = 0`), preventing historic completed alerts from polluting Tab 1 Monitoring.

---

## ⏱️ [0.6.5] - 2026-08-12

### 📈 Dynamic Plotly X-Axis Range Scaling Across Timeframes
- ⏱️ **Explicit Axis Range Scaling (`range=[cutoff_dt, now]`)**: Dynamically pass `xaxis_range` to Plotly for every timeframe option (`4h`, `1d`, `7d`, `30d`, `3m`, `6m`, `1y`, `5y`).
- 🎯 **Visual Context Expansion**: Ensures the X-axis visibly expands and scales to match the selected timeframe even when data is sparse or young (e.g. 14 hours of history).

---

## ⏱️ [0.6.4] - 2026-08-12

### 🐛 Timeframe Filter Datetime & Fallback Fixes
- ⏱️ **UTC Datetime Matching**: Enforced `pd.to_datetime(df['timestamp'], utc=True)` and `pd.Timestamp.now(tz='UTC')` matching to eliminate timezone-aware vs naive comparison exceptions in Streamlit.
- ℹ️ **Empty Timeframe Fallback Informative Notice**: Added explicit user notification when zero trades fall within narrow timeframe windows (e.g. `4h`), preventing silent chart resets.

---

## 📱 [0.6.3] - 2026-08-12

### 📱 Touch Safety & Plotly ModeBar Re-activation
- 🎛️ **Re-enabled Plotly ModeBar Toolbar**: Restored modebar controls (`displayModeBar: true`) so tools like zoom in/out, pan, autoscale, and download plot are available on demand.
- 📱 **Mobile Touch Drag Safety (`dragmode=False`)**: Disabled default touch dragging/zooming when swiping across chart areas on smartphones, allowing smooth page scrolling without accidental chart parameter mutations.

---

## 🎨 [0.6.2] - 2026-08-12

### 🐛 Chart Overlap Fixes & Benchmark Y-Axis Normalization
- 🎨 **Chart Title & Legend Collision Fix**: Extracted chart headers to Markdown headings (`st.markdown("#### ...")`) and positioned legend neatly at top-left (`y=1.02`, `x=0`), removing native Plotly rangeselector/modebar overlaps.
- 📈 **Benchmark Y-Axis Normalization**: Filtered out invalid 0.0 entry prices to prevent 3,000,000% Y-axis scaling spikes on benchmark series.

## 🔀 [0.8.0] - 2026-08-12

### 🚀 Multi-Symbol Trading Backend & State Isolation
- 🔀 **Per-Symbol State Isolation**: Extended `MMS4StateManager` to track operating states (`BASE_MODE` / `SCOUT_MODE`), leverage, and Stop-Loss triggers per active trading symbol independently.
- ⚡ **Multi-Symbol Asynchronous Execution**: Upgraded `MMS4Daemon` to execute parallel candle fetching, indicator calculation, and order evaluation across multiple configured active symbols (e.g. `BTC/USDC`, `ETH/USDC`, `SOL/USDC`).
- 🌐 **Multi-Symbol REST API Endpoints**: Added `GET /api/v1/symbols`, `POST /api/v1/symbols/add`, `POST /api/v1/symbols/pause`, and `DELETE /api/v1/symbols/{symbol}` endpoints.
- 🛡️ **Global Portfolio Risk Governor**: Implemented multi-symbol capital allocation split and circuit breaker.

---

## 📌 [0.6.1] - 2026-08-12

### 🐛 Fixes & Dynamic Version Resolution
- 📌 **Docker Volume Version Mounting**: Mounted `./VERSION:/app/VERSION:ro` in `docker-compose.yml` so `ui_dashboard` container accesses the authoritative root `VERSION` file.
- 🔄 **Dynamic Package Version Import**: Added fallback import of `mms4tk.__version__` in `app.py` to prevent stale version string fallbacks.

---

## 📊 [0.6.0] - 2026-08-12

### ✨ Scalable Analytics & Interactive Charts Redesign
- ⏱️ **Timeframe Range Selector**: Added interactive time range filter pills bar (`4h`, `1d`, `7d`, `30d`, `3m`, `6m`, `1y`, `5y`, `ALL`) dynamically filtering the trade history DataFrame.
- 📌 **Compact Legend Layout Fix**: Moved Plotly legend to a single horizontal line above chart (`orientation="h"`, `yanchor="bottom"`, `y=1.02`), restoring 100% of vertical chart height on mobile devices.
- 🎛️ **Plotly Interactive Range Buttons**: Embedded native Plotly range buttons inside chart headers.
- 🎨 **Visual Aesthetics Upgrade**: Applied dark theme (`#0f172a`), smooth gradient area fills (`fill='tozeroy'`), and unified hover tooltips (`hovermode='x unified'`).

---

## 🔔 [0.5.2] - 2026-08-12

### ✨ PWA Installation Prompt Popup
- Added automatic mobile PWA "Install App" bottom popup modal (`#pwa-install-banner`) triggering `beforeinstallprompt` on Android Chrome/Edge devices.
- Added native iOS Safari instructions popup ("Touch Share 📤 -> Add to Home Screen").
- Stored dismissal state in `localStorage.setItem('pwa_prompt_dismissed', 'true')` to avoid bothering returning users.

---

## 📱 [0.5.1] - 2026-08-12

### 📱 PWA & Mobile Enhancements
- Enforced mobile PWA `manifest.json` static serving (`--server.enableStaticServing=true`).
- Added iOS (`apple-mobile-web-app-capable`) & Android standalone viewport meta headers for instant "Add to Home Screen" installation on mobile devices.

---

## 🎛️ [0.5.0] - 2026-08-12

### ✨ Added
- 📱 **Mastermind Control Center Web UI (`ui_dashboard`)**:
  - Built dedicated Streamlit microservice (Python 3.11) with Mobile-First PWA support (`manifest.json` + iOS/Android viewport headers).
  - 4 Interactive Tabs: **Monitoring** (live OKX positions & Hermes HITL), **Analytics** (KPIs & 3 Plotly charts), **Settings** (.env editor & UI password change), **Symbols** (X-Perps manager).
- 🔄 **15-Min OKX Trade Sync Worker (`trade_sync.py`)**:
  - Asynchronous background worker syncing closed trades from OKX API into SQLite `closed_trades` table in `logs.db`.
- 📊 **KPI Analytics Engine (`logger_db.py`)**:
  - Instant calculations for Win Rate (Total/Long/Short), Profit Factor, Max Drawdown (MDD %), and Average R:R.
- 🛡️ **Traefik HTTPS & HTTP BasicAuth Protection**:
  - Traefik reverse proxy integration with Let's Encrypt TLS and HTTP BasicAuth middleware on `mastermind.srv1490214.hstgr.cloud`.
  - Added in-app password manager UI in Tab 3 Settings.
- 📌 **Application Versioning Protocol**:
  - Root `VERSION` file, `src/mms4tk/__init__.py`, `ui_dashboard/app.py` badge, and strict agent governance rules requiring version increment on every commit.

---

## 🇪🇺 [0.4.1] - 2026-08-12

### ✨ Added & Improved
- 🇪🇺 **OKX Europe (EEA) X-Perp Derivatives Support**:
  - Full support for OKX Europe regional REST domain `https://eea.okx.com` under MiCA/MiFID compliance.
  - **Dynamic Instrument Auto-Discovery (`discover_eea_xperp_symbol`)**: Automatically queries `GET /api/v5/account/instruments?instType=FUTURES` and resolves active live X-Perp contracts (`ruleType=xperp`, `settleCcy=USDC`) such as `BTC-USD_UM_XPERP-310404`.
  - **CCXT Unified Market Mapping**: Resolved internal CCXT market table mapping from OKX native `instId` to CCXT unified symbols (e.g. `BTC/USD:USD-310404`), eliminating `BadSymbol` exceptions.
  - **OKX Hedge Mode (`posMode: long_short_mode`)**: Enforced `posSide` (`"short"` for SELL, `"long"` for BUY) and `tdMode: "cross"` parameters across all derivative orders.
- 📚 **RAG Knowledge Base & Documentation**:
  - Created `.agents/specs/knowledge/okx_europe_eea_x_perps.md` expert integration guide synchronized from NotebookLM.
  - Registered node and edge links in `.agents/specs/graph.json`.
  - Updated `README.md` and `docs/API.md` with OKX Europe EEA derivative details.

---

## 🚀 [0.4.0] - 2026-08-11

### ✨ Added
- 🤖 **Native Hermes Architecture Integration**: Fully event-driven webhook subsystem (`hermes_webhook.py`) and SQLite event log (`logs.db`).
- ⏱️ **Scheduled Hermes Cron Workflows**:
  - `mms4tk_event_dispatcher.py` (`*/1 * * * *`): Automatic dispatch of pending `SCOUT_MODE` webhook events to Hermes Kanban.
  - `mms4tk_h4_analysis.py` (`0 */4 * * *`): Higher timeframe H4 TMA band & candle formation evaluation (SWEEP_WICK vs MARUBOZU_BREAKOUT) for dynamic `directional_bias` modulation.
  - `mms4tk_d1_atr_check.py` (`5 0 * * *`): Daily D1 ATR expansion monitor to throttle `risk_profile` on extreme volatility ($>2.5\times\text{ATR\_D1}$).
- 🙋‍♂️ **Human-in-the-Loop (HITL) Endpoint**: `POST /api/v1/hermes/hitl_resolve` enabling Web UI approval of ambiguous H4 candle sweeps to relax `SCOUT_MODE` back to `BASE_MODE` (`scout_override=True`).
- 💵 **Dynamic USD Position Sizing**: `MMS4_TRADE_AMOUNT_USD` environment variable enabling exact USD margin allocation per order (e.g. $10.0 USD), calculated dynamically against live coin prices.

### 🛡️ Security & Reliability
- 🔑 **API Key Sanitization**: Removed all static secret fallbacks from codebase and documentation. Enforced environment-driven strict authentication (`MMS4_API_KEY`).
- 🌐 **CCXT Symbol & Timeframe Normalization**: Fixed market symbol resolution (`BTC/USDC:USDC` Swap vs Spot) and normalized timeframe strings (`1d`, `4h`, `15m`) for OKX execution.

---

## 🔒 [0.3.0] - 2026-08-10

### ✨ Added
- 🎯 **Two-Tier Adaptive Autotrading System**:
  - Tactical Layer (`mms4tk`): M15 execution loop with TMA/ATR indicators and leverage modulation (`BASE_MODE` x1.0 vs `SCOUT_MODE` x0.1).
  - Strategic Layer (`Hermes Agent`): H4/D1 higher timeframe analytics via `HigherTimeframeAnalytics` and dynamic `StrategyProfile` updates.
- 🛠️ **OpenAPI Tool Generator**: `get_hermes_tools()` generating GFM function schemas for LLM tool-calling in Hermes Agent.
- 📡 **REST API Expansion**: `/api/v1/strategy/profile`, `/api/v1/analytics/higher_tf`, `/api/v1/balance`, and `/api/v1/stats/pnl`.

### 🧪 Testing
- 📊 Expanded test suite to 52 unit and integration tests (`52/52 PASSED`).

---

## 🛡️ [0.2.0] - 2026-08-09

### ✨ Added
- 💾 **SQLite State Persistence**: Added `LoggerDB` state saving and restoration upon daemon restart (`FIX-1`).
- 🕯️ **Closed Candle Signal Evaluation**: Modified `MMS4SignalEngine` to evaluate TMA/ATR strictly on closed candles (`candles[:-1]`) eliminating look-ahead bias (`FIX-2`).
- 🔐 **REST API Authentication**: Implemented `X-MMS4-API-KEY` header validation for all mutating endpoints (`POST /api/v1/emergency/*`, `/api/v1/trade/*`) (`FIX-3`).

---

## 📦 [0.1.0] - 2026-08-09

### ✨ Added
- 🏗️ **Core Foundation**:
  - `MMS4StateManager`: State machine tracking `BASE_MODE` (1.0x) and `SCOUT_MODE` (0.1x).
  - `OKXExecutor`: Async exchange executor using `ccxt.async_support.okx` with strict environment secret hygiene (R-SEC-01).
  - `MMS4SignalEngine`: Triangular Moving Average (TMA) and Average True Range (ATR) calculation engine.
  - `MMS4Daemon`: Async market evaluation loop running at 15-second intervals.
- 🐳 **Docker & Traefik Infrastructure**: Dockerfile, `docker-compose.yml`, and Traefik reverse proxy integration on Hostinger VPS.
