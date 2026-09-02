# 🔁 PLAN-010 (v2.0): Trend Filter Recalibration Mechanism (Backend + API + UI)

**Status:** Plan Approved / Ready for Builder  
**Standard:** AGENTS-OS v6.5 Swarm / MMS4 Strict Compliance  
**Target Version:** v0.43.0  
**Worktree Target:** `tmp/worktrees/feat-plan-010-trend-filter-recalibration`  

---

## 1. Problem Statement & Background

W ramach PLAN-008 Phase 3 wdrożono filtr nachylenia TMA (TMA Slope Trend Filter), który został skalibrowany jednorazowo na pełnym oknie 90 dni. Późniejszy obiektywny audyt (2026-08-29) wykazał, że parametry te **nie przeszły rygorystycznego testu dwukierunkowego (2-way train/test split)**: parametry wytrenowane na pierwszej połowie (H1) zawodziły na drugiej (H2) i odwrotnie. W rezultacie filtr został wyłączony w `system_settings` dla wszystkich par jako natychmiastowe zabezpieczenie.

Główne luki systemowe:
1. **Brak powtarzalnego, bezpiecznego procesu rekalibracji:** parametry były dobierane ręcznie w jednorazowych skryptach.
2. **Brak widoczności w UI:** `SymbolsTab.jsx` nie prezentował stanu filtra ani nie pozwalał operatorowi na bezpieczną ocenę i zatwierdzenie nowych parametrów.
3. **Ryzyko Overfittingu:** brak automatycznej bramki odrzucającej konfiguracje niespełniające warunku zyskowności w obu oknach OOS (`OVERFIT_REJECTED`).

---

## 2. Architektura & Zasady Żelazne (MMS4 Strict Compliance)

### 2.1. Ekstrakcja i Cache Danych (OHLCV Cache)
- **Tabela SQLite `ohlcv_cache`** w `logs.db`: `PRIMARY KEY (symbol, timeframe, timestamp)`.
- Metoda `fetch_candles_paginated(symbol, timeframe, lookback_days=90)`:
  - Odczytuje dane z lokalnej bazy SQLite.
  - Z giełdy OKX dociąga wyłącznie brakujące, najnowsze świece, omijając Rate Limity giełdy i przyspieszając obliczenia.

### 2.2. Wierny Silnik Symulacyjny (`backtest_engine.py`)
- Silnik korzysta z **produkcyjnych klas**: `TMAIndicator`, `MMS4SignalEngine`, `MMS4InactivityManager`.
- **Ścisłe zarządzanie kapitałem:**
  - Podział alokacji 70% Base / 30% Add-on.
  - Twardy Stop-Loss **2.00% od ceny wejścia**.
  - **Sekwencyjna Maszyna Stanów:**
    - Uderzenie w 2% SL natychmiast zrzuca lewar do `SCOUT_MODE (x0.1)`.
    - W `SCOUT_MODE` dokładki (`add`) są **bezwzględnie zablokowane**.
    - Powrót do `BASE_MODE (x1.0)` następuje **wyłącznie po 100% zamknięciu pozycji z zyskiem netto**. Cząstkowe TP nie resetuje stanu.
  - **Dual-Stage TP (PLAN-009 v2.0):**
    - Cząstkowe TP 50% na Midline TMA z zachowaniem twardego 2.0% SL dla pozostałych kontraktów.
    - Profit Stall Gate (zamknięcie 50% po 4 świecach wygasłego impetu przy uPnL $\ge 0.6 \times ATR$).
    - Negacja impulsu 38.2% Fibonacciego i bufor prowizji 0.12% w `BE_ESCAPE`.

### 2.3. Metodologia 2-Way OOS (Out-of-Sample Validation)
- Okno historyczne: **90 dni (~2160 świec H1)** podzielone na:
  - Połowa 1 (H1: pierwsze 45 dni)
  - Połowa 2 (H2: drugie 45 dni)
- Przestrzeń poszukiwań (Grid Search Space):
  - `slope_lookback` $\in [3, 5, 8, 13]$
  - `slope_threshold` $\in [0.0010, 0.0015, 0.0020, 0.0030]$ (16 kombinacji)
- **Kryterium `VALIDATED`:**
  Kandydat musi pobić baseline (filtr wyłączony) w **OBU kierunkach niezależnie**:
  1. Wytrenowany na H1 $\rightarrow$ bije baseline na H2 (pod kątem Net PnL oraz R:R).
  2. Wytrenowany na H2 $\rightarrow$ bije baseline na H1 (pod kątem Net PnL oraz R:R).
- Jeśli żaden kandydat nie spełni warunku $\rightarrow$ werdykt `OVERFIT_REJECTED`.

### 2.4. Asynchroniczność & Bezpieczeństwo API (ProcessPoolExecutor)
- Żadne ciężkie obliczenia nie mają prawa blokować pętli `asyncio` daemona.
- Wywołanie `POST /api/v1/symbols/{symbol}/recalibrate`:
  - Tworzy wpis w tabeli `recalibration_jobs` (`status: PENDING`).
  - Zwraca natychmiast `202 Accepted` z `job_id`.
  - Obliczenia wykonywane są w tle w osobnym procesie (`ProcessPoolExecutor`).
- Endpointy:
  - `GET /api/v1/symbols/{symbol}/recalibration-status` — status ostatniego zadania i dane historyczne.
  - `GET /api/v1/recalibration/jobs/{job_id}` — polling postępu.
  - `POST /api/v1/symbols/{symbol}/recalibration/apply` — jawna akcja operatora zatwierdzająca nowe parametry w `system_settings`.

### 2.5. UI Human-in-the-Loop (`SymbolsTab.jsx`)
- Panel "Filtr Trendu (TMA Slope)" per para handlowa.
- Przycisk "Rekalibruj (90D OOS)" uruchamia polling (Pending $\rightarrow$ Running $\rightarrow$ Done).
- **Zabezpieczenie:** Przycisk "Zastosuj nowe parametry" renderuje się **wyłącznie**, gdy `verdict === "VALIDATED"`. W przypadku `OVERFIT_REJECTED` UI wyświetla ostrzeżenie o braku stabilności i uniemożliwia włączenie filtra.

---

## 3. Hermetyczny Podział na Fazy i Zadania (Tickets)

### 🔹 FAZA 1: Cache Danych i Spójność API (Tickets 1–2)
- **TICKET-1 (`logger_db.py`, `okx_executor.py`):** Tabela `ohlcv_cache` w SQLite oraz `fetch_candles_paginated()` z buforowaniem i pobieraniem delty z OKX.
- **TICKET-2 (`api.py`, `daemon.py`):** Likwidacja rozjazdu `GET /api/v1/symbols` z `system_settings` dla parametrów filtra trendu (`tma_trend_filter_enabled`, `tma_slope_lookback`, `tma_slope_threshold`).

### 🔹 FAZA 2: Silnik OOS i Asynchroniczne API (Tickets 3–4)
- **TICKET-3 (`backtest_engine.py`):** Wdrożenie dwukierunkowej walidacji 2-way OOS (H1 $\leftrightarrow$ H2), wiernej Maszyny Stanów (Base x1.0 / Scout x0.1), blokady dokładek i wyliczania metryk.
- **TICKET-4 (`api.py`, `logger_db.py`):** Tabela `recalibration_jobs`, integracja `ProcessPoolExecutor`, endpointy `/recalibrate`, `/recalibration-status`, `/recalibration/apply`.

### 🔹 FAZA 3: Interfejs React & Human-in-the-Loop (Ticket 5)
- **TICKET-5 (`SymbolsTab.jsx`):** Panel "Filtr Trendu (TMA Slope)" z kartami metryk OOS (H1/H2 vs Baseline), pollingiem zadań oraz bezpiecznym przyciskiem "Zastosuj" dla statusu `VALIDATED`.

### 🔹 FAZA 4: Testy & Weryfikacja SemVer (Ticket 6)
- **TICKET-6 (`tests/test_trend_filter_recalibration.py`):** Pełny zestaw testów jednostkowych i integracyjnych, podbicie wersji do **`v0.43.0`** (MINOR).
