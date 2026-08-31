# 📋 TASK BACKLOG: PLAN-009 (v2.0) — Midline Partial TP & Dynamic Inactivity Engine

**Status:** Plan Approved / Ready for Swarm Worktree  
**Standard:** AGENTS-OS v6.5 Swarm / MMS4 Strict Compliance  
**Target Version:** v0.42.0  
**Git Worktree Target:** `tmp/worktrees/feat-plan-009-midline-tp-inactivity`

---

## 🎯 Cel
Wdrożenie czystego cząstkowego zysku na Linii Środkowej TMA (T1 - 50% pozycji) z bezwzględnym zachowaniem twardego 2.0% SL dla reszty pozycji, dedykowaną tabelą `tp_midline_ledger` (wzorzec fail-closed), priorytetem `MIDLINE_PARTIAL_TP` przed `SPLIT_50` oraz zaktualizowaniem `MMS4InactivityManager` o wykrywanie stagnacji w strefie zysku (*Profit Stall Gate* po 4 świecach), zaostrzenie negacji impulsu do 38.2% Fibonacciego i podniesienie bufora prowizji `BE_ESCAPE` do 0.12%.

---

## 🏗️ Jednostki Operacyjne Backlogu (Developer Tickets)

### 🔹 TICKET-1: Optymalizacje `MMS4InactivityManager`
- **Plik docelowy:** `src/mms4tk/inactivity_manager.py`
- **Wymagania:**
  1. *Profit Stall Gate:* Jeśli uPnL $\ge 0.6 \times \text{ATR}$, brak nowego ATH zysku przez 4 kolejne świece H1 (`profit_stall_candles >= 4`) emituje `StaleAction.SPLIT_50`.
  2. *Zaostrzenie Negacji Impulsu:* Dla impulsów $\ge 0.8 \times \text{ATR}$ obniż próg zniesienia z 50.0% na **38.2%** (Fibonacci).
  3. *Realny Bufor Prowizji:* Podnieś `DEFAULT_ROUND_TRIP_COST_PCT` z 0.0 na `0.0012` (0.12%).

### 🔹 TICKET-2: Tabela `tp_midline_ledger` & Fail-Closed Pattern
- **Plik docelowy:** `src/mms4tk/logger_db.py`
- **Wymagania:**
  1. Utworzenie tabeli `tp_midline_ledger` (`PRIMARY KEY (symbol, side)`).
  2. Implementacja `record_midline_tp()`, `is_midline_tp_active()` (wzorzec fail-closed: błąd odczytu $\rightarrow$ zwraca `True`) oraz `clear_midline_tp()`.

### 🔹 TICKET-3: Pętla Daemona & Netting Execution
- **Pliki docelowe:** `src/mms4tk/daemon.py`, `src/mms4tk/analytics.py`, `src/mms4tk/okx_executor.py`
- **Wymagania:**
  1. Wykrywanie przecięcia linii środkowej TMA na świecach H1.
  2. Sprawdzenie `is_midline_tp_active(symbol, side)`.
  3. Wykonanie czystej redukcji 50% pozycji (`midline_tp_fraction = 0.50`) od uśrednionego rozmiaru pozycji (Netting).
  4. Zmniejszenie wolumenu zlecenia `conditional` SL na OKX do pozostałych 50% z **bezwzględnym zachowaniem ceny aktywacji -2.00%**.
  5. Bezpieczna obsługa mikro-pozycji 1-kontraktowych (`T1_SKIPPED_NOT_DIVISIBLE`).
  6. Priorytet: `MIDLINE_PARTIAL_TP` ma bezwzględne pierwszeństwo przed `SPLIT_50`.

### 🔹 TICKET-4: Ochrona Maszyny Stanów (`MMS4StateManager`)
- **Plik docelowy:** `src/mms4tk/state_manager.py`
- **Wymagania:**
  1. W `record_trade()`: zablokowanie resetu dźwigni ze stanu `SCOUT_MODE` (x0.1) do `BASE_MODE` (x1.0) przy wyjściach cząstkowych (`is_partial=True` lub `remaining_contracts > 0`).
  2. Reset dozwolony **wyłącznie po 100% zamknięciu pozycji** z zyskiem netto.

### 🔹 TICKET-5: Powiadomienia Telegram & UI Dashboard
- **Pliki docelowe:** `src/mms4tk/hermes_webhook.py`, `scripts/hermes/mms4tk_position_close_monitor.py`, `ui_dashboard/src/components/SettingsTab.jsx`
- **Wymagania:**
  1. Alerty Telegram dla `MIDLINE_PARTIAL_TP` oraz `PROFIT_STALL_SPLIT`.
  2. Udostępnienie parametrów `midline_partial_tp_enabled` i `midline_tp_fraction` w UI Settings.

### 🔹 TICKET-6: Adversarial Test Suite & Weryfikacja Bramki
- **Plik docelowy:** `tests/test_midline_inactivity_tp.py`
- **Wymagania:**
  1. Testy jednostkowe dla Profit Stall Gate (4 świece).
  2. Testy dla negacji impulsu 38.2% i bufora 0.12% BE escape.
  3. Testy wzorca fail-closed w `tp_midline_ledger`.
  4. Testy zachowania twardego 2% SL po redukcji Midline.
  5. Adversarial tests: mikro-pozycja 1 kontrakt, restart w trakcie cyklu, blokada resetu Scout Mode.
  6. 100% zaliczenia pełnej bazy testowej (236+ testów).
  7. Podbicie wersji do **`v0.42.0`** (4 pliki SSOT).
