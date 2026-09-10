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

**Kolejność dalej wg audytu §5 (dla orientacji, nie zlecone jeszcze):** Etap 3 = P4 krok 0 (pomiar ATR%/SL per symbol) + P6.1 (naprawa buga persystencji) → Etap 4 = warunkowe (P3/P4#2/P6.2) → Etap 5 = P5 krok 0 (logowanie `volatility_zone`) → Etap 6 = P8 (analiza sesyjna).

---

## ✅ Następny krok

P9, P7, P2, P1, P6.1 i P4-krok-0 zaimplementowane. PR-y #28/#29/(ten) do
sprawdzenia/merge. Decyzja o P4 wariancie #2 (per-symbol SL grid) — czeka na
osobne polecenie, bo to głębsza zmiana doktryny.
P9, P7, P2 i P1 zaimplementowane, przetestowane, PR-y otwarte/zmergowane. P1
zakończyło się wynikiem "V0 zostaje" (gate nie przeszedł) — to prawidłowe,
kompletne zamknięcie ticketu, nie odłożenie. Etap 3 (P4 krok 0 + P6.1) czeka na
osobne polecenie.
