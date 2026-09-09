# 📋 TASK BACKLOG: Etap 0 — P9 (Higiena) + P7 (Naprawa parametrów silnika rekalibracji)

**Status:** P9 (TICKET-P9.1 do P9.7) ORAZ P7 (TICKET-P7.1 + 3 "same file" follow-ony)
ZAIMPLEMENTOWANE i przetestowane — 301/301 testów PASS. Decyzje architektoniczne dla
P9.2/P9.3 zatwierdzone przez usera (patrz commity `21ec44b`). P9.6 miało konflikt ze
świadomym testem (`test_risk_profile_leverage_modulation`) — rozstrzygnięty przez
usera: usunięto martwy kod, zaktualizowano test. P7 wykonane "po kolei" (jak
zlecono) — core fix, potem 3 follow-ony, każdy osobnym commitem/krokiem weryfikacji.
**Źródło:** `docs/AUDIT_MMS4_PROFIT_OPTIMIZATION_2026-09-08.md`, sekcje P7, P9, §5
**Standard:** AGENTS-OS v6.5 Swarm / MMS4 Strict Compliance
**Git Worktree:** `tmp/worktrees/feature/p9-hygiene-p7-recalibration-params`

---

## 🎯 Cel

Zrealizować **Etap 0** z rekomendowanej kolejności audytu (§5): P9 (7 poprawek
higienicznych) + P7 (naprawa parametrów silnika rekalibracji). Oba elementy dzieli
wspólna cecha — **żaden nie wymaga backtestu ani nowej hipotezy**, to naprawy
poprawności (kod robi coś innego, niż twierdzi jego własna dokumentacja/UI) lub
bezpieczniki. P7 dodatkowo **warunkuje zaufanie do całej reszty audytu** — bez niego
każdy przyszły werdykt PLAN-010 jest wyprodukowany dla nieistniejącej konfiguracji.

**Ten dokument to breakdown na tickety do dyskusji architektonicznej — nie zlecenie
implementacji.** Nikt nie zaczyna kodować, dopóki nie padnie wyraźna zgoda.

---

## 🏗️ Jednostki Operacyjne Backlogu

### 🔹 FAZA 0a: P9 — Higiena i bezpieczeństwo (7 ticketów, każdy niezależny)

#### ✅ DONE (dcdd33b) TICKET-P9.1: `midline_partial_tp_enabled` default → `False`
- **Pliki:** `src/mms4tk/analytics.py:146`, `src/mms4tk/api.py:911`, `src/mms4tk/api.py:1045`, `ui_dashboard/src/components/SettingsTab.jsx:18`
- **Dowód:** audyt §1.6 — funkcja odrzucona 6/6 (CHANGELOG 0.42.1) jest dziś fail-open: świeży deploy bez jawnego wpisu w `system_settings` włącza ją z powrotem. Wszystkie inne odrzucone filtry mają default `False`.
- **Zakres:** zmiana jednej wartości domyślnej w 4 miejscach, spójnie.

#### ✅ DONE (21ec44b, 4f1c8b2) TICKET-P9.2: Portfolio Margin Guard — ustawić `total_max_portfolio_margin_usd`
- **Pliki:** dane operacyjne w `system_settings` (nie kod — `daemon.py:150-173`/`api.py:941-949` już poprawne i kompletne, w tym zamknięcie race'u)
- **Dowód:** audyt §4 poz. 21 — dziś `total_max_portfolio_margin_usd = 0.0` → guard jest de facto wyłączony. Przy notional 2×($105+$45)=$300 na $100 equity próg rzędu 300-400 USD nic dziś nie blokuje, ale jest twardym hamulcem przy skalowaniu konta.
- **⚠️ Otwarte pytanie do architektury:** to nie jest zmiana kodu, tylko wartości konfiguracyjnej — czy ustawiamy ją operacyjnie (API/DB), czy zmieniamy fabryczny default w `daemon.py:135`? Audyt nie podaje jednej twardej liczby (mówi "300-400"), więc dokładną wartość i miejsce ustawienia trzeba uzgodnić przed implementacją.

#### ✅ DONE (21ec44b) TICKET-P9.3: Naprawić `/api/v1/emergency/reverse` (dziś pusty stub)
- **Plik:** `src/mms4tk/api.py:457-467`
- **Dowód:** audyt §1.9f — endpoint dziś zwraca `"success"` i nic nie robi. To "najgroźniejszy rodzaj buga" — raportowanie nieprawdy o zamknięciu pozycji w ścieżce awaryjnej.
- **Zakres (do wyboru na dyskusji):** (a) zaimplementować faktyczne odwrócenie pozycji, albo (b) zwracać `501 Not Implemented` do czasu implementacji. Ten sam problem dotyczy `killall` — sprawdzić analogicznie: albo zamyka pozycje, albo przestaje to twierdzić.

#### ✅ DONE (5c2cbf6) TICKET-P9.4: Rozszerzyć `HigherTimeframeResponse` o pola volatility
- **Plik:** `src/mms4tk/api.py` (response model), `src/mms4tk/analytics.py` (już liczy te wartości)
- **Pola do dodania:** `volatility_zone`, `is_volatility_expansion`, `atr_d1_expansion_threshold`
- **Dowód:** audyt §1.3 pkt 1 — dane są liczone w `analytics.py` i dziś odfiltrowywane przez `response_model` w `api.py:509`. Zero wpływu na egzekucję, ale prerekwizyt dla P5 (krok 0) z dalszej kolejności audytu.

#### ✅ DONE (1597d2e) TICKET-P9.5: Podnieść `limit=100` → `limit=150` w `daemon.py:733`
- **Plik:** `src/mms4tk/daemon.py:733`
- **Dowód:** audyt §1.9h — VR (Volatility Ratio) ma dziś mianownik liczony z ~86 okresów zamiast 100 (bo pobierane jest tylko 100 świec, a część idzie na rozgrzewkę wskaźnika), co przesuwa punkt przecięcia progów 1.6/0.8 właśnie wykalibrowanych w innym miejscu audytu.

#### ✅ DONE (c5e1545) TICKET-P9.6: Usunąć martwe `lev` z `analytics.py:532-537`, `:619-624`
- **Plik:** `src/mms4tk/analytics.py`
- **Dowód:** audyt §1.9b / §4 poz. 19 — wartości nieużywane w praktyce (`daemon.py` bierze lewar ze `state_manager`, nie stąd), a układ `AGGRESSIVE(2.0) < MODERATE(3.0)` jest mylący dla każdego przyszłego czytelnika, bo sugeruje odwrotną hierarchię ryzyka niż faktycznie obowiązująca.

#### ✅ DONE (8b0a993) TICKET-P9.7: `high_risk_add` — wołać `_record_addon`, gate per-symbol, nie łykać wyjątków
- **Plik:** `src/mms4tk/api.py:470`
- **Dowód:** audyt §1.9g — dzisiejsza implementacja omija Single Addon Guard i raportuje `executed: True` nawet gdy w środku wystąpił wyjątek połknięty przez `except`.

---

### 🔹 FAZA 0b: P7 — Naprawić parametry silnika rekalibracji 🟠 (SZYBKA WYGRANA, WYSOKI PRIORYTET)

#### ✅ DONE (b895439) TICKET-P7.1: `train_test_validate` ma czytać VR z `system_settings`, nie z hardcoded defaultów
- **Plik:** `src/mms4tk/backtest_engine.py:457` (defaulty), `:549` (`run_calibration_job`, wchodzi w default bez `params`)
- **Dowód:** audyt §1.5 / P7(a) — hardcoded `adaptive_vr_high=1.0, adaptive_vr_low=0.75` to progi już zresetowane na produkcji w tej sesji audytowej. Silnik rekalibracji dziś symuluje strategię, która **już nie istnieje**.
- **Mechanizm (P7(b)):** nie podnosi PnL bezpośrednio — przywraca wiarygodność jedynego mechanizmu chroniącego strategię przed overfittingiem. Bez tego każdy przyszły werdykt PLAN-010 jest bezwartościowy.
- **Wzorzec implementacyjny (P7(c)):** czytać `engine_mode`/`adaptive_vr_*`/`time_lock_*` z `system_settings` przez `LoggerDB.get_all_settings()` — dokładnie tak jak `daemon._get_indicator_params` (`daemon.py:678-690`, gotowy wzorzec do skopiowania/reużycia).
- **Test wymagany:** dwa uruchomienia z jawnie różnymi progami VR muszą dać różne `full_closes` — to jedyny dowód, że parametr faktycznie dotarł do silnika. Dodatkowo assert: `train_test_validate` wywołany bez `params` **nie może** cicho spaść na literały.
- **Zaimplementowane:** `train_test_validate()`'s default fallback czyta teraz `IndicatorParams()`'e env-var defaulty (nie hardcoded literały); nowy helper `_live_indicator_params(db)` w `backtest_engine.py` buduje params z `system_settings` (mirror `daemon._get_indicator_params`); `run_calibration_job()` jawnie przekazuje je do `train_test_validate(..., params=params)`.

**"Same file" follow-ons — zrobione po kolei, każdy osobnym commitem:**
- ✅ DONE (`913b34d`) Gate na liczbę transakcji przed `VALIDATED`: `MIN_FULL_CLOSES_HALF` 12→30, zastosowany też do TEST-half `generalizes` (wcześniej gate istniał tylko po TRAIN-side).
- ✅ DONE (`d67363f`) Siatka zredukowana 64→16 komórek (zatwierdzone 4×4: `lookback∈[3,5,8,13]`, `threshold∈[0.0010,0.0015,0.0020,0.0030]`).
- ✅ DONE (`b9b1e31`) Post-SL Cooldown (3 świece) zamodelowany w `run_backtest()` — mirror `daemon.py`'s `_sl_cooldown_until` (PLAN-008 Ph2), nowy param `sl_cooldown_candles=3` default.

7 nowych testów (3 wymagane przez audyt + txn-gate + grid-size guard + 2 cooldown). Pełny test suite: **301/301 PASS**.

---

## ⚠️ Uwaga przekrojowa (z audytu, sekcja końcowa)

P9 i P7 to **naprawy poprawności** — kod robi coś innego, niż twierdzi jego własna
dokumentacja/UI, nie ma tu hipotezy do zwalidowania, jest błąd do usunięcia. To
odróżnia je od P1/P3 (przywrócenie porzuconej specyfikacji) i od P4/P5/P6/P8 (nowe
hipotezy optymalizacyjne wymagające pełnej ostrożności backtestowej). **Ten backlog
celowo nie miesza kategorii** — Etap 0 to wyłącznie P9+P7.

---

## 🔗 Relacja do istniejącego `PLAN-010` / poprzedniego `task.md`

Poprzednia zawartość tego pliku (backlog `PLAN-010 v2.0` — mechanizm rekalibracji
filtra trendu, 6 ticketów, `.agents/plans/PLAN-010-trend-filter-recalibration-mechanism.md`)
**nie jest usunięta z historii Git**, tylko zastąpiona w tym pliku — audyt (§1.5)
stwierdza wprost, że silnik rekalibracji z PLAN-010 symuluje strategię, która już nie
istnieje (patrz P7 wyżej), więc dokończenie PLAN-010 przed P7 byłoby pracą na
niewłaściwym fundamencie. Kolejność z audytu (§5) stawia P9+P7 jako Etap 0, przed
jakąkolwiek kontynuacją PLAN-010.

---

## 🔹 Etap 1 (poza pierwotnym zakresem tego dokumentu, dodane 2026-09-09)

### ✅ DONE (9fe9879, fcecab0) P2 — Model prowizji i fundingu w `_stats()` 🔴 PREREKWIZYT
- **Plik:** `src/mms4tk/backtest_engine.py`
- **Dowód:** audyt §2.1 — zero `fee`/`funding`/`slippage` w silniku backtestu, mimo pełnej księgowości netto na produkcji (PLAN-011). Trzy decyzje produkcyjne (Midline TP off, trend filter off, high-vol filter off) podjęte na przyrządzie mierzącym brutto.
- **Zaimplementowane:** `ROUND_TRIP_FEE_PCT=0.0010` (OKX taker 0.05%×2, reużyte z już istniejącej stałej `inactivity_manager.py::DEFAULT_ROUND_TRIP_COST_PCT`, nie zgadywane) + `FUNDING_RATE_PCT_PER_INTERVAL=0.0001` per 8 świec (ceil division na czas trzymania pozycji). Nowy helper `_round_trip_cost_pct(candles_held)`. `_stats()` liczy win/loss/R:R/total_pnl na NET pnl, z defaultem `0.0` dla `fee_pct`/`funding_pct` → pełna wsteczna zgodność (syntetyczne testy bez tych pól dają identyczny wynik jak przed P2).
- **Test regresji (wymagany przez audyt), policzony naprawdę na fixture'ach:** ponowna walidacja Midline TP (6 okien BTC/ETH × H1/H2/full) i high-vol base filter (4 okna) pod NET pnl. **Wynik: żaden werdykt się nie odwrócił** — Midline TP nadal przegrywa 6/6, high-vol filter nadal bije baseline w dokładnie tych samych 2/4 oknach (BTC_H2, ETH_H2) co brutto. Przyrząd wykalibrowany; oba filtry NIE wracają do kolejki kandydatów.
- 8 nowych testów, pełny suite: **307/307 PASS**.

**Kolejność dalej wg audytu §5 (dla orientacji, nie zlecone jeszcze):** Etap 2 = P1 (SL dokładki, V0/V1/V2 — teraz może korzystać z wykalibrowanego przyrządu) → Etap 3 = P4 krok 0 + P6.1 → Etap 4 = warunkowe (P3/P4#2/P6.2) → Etap 5 = P5 krok 0 → Etap 6 = P8.

---

## ✅ Następny krok

P9, P7 i P2 zaimplementowane, przetestowane, PR-y otwarte/zmergowane. Etap 2 (P1)
czeka na osobne polecenie.
