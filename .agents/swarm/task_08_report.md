# TASK 08 REPORT: Dwupoziomowy Adaptacyjny System Autotradingu (Two-Tier Adaptive Autotrading System)

## 1. ARCHITEKTURA DWUPOZIOMOWA (TWO-TIER ADAPTIVE SYSTEM)

System autotradingu MMS v4 dzieli odpowiedzialność decyzyjną na dwa współpracujące poziomy:

1. **Silnik Wykonawczy M15 (`mms4tk`)**:
   - Działa na niskim interwale (M15), analizuje zamknięte świece (Eliminacja look-ahead bias).
   - Oblicza pasma TMA i ATR, wyłapuje sygnały odbicia od wstęg (`BASE_LONG`, `BASE_SHORT`) oraz kontynuacji trendu (`HIGH_RISK_ADD_LONG`, `HIGH_RISK_ADD_SHORT`).
   - Przestrzega ograniczeń aktywny profilu `StrategyProfile` oraz stanu ryzyka (`BASE_MODE` vs `SCOUT_MODE`).

2. **Kognitywny Strateg H4/D1 (`hermes-agent`)**:
   - Działa na wyższych interwałach (H4/D1), wywołuje REST API `mms4tk`.
   - Analizuje wskaźnik rozszerzenia zmienności D1 (`atr_d1_expansion_ratio`), położenie ceny względem wstęgi H4 TMA (`ABOVE`, `BELOW`, `INSIDE`) oraz formację świecy H4 (`SWEEP_WICK`, `MARUBOZU_BREAKOUT`, `NEUTRAL`).
   - Dynamicznie dostosowuje profil taktyczny `StrategyProfile` poprzez chroniony nagłówkiem `X-MMS4-API-KEY` endpoint `POST /api/v1/strategy/profile`.

---

## 2. ZREALIZOWANE KOMPONENTY I MODYFIKACJE

### A. Persystencja SQLite (`src/mms4tk/logger_db.py`)
- Utworzono tabelę `strategy_profile`:
  ```sql
  CREATE TABLE IF NOT EXISTS strategy_profile (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp TEXT NOT NULL,
      directional_bias TEXT NOT NULL,
      risk_profile TEXT NOT NULL,
      scout_override INTEGER NOT NULL,
      reason TEXT
  );
  ```
- Dodano metody `save_strategy_profile` oraz `get_latest_strategy_profile`.

### B. Model Profilu i Zarządzanie Stanem (`src/mms4tk/state_manager.py`)
- Zaimplementowano model Pydantic `StrategyProfile`:
  - `directional_bias`: `"BOTH" | "LONG_ONLY" | "SHORT_ONLY" | "FLAT"`
  - `risk_profile`: `"CONSERVATIVE" | "MODERATE" | "AGGRESSIVE"`
  - `scout_override`: `bool`
  - `reason`: `str`
  - `updated_at`: `Optional[str]`
- Zintegrowano `StrategyProfile` w `MMS4StateManager` z automatycznym wczytywaniem najnowszych ustawień z SQLite podczas inicjalizacji.
- Dodano metody `update_strategy_profile` oraz `relax_scout_mode` (służący do szybkiego powrotu ze stanu `SCOUT_MODE` do `BASE_MODE`).

### C. Analityka HTF & Filtrowanie Sygnałów (`src/mms4tk/analytics.py`)
- Dodano klasę `HigherTimeframeAnalytics`:
  - `calculate_higher_tf(candles_h4, candles_d1)` oblicza ATR D1, wskaźnik rozszerzenia zmienności D1, pozycję ceny H4 TMA oraz formację świecy H4.
- Zaktualizowano `MMS4SignalEngine.evaluate_signals()`:
  - Sygnały filtrowane według `directional_bias`:
    - `"LONG_ONLY"` odrzuca sygnały SHORT (z powodem `"Signal blocked by directional_bias=LONG_ONLY"`).
    - `"SHORT_ONLY"` odrzuca sygnały LONG (z powodem `"Signal blocked by directional_bias=SHORT_ONLY"`).
    - `"FLAT"` odrzuca wszystkie sygnały wejścia (z powodem `"Signal blocked by directional_bias=FLAT"`).
  - Modulacja dźwigni i profilu ryzyka:
    - `"CONSERVATIVE"`: ogranicza dźwignię sygnałów bazowych do max 0.5x oraz blokuje pozycje `HIGH_RISK_ADD`.
    - `"MODERATE"`: stosuje domyślną dźwignię 1.0x (lub 0.1x w `SCOUT_MODE`).
    - `"AGGRESSIVE"`: podnosi dźwignię sygnałów bazowych do 2.0x w `BASE_MODE`.

### D. Interface REST API (`src/mms4tk/api.py`)
- `GET /api/v1/analytics/higher_tf`: Zwraca analizę H4/D1 w modelu `HigherTimeframeResponse`.
- `GET /api/v1/strategy/profile`: Zwraca aktywny `StrategyProfile`.
- `POST /api/v1/strategy/profile`: Aktualizuje `StrategyProfile` z weryfikacją nagłówka `X-MMS4-API-KEY`. Jeśli `scout_override=True`, natychmiast wywołuje `relax_scout_mode()`.

### E. Integration Narzędzi Hermesa (`src/mms4tk/hermes_tools.py` & `.agents/swarm/openapi.json`)
- Zarejestrowano narzędzia Hermesa `mms4_get_higher_tf` oraz `mms4_set_strategy_profile`.
- Zregenerowano definicję OpenAPI `.agents/swarm/openapi.json`.

---

## 3. WERYFIKACJA I TESTY JEDNOSTKOWE (`pytest`)

Utworzono dedykowany zestaw testów w `tests/test_adaptive_tactics.py`:
1. `test_higher_timeframe_analytics`: Weryfikacja obliczeń ATR D1, rozszerzenia zmienności i formacji świec H4 (`SWEEP_WICK`, `MARUBOZU_BREAKOUT`).
2. `test_strategy_profile_api`: Test endpointów API z autoryzacją nagłówka `X-MMS4-API-KEY` (odrzucenie bez klucza 401, poprawna zmiana profilu 200).
3. `test_directional_bias_signal_filtering`: Weryfikacja blokowania sygnałów dla `LONG_ONLY`, `SHORT_ONLY`, `FLAT`.
4. `test_scout_mode_relaxation_by_scout_override`: Weryfikacja relaksacji `SCOUT_MODE` do `BASE_MODE` przez wywołanie API z `scout_override=True`.
5. `test_risk_profile_leverage_modulation`: Weryfikacja modulacji dźwigni dla profilu `CONSERVATIVE` (0.5x) i `AGGRESSIVE` (2.0x).

### Wyniki uruchomienia pakietu testów:
```text
============================== 36 passed in 1.44s ==============================
```
Wszystkie 36 testów w repozytorium zakończone wynikiem **PASS (100%)**.

---

## 4. PODSUMOWANIE HANDSHAKE

> **Handshake Verified:** Plan-Alignment, Math-Consistency, and Two-Tier Adaptive Architecture checked. Ready for Coordinator Push.
