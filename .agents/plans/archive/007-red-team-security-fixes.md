# PLAN 007: TASK 07 - Red Team Security Fixes (State Persistence, Closed Candles, REST API Auth)

## Status: COMPLETE
**Worktree:** `tmp/worktrees/task_07`  
**Branch:** `task_07`  

---

## 1. CEL
Wdrożenie 3 krytycznych poprawek bezpieczeństwa i odporności produkcyjnej wyznaczonych podczas audytu Red Team w module `mms4tk`:

1. **FIX-1 (Persystencja Stanu Maszyny w SQLite)**:
   - Zmiana `MMS4StateManager` tak, aby przy inicjalizacji automatycznie odczytywał ostatni zarejestrowany stan z bazy SQLite (`.agents/swarm/logs.db`).
   - Przejścia stanów (`BASE_MODE` <-> `SCOUT_MODE`) są natychmiast zapisywane w tabeli bazy danych.
   - Po restarcie kontenera/serwera stan `SCOUT_MODE` i aktywny lewar są przywracane 1:1.

2. **FIX-2 (Kalkulacje na Utwardzonych Świecach)**:
   - Zmiana `MMS4SignalEngine.evaluate_signals()` tak, aby analiza wstęg TMA/ATR oraz wyliczanie sygnałów zakupu/sprzedaży odbywało się **wyłącznie na zamkniętych świecach** (`candles[:-1]`).
   - Eliminacja zjawiska Look-ahead Bias i fałszywych wyzwoleń na żywej świecy.

3. **FIX-3 (Autoryzacja REST API dla Punktów Mutujących)**:
   - Dodanie w FastAPI (`api.py`) weryfikacji sekretnego nagłówka HTTP `X-MMS4-API-KEY` dla wszystkich końcówek typu POST (`POST /api/v1/emergency/*`, `POST /api/v1/trade/*`).
   - Konfiguracja zmiennej środowiskowej `MMS4_API_KEY` (z bezpiecznym domyślnym fallbackiem).
   - Aktualizacja generatora narzędzi `hermes_tools.py` i reguł OpenAPI w `.agents/swarm/openapi.json`.

---

## 2. ETAPY WYKONANIA (BUILDER)
- [x] Modyfikacja `logger_db.py` (dodanie tabeli/metod `save_state` oraz `get_latest_state`)
- [x] Modyfikacja `state_manager.py` (obsługa odczytu i zapisu stanu z/do SQLite)
- [x] Modyfikacja `analytics.py` (filtracja `candles[:-1]` w `evaluate_signals`)
- [x] Modyfikacja `api.py` (weryfikacja `X-MMS4-API-KEY` header middleware/dependency)
- [x] Modyfikacja `hermes_tools.py` i regeneracja `openapi.json`
- [x] Utworzenie testów: `tests/test_state_persistence.py`, `tests/test_closed_candles.py`, `tests/test_api_auth.py`
- [x] Uruchomienie pytest (31/31 100% PASS)
- [x] Wdrożenie produkcyjne na VPS Hostinger
- [x] Wygenerowanie `.agents/swarm/handshake.json` i `.agents/swarm/task_07_report.md`

---

## 3. AUDYT & HANDSHAKE
- [x] Wszystkie testy jednostkowe z wynikiem PASS (`pytest`: 31 passed).
- [x] Odczyt stanu z bazy SQLite po awaryjnym restarcie zaktualizowany.
- [x] Handshake: Plan-Alignment, Math-Consistency, and Security Fixes verified.

