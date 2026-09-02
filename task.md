# 📋 TASK BACKLOG: PLAN-010 (v2.0) — Mechanizm Rekalibracji Filtra Trendu (OOS & Cache)

**Status:** Plan Approved / Ready for Builder  
**Standard:** AGENTS-OS v6.5 Swarm / MMS4 Strict Compliance  
**Target Version:** v0.43.0  
**Git Worktree Target:** `tmp/worktrees/feat-plan-010-trend-filter-recalibration`  

---

## 🎯 Cel
Zaprojektowanie i wdrożenie produkcyjnego mechanizmu rekalibracji filtra trendu (TMA Slope), eliminującego ryzyko overfittingu poprzez dwukierunkowy podział danych 2-way OOS (H1 $\leftrightarrow$ H2), lokalny cache świec w SQLite (`ohlcv_cache`), asynchroniczne kolejkowanie zadań (`ProcessPoolExecutor`) oraz interfejs React z procedurą Human-in-the-Loop (blokada automatycznego włączania).

---

## 🏗️ Jednostki Operacyjne Backlogu (Developer Tickets)

### 🔹 FAZA 1: Cache Danych i Spójność API (Tickets 1–2)

#### 🔹 TICKET-1: Tabela `ohlcv_cache` w SQLite & Paginowane Pobieranie Świec
- **Pliki docelowe:** `src/mms4tk/logger_db.py`, `src/mms4tk/okx_executor.py`
- **Wymagania:**
  1. Inicjalizacja tabeli `ohlcv_cache` w SQLite (`PRIMARY KEY (symbol, timeframe, timestamp)`).
  2. Implementacja metod `LoggerDB.save_candles()`, `LoggerDB.get_cached_candles()`, `LoggerDB.get_latest_cached_timestamp()`.
  3. W `OKXExecutor`: implementacja `fetch_candles_paginated(symbol, timeframe, lookback_days=90)` — odczytuje cache i dociąga z OKX jedynie brakujące najnowsze świece, omijając limity zapytań (Rate Limits).

#### 🔹 TICKET-2: Likwidacja Rozjazdu Parametrów Filtra w API
- **Pliki docelowe:** `src/mms4tk/api.py`, `src/mms4tk/daemon.py`
- **Wymagania:**
  1. W `GET /api/v1/symbols`: odczyt `tma_trend_filter_enabled`, `tma_slope_lookback`, `tma_slope_threshold` bezpośrednio z `system_settings` (SSOT), eliminując cichą rozbieżność z konfiguracją daemona.
  2. W `POST /api/v1/symbols/update`: spójna aktualizacja obu warstw.

---

### 🔹 FAZA 2: Silnik OOS i Asynchroniczne API (Tickets 3–4)

#### 🔹 TICKET-3: Produkcyjny Silnik Symulacyjny & 2-Way OOS Validation
- **Plik docelowy:** `src/mms4tk/backtest_engine.py`
- **Wymagania:**
  1. Integracja produkcyjnych klas: `TMAIndicator`, `MMS4SignalEngine`, `MMS4InactivityManager`.
  2. Wierna Maszyna Stanów: zrzut lewaru do `SCOUT_MODE (x0.1)` po uderzeniu w 2% SL, bezwzględna blokada dokładek (`add = False`) w trybie Scout, powrót do `BASE_MODE (x1.0)` wyłącznie po 100% zamknięciu z zyskiem netto.
  3. Obsługa Dual-Stage TP (T1 50% Midline, Profit Stall Gate 4 świece, negacja Fib 38.2%).
  4. Dwukierunkowa walidacja 2-way OOS (okno 90 dni, podział H1/H2):
     - Siatka parametrów: lookback $\in [3, 5, 8, 13]$, threshold $\in [0.0010, 0.0015, 0.0020, 0.0030]$.
     - Werdykt `VALIDATED` tylko jeśli kandydat bije baseline pod kątem Net PnL i R:R w OBU kierunkach niezależnie.
     - W przeciwnym razie: `OVERFIT_REJECTED`.

#### 🔹 TICKET-4: Zarządzanie Zadaniami Asynchronicznymi (`ProcessPoolExecutor`)
- **Pliki docelowe:** `src/mms4tk/api.py`, `src/mms4tk/logger_db.py`
- **Wymagania:**
  1. Utworzenie tabeli `recalibration_jobs` w SQLite (job_id, symbol, status, verdict, candidate_params, h1_metrics, h2_metrics, baseline_metrics, created_at, completed_at, applied_at).
  2. Endpoint `POST /api/v1/symbols/{symbol}/recalibrate` zwracający natychmiast `202 Accepted` z `job_id`, wykonujący obliczenia w `ProcessPoolExecutor`.
  3. Endpoint `GET /api/v1/recalibration/jobs/{job_id}` do pollingu stanu.
  4. Endpoint `GET /api/v1/symbols/{symbol}/recalibration-status` zwracający podsumowanie ostatniego zadania.
  5. Endpoint `POST /api/v1/symbols/{symbol}/recalibration/apply` do jawnego zaaplikowania parametrów przez operatora.

---

### 🔹 FAZA 3: Interfejs React & Human-in-the-Loop (Ticket 5)

#### 🔹 TICKET-5: Panel Filtra Trendu w `SymbolsTab.jsx`
- **Plik docelowy:** `ui_dashboard/src/components/SymbolsTab.jsx`
- **Wymagania:**
  1. Dodanie dedykowanej sekcji "Filtr Trendu (TMA Slope)" per symbol.
  2. Wyświetlanie aktualnego stanu filtra, parametrów i daty ostatniej walidacji.
  3. Przycisk "Rekalibruj (90D OOS)" uruchamiający polling zadania ze wskaźnikiem postępu (*Pending* $\rightarrow$ *Running* $\rightarrow$ *Completed*).
  4. Prezentacja wyników walidacji: karty porównawcze H1 i H2 vs Baseline.
  5. **Bramka Bezpieczeństwa:** Przycisk "Zastosuj nowe parametry" renderuje się **wyłącznie** przy statusie `VALIDATED`. Przy `OVERFIT_REJECTED` wyświetlane jest wyraźne czerwone ostrzeżenie bez możliwości aktywacji.

---

### 🔹 FAZA 4: Test Suite & Weryfikacja SemVer (Ticket 6)

#### 🔹 TICKET-6: Adversarial Test Suite & Version Bump
- **Pliki docelowe:** `tests/test_trend_filter_recalibration.py`, `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `ui_dashboard/package.json`, `CHANGELOG.md`
- **Wymagania:**
  1. Testy jednostkowe dla `ohlcv_cache` (zapis, odczyt, incremental delta fetch).
  2. Testy wierności Maszyny Stanów w `backtest_engine.py` (Scout Mode x0.1 po SL, blokada add-ons).
  3. Testy dwukierunkowej walidacji OOS (`VALIDATED` vs `OVERFIT_REJECTED`).
  4. Testy endpointów asynchronicznych i tabeli `recalibration_jobs`.
  5. 100% zaliczenia pełnej bazy testowej (245+ testów).
  6. Podbicie wersji do **`v0.43.0`** (MINOR) we wszystkich 5 plikach wersji.
