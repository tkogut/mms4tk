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

**Kolejność dalej wg audytu §5:** Etap 2 = P1 (osobny PR #28: wynik "V0 zostaje",
bramka 5/6 nieprzeszła) → Etap 3 = P4 krok 0 + P6.1 (P6.1: osobny PR #29) → Etap 4 =
warunkowe (P3/P4#2/P6.2) → Etap 5 = P5 krok 0 → Etap 6 = P8.

---

## 🔹 Etap 3 (część 2): P4 krok 0 — pomiar ATR%/SL per symbol 🟠

### ✅ DONE (e4401a9, e1e61bf) — czysty pomiar, ZERO zmian w kodzie produkcyjnym
- **Plik:** `tests/test_atr_sl_measurement.py` (nowy, tylko testy — brak zmian w `src/`)
- **Hipoteza audytu:** SL jest price-relative (2% zawsze), wstęga jest ATR-relative → stop ETH może być tłumaczeniem gorszego R:R na ETH.
- **Policzone naprawdę** na 90-dniowych fixture'ach, produkcyjnym `TMAIndicator`/`IndicatorParams` (tma_length=20, atr_period=14):
  - **BTC/USDC:** ATR/cena median=0.498% (mean=0.556%) → SL_distance/ATR median=4.017 (mean=4.888)
  - **ETH/USDC:** ATR/cena median=0.672% (mean=0.735%) → SL_distance/ATR median=2.975 (mean=3.567)
  - **Stop ETH jest ~26-27% ciaśniejszy w jednostkach ATR niż BTC** — kierunek zgodny z hipotezą, skala mniejsza niż ilustracyjne ~40% z samego dokumentu audytu (te liczby były jawnie oznaczone jako "ilustracyjne, nie ustalenie").
- **Wynik bramki kroku 0** (audyt: "jeśli różnica okaże się mała, hipoteza upada i tu kończymy"): różnica **NIE jest mała** (26-27% ≫ 15% przyjęte jako konserwatywny próg "nie-mały") → **hipoteza NIE upada**. To NIE oznacza automatycznego przejścia do wariantu #2 (per-symbol `base_sl_pct` grid, 2-way OOS) ani #3 (`SL=k·ATR`, zmiana doktryny KB 1.4) — to osobna, większa decyzja wymagająca zgody właściciela strategii, poza zakresem kroku 0.
- 2 nowe testy: jeden lockuje pomiar z konserwatywnym progiem 15% (nie dokładną wartością — nie sflaczeje na drobnych zmianach fixture/wskaźnika), drugi to sanity check zgodności z produkcyjnymi defaultami. Pełny suite: **309/309 PASS**.
- **Niezależna recenzja (Opus, drugi agent, 2026-09-10):** liczby przeliczone niezależnie, zgodne co do 4. cyfry znaczącej. Metodologia potwierdzona jako zgodna z produkcją. Zero zastrzeżeń poza kosmetyką opisu (poprawione powyżej). Rekomendacja: **MERGE**. Bonus finding: BTC SL/ATR≈4.02 to praktycznie cała szerokość wstęgi (4·ATR) — stop na BTC jest wręcz *za szeroki*, co wzmacnia hipotezę P4 mocniej niż oryginalnie napisano.

---

## 🔹 Etap 2: P1 — Odseparować SL dokładki od SL bazy 🔴 PRIORYTET NAJWYŻSZY

### ⚠️ HARNESS DONE, WNIOSEK SKORYGOWANY po niezależnej recenzji (76e79d3, 4dd327e, f0a9e9f, `f8c9867`)
- **Plik:** `src/mms4tk/backtest_engine.py`
- **Dowód:** audyt §1.1+§1.2 — `closeFraction="1"` + kasowanie poprzednich algo + `entry_price = cena dokładki` = de facto stop BE ~0.5% na całej pozycji przy ~70% pozycji. Narusza "Żelazną Regułę" ze spec.
- **Zaimplementowane (harness, `76e79d3`):** `run_backtest(addon_sl_variant="V0"|"V1"|"V2")`.
  - **V0** (domyślny, = obecna produkcja, niezmieniony): kompozyt 1.25% od uśrednionej (avgPx) ceny wejścia.
  - **V1**: kompozyt 2.0% od avgPx — ten sam mechanizm, szerszy próg.
  - **V2**: SL bazy zostaje przypięty do ORYGINALNEJ ceny wejścia bazy (nowe pole `position["base_entry_price"]`, nigdy nieaktualizowane przez uśrednienie dokładki), dokładka bez własnego SL — najbliższe intencji spec.
- **Pierwsza analiza (2026-09-09) była wadliwa** — niezależna recenzja (2. agent, Opus) znalazła, że użyła `adaptive_vr_high=1.0/adaptive_vr_low=0.75` — dokładnie te literały, które P7 nazywa "nieaktualnymi" (produkcja ma 1.6/0.8). Pod starymi progami BTC_H1 i ETH_H2 miały odpowiednio 5 i 6 dokładek (nie zero — poprzednia wersja tego zdania też była nieścisła, poprawione po 3. turze recenzji), ale **zero dokładek zamkniętych na STOP_LOSS** — czyli mechanizm SL, który P1 bada, nigdy się tam nie uaktywnił, mimo że okna liczyły się do bramki 5/6.
- **Analiza poprawiona (`f8c9867`), produkcyjne VR 1.6/0.8, policzona naprawdę:**
  - **Ścisła bramka** (R:R **i** total_pnl netto oba muszą bić V0): V1 = **3/6**, V2 = **3/6**. Bramka wymaga ≥5/6 — **żaden wariant jej formalnie nie przechodzi**.
  - **Ale samo total_pnl netto** (bez wymogu R:R): V1 = **5/6** (remis tylko w ETH_H2 — to okno MA 16 dokładek, nie brak dokładek; mechanizm SL po prostu nigdy nie zróżnicował tam wyniku żadnej transakcji) — realny, spójny kierunkowo sygnał, który ścisła bramka odrzuca przez zmiękczenie R:R w 2 oknach (BTC_H1: 1.3235→1.1818, realnie ~10,7%, nie "kilka %"; ETH_H1: 1.3121→1.2919, ~1,5%).
  - **Dodatkowo, wciąż nienaprawione:** V0-w-harnessie liczy SL dokładki od avgPx (1.25%), a produkcja liczy go od **ceny dokładki** (`daemon.py` `attach_stop_loss`) — w przykładzie z audytu §1.1 to ~0.54% realnego ryzyka, nie 1.25%. Ten harness nigdy nie zamodelował patologii z §1.1 — null result jej NIE falsyfikuje.
- **Decyzja skorygowana:** to **NIE jest** czyste "V0 empirycznie wygrał, hipoteza upadła". Formalna bramka nie przechodzi (3/6 < 5/6), więc **kod produkcyjny zostaje nietknięty na razie** — ale to jest **eskalacja do właściciela strategii**, nie zamknięty temat: sygnał PnL jest realny i spójny (5/6, nigdy gorzej), a baseline V0 sam w sobie nie modeluje mechanizmu, który P1 miał zbadać.
- Wynik zablokowany testem regresyjnym `test_p1_addon_sl_gate_analysis` (`@pytest.mark.slow`, przemianowany z `..._v0_stays_...` — nazwa też była nieuprawniona).
- **5 nowych testów** (nie 9, jak błędnie podano wcześniej — poprawiono). Pełny suite: **312/312 PASS**.

**Stan wg audytu §5, po zmergowaniu P1/P4-krok-0/P6.1 (ten dokument, poniżej):**
Etap 0 (P9+P7), Etap 1 (P2), Etap 2 (P1 — wynik "V0 zostaje", eskalacja do
właściciela strategii, patrz PR #28), Etap 3 (P4 krok 0 + P6.1) — **wszystkie
zamknięte**. Dalej: Etap 4 = warunkowe (P3/P4#2/P6.2) → Etap 5 = P5 krok 0
(logowanie `volatility_zone`) → Etap 6 = P8 (analiza sesyjna). Żaden z Etapu 4+ nie
został jeszcze zlecony.

---

## 🔹 Etap 3 (część 1): P6.1 — Naprawa buga persystencji per-symbol config Inactivity 🟠

### ✅ DONE (13a2c49, 88e7877) — bezwarunkowa naprawa poprawności, bez backtestu
- **Pliki:** `src/mms4tk/api.py`, `src/mms4tk/daemon.py`
- **Dowód:** audyt §1.7 — `stale_candles_threshold`/`stale_atr_threshold`/`impulse_negation_enabled` były zapisywane WYŁĄCZNIE do `configured_symbols_store`/`symbols.json`, nigdy do `system_settings`. `load_symbols_config()` preferuje SQLite `symbol_configurations` (stała schema, brak kolumn dla tych pól) gdy tylko ma jakiekolwiek wiersze (zawsze ma, gdy istnieje symbol) → po każdym restarcie/redeployu ustawienia operatora cicho resetowały się do hardcoded defaultów (12/0.5/True), a UI kłamało, że są zapisane.
- **Naprawa (dokładnie ten sam wzorzec co dla trend-filtra):**
  1. `_trend_filter_fields` w `api.py` rozszerzone o te 3 pola (czyta `system_settings` per-symbol-qualified pierwsze, potem `configured_symbols_store`).
  2. `add_or_update_trading_symbol` zapisuje teraz te 3 klucze do `logger_db.set_settings_bulk` per-symbol-qualified.
  3. `GET /api/v1/symbols` czyta je przez `_trend_filter_fields`, nie bezpośrednio z `config.get()`.
  4. `daemon.py`: nowa metoda `_get_inactivity_overrides(symbol)` (analogiczna do `_get_indicator_params`) zastępuje inline blok czytający wyłącznie `configured_symbols_store` — `system_settings` jest teraz SSOT, fallback zachowany bez regresji.
- 8 nowych testów (6 daemon-level, w tym test symulujący restart przez świeży `LoggerDB` handle na tym samym pliku db; 2 API-level end-to-end przez `TestClient`). Pełny suite: **315/315 PASS**.
- **Dwie tury niezależnej recenzji (Opus, drugi agent):**
  - **1. tura** znalazła krytyczne zanieczyszczenie żywej `.agents/swarm/logs.db` (docker-mounted do demona) przez nowe testy bez cleanupu. Naprawione (`658d55a`): nowa `LoggerDB.delete_setting()`, `conftest.py::_isolate_system_settings` rozszerzony o pełny snapshot/restore całej tabeli `system_settings`, jawny try/finally w testach.
  - **2. tura** (weryfikacja naprawy) potwierdziła empirycznie, że naprawa działa dla kluczy per-symbol, ale znalazła: (a) komentarz przesadnie twierdził "ANY key" — 5 kluczy globalnych (`engine_mode`/`adaptive_vr_*`/`time_lock_*`) jest zawsze wymuszanych na kanoniczne wartości niezależnie od snapshotu, **zachowanie sprzed tego PR-a** (commit `3a9c688`), niezmienione tutaj, tylko doprecyzowany opis; (b) endpoint `/symbols/update` pisze też do `symbol_configurations`/`symbols.json`, czego mój cleanup nie przywracał — naprawione (`84b5de7`), test teraz przechwytuje pełny config i przywraca przez `save_symbols_config()`.
  - **3. tura** (weryfikacja obu poprzednich) znalazła jeszcze jeden realny defekt: cleanup w teście sprawdzał `if before_full_config:` (truthiness) zamiast `is not None` — dla symbolu nieskonfigurowanego wcześniej (pusty dict, falsy) restore był pomijany całkowicie, a endpoint tworzy symbole bezwarunkowo → test mógł **trwale pozostawić aktywny symbol handlowy** w konfiguracji. Naprawione (`1f73481`): sentinel na `is not None`, gałąź "nie istniał wcześniej" usuwa symbol przez `logger_db.delete_symbol_config()`. Zweryfikowane empirycznie — odtworzono dokładny scenariusz, symbol poprawnie wraca do stanu `None` po teście.
  - **Nie w zakresie tego PR-a (osobne tickety):** pełna izolacja testów od żywej bazy (np. `MMS4_DB_PATH` na katalog tmp przed importem `mms4tk`); wyciek `strategy_profile.scout_override=True` do współdzielonej bazy przez INNE (niepowiązane z tym PR-em) testy — realne ryzyko dla demona przy restarcie, znalezione przy okazji.

---

## 🔹 Etap 4: P3 — Przywrócić rozróżnianie stop-outu z dokładką w rekoncyliacji 🟠

### ⚠️ Pierwsza wersja (027f9af) była wadliwa — niezależna recenzja (Opus, drugi
agent) znalazła realny bug + 3 przekłamania w tym dokumencie. Poprawiona poniżej.

**Pierwsza wersja liczyła próg jako `% od avgPx` (`addon_sl_pct` zamiast
`base_sl_pct`)** — ale audyt §1.1 sam pokazuje, że composite SL dokładki jest
zakotwiczony w **cenie wypełnienia samej dokładki** (`okx_executor.attach_stop_loss`
/ `execute_order`), nie w avgPx. Recenzent uruchomił kanoniczny przykład z audytu
(SHORT base@100, dokładka@99, avgPx=99.70, SL=100.24, realna strata **0.54%**) na
tamtym kodzie — próg 0.9375% (`addon_sl_pct*0.75`) go nadal przepuszczał jako
"closed without SL trigger". Na fixture'ach BTC/ETH z repo pierwsza wersja
poprawiała klasyfikację tylko do ~85%/64% dokładek — zostawało 15-36% nadal źle
sklasyfikowanych, w tym przypadki, gdzie zmierzona strata względem avgPx jest
**ujemna** (SL trafia po zyskownej stronie avgPx — żaden próg % od avgPx nigdy by
tego nie złapał).

### ✅ DONE (poprawione) — real trigger price jako SSOT, nie % od avgPx

- **Pliki:** `src/mms4tk/logger_db.py` (nowa kolumna `position_addons.sl_trigger_price`
  + `record_addon`/`get_addon_sl_trigger_price`/`set_addon_sl_trigger_price`),
  `src/mms4tk/okx_executor.py` (`_compute_sl_trigger_price` — jedna formuła
  współdzielona przez `attach_stop_loss` i `execute_order`; `execute_order` zwraca
  teraz `sl_trigger_price` w wyniku), `src/mms4tk/daemon.py` (`reconcile_positions`,
  Self-Healing SL Guard, HIGH_RISK_ADD branch), `src/mms4tk/api.py`
  (`/api/v1/trade/high_risk_add`). Testy: `tests/test_reconciliation_pnl.py`,
  `tests/test_okx_sl_algo_instid.py`.
- **Naprawa:** przy egzekucji dokładki (`daemon.py` automatyczny High Risk Add i
  `api.py` ręczny endpoint) persystujemy w ledgerze `position_addons` **absolutną
  cenę**, w której faktycznie zawisł algo SL (`sl_trigger_price` z wyniku
  `execute_order`), zamiast próbować odtworzyć ją z procentu liczonego od avgPx.
  `reconcile_positions` porówuje `exit_price` bezpośrednio z tą realną ceną —
  tolerancja to 0.75 rzeczywistego dystansu avgPx→trigger (ten sam współczynnik co
  w gałęzi bazowej, ale liczony w cenie, nie w %), co poprawnie klasyfikuje też
  przypadek "SL po zyskownej stronie avgPx" (dystans/próg wtedy się kurczy lub
  staje ujemny, więc nawet mały/zerowy ruch adwersalny już go przekracza). Gdy
  cena triggera nie jest znana (dokładka bez adresu ceny, albo brak dokładki w
  ogóle) — fallback do starego `base_sl_pct*0.75` liczonego od avgPx, bez zmian.
- **Self-Healing SL Guard** (`daemon.py` ok. linii 998) re-attachuje SL zakotwiczony
  w avgPx (innym punkcie odniesienia niż oryginalna dokładka) — jeśli kiedykolwiek
  się uruchomi na pozycji z dokładką, teraz **synchronizuje** ledger
  (`_update_addon_sl_trigger_price`, tylko cena, licznik dokładek nietknięty), żeby
  nie zostawić w nim stałej, nieaktualnej ceny.
- **Efekt (uczciwie, nie 4/4):** poprawnie zaklasyfikowany `is_stop_loss=True`
  odblokowuje **3-świecowy Post-SL Cooldown** (`daemon.py`, gate na samym
  `is_stop_loss`, bez własnego progu %) — **to jest dostarczone, o ile mechanizm
  w ogóle poprawnie klasyfikuje — patrz Błąd B niżej, wciąż otwarty**. **Próg 2%
  w `state_manager.on_stop_loss_hit` (wejście w SCOUT_MODE + webhook Hermes H4)
  pozostaje niedotknięty** — audytowe zdanie "odblokowuje też trigger H4 dla
  Hermesa" **nie jest tym PR-em spełnione**. User (2026-09-10) zakwestionował
  wydzielenie tego do osobnego ticketu ("P3.1") — chce to złożone w ten sam
  workstream P3, bo audyt to przeoczył. Nieprzeanalizowane jeszcze w tej rundzie.
- **Znane, nienaprawione tu drobiazgi:** `logger_db.get_addon_count`/
  `get_addon_sl_trigger_price` dopasowują `symbol` dosłownie, nie przez
  `normalize_symbol()`. Przedawnione (pre-existing), niezwiązane z tym PR-em.

### ⚠️ Druga tura niezależnej recenzji (Opus) — werdykt "NIE mergować", 2 realne błędy

Pierwsza poprawka powyżej ("real trigger price jako SSOT") była kierunkowo słuszna,
ale druga tura recenzji znalazła, że jest **dalej niekompletna**:

**🔴 Błąd A (naprawiony, `9be...`/kolejny commit):** `adverse_move >= signed_distance
* 0.75` **odwraca się**, gdy `signed_distance <= 0` — a to nie jest przypadek
brzegowy: `HIGH_RISK_ADD` piramiduje **w zysk** (`analytics.py`), więc composite SL
dokładki regularnie ląduje po zyskownej stronie avgPx (na fixture'ach repo:
0.8-2.4% par świec-kandydatów, najgorszy przypadek -3.13% avgPx). Recenzent
zweryfikował na kodzie: realny stop-out dokładnie w cenie triggera dawał
`is_stop_loss=False` (przegapiony), a płytkie, wciąż zyskowne zamknięcie dawało
`is_stop_loss=True` (fałszywy alarm) — dokładnie odwrotnie niż powinno. **Naprawa:**
gdy `signed_distance <= 0`, nie próbować formuły dystansowej wcale — fallback na
stary `base_sl_pct*0.75` liczony od avgPx (ta sama gałąź co "brak dokładki"), plus
`logger.warning`. Bezpieczne (nie odwraca się nigdy), ale nie próbuje już
"poprawnie klasyfikować SL po zyskownej stronie avgPx" jak twierdziła poprzednia
wersja tego dokumentu (**fałsz, usunięty stąd**) — w tym degenerowanym przypadku po
prostu wraca do heurystyki sprzed P3. Nowy test:
`test_reconciliation_degenerate_trigger_on_profitable_side_falls_back_and_does_not_invert`.

**🔴 Błąd B (NIENAPRAWIONY, otwarty):** nawet w normalnej gałęzi
(`signed_distance > 0`), `exit_price` w `reconcile_positions` to `signal.price` —
close OSTATNIEJ ZAMKNIĘTEJ świecy, nie realna cena wypełnienia. Gdy realny dystans
avgPx→trigger jest mały (częsty przypadek), ta "spóźniona o świecę" cena potrafi być
dalej od triggera niż cała tolerancja. Recenzent zmierzył na fixture'ach OHLCV z
repo, symulując pełną produkcyjną geometrię (baza@close[i], dokładka@close[i+1],
`exit_price=close[j-1]`): realny recall stop-outów z dokładką to **BTC short
20.3%, BTC long 22.9%, ETH short 7.7%, ETH long 11.1%** — poprawka pomaga (0%→
kilkanaście-dwadzieścia kilka %), ale jest daleka od kompletności. `trade_sync.py`
**nie** klasyfikuje `is_stop_loss` niezależnie, więc nie ma dziś wolniejszego,
dokładniejszego backstopu dla tej konkretnej flagi. **Nie naprawione w tej
sesji** — wymaga albo realnej ceny wypełnienia jako źródła `exit_price` w tym
miejscu, albo świadomej decyzji, że taki recall jest akceptowalny. Patrz też
propozycja **V2** niżej — może to zrobić nieaktualnym.

**Korekty do tego dokumentu (poprzednia wersja miała 3 przekłamania, druga runda
recenzji znalazła 2 kolejne w międzyczasie — wszystkie poprawione powyżej):**
liczba nowych testów to **+6 w `test_reconciliation_pnl.py`** (nie 8 — jedna
pozycja z poprzedniej listy była zmianą nazwy istniejącego testu, nie nowym) **+1
w `test_okx_sl_algo_instid.py`** **+1 na Błąd A** (test degenerowanej geometrii;
korekta self-healing `has_addon`-proxy to zmiana kodu bez nowego testu) = suite
**320 → 327** (zweryfikowane: `327 passed, 4 deselected`), nie "326/326" ani "8 nowych".

### GATE (2026-09-10) — sprawdzone i domknięte

Przed V2 user sam sprawdził (dashboard UI, bez SSH/poświadczeń) `Settings`,
`Symbols`, i 100 ostatnich transakcji z `Analytics`. Wynik:
- **Ustawienia produkcyjne potwierdzone:** `engine_mode=ADAPTIVE`, VR 1.6/0.8,
  base SL 2.00%, addon SL 1.25% — zgodne z założeniami całej pracy nad P3 do tej
  pory, żadnej niespodzianki.
- **ETH/SOL/ICP: decyzja już podjęta, nie do dyskusji** — tylko BTC i ETH są
  aktywne; SOL/ICP/XRP są WSTRZYMANE. Punkt "decyzja czy handlować ETH/SOL/ICP"
  z kolejności Etapu 4 (niżej) jest przez to nieaktualny — nie ma czego decydować.
- **Realne dane transakcji (ja, z hosta VPS, po jawnej zgodzie usera):** wyeksportowałem
  `closed_trades` od `2026-08-30` (v0.41.0 — pierwsza wersja z realnym `fee_usd`;
  wcześniejsze wiersze mają go domyślnie 0.0, user sam ostrzegł że najstarsze
  pozycje są "błędne") przez `sqlite3 -readonly` na KOPII pliku (bind-mount hosta
  `/docker/mms4tk/.agents/swarm/logs.db`, kopia w `/tmp` usunięta zaraz po odczycie
  — zero ryzyka dla żywego procesu). 49 transakcji, tylko 2 z niekorzystnym ruchem
  ≥1% (ETH SHORT +1.35% amt=61, BTC SHORT +2.02% amt=14) — obie na `amount` większym
  niż typowy dla swojego symbolu w tym oknie (spójne z, ale niedowodzące, hipotezy
  o dokładce). Próbka zbyt cienka, żeby cokolwiek rozstrzygnąć statystycznie o
  rozkładzie strat — ale to i tak nie było uzasadnieniem dla V2 (patrz niżej).

### ✅ DONE — P3 V2: SL bazy jako jedyny, stały SSOT (2026-09-10)

**User (po GATE) potwierdził: kontynuować rekomendacje.** Zaimplementowane w tej
samej sesji, **zastępuje** (nie tylko uzupełnia) mechanizm z rund 1-2 powyżej
(persystowana cena triggera dokładki, Błąd A/B) — ten mechanizm okazał się
architektonicznie zbędny, gdy addon w ogóle przestaje mieć własny SL.

- **Uzasadnienie usera (nie optymalizacja hipotezy, bugfix determinizmu):** ta
  sama pozycja mogła dostać dwie różne ceny SL zależnie od tego, która ścieżka
  kodu odpaliła ostatnia — `execute_order` przy egzekucji dokładki (kotwica: cena
  wypełnienia dokładki) vs Self-Healing Guard (kotwica: żywe avgPx). V2 usuwa to
  strukturalnie: **dokładka nigdy nie dostaje własnego SL.**
- **Mechanizm:** `position_snapshots` ma nową kolumnę `base_entry_price` —
  ustawianą RAZ, przy pierwszym zapisie snapshotu dla (symbol, side) (czyli avgPx
  zanim cokolwiek zostało uśrednione — dokładnie prawdziwa cena bazy), i NIGDY
  nieaktualizowaną przy kolejnych zapisach (`ON CONFLICT DO UPDATE` świadomie
  pomija tę kolumnę, z wyjątkiem samo-leczenia wiersza sprzed migracji, gdzie
  była 0.0). `reconcile_positions` liczy próg WYŁĄCZNIE z `base_entry_price` i
  `base_sl_pct` — bezwarunkowo, bez świadomości dokładki w ogóle. Self-Healing
  Guard i re-attach po Midline Partial TP robią dokładnie to samo (ta sama kotwica,
  ten sam %, zawsze). HIGH_RISK_ADD (`daemon.py`) woła `execute_order(...,
  stop_loss_pct=0.0, ...)` — istniejący algo SL na giełdzie ma `closeFraction="1"`
  (chroni CAŁĄ pozycję, niezależnie od jej bieżącego rozmiaru), więc pozostawienie
  go nietkniętym już chroni dokładkę, bez wystawiania nowego zlecenia z inną kotwicą.
  To założenie NIE jest nową, niezweryfikowaną hipotezą — audyt (§1.1,
  `docs/AUDIT_MMS4_PROFIT_OPTIMIZATION_2026-09-08.md`) już wcześniej ustalił kodem,
  że `closeFraction="1"` obejmuje 100% pozycji, stąd oryginalny problem ("po
  dokładce cała pozycja stoi na wąskim stopie"). Odstępstwo od litery audytu: audyt
  sugerował dla V2 `closeFraction` proporcjonalny do samego legu bazy (SL zamyka
  tylko kontrakty bazy, dokładka zostaje bez SL) — ta implementacja zostawia
  `"1"`, więc trigger zamyka RÓWNIEŻ dokładkę. Bardziej ochronne, nie mniej
  (mieści się w spec-owym limicie strat), ale to inna semantyka niż dosłowny
  audytowy opis V2 — nazwane tu wprost, nie jako "zgodne z audytem".
- **Efekt na Błąd A:** **strukturalnie wyeliminowany.** `base_entry_price` to
  realna, już zrealizowana cena, a `base_sl_pct` to zawsze dodatni dystans w stronę
  niekorzystną z definicji — nie ma już żadnej geometrii, w której trigger mógłby
  wylądować "po zyskownej stronie" czegokolwiek.
- **Efekt na Błąd B:** zredukowany do poziomu sprzed P3 (nie wyeliminowany —
  `exit_price` to nadal `signal.price`, close ostatniej świecy), ale tolerancja
  wraca do szerokości `base_sl_pct` (2%), nie wąskiego `addon_sl_pct` (~1.25%) —
  ten sam poziom niedokładności, jaki zespół uznawał za akceptowalny przed całą
  tą pracą nad P3. **Nie jest prawdą, że `sl_percent` wynosi ~2% przy KAŻDYM
  realnym stopie** (przekłamanie znalezione i usunięte stąd przez recenzję rundy
  3) — w paśmie tolerancji `[0.75·base_sl_pct, base_sl_pct)` `is_stop_loss=True`
  wychodzi poprawnie, ale `sl_percent` może wyjść np. 1.5%, czyli poniżej progu
  `>=2%` w `state_manager.py:344` — bramka SCOUT_MODE/H4 wtedy nie odpala, mimo
  poprawnie zaklasyfikowanego stop-outu. To węższe okno niż przed V2 (bo tolerancja
  jest teraz zawsze pełne `base_sl_pct`, nie wąskie `addon_sl_pct`), ale nie zero.
- **Efekt na `state_manager.py:344` (SCOUT_MODE/Hermes H4) — naprawiony JAKO SKUTEK
  UBOCZNY dla trafień blisko/w trigger, zero zmian w `state_manager.py`:**
  `sl_percent` przekazywany do `record_trade`/`on_stop_loss_hit` jest teraz liczony
  z `base_entry_price` (nie z avgPx), więc próg `>=0.02` przechodzi teraz poprawnie
  dla pozycji z dokładką przy trafieniu blisko triggera (zweryfikowane LONG i
  SHORT) — zastrzeżenie o paśmie tolerancji wyżej. `pnl_percent`/`pnl_usd`
  (księgowość, `closed_trades`) zostają liczone od avgPx, bez zmian — to inna,
  świadomie odseparowana liczba (zweryfikowane, brak przecieku).
- **🔴 BŁĄD ZNALEZIONY I NAPRAWIONY W TEJ SAMEJ SESJI (recenzja rundy 3, blocker):**
  re-attach SL po **Midline Partial TP** (`daemon.py`, ok. linii 1196) nadal
  kotwiczył się w żywym avgPx (`active_entry_price`), NIE w `base_entry_price` —
  cicho odtwarzając dokładnie ten sam problem determinizmu/klasyfikacji, który V2
  miało usunąć, dla KAŻDEJ pozycji z dokładką, która przejdzie przez Midline TP.
  Naprawione: re-attach też liczy kotwicę przez `_base_entry_price(...)`. Dziś
  latentne (`midline_partial_tp_enabled` domyślnie `False`), ale był to realny bug,
  nie hipotetyczny — dodany test regresyjny
  (`test_midline_tp_reattaches_sl_at_base_entry_price_not_avgpx`,
  `test_midline_inactivity_tp.py`).
- **Luka migracyjna (nieudokumentowana wcześniej, teraz jawnie zapisana):**
  samo-leczenie wiersza z `base_entry_price=0.0` (stary wiersz sprzed migracji)
  seeduje z BIEŻĄCEGO avgPx, nie z prawdziwej historycznej ceny bazy. Dla pozycji
  z już otwartą dokładką w momencie wdrożenia tej zmiany, `base_entry_price`
  dostanie wartość avgPx-po-uśrednieniu (nieco ciaśniejszy SL / klasyfikacja od
  niewłaściwej ceny) do czasu zamknięcia tej konkretnej pozycji. Łagodna,
  jednorazowa degradacja przy deployu, nie błąd trwały.
- **Znany, udokumentowany, świadomie nierozwiązany kompromis — szerszy niż
  pierwotnie zapisano:** ręczny endpoint `/api/v1/trade/high_risk_add` (`api.py`)
  NADAL przyjmuje własny `stop_loss_pct` (domyślnie 1%) i attachuje niezależny SL,
  kasując bazowy. To nie tylko "wraca problem determinizmu" — **klasyfikacja
  stop-outu w `reconcile_positions` przestaje działać dla tej ścieżki w ogóle**
  (nie czyta już `position_addons.sl_trigger_price`, a nowa cena z tego ręcznego
  attachu nigdzie nie trafia do `base_entry_price`). Nie zmienione w tej sesji
  (inne narzędzie, inny cel; zmiana jego kontraktu nie była dyskutowana) — do
  rozważenia osobno.
- **Stary mechanizm (`position_addons.sl_trigger_price`, `_addon_sl_trigger_price`,
  `_update_addon_sl_trigger_price`, `OKXExecutor._compute_sl_trigger_price`) —
  zostawiony w kodzie** (schemat + metody), ale **już nieużywany** przez główną
  logikę (`reconcile_positions`/Self-Healing Guard go nie czytają;
  `execute_order` dla dokładki nie zapisuje już `sl_trigger_price`, bo w ogóle
  nie attachuje SL). Świadomie nieusunięty w tej sesji — bezpieczniejsze niż
  ryzykowna operacja usuwania pod presją czasu; do sprzątnięcia jako osobny,
  niski-ryzyko follow-up.
- **Testy** w `test_reconciliation_pnl.py`: 6 nietkniętych + 2 przepisane (SHORT
  canonical + far-from-trigger, teraz na `base_entry_price`) + 1 ze zmienionym
  tylko docstringiem (nieaktualny opis po V2) + 4 nowe (LONG canonical variant,
  `base_entry_price` przeżywa uśrednienie, samo-leczenie z 0.0, fallback na
  poziomie daemona) = 13 testów w pliku. Plus 1 nowy w `test_okx_sl_algo_instid.py`
  (Self-Healing Guard używa `base_entry_price`, NIE żywego avgPx) + 1 nowy w
  `test_midline_inactivity_tp.py` (regresja na blocker powyżej) + fix istniejącego
  `test_daemon_70_30_capital_split_calculation` (`stop_loss_pct=0.0`). Pełny suite:
  **330/330 PASS.**

### ✅ DONE — Higiena conftestu (produkcyjny risk z pkt 5, faktycznie naprawiony)

`tests/conftest.py` wymusza teraz **bezwarunkowo**, zanim jakikolwiek moduł
`mms4tk.*` zostanie zaimportowany: (a) `MMS4_DB_PATH` na izolowany katalog tymczasowy
per sesja testowa (sprzątany `atexit`), (b) całą zawartość
`okx_executor.MAINTENANCE_LOCK_PATHS` nadpisaną na jeden plik w tym samym katalogu
tymczasowym. Recenzja rundy 3 znalazła, że `set_maintenance_lock(True)` miał
OSOBNĄ, zahardkodowaną ścieżkę (`/app/.agents/swarm/MAINTENANCE_LOCK`), więc
nadpisanie listy jej nie obejmowało — naprawione (`okx_executor.py`, `enable`
też czyta `MAINTENANCE_LOCK_PATHS[0]`). Dziś żaden test nie woła
`set_maintenance_lock(True)`, więc to była luka utajona, nie aktywna, ale
komentarz w `conftest.py`/`MEMORY.md` przed tą poprawką za mocno twierdził "every
path". Uruchomienie `pytest` przez `docker exec antigravity_core` nie powinno już
dotykać `/app/.agents/swarm/logs.db` ani `/app/.agents/swarm/MAINTENANCE_LOCK` (w
żadnym kierunku, enable czy disable) — zweryfikowane: 330/330 PASS z tą zmianą.

### ✅ DONE — Follow-up cleanup po V2 (2026-09-10, worktree `chore/p3-followup-cleanup`)

Domyka 3 z 4 wcześniej odłożonych, niskopriorytetowych punktów (A/B `engine_mode`,
P4 wariant #2, P6.2 wciąż nie zaczęte — patrz niżej):

- **Martwy kod z rund 1-2 P3 usunięty:** `logger_db.py` — kolumna
  `position_addons.sl_trigger_price` przestaje być tworzona/czytana/pisana
  (`get_addon_sl_trigger_price`/`set_addon_sl_trigger_price` usunięte,
  `record_addon` wraca do 3 argumentów); `daemon.py` — `_addon_sl_trigger_price`/
  `_update_addon_sl_trigger_price` usunięte, `_record_addon` wraca do 3
  argumentów; `okx_executor.py` — `execute_order` przestaje zwracać
  `sl_trigger_price` (zachowany błąd-log gdy `attach_stop_loss` faktycznie
  zawiedzie). `_compute_sl_trigger_price` **zostaje** — nadal używane wewnętrznie
  przez `attach_stop_loss` do realnego wystawiania zleceń SL. Istniejąca w
  produkcyjnej bazie kolumna `sl_trigger_price` zostaje fizycznie w schemacie
  (SQLite nie wymaga migracji, żeby zignorować nieużywaną kolumnę) — nie
  usuwana z żywej bazy, świadomie, żeby uniknąć ryzykownej operacji DROP COLUMN
  na produkcji dla zerowej korzyści funkcjonalnej.
- **`/api/v1/trade/high_risk_add` wprowadzony do spójności V2:** ręczny dodatek
  też przestaje attachować niezależny SL (`stop_loss_pct=0.0` do
  `execute_order`, zamiast `req.stop_loss_pct`) — pole `stop_loss_pct` w
  `HighRiskAddRequest` zostaje w modelu (kompatybilność wsteczna dla starych
  wywołujących) ale oznaczone jako deprecated/ignorowane; odpowiedź raportuje
  teraz `daemon.base_sl_pct` jako realnie obowiązujący SL, nie ignorowaną
  wartość z requestu.
- **Brakujące testy dodane:** hedge-mode (long+short jednocześnie) dla
  Self-Healing Guard (`test_okx_sl_algo_instid.py`) — potwierdza, że każda strona
  dostaje WŁASNY `base_entry_price`, bez mieszania; pozycja w pełni zamknięta i
  otwarta od nowa (`test_reconciliation_pnl.py`) — potwierdza reset
  `base_entry_price` na świeżej pozycji, nie dziedziczenie starej wartości.
  Usunięte testy dla skasowanej funkcjonalności (`test_execute_order_reports_real_sl_trigger_price`,
  `test_update_addon_sl_trigger_price_*`).
- **Docs:** `docs/STRATEGY_AND_ALGORITHM.md` (tabela sygnałów — `HIGH_RISK_ADD`
  nie ma już własnego SL) i `docs/MASTERMIND_STRATEGY.md` (sekcja alokacji
  kapitału — usunięty opis "ciasnego SL 1.0%" dla dokładki, poprawiony wzór
  Max Loss) zaktualizowane pod V2. `docs/AUDIT_MMS4_PROFIT_OPTIMIZATION_2026-09-08.md`
  — sekcja P3 dostała status-owy nagłówek "ZAIMPLEMENTOWANE" z odnośnikiem do
  `task.md`; **treść historycznej diagnozy audytu celowo NIE zmieniona** (to
  zapis stanu z dnia audytu, nie żywa dokumentacja).
- Pełny suite: **329/329 PASS** (330 z poprzedniej sesji − 3 usunięte testy
  martwej funkcjonalności + 2 nowe = 329).

### 🔜 Wciąż otwarte (nie zaadresowane w tej sesji)

- **A/B `engine_mode` CLASSIC-vs-ADAPTIVE** (nigdy nie testowane, efekt 5.8× na
  BTC_full wg usera) — nie zbadane. Produkcja potwierdzona jako `ADAPTIVE`.
- **P4 wariant #2** (poprawiony: BTC przypięty na 2.0%, siatka tylko na ETH) —
  nie zaczęte.
- **P6.2** — nie zaczęte.
- Endpoint `/api/v1/trade/high_risk_add` nadal poza spójnością V2 — patrz wyżej.

---

## 🔹 Sesja A1+A2 (2026-09-10) — dwa braki wskazane przez niezależnego doradcę

Worktree/branch: `chore/session-a1-a2-fee-calibration-epoch-kpi`. Oba punkty to
**domknięcia rzeczy już zleconych przez audyt, ale nigdy nie wykonanych** — nie nowe
hipotezy. Pełny suite: **340/340 PASS** (`-m "not slow"`), plus 2 testy `slow`
z regresji P2 uruchomione osobno.

### ✅ DONE (A1) — Kalibracja `ROUND_TRIP_FEE_PCT` na realnych danych produkcyjnych

Domyka **P2(c) punkt 3** z audytu, cytat: *"Kalibracja `round_trip_cost_pct` na
realnych danych: `logger_db.get_closed_trades()` ma `fee_usd` per transakcja — nie
zgadywać, wyprowadzić z produkcji."* Do tej pory `ROUND_TRIP_FEE_PCT` było
**założeniem** (`TAKER_FEE_PCT * 2`, samo reużyte z
`inactivity_manager.DEFAULT_ROUND_TRIP_COST_PCT`), nie pomiarem.

- **Plik:** `src/mms4tk/backtest_engine.py` (stała + pełne uzasadnienie w komentarzu),
  `tests/test_backtest_engine.py` (nowy test lockujący wartość).
- **Wzór:** `SUM(fee_usd) / SUM(notional)`, gdzie
  `notional = entry_price * amount * contract_size`
  (`contract_size` wg `daemon._get_contract_size`: BTC `1e-4`, ETH `1e-3`).
  `fee_usd` w `closed_trades` to prowizja za CAŁY round-trip (open+close już
  rozliczone razem), więc jest bezpośrednio porównywalna z tą stałą — bez ×2 i bez ÷2.
- **Dane:** produkcyjne `closed_trades`, okno **2026-08-30 .. 2026-09-10** (od
  v0.41.0 — pierwsza wersja z realnym `fee_usd`; starsze wiersze mają domyślne 0.0
  i są celowo pominięte), **n = 52** (BTC 29, ETH 23).
- **Pomiar:**

  | zakres | n | Σ fee_usd | Σ notional | stawka round-trip |
  |---|---|---|---|---|
  | BTC | 29 | $1.942895 | $1943.5098 | **0.00099968** |
  | ETH | 23 | $1.585347 | $1583.7099 | **0.00100103** |
  | **BTC+ETH** | **52** | **$3.528242** | **$3527.2197** | **0.00100029** |

- **Decyzja:** stawka **łączna** (BTC+ETH). Rozrzut per-symbol to `1.35e-6`
  bezwzględnie, czyli **0.13% stawki łącznej** — o rzędy wielkości poniżej poziomu,
  na którym stała per-symbol mogłaby odwrócić jakikolwiek werdykt backtestu, i
  głęboko w szumie próbki 52 transakcji. Nowa wartość: **`ROUND_TRIP_FEE_PCT =
  0.0010003`**.
- **Wynik pomiaru w kategoriach jakości przyrządu:** pomiar **POTWIERDZIŁ**
  wcześniejsze założenie z dokładnością do **0.03% względnie** (0.00100029 vs
  0.0010). Stała jest jednak zapisana jako liczba zmierzona, nie jako
  `TAKER_FEE_PCT * 2` — żeby była **śledzalna do danych produkcyjnych**, a nie do
  założenia innej stałej. Przy zmianie progów prowizyjnych OKX trzeba ją
  **przemierzyć**, nie przeliczyć.
- **Regresja P2 przepuszczona ponownie pod skalibrowaną stawką** (bramka decyzyjna
  z audytu, `-m slow`): `test_p2_midline_tp_still_loses_all_6_windows_net_of_fees`
  i `test_p2_high_vol_base_filter_verdict_unchanged_net_of_fees` — **oba PASS**.
  **Żaden werdykt się nie odwrócił:** Midline TP nadal przegrywa **6/6**, high-vol
  base filter nadal bije baseline w **dokładnie tych samych 2/4 oknach**
  (`BTC_H2`, `ETH_H2`). Przyrząd pozostaje spójny; oba filtry NIE wracają do
  kolejki kandydatów.
- 1 nowy test (`test_round_trip_fee_pct_is_the_production_calibrated_value_not_an_assumption`)
  — lockuje wartość i jawnie zakazuje przywrócenia formy arytmetycznej z
  `TAKER_FEE_PCT`. Skrypt kalibracyjny liczony w scratchpadzie sesji, **celowo nie
  dodany do repo** (jednorazowy pomiar na danych, których w repo nie ma).

### ✅ DONE (A2) — KPI liczone od epoki (`data_collection_epoch_start`)

Problem: `LoggerDB.get_kpi_summary()` liczyło `realized_net_pnl_usd`,
`total_fees_usd`, `max_drawdown`, `average_rr` i `net_win_rate_pct` z **całej
historii bez filtrowania**, mimo że pole `data_collection_epoch_start` (per-symbol,
`system_settings`, klucz `data_collection_epoch_start:<SYMBOL>`) istnieje w
schemacie od PLAN-010 i było eksponowane **wyłącznie** w
`/api/v1/symbols/{symbol}/recalibration-status`. Skutkiem tego headline'owe KPI na
dashboardzie były zanieczyszczone transakcjami sprzed napraw P2/P3/PLAN-011.

- **`src/mms4tk/logger_db.py`:**
  - nowy, opcjonalny parametr **`get_kpi_summary(initial_equity=0.0, since=None)`**
    (ISO8601) — filtruje `closed_trades` po `timestamp >= since` **przed**
    agregacją, więc epoka obejmuje wszystko w dół: win rate, profit factor, fee,
    R:R i krzywą equity dla drawdownu.
  - nowy helper modułowy `_timestamp_at_or_after()` — porównanie **datetime**, nie
    leksykalne: wiersze `closed_trades` mają mikrosekundy i jawny offset, a epoka
    ustawiana przez operatora jest zwykle minutowa i może mieć gołe `Z`. Naiwny
    timestamp czytany jako UTC; przy nieparsowalnej wartości fallback na
    porównanie leksykalne (konwencja z `count_clean_closed_trades`), żeby zepsuty
    wiersz degradował do starego zachowania zamiast wywalać odczyt KPI.
  - **Pełna wsteczna kompatybilność:** `since=None` (default) = cała historia,
    wynik identyczny co do dicta z zachowaniem sprzed A2; `initial_equity` nadal
    działa pozycyjnie (`since` dołożone NA KOŃCU sygnatury).
  - **Świadoma konsekwencja, udokumentowana w docstringu:** przy `since` krzywa
    equity **startuje od nowa** na granicy epoki, więc `max_drawdown` to najgorszy
    peak-to-trough **wewnątrz** epoki, nie wycinek krzywej all-time. Kotwiczenie
    bieżącego reżimu w szczycie equity sprzed reżimu byłoby odpowiedzią na inne
    pytanie niż zadane.
- **`src/mms4tk/api.py`:** rozszerzony **istniejący** `/api/v1/stats`
  (i alias `/api/v1/stats/pnl`) — **bez nowego endpointu i bez ruszania żadnego
  dotychczasowego pola**. Wszystkie pola top-level zostają **all-time**, więc żaden
  istniejący konsument nie zmienia liczby. Dołożony blok `since_epoch`
  (nowy model `EpochKpiBlock` w `PnLResponse` — konieczny, bo `response_model`
  odfiltrowałby surowy dict) z: `epoch_start`, `per_symbol_epochs` oraz kompletem
  KPI liczonych od epoki.
  - Dwa nowe helpery: `_active_symbol_epochs()` (epoki tylko **aktywnych** symboli
    — SOL/ICP/XRP są WSTRZYMANE, patrz GATE 2026-09-10, i epoka symbolu
    wstrzymanego nie może cofnąć okna raportowania w prehistorię) oraz
    `_earliest_epoch()` (**najwcześniejsza**, nie najpóźniejsza — symbol, któremu
    epoki nigdy nie przesunięto, nadal handluje całą swoją historią, więc cięcie po
    najpóźniejszej ukryłoby jego transakcje; wartość nieparsowalna sortuje się na
    koniec i nigdy nie wygrywa `min()`).
  - Gdy **żaden** aktywny symbol nie ma ustawionej epoki: blok jest obecny z
    `epoch_start: null` i lustrzanym odbiciem liczb all-time — stabilny kształt
    odpowiedzi bije pole, które pojawia się i znika pod dashboardem.
- **10 nowych testów** w nowym pliku `tests/test_epoch_kpi.py`: 5 bezpośrednio na
  `LoggerDB.get_kpi_summary(since=...)` (filtrowanie wszystkich agregatów, restart
  krzywej drawdownu, inkluzywność granicy `>=`, mieszane formaty timestampów,
  pusty wynik po epoce), 1 regresja wstecznej kompatybilności (brak parametru ≡
  `since=None`, plus `initial_equity` pozycyjnie) i 4 end-to-end przez `TestClient`
  (blok obecny obok all-time, fallback bez epoki, alias `/stats/pnl` niesie ten sam
  blok, odporność `_earliest_epoch` na śmieci).

---

## ✅ Następny krok

**Wszystkie 6 ticketów Etapów 0-3 (P9, P7, P2, P1, P4-krok-0, P6.1) zaimplementowane,
przetestowane, po trzech turach niezależnej recenzji (Opus) i zmergowane do `main`**
(PR #24/#26/#27/#30/#28/#29). P3 (Etap 4) zaimplementowane w tej sesji, worktree
`feature/p3-ssot-position-sl`, jeszcze przed PR-em — 2 rundy niezależnej recenzji
(Opus) na pierwszym mechanizmie (composite trigger price + tolerancja), po czym
**zastąpione architektonicznie przez V2** (SL bazy jako jedyny, stały SSOT —
`base_entry_price`) na jawne polecenie usera, GATE przez dashboard-check
przeprowadzony i domknięty. `state_manager.py:344` naprawione jako efekt uboczny
V2, bez osobnej zmiany w `state_manager.py` — **ticket "P3.1" jest tym samym
nieaktualny/domknięty**, nie osobny follow-up. Higiena `conftest.py` (produkcyjny
risk uruchomienia pytest na VPS) też naprawiona w tej samej sesji. **V2 jeszcze
NIE przeszło niezależnej recenzji** — to duża zmiana architektoniczna
(egzekucja zleceń, self-healing, klasyfikacja stop-outu), zalecana kolejna runda
przed PR-em. Otwarte, nie zaczęte: A/B `engine_mode` CLASSIC-vs-ADAPTIVE, P4
wariant #2, P6.2 (patrz sekcja wyżej). Ten dokument wymaga też jednorazowego
porządkowania (kolejność sekcji nie odpowiada kolejności etapów — Etap 1 → Etap
3cz.2 → Etap 2 → Etap 3cz.1 → Etap 4 → Etap 5 — bo tak scaliły się branch'e; treść jest
poprawna, tylko kolejność wizualna nielogiczna).

---

## 🔹 Sesja A3: długa historia 1h jako nowe fixture'y (2026-09-11)

Worktree `chore/session-a3-long-history-fixtures`. Zadanie: zbudować 24-miesięczną
historię 1h dla BTC/ETH jako nowe fixture'y, **bez ruszania istniejących**
(`ohlcv_BTC_USDC_1h.csv` / `ohlcv_ETH_USDC_1h.csv` — nietknięte, dalej używane
przez testy). Powód: cała dotychczasowa walidacja (P1-P4, A/B) stoi na JEDNYM
oknie 90 dni (2160 świec/symbol), co daje ~±20 pp 90% przedziału ufności.

### ⚠️ Ustalenie blokujące: 24 miesięcy BTC/USDC na OKX NIE MA

`fetch_candles_paginated("BTC/USDC", ...)` — ta sama ścieżka, której używa produkcja
— rozwiązuje się w ccxt do **instrumentu SPOT `BTC-USDC`** (nie swap; `defaultType:
swap` nie zmienia notacji bez sufiksu `:USDC`, a `BTC/USDC:USDC` w ogóle nie
istnieje w markets OKX). `listTime` tego instrumentu to **2025-08-21T08:00:00Z** dla
BTC-USDC i ETH-USDC — czyli maksymalna dostępna głębokość to ~386 dni, nie 730.
Żądanie `days=730` zwracało **0 świec** (pierwsza strona paginacji ląduje przed
listingiem → pusta → pętla produkcyjna kończy na pierwszej pustej stronie).
Zweryfikowane niezależnie: `/api/v5/public/instruments` (SPOT BTC-USDC listTime =
2025-08-21T08:00Z; `instId=BTC-USDC-SWAP` **nie istnieje**) i binary search po
`days` (granica 398 dni, obie monety identycznie).

Dlatego zamiast jednego kompletu powstały **dwa**, a plik nie nazywa się `_24m`,
jeśli nie ma w nim 24 miesięcy:

| plik | świece | zakres (UTC) | span |
|---|---|---|---|
| `tests/fixtures/ohlcv_BTC_USDC_1h_max.csv` | **9216** | 2025-08-22T23:00 .. 2026-09-10T22:00 | 383.96 d |
| `tests/fixtures/ohlcv_ETH_USDC_1h_max.csv` | **9216** | 2025-08-22T23:00 .. 2026-09-10T22:00 | 383.96 d |
| `tests/fixtures/ohlcv_BTC_USDT_1h_swap_24m.csv` | **17520** | 2024-09-10T23:00 .. 2026-09-10T22:00 | 729.96 d |
| `tests/fixtures/ohlcv_ETH_USDT_1h_swap_24m.csv` | **17520** | 2024-09-10T23:00 .. 2026-09-10T22:00 | 729.96 d |

- `_max` = pełna dostępna głębokość **dokładnie tej serii, której używa produkcja**
  (BTC/USDC, ETH/USDC spot). 4,27× więcej świec niż stare okno 90-dniowe.
- `_swap_24m` = `BTC/USDT:USDT` / `ETH/USDT:USDT` (swap USDT, listing 2019) —
  **jedyna** seria na OKX z realną 24-miesięczną głębokością. To **proxy**, nie ten
  sam instrument; wolno go używać dokładnie na tych samych prawach, na jakich cała
  produkcja używa BTC/USDC jako proxy dla realnie handlowanego X-Perp (patrz
  docstring `fetch_candles_paginated`) — ale **musi być tak nazwane w każdym
  wyniku**, nigdy jako "dane instrumentu produkcyjnego".
  Zmierzona rozbieżność proxy vs produkcyjna seria na 9216 wspólnych świecach:
  **korelacja zwrotów 1h = 0.9614 (BTC) / 0.9648 (ETH)**, mediana |spread cen| =
  0.055% (BTC) / 0.064% (ETH), max 0.75% / 1.22%.

### Weryfikacja jakości danych (policzona, nie założona)

1. **Liczba świec:** dokładnie zgodna z oczekiwaniem — 9216 = 384 d × 24 (po
   przycięciu do listingu), 17520 = 730 d × 24. Zero brakujących.
2. **Dziury:** **0 przerw > 6 h** we wszystkich 4 plikach. Mocniejszy check też
   przeszedł: **każdy krok to dokładnie 3 600 000 ms**, 0 duplikatów timestampu,
   0 miejsc nierosnących.
3. **Ciągłość ze starym oknem:** stare okno (2026-05-30T17:00 .. 2026-08-28T16:00,
   2160 świec) mieści się **w całości** wewnątrz wszystkich 4 nowych plików; 0 starych
   timestampów nieobecnych w nowych. Dodatkowo policzona identyczność wartości na
   części wspólnej z `_max`: **2159/2160 świec bit-identycznych** (OHLCV + volume).
   Jedyna różnica to **ostatnia świeca starego fixture'a** (2026-08-28T16:00) —
   stara ma close 77887.1 / vol 107.10628945, nowa 77790.0 / 107.80341082 (ETH
   analogicznie): to świeca złapana w trakcie formowania przy generowaniu starego
   pliku. Nowe dane są więc **ścisłym supersetem**, nie osobnym segmentem.
4. **Regresja zero:** `python3 -m pytest -q -m "not slow"` → **329 passed, 4
   deselected** — identycznie jak baseline przed tą sesją. Dodanie plików nic nie
   zepsuło (żaden istniejący test ich nie widzi).

### Czy 24 miesiące odblokowują P5 i P8? (policzone realnym silnikiem, nie szacunkiem)

`scripts/fixture_power_analysis.py` tnie fixture na kolejne, **nienachodzące** okna
121 d (~4 mies.) i puszcza na każdym prawdziwy `run_backtest()` z produkcyjnymi
parametrami (ADAPTIVE, VR 1.6/0.8, `stale_candles_threshold=12`, trend filter off).
Strefa zmienności i sesja UTC są kubełkowane po świecy WYJŚCIA (`exit_idx` to
jedyny indeks, który zwraca `run_backtest`) — przybliżenie kubełka wejścia, nie jego
dokładna reprodukcja.

- **24 mies. dają 6 pełnych niezależnych okien 121-dniowych** (730 / 121.7 = 6.0);
  seria `_max` (384 d) daje 3 takie okna.
- **Realna częstotliwość: ~1.79 txn/dzień (BTC) i ~1.94 txn/dzień (ETH)** na
  `_swap_24m` (odpowiednio 1300 i 1406 transakcji w 24 mies., w tym 870/853 pełnych
  domknięć). Na `_max`: 1.98 (BTC) / 2.14 (ETH) txn/dzień. Zakładane w poprzednim
  raporcie ~2 txn/dzień/symbol **potwierdzone empirycznie**.
- **~187-266 transakcji na okno 4-miesięczne na symbol** (min 189, max 266 w 12
  oknach BTC+ETH na `_swap_24m`).

**P8 (analiza sesyjna, ~30 txn/komórkę): ODBLOKOWANE.** Najsłabsza komórka sesyjna
(ASIA 00-08 / EU 08-16 / US 16-24 UTC) w **każdym** z 6 okien i dla obu symboli to
51-83 transakcji — z zapasem ≥1.7× nad progiem 30. Nawet pojedyncze 4-miesięczne
okno wystarcza; 24 mies. dają 6 niezależnych powtórzeń tego samego testu.

**P5 (parametry per strefa zmienności, ~19 txn/strefę × 3 strefy): ODBLOKOWANE
TYLKO POOLOWANE PO CAŁYCH 24 MIES., NIE PER OKNO 4-MIESIĘCZNE.** Wiążącym
ograniczeniem jest strefa **HIGH (VR > 1.6)** — z definicji rzadka:
- per okno 121 d, strefa HIGH: BTC `[23, 20, 9, 23, 14, 23]`, ETH `[16, 29, 19, 16,
  16, 19]` → **próg 19 nie jest spełniony w 2/6 okien BTC i 4/6 okien ETH.**
- pooled po całych 24 mies.: HIGH = **112 (BTC) / 115 (ETH)**, NORMAL = 932/1044,
  LOW = 256/247 → wszystkie 3 strefy z dużym zapasem nad 19; łącznie dla obu
  symboli HIGH = **227**.
- Na `_max` (384 d, okna 122 d) strefa HIGH wypada `[35, 32, 27]` (BTC) i
  `[31, 37, 28]` (ETH) → per okno **PASS**, ale to tylko 3 okna i inny (nowszy,
  bardziej zmienny) reżim rynkowy — nie zastępuje 6 okien z `_swap_24m`.

**Wniosek operacyjny:** P8 można prowadzić z walidacją out-of-sample okno-po-oknie.
P5 wymaga albo poolowania po całych 24 miesiącach (wtedy tracimy niezależne okna
OOS na strefie HIGH), albo podziału na 2 połowy po 12 mies. (HIGH ≈ 56/symbol na
połowę — dalej ≥19), albo poolowania BTC+ETH razem. Per-okno-4-miesięczne testowanie
strefy HIGH **nadal nie jest statystycznie możliwe** i nie należy go obiecywać.

### Narzędzia (odtwarzalne, zostawione w repo)

- `scripts/fetch_ohlcv_fixture.py` — pobiera historię **produkcyjną ścieżką**
  (`OKXExecutor.fetch_candles_paginated`, PLAN-010 §4.1a, bez poświadczeń — publiczne
  dane rynkowe), zapisuje CSV w identycznym formacie co istniejące fixture'y,
  domyślnie **nie nadpisuje** istniejącego pliku (`--force` wymagane) i **przycina
  okno do `listTime` instrumentu**, zamiast po cichu zwrócić 0 wierszy. Sam raportuje
  liczbę świec, zakres, dziury > 6 h, duplikaty i niemonotoniczność.
- `scripts/fixture_power_analysis.py` — powyższa analiza mocy (txn/okno, per strefa,
  per sesja, bramki P5/P8).

Odtworzenie:
```bash
PYTHONPATH=src python3 scripts/fetch_ohlcv_fixture.py --symbols BTC/USDC ETH/USDC --days 730 --suffix _max
PYTHONPATH=src python3 scripts/fetch_ohlcv_fixture.py --symbols BTC/USDT:USDT ETH/USDT:USDT --days 730 --suffix _swap_24m
PYTHONPATH=src python3 scripts/fixture_power_analysis.py --window-days 121 \
  --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
```

### Czego ta sesja NIE zrobiła

Zero zmian w `src/` i zero zmian w istniejących testach — to sesja danowa. Żaden
istniejący werdykt (P1-P4, A/B) **nie został przeliczony** na nowych danych; nowe
fixture'y są dostępne, ale dopóki ktoś tego nie zrobi, wszystkie dotychczasowe
wnioski dalej stoją na jednym oknie 90-dniowym z ~±20 pp przedziałem ufności.

---

## 🔹 Etap 5: Raport strategiczny (Opus) — czy w ogóle zarabiamy, co dalej

### 📊 Rekomendacja strategiczna (2026-09-10) — zlecona po pytaniu usera "chcę w końcu zarabiać"

Niezależny doradca (Opus, nowa tura tej samej sesji, zero zmian w repo — czyste
zadanie analityczne) dostał zadanie: ocenić rentowność strategii i
spriorytetyzować pozostałe tickety audytu (A/B `engine_mode`, P4#2, P6.2, P5, P8)
pod kątem realnego wpływu na PnL. Metodologia: backtest na 90-dniowych fixture'ach
(2026-05-30 → 2026-08-28), produkcyjna konfiguracja, netto z modelem P2 (wtedy
jeszcze niekalibrowanym z produkcji — patrz Etap 5, sekcja A1 niżej).

**Werdykt:** strategia **nie ma dziś wykazanego edge'a** — problem strukturalny
(koszty ≈ zysk brutto), nie brak danych. Atrybucja PnL po typie wyjścia: cały
edge siedzi w `TP_AND_FLIP` (dotknięcie przeciwnej wstęgi, +1,25%/txn, tylko 13%
zdarzeń); 75% aktywności (Inactivity: `FORCE_CLOSE`+`STALE_SPLIT_50`) generuje
gross dodatni ale **netto ujemny** po kosztach. Koszt = 94% grossu na BTC. Moc
statystyczna: żeby wykazać istotność dzisiejszego "edge'a" na BTC (t=2) trzeba
**~7000 transakcji ≈ 9 lat** przy obecnej częstotliwości (~2/dzień/symbol) — stąd
Etap 5 sekcja A3 (długa historia) niżej.

**Ważne zastrzeżenie usera, przyjęte:** doradca w pierwszej wersji wliczył koszt
utrzymania VPS do rachunku rentowności — **odrzucone jako błędna rama**, bo ten
VPS hostuje kilkanaście niezwiązanych projektów (n8n×3, budzet-*,
portfolio-sentinel, hermes-agent, osint-lead-tracker, linkedin_tracker_app) —
przypisanie mu pełnego kosztu do PnL mms4tk to nie jest analiza kosztu
krańcowego. Reszta wniosków (poniżej) nie zależy od tego punktu i zostaje w mocy.

**Trzy tickety zmierzone i zamykane teraz** (patrz sekcje niżej, każdy z
konkretnymi liczbami z tego pomiaru):

#### ✅ ZAMKNIĘTE — A/B `engine_mode` CLASSIC-vs-ADAPTIVE: brak sygnału

6 okien (BTC/ETH × H1/H2/full), produkcyjne VR 1.6/0.8, netto:

| okno | CLASSIC | ADAPTIVE | ratio | txn C→A |
|---|---|---|---|---|
| BTC_H1 | +4,55% | −1,84% | −0,41 | 83→80 |
| BTC_H2 | +2,16% | +4,62% | 2,14 | 101→92 |
| **BTC_full** | **+4,73%** | **+0,81%** | **5,84** | 189→177 |
| ETH_H1 | −3,78% | −6,73% | 1,78 | 98→96 |
| ETH_H2 | −4,37% | −3,09% | 0,71 | 81→74 |
| ETH_full | −6,27% | −7,95% | 1,27 | 183→174 |

Liczba "5,8× na BTC_full" (z pamięci usera) się zgadza co do wartości —
**ale znak jest odwrócony**: to CLASSIC jest 5,8× lepszy od ADAPTIVE, nie
odwrotnie, a produkcja stoi na ADAPTIVE. **Mimo to NIE przełączać na CLASSIC** —
`ADAPTIVE` różni się mechanicznie tylko blokadą ~6% wejść (177 vs 189 txn na
BTC), więc krotność 5,8× to artefakt dzielenia dwóch liczb bliskich zeru przy
90% CI ±20pp (pkt Etap 5/A3 niżej — te CI zawężą się po długiej historii).
CLASSIC ≥ ADAPTIVE w 4/6 okien netto i 5/6 na R:R — kierunkowo spójne, ale
całkowicie w szumie przy obecnej ilości danych. **Status: zamknięte jako
"zmierzone, brak rozstrzygającego sygnału". Produkcja zostaje na `ADAPTIVE`.**
Do ponownego sprawdzenia dopiero po Etapie 5/A3 (długa historia), nie wcześniej.

#### ✅ ZAMKNIĘTE — P4 wariant #2 (SL per-symbol): hipoteza SFALSYFIKOWANA

Siatka `base_sl_pct` ∈ {1.5, 2.0, 2.5, 3.0, 3.5}%, netto, 2 połowy OOS + full:

| SL% | BTC full | ETH H1 | ETH H2 | **ETH full** | ETH SL-exits |
|---|---|---|---|---|---|
| 1,5 | −3,08% | +1,16% | −13,22% | −12,63% | 37 |
| **2,0 (dziś)** | **+0,81%** | −6,73% | −3,09% | **−7,95%** | 26 |
| 2,5 | +2,50% | −8,96% | −7,28% | −13,90% | 24 |
| 3,0 | +1,44% | −10,41% | −7,11% | −14,73% | 23 |
| 3,5 | +0,52% | −10,51% | −9,09% | −16,37% | 20 |

**Na ETH poszerzanie SL monotonicznie pogarsza wynik netto w OBU połowach OOS
niezależnie** (H1: −6,73→−10,51; H2: −3,09→−9,09), mimo malejącej liczby
stop-outów (26→20) — mechanizm: szerszy stop nie zamienia SL w `TP_AND_FLIP`,
tylko utrzymuje przegrane pozycje dłużej, żeby przegrały więcej. Pomiar
`P4-krok-0` (SL/ATR: 2,98 ETH vs 4,02 BTC) był poprawny jako pomiar, ale
**hipoteza przyczynowa (szerszy SL na ETH pomoże) się nie potwierdza** — ten sam
wzorzec co Midline TP: sensowna arytmetyka, empirycznie szkodliwa. Na BTC 2,0-2,5%
to płaskowyż bez wyraźnej przewagi. **Status: ODRZUCONE. `base_sl_pct=2.0%`
zostaje niezmienione dla obu symboli. Wariant #3 (`SL=k·ATR`) tym bardziej nie ma
podstaw i nie jest rekomendowany.**

#### ✅ ZAMKNIĘTE — P6.2 (dobór progów Inactivity): efekt zerowy

Ablacja na pełnych 90 dniach, netto (baseline = produkcja: stale=12, impulse
negation on, profit stall gate on):

| wariant | BTC net | ETH net |
|---|---|---|
| **produkcja** | **+0,81%** | **−7,95%** |
| `stale=6` | −0,41% | −6,38% |
| `stale=24` | −0,11% | −8,03% |
| `stale=48` | −0,11% | −7,99% |
| bez negacji impulsu | +15,37% | **−18,26%** |
| INACTIVITY OFF (całkowicie) | +0,66% | −32,97% |

`stale_candles_threshold` (6/12/24/48) nie ma znaczenia — rozrzut 1-2pp przy CI
±20pp z jednego okna. Negacja impulsu daje **przeciwne znaki** na BTC (+15,4pp)
i ETH (−18,3pp) — największy efekt w całej ablacji i klasyczny podpis
przeuczenia na pojedynczym oknie — **nie ruszać**. Argument z audytu §1.8 (próg
12 świec płaci 2 naliczenia fundingu) jest arytmetycznie prawdziwy, ale funding
to tylko 1,53% z 12,37% kosztu na BTC — nie ten kanał. **Status: ODRZUCONE, nie
uruchamiać siatki kalibracyjnej 5×3×2 z 2-way OOS dla efektu, którego nie da się
zmierzyć na tej ilości danych.**

#### 🔜 Odłożone (nie odrzucone) — czeka na długą historię (Etap 5/A3)

- **P5 (`volatility_zone`)** — dobra hipoteza, zła metoda dowodowa: 2-tygodniowy
  termin z audytu daje ~19 txn/strefę, za mało żeby cokolwiek rozstrzygnąć.
  Logowanie (prerekwizyt P9.4) zostaje włączone, ale bramka decyzyjna przenosi
  się na długą historię.
- **P8 (sesyjność)** — siatka 5 dni × 24h przy obecnej częstotliwości daje ~1
  txn/komórkę; bramka "≥30 txn/komórkę" z audytu wymaga ~3600 transakcji ≈ 2,5
  roku. Niewykonalne na obecnych danych, automatycznie odblokowuje się po
  Etapie 5/A3 (patrz tam dokładne przeliczenie). Kalendarz makro (CPI/FOMC)
  odrzucony jako osobna zależność operacyjna nieuzasadniona przy alokacji $100.

#### 🆕 Nowe priorytety, których audyt nie miał (ważniejsze niż jego oryginalna lista)

1. **Kalibracja realnej stawki prowizji z produkcji** (P2(c) punkt 3, nigdy
   niezrealizowany) — ✅ zrobione, patrz sekcja "Sesja A1+A2" wyżej.
2. **KPI netto liczone od epoki** (`data_collection_epoch_start` istnieje w
   schemacie, nigdy nieużywane do filtrowania `get_kpi_summary()`) — dzisiejszy
   dashboardowy "39% winrate" miesza dane sprzed napraw P2/P3. ✅ zrobione, patrz
   sekcja "Sesja A1+A2" wyżej.
3. **Egzekucja 100% `market`/taker, zero `postOnly`/maker** (`okx_executor.py`,
   `execute_order` + zamknięcia) — przeliczenie tej samej populacji transakcji na
   stawkę maker (0,02% vs dzisiejsze 0,05%/leg taker) daje **BTC netto +0,81% →
   +7,31%**. Jedyna dźwignia w projekcie działająca deterministycznie (arytmetyka
   kosztu, nie hipoteza rynkowa) — kandydat do PoC w Etapie 6 (Sesja B), po
   domknięciu Etapu 5.
4. **ETH nie wykazał edge'a w ~30 zmierzonych konfiguracjach** (wszystkie
   powyższe tabele + dodatkowy test timeframe 2h/4h, negatywny, patrz niżej) —
   kandydat do zawieszenia w Etapie 6 (Sesja B), warunkowany wynikiem długiej
   historii.
5. Przy $100 kapitału realna skala projektu to dziś platforma R&D/harness
   poprawnościowy, nie źródło dochodu — sam ten fakt nie blokuje pracy, ale
   uczciwie ramuje oczekiwania na obecnym etapie skali.

**Sprawdzone i odrzucone przy okazji (negatywny wynik, zapisany żeby nikt nie
proponował tego ponownie):** zmiana interwału świec 1h→2h/4h — średni ruch na
transakcję faktycznie rośnie zgodnie z hipotezą, ale stosunek SL:TP_flip psuje
się drastycznie na wyższych TF (BTC 4h: 16 SL na 9 TP vs 1h: 22 SL na 23 TP) →
gross się załamuje na obu symbolach. Nie realizować.

### Sesja A — status

- **✅ A1 + A2 DONE, zmergowane (PR #34, `09dc681`, v0.44.5).** Zlecone agentowi
  Opus, przejrzane przeze mnie samodzielnie (diff + niezależne ponowne
  uruchomienie 340 szybkich testów + 2 wolnych testów regresji P2, oba PASS) —
  bez dodatkowej pełnej rundy adwersarialnej (niskie ryzyko: zmiana czysto
  addytywna, nie dotyka egzekucji zleceń). Wyniki: `ROUND_TRIP_FEE_PCT` = pomiar
  z produkcji, 0.0010003 (potwierdza starą wartość ±0.03%), żaden werdykt P2 się
  nie odwrócił; `since_epoch` w `/api/v1/stats`. Szczegóły: sekcja "Sesja A1+A2"
  wyżej w tym dokumencie.
- **🔜 A3 w trakcie** (worktree `chore/session-a3-long-history-fixtures`): budowa
  24-miesięcznej historii 1h (BTC/ETH) przez produkcyjną
  `OKXExecutor.fetch_candles_paginated` (publiczne API OKX, potwierdzony
  bezpośredni dostęp do internetu z tego środowiska), nowe fixture'y
  `ohlcv_{BTC,ETH}_USDC_1h_24m.csv`, stare zostają nietknięte. **Wynik do
  uzupełnienia po przeglądzie.**
- **✅ A4 (ten wpis) DONE** — zamknięcie 3 ticketów powyżej.

Po przeglądzie A1-A3 (przez nadrzędną sesję, nie automatycznie) — Sesja B:
zawieszenie ETH (warunkowane wynikiem A3), PoC egzekucji maker-first (z twardą
bramką rollbacku: fill-rate &lt;80% = natychmiastowy powrót do `market`), powtórka
całej ablacji na długiej historii ze skalibrowaną stawką z A1.

### Czego świadomie NIE robić teraz

Nie ruszać progów Inactivity, `engine_mode`, `base_sl_pct`, nie budować filtra
sesyjnego, nie skalować alokacji, nie wracać do Midline TP ani high-vol filtra
(dopóki A1 nie odwróci ich werdyktu — jeśli odwróci, to osobny, ważny temat, nie
cichy fakt do zignorowania).
