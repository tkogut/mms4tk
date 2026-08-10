# PLAN 008: TASK 08 - Dwupoziomowy Adaptacyjny System Autotradingu (MMS v4 + Hermes Strategist)

## Status: COMPLETE
**Worktree:** `tmp/worktrees/task_08`  
**Branch:** `task_08`  

---

## 1. CEL
Wdrożenie pełnego Dwupoziomowego Adaptacyjnego Systemu Autotradingu (Two-Tier Adaptive Autotrading System), dzielącego rolę między szybki silnik M15 (`mms4tk`) a kognitywnego stratega H4/D1 (`hermes-agent`):

1. **Model Strategii (`StrategyProfile`) & Persystencja SQLite**:
   - Pola: `directional_bias` (`BOTH`, `LONG_ONLY`, `SHORT_ONLY`, `FLAT`), `risk_profile` (`CONSERVATIVE`, `MODERATE`, `AGGRESSIVE`), `scout_override` (`bool`), `reason` (`str`), `updated_at` (`str`).
   - Persystencja profilu w tabeli `strategy_profile` bazy SQLite (`.agents/swarm/logs.db`).

2. **Analiza Wyższych Interwałów H4 / D1 (`HigherTimeframeAnalytics`)**:
   - Pobór świec H4 i D1 z OKX przez `OKXExecutor`.
   - Wyliczanie wskaźnika rozszerzenia zmienności D1 (`atr_d1_expansion_ratio`).
   - Ocena układu H4: Położenie ceny względem wstęgi TMA_H4 (`ABOVE`, `BELOW`, `INSIDE`) oraz ocena świecy H4 (`SWEEP_WICK` vs `MARUBOZU_BREAKOUT`).

3. **Filtracja Sygnałów i Modulacja Ryzyka w M15 Engine**:
   - Sygnały `MMS4SignalEngine` filtrowane pod kątem `directional_bias` (np. `LONG_ONLY` odrzuca `BASE_SHORT`).
   - Modulacja dźwigni pod kątem `risk_profile` (`CONSERVATIVE` x0.5, `MODERATE` x1.0, `AGGRESSIVE` x2.0).
   - Obsługa `scout_override`: Umożliwia Hermesowi wywołanie szybszego powrotu do `BASE_MODE` po analizie kciuka H4 (`SWEEP_WICK`).

4. **Endpointy REST API & Narzędzia Hermesa**:
   - `GET /api/v1/analytics/higher_tf`: Zwraca analizę H4/D1 dla Hermesa.
   - `POST /api/v1/strategy/profile`: Umożliwia Hermesowi aktualizację profilu (z nagłówkiem `X-MMS4-API-KEY`).
   - `GET /api/v1/strategy/profile`: Zwraca aktywny profil.
   - Narzędzia Hermesa `mms4_get_higher_tf` oraz `mms4_set_strategy_profile` zarejestrowane w `.agents/swarm/openapi.json`.

---

## 2. ETAPY WYKONANIA (BUILDER)
- [x] Modyfikacja `logger_db.py` (tabela `strategy_profile`, metody `save_strategy_profile` i `get_latest_strategy_profile`)
- [x] Modyfikacja `state_manager.py` (integracja `StrategyProfile` z persystencją)
- [x] Modyfikacja `analytics.py` (dodanie `HigherTimeframeAnalytics` oraz filtrowanie sygnałów w `MMS4SignalEngine`)
- [x] Modyfikacja `api.py` (endpointy `/api/v1/analytics/higher_tf`, `POST /api/v1/strategy/profile`, `GET /api/v1/strategy/profile`)
- [x] Modyfikacja `hermes_tools.py` i regeneracja `.agents/swarm/openapi.json`
- [x] Utworzenie testu `tests/test_adaptive_tactics.py`
- [x] Uruchomienie pytest (36/36 100% PASS)
- [x] Wdrożenie produkcyjne na VPS Hostinger & test z kontenera `hermes-agent`
- [x] Wygenerowanie `.agents/swarm/handshake.json` i `.agents/swarm/task_08_report.md`

---

## 3. AUDYT & HANDSHAKE
- [x] Wszystkie testy jednostkowe z wynikiem PASS (`pytest`: 36 passed).
- [x] Poprawne wyliczenie H4/D1 oraz modyfikacja profilu z kluczem autoryzacyjnym.
- [x] Handshake: Plan-Alignment, Math-Consistency, and Two-Tier Adaptive Architecture checked.

