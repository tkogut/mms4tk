# PLAN 003: TASK 03 - Moduł Analityczny i Silnik Sygnałów MMS v4

## Status: COMPLETE
**Worktree:** `tmp/worktrees/task_03`  
**Branch:** `task_03`  

---

## 1. CEL
Zbudowanie i integracja modułu analitycznego (`analytics.py`) oraz silnika sygnałów dla strategii MMS v4 z `MMS4StateManager`:
- Obliczanie wskaźników **TMA** (Triangular Moving Average) oraz **ATR Bands** z wykorzystaniem `pandas`/`numpy`.
- Detekcja sygnałów wejścia bazowego (**BASE_LONG**, **BASE_SHORT**) po reakcji na zewnętrzne wstęgi ATR.
- Wykrywanie sygnałów kaskadowej dokładki **HIGH_RISK_ADD** po zamknięciu świecy potwierdzającej kierunek (z oddzielnym SL max 1%).
- **Twarda blokada**: Jeśli `MMS4StateManager` znajduje się w stanie `SCOUT_MODE` (lewar x0.1), funkcja **HIGH_RISK_ADD** jest bezwzględnie zablokowana.
- REST API endpointy do analizy świec OHLCV i wywoływania kalkulacji sygnałów.
- Raport wykonania oraz zrzut w pliku handshake (`.agents/swarm/handshake.json`).

---

## 2. ETAPY WYKONANIA
- [x] Utworzenie modułu `src/mms4tk/analytics.py` (klasy `TMAIndicator`, `MMS4SignalEngine`, modele pydantic `Candle`, `SignalResult`).
- [x] Rozbudowa `src/mms4tk/state_manager.py` o weryfikację `is_high_risk_add_allowed()`.
- [x] Rozbudowa `src/mms4tk/api.py` o endpointy `/api/v1/analytics/calculate` oraz `/api/v1/signals`.
- [x] Stworzenie pakietu testów `tests/test_analytics.py` i `tests/test_integration.py`.
- [x] Generowanie pliku `.agents/swarm/handshake.json` oraz `.agents/swarm/task_03_report.md`.

---

## 3. AUDYT & HANDSHAKE
- [x] Wszystkie testy jednostkowe z wynikiem PASS (`pytest`: 7 passed).
- [x] Weryfikacja twardego wyłączenia dokładek w SCOUT_MODE.
- [x] Handshake: Plan-Alignment and Math-Consistency checked.

