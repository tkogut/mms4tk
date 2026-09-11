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

### ✅ ZAMKNIĘTE 2026-09-11 (patrz „P1 ZAMKNIĘTE FORMALNIE" na końcu tej sekcji) — historia niżej zostaje jako zapis, ale bramka „V0 vs V1 vs V2" jest nieaktualna

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

### ✅ P1 ZAMKNIĘTE FORMALNIE (2026-09-11, worktree `chore/harness-v2-default-and-p1-closure`)

**Powód zamknięcia: produkcja została zmieniona niezależnie (P3 V2), nie dlatego że
któryś wariant „wygrał" w sensie audytowym.** To rozróżnienie jest istotne i nie
wolno go później zgubić:

- Pierwotna bramka P1 („V1 albo V2 musi bić V0 na R:R **i** total_pnl netto w ≥5/6
  okien") **nigdy nie została przeszła** — V1 = 3/6, V2 = 3/6 strict. Ta bramka jest
  dziś **nieaktualna/zdezaktualizowana**, bo pytanie, na które odpowiadała („czy
  warto zmienić produkcyjny mechanizm SL dokładki na podstawie wyniku PnL"),
  przestało istnieć: P3 V2 (2026-09-10, v0.44.4+) zmieniło ten mechanizm z **innego,
  niezależnego powodu — determinizmu egzekucji** (ta sama pozycja mogła dostać dwie
  różne ceny SL zależnie od tego, która ścieżka kodu attachowała ostatnia), a **nie**
  optymalizacji PnL. Żaden wynik backtestu nie był podstawą tej decyzji i nie wolno
  go retroaktywnie tak przedstawiać.
- Wariant, który produkcja realizuje po P3, to dokładnie to, co harness nazywa
  **V2**. Zweryfikowane 1:1 przeciwko trzem produkcyjnym miejscom (`daemon.py`:
  `reconcile_positions` — klasyfikacja od `base_entry_price * base_sl_pct`;
  Self-Healing SL Guard — re-attach przez `_base_entry_price(...)`, nigdy żywe avgPx;
  re-attach po Midline TP — ta sama kotwica; gałąź HIGH_RISK_ADD — `execute_order(...,
  stop_loss_pct=0.0, ...)`, czyli dokładka bez własnego SL, bo istniejące algo ma
  `closeFraction="1"`). Formuła ceny triggera to ta sama
  `okx_executor._compute_sl_trigger_price` (`ref*(1−pct)` long / `ref*(1+pct)` short).
  **Rozbieżności nie było — harness V2 zgadzał się z produkcją co do formuły,
  kotwicy i zakresu (cała pozycja).** Poprawki wymagał wyłącznie **domyślny wariant**
  (patrz niżej), nie sama implementacja V2.
- **Zmieniony domyślny `addon_sl_variant`: `"V0"` → `"V2"`** w `run_backtest()`
  (`src/mms4tk/backtest_engine.py`). Do 2026-09-11 harness domyślnie modelował
  mechanizm, którego produkcja **już nie używa** — czyli P4 (siatka SL), A/B
  `engine_mode`, cała ablacja z Etapu 5 i sama bramka P1 mierzyły retired V0.
  Kierunek problemu z P1 się odwrócił: audyt mówił „przywrócenie zapisanej
  specyfikacji, którą implementacja cicho porzuciła" — teraz to **harness musiał
  dogonić produkcję**.
- **Wpływ defaultu, zmierzony naprawdę** (6 okien, produkcyjne VR 1.6/0.8, netto,
  bez filtra trendu — baseline identyczny jak w tabelach Etapu 5):

  | okno | V0 total_pnl | V2 total_pnl | V0 R:R | V2 R:R | V0 SL-exits | V2 SL-exits | dokładki |
  |---|---|---|---|---|---|---|---|
  | BTC_H1 | −1,84% | **+0,62%** | 1,3234 | 1,3984 | 13 | 11 | 17 |
  | BTC_H2 | +4,62% | +3,64% | 1,3331 | 1,5656 | 8 | 6 | 30 |
  | BTC_full | +0,80% | **+4,14%** | 1,2630 | 1,4770 | 22 | 18 | 46 |
  | ETH_H1 | −6,74% | **−0,02%** | 1,3120 | 1,2892 | 17 | 15 | 12 |
  | ETH_H2 | −3,09% | −3,09% | 0,7343 | 0,7343 | 9 | 9 | 16 |
  | ETH_full | −7,95% | **−1,24%** | 1,0339 | 1,0962 | 26 | 24 | 28 |

  Efekt **nie jest kosmetyczny**: szerszy, przypięty SL zdejmuje 2-4 stop-outy na
  okno i przesuwa netto o kilka pp. **Konsekwencja do zapisania wprost:** liczby w
  tabelach Etapu 5 (A/B `engine_mode`, siatka P4 `base_sl_pct`, ablacja P6.2) są
  liczone pod V0 i **nie odpowiadają dzisiejszej produkcji**. Nie unieważnia to
  automatycznie ich werdyktów (wszystkie trzy były „brak sygnału / odrzucone",
  a kierunki są mierzone wewnątrz tego samego wariantu), ale **każde ich ponowne
  użycie jako liczby bezwzględnej wymaga przeliczenia pod V2** — w szczególności
  konkluzja „strategia nie ma dziś wykazanego edge'a" była mierzona na retired
  mechanizmie SL. Przeliczenie całej ablacji = osobne zlecenie, nie ta sesja.
- **Werdykty regresyjne pod nowym defaultem: ŻADEN SIĘ NIE ODWRÓCIŁ.**
  `test_p2_midline_tp_still_loses_all_6_windows_net_of_fees` (Midline TP nadal
  przegrywa 6/6) i `test_p2_high_vol_base_filter_verdict_unchanged_net_of_fees`
  (high-vol filter nadal bije baseline dokładnie w `BTC_H2` + `ETH_H2`) — oba PASS
  po zmianie defaultu, bez modyfikacji asercji. Silnik rekalibracji (P7,
  `train_test_validate`/`run_calibration_job`/`_grid_search_train`) korzysta teraz z
  V2 we wszystkich wywołaniach `run_backtest()` bez jawnego wariantu; regresja
  `test_real_btc_90day_data_does_not_validate_with_small_grid` nadal NIE zwraca
  `VALIDATED`.
- `test_p1_addon_sl_gate_analysis` (`@pytest.mark.slow`) **zostaje bez zmian** —
  woła wszystkie trzy warianty jawnie, więc jest odporny na zmianę defaultu i nadal
  lockuje historyczne liczby bramki (V1 3/6, V2 3/6 strict; 5/6 i 4/6 pnl-only).
  Jego znaczenie zmienia się z „bramki decyzyjnej" na **zapis historyczny**.
- Testy: `test_addon_uses_tighter_composite_sl` przepisany na parę (default **nie**
  zacieśnia SL / jawne `V0` zacieśnia), plus 2 nowe: default ≡ jawne `"V2"`
  bit-w-bit oraz zgodność ceny triggera z produkcyjną
  `OKXExecutor._compute_sl_trigger_price(side, base_entry_price, base_sl_pct)` (LONG
  i SHORT, przeciwko zaimportowanej funkcji produkcyjnej, nie skopiowanemu
  literałowi).

**Stan wg audytu §5, po zmergowaniu P1/P4-krok-0/P6.1 (ten dokument, poniżej):**
Etap 0 (P9+P7), Etap 1 (P2), Etap 2 (P1 — **zamknięte formalnie 2026-09-11**:
harness domyślnie modeluje teraz produkcyjne V2, pierwotna bramka
"V0 zostaje"/eskalacja z PR #28 jest zdezaktualizowana przez P3, nie rozstrzygnięta),
Etap 3 (P4 krok 0 + P6.1) — **wszystkie zamknięte**. Dalej: Etap 4 = warunkowe (P3/P4#2/P6.2) → Etap 5 = P5 krok 0
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
  **nieaktualne**: zmierzone i zamknięte w Etapie 5 (siatka na obu symbolach),
  a następnie **przeliczone na 730 dniach pod V2** (2026-09-11) — patrz Etap 5,
  sekcja „P4 wariant #2 PRZELICZONY NA DŁUGIEJ HISTORII POD V2".
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

> ⚠️ **Powyższa tabela jest liczona pod retired `addon_sl_variant="V0"`** (harness
> przełączył default na produkcyjne V2 dopiero 2026-09-11, patrz Etap 2 / „P1
> ZAMKNIĘTE FORMALNIE") **i na JEDNYM 90-dniowym oknie.** Przeliczenie pod V2 na
> 24-miesięcznej historii: sekcja „Faza 2a" bezpośrednio niżej. Werdykt się **nie
> zmienia**, ale liczby bezwzględne z tej tabeli są nieaktualne — nie cytować ich
> jako dzisiejszego stanu.

#### 🔁 FAZA 2a (2026-09-11) — A/B `engine_mode` przeliczone: 24 mies. historii, mechanizm SL V2

Worktree/branch `chore/phase2a-engine-mode-long-history`. **Zadanie czysto
analityczne — zero zmian w `src/`.** Powód przeliczenia: obie przesłanki
poprzedniego pomiaru upadły. (a) Tamta tabela mierzyła `addon_sl_variant="V0"`,
mechanizm SL wycofany z produkcji przez P3 V2 (2026-09-10); harness domyślnie
liczy dziś V2. (b) Tamten pomiar to 6 okien wykrojonych z JEDNEGO 90-dniowego
fixture'a (~±20 pp CI), a od sesji A3 są fixture'y 730-dniowe.

**Konfiguracja pomiaru** (`scripts/ab_engine_mode_analysis.py`, dodany w tej sesji):
- Fixture główny: `ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv` — 17 520 świec, 730 d.
  **To PROXY (`BTC/USDT:USDT`, `ETH/USDT:USDT`, swap USDT), nie instrument
  produkcyjny** — jedyna seria na OKX z realną 24-mies. głębokością (A3: korelacja
  zwrotów 1h 0,9614 / 0,9648, mediana |spread| 0,055% / 0,064%).
- Podział na okna: **5 kolejnych, nienachodzących okien po 122 d** na symbol —
  dokładnie ten sam mechanizm i ten sam default `--window-days` co
  `scripts/fixture_power_analysis.py`, czyli granulacja poprzedniego raportu
  strategicznego. `17520 // (122·24) = 5`; **ostatnie ~120 d (2026-05-13 ..
  2026-09-10) nie tworzy pełnego okna i NIE jest objęte tą tabelą** — ten ogon
  pokrywa dodatkowy check na `_max` niżej.
- Parametry: produkcyjne VR **1,6/0,8**, `stale_candles_threshold=12`, filtr trendu
  OFF, reszta defaultów. **`addon_sl_variant` celowo NIE podawany** — skrypt
  korzysta z defaultu `run_backtest()`, czyli **V2**.
- PnL **netto**, model P2 ze stawką skalibrowaną z produkcji w A1
  (`ROUND_TRIP_FEE_PCT = 0.0010003`) + funding 0,01%/8 świec.
- `total_pnl` to, jak w każdej tabeli w tym dokumencie, **suma ważonych zwrotów
  procentowych per transakcja, nie krzywa equity ze składaniem** — przy ~200-300
  transakcjach na okno liczby rzędu −30% to ≈ −0,12%/transakcję.

**Wyniki — 10 niezależnych okien 122-dniowych (`_swap_24m`, V2, netto):**

| okno | zakres (UTC) | CLASSIC | ADAPTIVE | Δ (C−A) pp | C R:R | A R:R | txn C→A | SL C→A |
|---|---|---|---|---|---|---|---|---|
| BTC_W1 | 2024-09-10..2025-01-10 | −33,91% | −28,84% | **−5,08** | 0,7967 | 0,8274 | 187→173 | 35→32 |
| BTC_W2 | 2025-01-10..2025-05-12 | −27,50% | −33,43% | **+5,93** | 0,8190 | 0,7204 | 212→200 | 48→47 |
| BTC_W3 | 2025-05-12..2025-09-11 | −21,84% | −20,35% | **−1,48** | 1,1038 | 1,0727 | 220→211 | 17→17 |
| BTC_W4 | 2025-09-11..2026-01-11 | −8,91% | +1,13% | **−10,04** | 1,1755 | 1,3668 | 212→209 | 30→27 |
| BTC_W5 | 2026-01-11..2026-05-13 | −24,23% | −22,83% | **−1,40** | 0,7003 | 0,6609 | 185→175 | 31→30 |
| ETH_W1 | 2024-09-10..2025-01-10 | −17,60% | −15,72% | **−1,88** | 0,8720 | 0,7531 | 243→227 | 48→46 |
| ETH_W2 | 2025-01-10..2025-05-12 | −18,20% | −14,38% | **−3,82** | 0,9595 | 0,9408 | 255→237 | 64→59 |
| ETH_W3 | 2025-05-12..2025-09-11 | −38,02% | −45,09% | **+7,07** | 0,9885 | 0,7570 | 298→279 | 53→49 |
| ETH_W4 | 2025-09-11..2026-01-11 | +0,76% | −6,19% | **+6,95** | 1,1771 | 1,0631 | 255→243 | 38→36 |
| ETH_W5 | 2026-01-11..2026-05-13 | −23,23% | −32,06% | **+8,83** | 0,8264 | 0,5318 | 241→222 | 40→40 |

**Test istotności (dokładny dwustronny test znaków + t na różnicach parowanych):**

| populacja | C>A na netto | p (znaki) | mean Δ netto | t | C>A na R:R | p (znaki) | mean Δ R:R | t |
|---|---|---|---|---|---|---|---|---|
| BTC (n=5) | **1/5** | 0,3750 | −2,41 pp | −0,92 | 3/5 | 1,0000 | −0,011 | −0,21 |
| ETH (n=5) | 3/5 | 1,0000 | +3,43 pp | +1,32 | **5/5** | 0,0625 | +0,156 | +3,21 |
| **BTC+ETH (n=10)** | **4/10** | **0,7539** | **+0,51 pp** | **+0,26** | **8/10** | 0,1094 | +0,073 | +1,69 |

Pooled (suma okien): BTC CLASSIC −116,39% vs ADAPTIVE −104,33%; ETH CLASSIC
−96,29% vs ADAPTIVE −113,44%. Różnica mechaniczna: ADAPTIVE blokuje **5,7% wejść**
(2308→2176 txn) i **5,2% stop-outów** (404→383) — potwierdza „~6%" z poprzedniego
raportu na 3,3× większej próbce.

**Dodatkowy check na realnym USDC (`ohlcv_{BTC,ETH}_USDC_1h_max.csv`, 384 d, 3 okna
122 d na symbol, ten sam setup):**

| okno | zakres (UTC) | CLASSIC | ADAPTIVE | Δ (C−A) pp | C R:R | A R:R | txn C→A | SL C→A |
|---|---|---|---|---|---|---|---|---|
| BTC_W1 | 2025-08-22..2025-12-22 | +1,64% | +2,44% | −0,80 | 1,3845 | 1,3596 | 288→252 | 28→27 |
| BTC_W2 | 2025-12-22..2026-04-23 | −26,37% | −24,03% | −2,34 | 0,8193 | 0,7588 | 228→208 | 36→34 |
| BTC_W3 | 2026-04-23..2026-08-23 | +1,72% | −3,33% | +5,05 | 1,5730 | 1,4495 | 240→230 | 21→21 |
| ETH_W1 | 2025-08-22..2025-12-22 | +8,82% | −6,35% | +15,17 | 1,4765 | 1,1416 | 331→297 | 39→37 |
| ETH_W2 | 2025-12-22..2026-04-23 | −22,28% | −26,78% | +4,51 | 0,9478 | 0,8296 | 290→245 | 47→48 |
| ETH_W3 | 2026-04-23..2026-08-23 | +0,75% | +0,10% | +0,64 | 1,4659 | 1,3260 | 272→257 | 30→29 |

BTC+ETH (n=6): netto **4/6** (p = 0,6875, mean Δ +3,71 pp, t = +1,43); R:R **6/6**
(p = 0,0312, mean Δ +0,134, t = +3,04). **Te 6 okien NIE są niezależne od 10
powyżej** — pokrywają się w czasie z `_swap_24m` W4/W5 i to skorelowany instrument
(ρ ≈ 0,96), więc **nie wolno ich sumować do „14/16"** jako niezależnych prób.
Jedyny fragment, którego `_swap_24m` w ogóle nie obejmuje, to `_max` W3
(2026-04-23..2026-08-23).

#### Werdykt Fazy 2a: **werdykt „brak sygnału, ADAPTIVE zostaje" UTRZYMUJE SIĘ**

1. **Na netto — nic się nie zmieniło, i to mocniej niż wcześniej.** 4/10 okien na
   korzyść CLASSIC (p = 0,75), mean Δ **+0,51 pp przy sd 6,30 pp** — rozrzut jest
   **12× większy niż efekt**. To nie jest „za mało danych, żeby wykryć" — to jest
   pomiar zgodny z zerem przy 3,3× większej próbce niż poprzednio.
2. **Liczba „5,8× na BTC_full" jest ostatecznie rozbrojona.** Nie tylko była
   artefaktem dzielenia dwóch liczb bliskich zeru — na 24 mies. historii **znak na
   BTC się odwraca**: CLASSIC wygrywa tylko w 1/5 okien BTC, pooled przegrywa o
   12,06 pp. Gdyby przełączyć produkcję na CLASSIC w oparciu o tamtą liczbę, na
   BTC byłby to wybór gorszej strony.
3. **Kierunki są sprzeczne między symbolami** (BTC → ADAPTIVE, ETH → CLASSIC) —
   klasyczna sygnatura braku efektu, nie efektu zależnego od symbolu: żadne z tych
   per-symbolowych przechyleń nie jest istotne (p = 0,375 i p = 1,0).
4. **JEDNA rzecz faktycznie się zmieniła: R:R stwardniało kierunkowo na ETH.**
   Poprzednio „5/6 na R:R, kierunkowo spójne, ale w szumie"; teraz na ETH **5/5
   (`_swap_24m`) + 3/3 (`_max`) = 8/8 okien**, mean Δ R:R +0,156, t = +3,21.
   Na BTC tego nie ma (3/5, t = −0,21). **To NIE jest sygnał do przełączenia trybu**
   i nie wolno go tak przedstawiać, z dwóch powodów:
   - **Nie konwertuje się na pieniądze.** ETH netto to dalej 3/5 (p = 1,0), a na
     BTC R:R jest neutralne. R:R jest ilorazem ŚREDNICH MAGNITUD (`avg_win/avg_loss`),
     nieczułym na liczbę transakcji — mix transakcji może go podnieść bez wnoszenia
     złotówki.
   - **Różnica jest zdominowana przez przesunięcie całej ścieżki, nie przez wartość
     odfiltrowanych wejść.** Zdekomponowane na ETH_W5 (największa ΔR:R = +0,295):
     CLASSIC ma 241 transakcji, ADAPTIVE 222, ale tylko **28 transakcji CLASSIC-a
     nie ma odpowiednika** w ADAPTIVE (klucz: `exit_idx`/`exit_reason`/`side`) i
     wnoszą one **+3,76 pp z +8,83 pp** całej różnicy. Pozostałe ~5 pp pochodzi z
     transakcji o tym samym kluczu, które mają inne ceny wejścia/wagi — czyli z
     tego, że zablokowanie jednego wejścia przesuwa cały późniejszy ciąg (maszyna
     stanów, SCOUT_MODE, Post-SL Cooldown, Single Addon Guard). `avg_loss` rośnie
     wtedy z 1,0709% (C) na 1,2032% (A) na transakcjach, których filtr w ogóle nie
     dotknął. **Efektu nie da się przypisać selektywności filtra** — a jeśli nie
     da się go przypisać mechanizmowi, to nie jest podstawą do zmiany produkcji.
5. **Uboczne, ważniejsze niż sam A/B:** na 610 dniach proxy `_swap_24m` **oba tryby
   są głęboko ujemne** — mediana okna ≈ −23%, czyli **≈ −0,12 %/transakcję**, przy
   koszcie round-trip 0,10% + funding. Poprzedni werdykt Etapu 5 („strategia nie ma
   dziś wykazanego edge'a, koszty ≈ zysk brutto") był postawiony na jednym,
   nietypowo łagodnym oknie i **na dłuższej historii się nie łagodzi, tylko
   zaostrza**. Dwa okna bliskie zeru (`_max` W1/W3) to wyjątek, nie reguła. Tego
   nie zmieni żaden wybór `engine_mode` — różnica C−A (±5 pp) jest o rząd
   wielkości mniejsza niż sama strata (−23 pp).

**Status: A/B `engine_mode` ZAMKNIĘTE PONOWNIE, tym razem pod produkcyjnym
mechanizmem SL i na 24-mies. historii. Produkcja zostaje na `ADAPTIVE`. Nie
otwierać tego ticketu po raz trzeci bez nowego mechanizmu do zbadania** — sam
przyrost danych już nie pomoże, bo efekt jest zgodny z zerem, a nie „za mały, żeby
go wykryć". Zastrzeżenie, które trzeba trzymać przy tym werdykcie: główna seria to
proxy USDT-swap, a 4 ostatnie miesiące (2026-05-13..2026-09-10) są objęte tylko
przez `_max` W3.

**Odtworzenie:**
```bash
PYTHONPATH=src python3 scripts/ab_engine_mode_analysis.py --window-days 122 \
  --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
PYTHONPATH=src python3 scripts/ab_engine_mode_analysis.py --window-days 122 \
  --fixtures ohlcv_BTC_USDC_1h_max.csv ohlcv_ETH_USDC_1h_max.csv
```
Pełny suite po tej sesji: **344 passed, 4 deselected** (`-m "not slow"`) — bez
zmian względem baseline'u, bo sesja nie dotyka `src/` ani testów.

#### ✅ ZAMKNIĘTE — P4 wariant #2 (SL per-symbol): hipoteza SFALSYFIKOWANA

> ⚠️ **Ta tabela jest liczona pod wycofanym wariantem V0 i na jednym 90-dniowym
> oknie.** Przeliczona 2026-09-11 na 730 dniach pod produkcyjnym V2 — decyzja
> (`2.0%` zostaje) się utrzymuje, ale **uzasadnienie poniżej („monotonicznie
> szkodzi w obu połowach OOS") zostało obalone jako artefakt V0**. Nie cytować
> tego akapitu bez sekcji „P4 wariant #2 PRZELICZONY NA DŁUGIEJ HISTORII POD V2"
> niżej.

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

#### 🔁 P4 wariant #2 PRZELICZONY NA DŁUGIEJ HISTORII POD V2 (2026-09-11) — decyzja `2.0%` ZOSTAJE, ale jej ÓWCZESNE UZASADNIENIE JEST OBALONE

Worktree/branch: `chore/phase2b-p4-sl-grid-long-history`. Skrypt:
`scripts/sl_grid_analysis.py` (zostawiony w repo — w przeciwieństwie do
jednorazowej kalibracji A1 ten pomiar jest w 100% odtwarzalny, bo wszystkie
fixture'y są w repo).

**Dlaczego tabela wyżej wymagała przeliczenia — dwa niezależne powody:**
1. Była liczona pod `addon_sl_variant="V0"` (default harnessu do 2026-09-11),
   czyli pod **wycofanym** mechanizmem SL sprzed P3 (kompozyt 1,25% od avgPx).
   Produkcja realizuje dziś **V2** i harness domyślnie liczy V2 (patrz „P1
   ZAMKNIĘTE FORMALNIE").
2. Była liczona na JEDNYM 90-dniowym fixture'cie pociętym na 2 połowy OOS
   (~±20 pp CI). Dostępne są teraz `ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv`
   (730 d, **proxy** USDT-swap — OKX nie ma 24 mies. historii dla USDC) oraz
   `ohlcv_{BTC,ETH}_USDC_1h_max.csv` (384 d, realne USDC).

**Uwaga koncepcyjna — zmienia się, CO ta siatka mierzy:** pod V2 dokładka nie ma
własnego SL, więc `base_sl_pct` jest **jedynym SL w całym systemie**. Stara
siatka mierzyła kompromis między dwoma progami (baza 2% vs kompozyt 1,25% po
dokładce); nowa mierzy jedną, wspólną zmienną decyzyjną.

**Konfiguracja pomiaru:** `engine_mode=ADAPTIVE`, VR 1.6/0.8,
`stale_candles_threshold=12`, trend filter off, `addon_sl_variant` = default
(V2), netto wg modelu P2 ze **skalibrowaną** stawką `ROUND_TRIP_FEE_PCT =
0.0010003` (A1) + funding 0,01%/8 świec. 5 kolejnych, **nienachodzących** okien
122-dniowych na symbol (122 d = wzorzec z `fixture_power_analysis.py`;
730/122 = 5,98 → 5 pełnych okien), 173-279 transakcji na komórkę, 50 komórek.
`base_sl_pct` **nie jest** parametrem `run_backtest()` — to modułowa stała
`backtest_engine.BASE_SL_PCT`, czytana w locie zarówno przez sam check SL, jak i
przez przypisanie `sl_pct_override` w gałęzi V2; siatka podmienia ją per proces
roboczy (jeden proces na komórkę → zero przecieków między komórkami).

##### Tabela: netto (`total_pnl`) per okno — BTC (proxy `_swap_24m`)

| SL% | W1 09-24→01-25 | W2 →05-25 | W3 →09-25 | W4 →01-26 | W5 →05-26 | SUMA | SL-exits | txn |
|---|---|---|---|---|---|---|---|---|
| 1,5 | −44,55% | −38,22% | −21,55% | −12,18% | −53,55% | −170,05% | 217 | 1268 |
| **2,0 (dziś)** | **−28,84%** | **−33,43%** | −20,35% | **+1,13%** | −22,83% | **−104,33%** | 153 | 968 |
| 2,5 | −38,67% | −40,46% | **−17,85%** | −0,14% | **−16,79%** | −113,92% | 115 | 977 |
| 3,0 | −38,33% | −42,01% | −22,60% | −9,65% | −18,24% | −130,83% | 94 | 985 |
| 3,5 | −42,79% | −46,66% | −30,64% | −13,66% | −23,45% | −157,21% | 81 | 980 |

##### Tabela: netto per okno — ETH (proxy `_swap_24m`)

| SL% | W1 09-24→01-25 | W2 →05-25 | W3 →09-25 | W4 →01-26 | W5 →05-26 | SUMA | SL-exits | txn |
|---|---|---|---|---|---|---|---|---|
| 1,5 | −42,94% | −46,40% | −79,04% | **+8,04%** | −36,88% | −197,22% | 307 | 1180 |
| **2,0 (dziś)** | **−15,72%** | −14,38% | −45,09% | −6,19% | **−32,06%** | −113,44% | 230 | 1208 |
| 2,5 | −24,61% | −19,28% | −36,11% | +1,81% | −38,68% | −116,88% | 184 | 1198 |
| 3,0 | −33,68% | **+10,97%** | −31,30% | −1,23% | −35,62% | **−90,85%** | 147 | 1186 |
| 3,5 | −29,20% | +1,85% | **−23,85%** | −13,42% | −44,89% | −109,50% | 133 | 1171 |

(Pogrubione = najlepsza wartość SL w danym oknie. Zakresy dat okien: W1
2024-09-10..2025-01-10, W2 ..2025-05-12, W3 ..2025-09-11, W4 ..2026-01-11,
W5 2026-01-11..2026-05-13.)

##### Tabela: R:R per okno (ta sama siatka)

| SL% | BTC W1..W5 | średnia BTC | ETH W1..W5 | średnia ETH |
|---|---|---|---|---|
| 1,5 | 1,027 / 1,007 / 1,198 / 1,337 / 0,868 | **1,087** | 1,056 / 1,434 / 1,042 / 1,419 / 1,179 | **1,226** |
| 2,0 | 0,827 / 0,720 / 1,073 / 1,367 / 0,661 | 0,930 | 0,753 / 0,941 / 0,757 / 1,063 / 0,532 | 0,809 |
| 2,5 | 0,741 / 0,657 / 1,035 / 1,298 / 0,767 | 0,900 | 0,814 / 0,832 / 0,805 / 1,010 / 0,532 | 0,798 |
| 3,0 | 0,735 / 0,616 / 0,979 / 1,186 / 0,810 | 0,865 | 0,686 / 1,258 / 0,786 / 0,948 / 0,627 | 0,861 |
| 3,5 | 0,714 / 0,535 / 0,855 / 1,110 / 0,735 | 0,790 | 0,672 / 1,120 / 0,948 / 0,811 / 0,555 | 0,821 |

##### Test kierunku okno-po-oknie (10 niezależnych komórek = 2 symbole × 5 okien)

To jest dokładnie ten test, który poprzednio odróżnił „realny efekt" od szumu —
tyle że na 10 komórkach zamiast 2 połówek:

| porównanie (netto) | wygrywa z 2,0% w |
|---|---|
| 1,5% vs 2,0% | **1/10** komórek |
| 2,5% vs 2,0% | 4/10 |
| 3,0% vs 2,0% | 4/10 |
| 3,5% vs 2,0% | 2/10 |

- **Najlepsza wartość SL w komórce:** 2,0% → **5/10**, 2,5% → 2/10, 1,5%/3,0%/3,5%
  → po 1/10. Ranking 2,0% w komórce: 1. miejsce ×5, 2. ×1, 3. ×2, 4. ×2,
  **ostatnie miejsce ani razu**.
- **Monotoniczność poszerzania (2,0→2,5→3,0→3,5):** monotonicznie GORZEJ w
  **2/10** komórek, monotonicznie LEPIEJ w **1/10**, brak monotoniczności w 7/10.
- **Dokładna teza starego raportu („każde poszerzenie gorsze od 2,0%"):**
  prawdziwa w **5/10** komórek — czyli rzut monetą, nie efekt.
- **Na samym ETH (5 okien):** poszerzanie szkodzi w komplecie tylko w W1 i W5,
  a w W2 i W3 **pomaga mocno** (3,0%: +25,35 pp w W2; 3,5%: +21,24 pp w W3).
  Suma po ETH faworyzuje nawet 3,0% (−90,85% vs −113,44%), ale ta suma stoi na
  dwóch oknach z sześciu — **to nie jest sygnał, to rozrzut**.

##### Stare vs nowe liczby — co się utrzymało, a co nie

| teza ze starego raportu (V0, 1 okno 90 d) | status na 730 d pod V2 |
|---|---|
| „Na ETH poszerzanie SL **monotonicznie** pogarsza wynik w OBU połowach OOS" | **NIE POTWIERDZONE.** Monotoniczne pogorszenie w 2/10 komórek (ETH: 0/5 pełnej monotoniczności, kierunek „wszystko szersze gorsze" tylko w 2/5). Efekt **niepowtarzalny.** |
| „Mechanizm: szerszy stop nie zamienia SL w `TP_AND_FLIP`, tylko utrzymuje przegrane dłużej" | **NIEROZSTRZYGNIĘTE.** Liczba SL-exitów faktycznie spada monotonicznie (BTC 217→81, ETH 307→133), ale wynik netto nie podąża monotonicznie za tym spadkiem w żadnym z symboli. |
| „Na BTC 2,0-2,5% to płaskowyż" | **POTWIERDZONE.** 2,0% i 2,5% to 1. lub 2. miejsce w 5/5 okien BTC; różnica sumaryczna 9,6 pp na 5 okien przy rozrzucie międzyokiennym rzędu 30-50 pp. |
| „`base_sl_pct=2.0%` zostaje" | **UTRZYMANE**, ale z innego powodu: nie dlatego, że poszerzanie szkodzi (nie szkodzi konsekwentnie), tylko dlatego, że **żadna alternatywa nie wygrywa w większości niezależnych okien**, a 2,0% ma najlepszy profil rankingowy (5×1. miejsce, 0× ostatnie). |
| „Wariant #3 (`SL=k·ATR`) nie ma podstaw" | **NIEZMIENIONE** — nic w tym pomiarze nie daje mu podstawy, ale też nie jest tu testowany. |

**Poziomy bezwzględne nie są porównywalne ze starą tabelą** (stare ETH_full
−7,95% vs nowe okna −15..−45%): inne okno (122 d vs 90 d), inny instrument
(proxy USDT-swap vs USDC), inny reżim rynkowy (2024-09..2026-05 vs
2026-05..2026-08) i inny wariant SL. Porównywalny jest wyłącznie **kierunek**
wewnątrz tego samego pomiaru.

##### Efekt uboczny wart zapisania: 1,5% jest jednoznacznie złe, a R:R o tym kłamie

`base_sl_pct=1,5%` przegrywa z 2,0% w **9/10** komórek netto, ale **wygrywa
R:R w 9/10** — bo ciaśniejszy stop obcina średnią stratę (mianownik R:R), przy
czym robi to kosztem znacznie większej liczby stop-outów (BTC 153→217, ETH
230→307). To jest bezpośredni dowód, dlaczego bramka „R:R **i** total_pnl"
z P1 była dobrym pomysłem, i ostrzeżenie przed optymalizowaniem samego R:R.

**Znane sprzężenie, świadomie zostawione jako produkcyjnie wierne:**
`state_manager.on_stop_loss_hit` wchodzi w SCOUT_MODE dopiero przy
`sl_percent >= 0.02` (twardy literał). Przy `base_sl_pct=1,5%` **żaden**
stop-out nie może wejść w SCOUT_MODE, więc wiersz 1,5% różni się od reszty
siatki czymś więcej niż dystansem stopu (SCOUT blokuje też dokładki — na BTC W1
80 transakcji z dokładką przy 1,5% vs 32 przy 2,0%). Dokładnie to zrobiłoby
wdrożenie 1,5% na produkcję dzisiaj, więc jest zamodelowane, a nie usunięte —
ale wiersz 1,5% **nie jest** czystym eksperymentem „tylko dystans stopu".

##### 🔬 Izolacja przyczyny: to był artefakt V0, nie mała próbka

Żeby rozdzielić dwa możliwe wyjaśnienia rozbieżności („stary wynik był szumem
jednego okna" vs „stary wynik był prawdziwy, ale dla wycofanego mechanizmu"),
ta sama siatka została puszczona **na tym samym starym 90-dniowym fixture'cie**
(`ohlcv_{BTC,ETH}_USDC_1h.csv`, te same okna: full / H1 / H2), raz pod **V0**,
raz pod **V2** — zmienna jest wyłącznie wariant SL.

**Walidacja metody:** kolumna V0 **odtwarza starą tabelę co do drugiego miejsca
po przecinku** (BTC full: −3,09 / +0,80 / +2,50 / +1,44 / +0,52 vs opublikowane
−3,08 / +0,81 / +2,50 / +1,44 / +0,52; ETH full: −12,63 / −7,95 / −13,90 /
−14,74 / −16,37 — identyczne; kolumna ETH SL-exits 37/26/24/23/20 — identyczna).
Nowy skrypt liczy więc dokładnie to samo, co poprzednia sesja; różnice niżej
**nie są** różnicą narzędzia.

| ETH/USDC, 90 d | V0 full | **V2 full** | V0 H1 | **V2 H1** | V0 H2 | **V2 H2** |
|---|---|---|---|---|---|---|
| 1,5 | −12,63% | −11,05% | +1,16% | +3,63% | −13,22% | −14,10% |
| **2,0** | **−7,95%** | **−1,24%** | **−6,74%** | **−0,02%** | **−3,09%** | **−3,09%** |
| 2,5 | −13,90% | −11,07% | −8,96% | −2,76% | −7,28% | −10,64% |
| 3,0 | −14,74% | **−5,17%** | −10,41% | **−0,85%** | −7,11% | −7,11% |
| 3,5 | −16,37% | −7,11% | −10,51% | −2,72% | −9,09% | −7,62% |

- Pod **V0** monotoniczne pogorszenie przy poszerzaniu jest widoczne w obu
  połowach (to jest dokładnie ten wzorzec, na którym oparto stary werdykt).
- Pod **V2**, na **tych samych świecach**, monotoniczność **znika w obu
  połowach** (H1: 3,0% lepsze od 2,5%; H2: 3,5% lepsze od 2,5% i od 3,0%;
  full: 3,0% lepsze od 2,5%).
- **Wniosek:** teza „poszerzanie SL na ETH monotonicznie szkodzi" była
  **własnością wycofanego mechanizmu V0**, a nie własnością strategii. Stary
  werdykt nie był „szumem" w sensie losowym — był poprawnym pomiarem
  **nieistniejącej dziś konfiguracji**. To ten sam rodzaj błędu, który P1
  nazwał wprost: harness mierzył retired V0.
- Przy okazji: pod V2 na tym jednym 90-dniowym oknie BTC wygląda, jakby
  poszerzanie **pomagało** (3,5% → +12,79% vs 2,0% → +4,14%, SL-exity 18→7).
  Na 730 dniach ten efekt **nie replikuje się w ogóle** (3,5% jest najgorsze w
  5/5 okien BTC) — modelowy przykład, dlaczego pojedyncze okno nie może być
  podstawą decyzji o parametrze.

##### Kontrole odporności (dwa niezależne przecięcia tych samych/innych danych)

1. **Okna 121 d → 6 okien na symbol (12 komórek), ten sam fixture proxy.**
   Dodaje najświeższy kawałek historii, który siatka 122-dniowa obcina
   (W6 = 2026-05-08..2026-09-06). Werdykt bez zmian: 2,0% jest najlepsze w
   **5/12** komórek i **ani razu ostatnie**; żadna alternatywa nie bije 2,0% w
   więcej niż **4/12** komórek (1,5%: 3/12, 2,5%: 4/12, 3,0%: 4/12, 3,5%: 3/12);
   monotoniczne pogorszenie przy poszerzaniu w **2/12**, monotoniczna poprawa w
   1/12; teza „każde szersze gorsze" prawdziwa w 6/12. Sumy: BTC 2,0% = −111,27%
   (najlepsza), ETH 3,0% = −102,41% vs 2,0% = −123,63% (ten sam, niepowtarzalny
   po oknach obraz co przy 122 d).
2. **Realne USDC (`_max`, 384 d, 3 okna 122-dniowe na symbol), nie proxy.**
   Sprawdza, czy wynik nie jest artefaktem serii USDT-swap:

   | SL% | BTC W1 | BTC W2 | BTC W3 | ETH W1 | ETH W2 | ETH W3 |
   |---|---|---|---|---|---|---|
   | 1,5 | −19,42% | −46,91% | −14,22% | −18,07% | −70,78% | −1,41% |
   | **2,0** | **+2,44%** | **−24,03%** | −3,33% | −6,35% | **−26,78%** | +0,10% |
   | 2,5 | −0,31% | −28,47% | −0,37% | +18,10% | −29,51% | −2,66% |
   | 3,0 | −6,49% | −29,13% | +0,88% | **+25,20%** | −37,70% | **+1,93%** |
   | 3,5 | −8,39% | −35,51% | **+6,76%** | +18,55% | −45,30% | −0,86% |

   (okna: W1 2025-08-22..2025-12-22, W2 ..2026-04-23, W3 ..2026-08-23)
   Ten sam obraz: 2,0% najlepsze w 3/6 komórek i ani razu ostatnie, ale w
   BTC W3 i ETH W1/W3 wygrywa coś szerszego. **Kierunek nie jest powtarzalny
   także na realnym instrumencie** — więc to nie jest artefakt proxy.
   1,5% znów przegrywa w **6/6** komórek netto.

##### 🟢 WERDYKT (2026-09-11)

- **`base_sl_pct = 2,0%` ZOSTAJE, dla obu symboli** — ale **uzasadnienie się
  zmienia** i stare zdanie z tabeli wyżej nie może być dalej cytowane:
  poszerzanie SL **nie** szkodzi monotonicznie ani konsekwentnie; ono po prostu
  **nie wykazuje powtarzalnej przewagi** (najlepiej 4/12 okien). Utrzymujemy
  status quo z braku dowodu na zmianę, a nie z powodu dowodu przeciw zmianie.
- **Falsyfikacja hipotezy P4 („szerszy SL na ETH poprawi wynik") POZOSTAJE w
  mocy** — nadal nie ma podstawy, żeby poszerzać ETH. Ale jej ówczesne
  uzasadnienie („monotonicznie szkodzi w obu połowach OOS") jest **obalone
  jako artefakt V0** i tak należy je odtąd opisywać.
- **Zacieśnianie do 1,5% jest jednoznacznie szkodliwe** — jedyny naprawdę
  powtarzalny wynik tej siatki (przegrywa 9/10 na 122 d, 9/12 na 121 d, 6/6 na
  realnym USDC), mimo że poprawia R:R. To jest wynik mocniejszy niż cokolwiek,
  co dała stara tabela.
- **Wariant #3 (`SL=k·ATR`)** — nadal nie testowany i nadal bez podstawy w tych
  danych; ale argument „skoro poszerzanie szkodzi monotonicznie, to ATR-owy SL
  tym bardziej nie ma sensu" **przestał być prawdziwy** i nie wolno go używać
  jako powodu.
- **Czego ten pomiar NIE mówi:** nie mówi, że strategia zarabia. Wszystkie sumy
  po 5-6 oknach są głęboko ujemne na obu symbolach przy każdej wartości SL
  (BTC −104..−170%, ETH −91..−197% na 730 d) — to potwierdza główny wniosek
  raportu strategicznego („brak wykazanego edge'a, koszty ≈ gross") na 8×
  większej próbce, i jednocześnie pokazuje, że **strojenie samego `base_sl_pct`
  nie jest dźwignią, która to odwróci**.

##### Odtworzenie (wszystkie dane w repo, zero zależności zewnętrznych)

```bash
# pomiar główny (5 okien 122 d x 2 symbole x 5 wartosci SL = 50 komorek, ~15 min)
PYTHONPATH=src python3 scripts/sl_grid_analysis.py --window-days 122 \
  --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
# kontrola 1: 6 okien 121 d (dokłada najświeższy fragment historii)
PYTHONPATH=src python3 scripts/sl_grid_analysis.py --window-days 121 \
  --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
# kontrola 2: realne USDC, 3 okna 122 d
PYTHONPATH=src python3 scripts/sl_grid_analysis.py --window-days 122 \
  --fixtures ohlcv_BTC_USDC_1h_max.csv ohlcv_ETH_USDC_1h_max.csv
# izolacja V0 vs V2 na STARYM fixture'cie (--window-days 90 = "full", 45 = H1/H2)
PYTHONPATH=src python3 scripts/sl_grid_analysis.py --window-days 45 --variant V0 \
  --fixtures ohlcv_BTC_USDC_1h.csv ohlcv_ETH_USDC_1h.csv
PYTHONPATH=src python3 scripts/sl_grid_analysis.py --window-days 45 --variant V2 \
  --fixtures ohlcv_BTC_USDC_1h.csv ohlcv_ETH_USDC_1h.csv
```

Zero zmian w `src/` — to sesja pomiarowa, nie zmiana kodu produkcyjnego.
Pełny suite po tej sesji: **344 passed, 4 deselected** (`-m "not slow"`).

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

**Postęp „powtórki ablacji na długiej historii":** pierwszy ticket z tej listy —
A/B `engine_mode` — jest **przeliczony i zamknięty ponownie** (Faza 2a, sekcja
w Etapie 5 wyżej): 10 niezależnych okien 122 d na 24-mies. historii, pod
produkcyjnym V2, ze stawką z A1. Werdykt się nie odwrócił. Pozostają do
przeliczenia pod V2/długą historią: P4 wariant #2 (siatka `base_sl_pct`) i P6.2
(progi Inactivity) — oba zamknięte dotąd wyłącznie na 90-dniowym oknie pod
retired V0. Pomiar Fazy 2a dostarcza przy okazji mocniejszą przesłankę dla
„zawieszenia ETH": na `_swap_24m` ETH jest ujemne w 9/10 kombinacjach
okno×tryb — ale **BTC dokładnie tak samo, 9/10**, więc **nie jest to argument
różnicujący ETH względem BTC**, tylko kolejny dowód na brak edge'a w ogóle.

### 🔎 Sanity-check głównego znaleziska Fazy 2 (2026-09-11, ja sam, nie agent)

Obaj agenci Fazy 2 niezależnie znaleźli to samo: na 610 dniach (`_swap_24m`)
**każda kombinacja okno×tryb×`base_sl_pct` jest głęboko ujemna** — mediana
okna ≈ −23%, nie "brak edge'a", tylko realna, duża strata. Zanim to przyjąć
jako fakt o strategii (a nie artefakt narzędzia/proxy), sprawdziłem sam
charakter ruchu ceny w najgorszym oknie (BTC W1, `2024-09-10 → 2025-01-10`,
najgłębsza strata w obu tabelach Fazy 2):

```
open 57 644.6 -> close 94 780.9  (+64.4% w oknie)
max close w oknie: 108 254.4
max intra-window drawdown od rolling peak: tylko 15.1%
```

To nie przypadek ani błąd danych — to udokumentowany, silny, uporczywy trend
wzrostowy BTC (rajd po wyborach w USA, szczyt ok. $108-109k na przełomie
2024/2025). **Strategia jest z konstrukcji mean-reversion** (wejście na
dotknięciu wstęgi TMA + odrzucenie, zakład na powrót do środka) — to jest
dokładnie najgorszy możliwy reżim rynkowy dla tego mechanizmu: każde SHORT na
górnej wstędze zostaje przejechane przez kontynuację trendu i wybite stopem,
raz za razem. **To nie jest artefakt obliczeniowy — to oczekiwana porażka tej
konkretnej klasy strategii w silnym trendzie.**

**Konsekwencja strategiczna, ważniejsza niż same tabele A/B i P4:** poprzedni
raport strategiczny (mierzony na spokojniejszym, 90-dniowym oknie) dawał obraz
"brak wykazanego edge'a, ale blisko zera" — **to nie jest reprezentatywne dla
szerszego reżimu rynkowego.** Realny obraz na 24 miesiącach to: strategia
przeżywa/jest OK w okresach zakresowych (jak to 90-dniowe okno), ale **krwawi
poważnie w silnych trendach** — a takie okresy zdarzają się regularnie w
krypto (rajd 2024/2025 to nie wyjątek, to normalna część cyklu). Filtr trendu
(TMA slope) był już testowany i odrzucony we wcześniejszych sesjach — ale był
testowany **na tym samym, niereprezentatywnym 90-dniowym oknie**. To pytanie
zasługuje na ponowne, uczciwe przeliczenie na długiej historii, zanim
którakolwiek strona (wdrożyć filtr / nie wdrażać) zostanie uznana za
rozstrzygniętą — nie zrobione w tej sesji, jawnie zapisane jako otwarte.

### Czego świadomie NIE robić teraz

Nie ruszać progów Inactivity, `engine_mode`, `base_sl_pct`, nie budować filtra
sesyjnego, nie skalować alokacji, nie wracać do Midline TP ani high-vol filtra
(dopóki A1 nie odwróci ich werdyktu — jeśli odwróci, to osobny, ważny temat, nie
cichy fakt do zignorowania). **Nowe, po Fazie 2:** nie traktować dotychczasowego
odrzucenia filtra trendu (TMA slope) jako ostatecznego — było mierzone tylko na
niereprezentatywnym 90-dniowym oknie, patrz sanity-check wyżej.

---

## 🔹 Faza 3 (część 1): Audyt wierności SCOUT_MODE i dyscypliny closed-candle (2026-09-11)

Worktree/branch: `chore/phase3-1-scout-mode-audit`. **Zadanie audytowe (read-heavy)**,
zlecone równolegle do `chore/phase3-4-d1-atr-exhaustion-filter` (filtr D1 ATR
blokujący HIGH_RISK_ADD) — bo ten filtr zależy od tego, czy `backtest_engine.py`
poprawnie replikuje istniejącą blokadę SCOUT_MODE→HIGH_RISK_ADD (patrz A4).
Źródła: `state_manager.py::on_stop_loss_hit`/`on_take_profit_hit`,
`backtest_engine.py` (maszyna stanów SCOUT_MODE, ~linie 20/149/171-207/272-330),
`analytics.py` (HIGH_RISK_ADD gating :424-493, closed-candle :307, `use_closed_candles` :694-706),
`daemon.py` (closed-candle :1066/1282/1424, dedup `_last_candle_ts`/`_last_signal_candle_ts`),
`.agents/specs/knowledge/Architektura Systemowa mms4tk...md` (§2 H1_VALIDATION, §3 Hermes/ATR_LOOKBACK/SCOUT_MODE).

Rozszerzone w trakcie sesji o trzeci punkt (C), doprecyzowany przez usera:
dosłowna weryfikacja tezy "SCOUT_MODE zrzuca leverage do 0.1x, powrót do
BASE_MODE tylko po zamknięciu w pełni zyskownej transakcji netto".

### A) Parytet stanu SCOUT_MODE: backtest_engine.py vs state_manager.py

**A1 — maszyna stanów BASE_MODE/SCOUT_MODE: POTWIERDZONE, wierna.**
`backtest_engine.run_backtest()` konstruuje prawdziwy `MMS4StateManager` (`sm`,
`backtest_engine.py:335`, `logger_db=None` — bezpieczne, każde miejsce zapisu w tej
klasie jest już strzeżone `if self.logger_db is not None`) i woła jego prawdziwe
`record_trade()`/`on_stop_loss_hit()`/`on_take_profit_hit()` na każdym zamknięciu
(`close_position()` :365-394, `record_partial()` :396-405) — zero re-implementacji
logiki przejść stanu w harnessie. Zweryfikowane zdanie po zdaniu:
- Próg 2% (`sl_percent >= 0.02` w `on_stop_loss_hit`) — harness przekazuje
  `sl_percent = position.get("sl_pct_override", BASE_SL_PCT)` tylko dla
  `reason == "STOP_LOSS"` (:381), czyli dokładnie to, co produkcja liczy z
  `base_entry_price`/`base_sl_pct` po P3 V2 (task.md, sekcja "P3 V2" wyżej) —
  potwierdzone komentarzem :373-380 i zgodne z `daemon.reconcile_positions`.
- Powrót do BASE_MODE tylko na PEŁNYM (nie-partial) TP — `_trade_record_flags()`
  (:146-167) mapuje `STALE_SPLIT_50`/`MIDLINE_PARTIAL_TP` na
  `is_take_profit=True, is_partial=True` i `record_partial()` (:396-405) woła
  `record_trade()` z tymi samymi flagami — identyczne z `state_manager.py`'s
  `record_trade()`/`on_take_profit_hit()`, które explicite NIE resetują stanu przy
  `is_partial=True` (`state_manager.py:395,419-420`).
- `relax_scout_mode()`/Hermes webhook: harness nigdy ich nie woła (patrz A3) —
  ale to nie jest niewierność SAMEJ maszyny stanów opisanej w state_manager.py,
  to osobne, świadomie nie-modelowane wejście zewnętrzne (patrz A3).

**A2 — sizing scout-entry (`_size_new_entry`, ~linie 170-207): POTWIERDZONE jako
bezpieczne, udokumentowane uproszczenie, nie kierunkowe zniekształcenie.**
`_size_new_entry()` liczy `notional_weight = 1.0 / notional_reduction_factor`,
gdzie `notional_reduction_factor` to WPROST `sm.get_risk_profile_snapshot()`'s
własna wartość (`base_leverage / (leverage * size_mult)`) — harness nie
re-implementuje formuły, tylko woła tę samą, którą `state_manager.py` już liczy
(`get_risk_profile_snapshot`, :187-201). Kontrakty (`BASE_CONTRACTS[symbol] *
size_mult`) mogą wyjść ułamkowe (np. `4.0 * 0.3 = 1.2`) — udokumentowane wprost w
komentarzu :187-191 jako świadoma aproksymacja: realne zlecenie zawsze jest
całkowitą liczbą kontraktów, ale `_stats()` waży PnL przez `notional_weight`
(NIE przez same kontrakty), więc frakcyjność kontraktów nie wycieka do wyniku —
tylko NOTIONAL musi być proporcjonalnie poprawny, i jest (zweryfikowane: ta sama
formuła co produkcja). **Ocena: to jest bezpieczne uproszczenie** — nie zniekształca
wyników kierunkowo, bo jedyna wielkość, która faktycznie wchodzi do `_stats()`
(`weight`), jest matematycznie identyczna z tym, co dałaby produkcja przy tej samej
notional. Audyt nie znalazł tu bugu.

**A3 — ścieżka Hermes H4 webhook / `scout_override` / `directional_bias`:
POTWIERDZONE — backtest NIE modeluje tej ścieżki, i to było CICHE (nieudokumentowane)
do tej sesji.** Grep `backtest_engine.py` za `hermes_webhook`/`directional_bias`/
`scout_override`/`filter_bias` — **zero trafień**. `sm = MMS4StateManager(...)` w
`run_backtest()` (:335) nie dostaje `hermes_webhook` (zostaje `None` — `on_stop_loss_hit`
łagodnie pomija emisję webhooka, `state_manager.py:348` `if self.hermes_webhook:`),
i nigdy nie woła `sm.update_strategy_profile(...)` ani `sm.relax_scout_mode(...)` —
`StrategyProfile()` zostaje na fabrycznym defaultcie (`scout_override=False`,
`directional_bias="BOTH"`) przez cały backtest. Skutek: **harness zakłada "brak
override" bezwzględnie** — SCOUT_MODE zawsze trwa do naturalnego wygaśnięcia
(pełny TP), nigdy nie zostaje wcześniej zrelaksowany przez SWEEP_WICK (Hermes H4,
`api.py:838`/`hermes_webhook.py:131-156`), i `directional_bias` nigdy nie filtruje
sygnałów (MARUBOZU_BREAKOUT ścieżka też nie-modelowana). **To założenie NIE było
udokumentowane w kodzie ani w `task.md` przed tą sesją** — docstring `run_backtest()`
opisywał maszynę stanów jako "modeled faithfully" bez zastrzeżenia o Hermes. Naprawione
w tej sesji (nie jako bug, jako doc-fix): nowy akapit w docstringu `run_backtest()`
(`backtest_engine.py`, tuż po opisie SCOUT_MODE) explicite nazywa to założenie i
wyjaśnia, dlaczego harness nie może tej ścieżki zamodelować (zależy od
zewnętrznej, human/LLM-in-the-loop klasyfikacji świecy H4 — nie jest to
deterministyczna funkcja OHLCV). **Wniosek: każdy dotychczasowy wynik backtestu w
tym repo należy czytać jako "przy założeniu że Hermes nigdy nie interweniuje"** —
nie jako twierdzenie, że interwencja Hermesa jest rzadka lub nieistotna w produkcji.

**A4 — blokada HIGH_RISK_ADD w SCOUT_MODE: POTWIERDZONE, replikowana identycznie
(podwójna bramka, tak jak produkcja).** `analytics.py` blokuje HIGH_RISK_ADD w
SCOUT_MODE wewnątrz `evaluate_signals()` (`active_state != MMS4State.BASE_MODE` →
`is_allowed=False`, :424-433 SHORT / :474-483 LONG) — backtest_engine's główna
pętla odrzuca każdy `not signal.is_allowed` sygnał (:499 `if not signal.is_allowed
or trend_blocked: continue`), więc ten sygnał NIGDY nie dociera do sekcji
addonów. Backtest DODATKOWO powtarza tę samą bramkę explicite przy samym
wykonaniu addonu (:516 `and sm.get_symbol_state(symbol) == MMS4State.BASE_MODE
# addons blocked in SCOUT — daemon.py`) — redundantne, ale NIE błędne: to
dokładnie ta sama podwójna bramka co `daemon.py` (który TAKŻE sprawdza
`sym_state != MMS4State.BASE_MODE` niezależnie od `analytics.py`'s własnej
bramki, `daemon.py:1655`, `outcome = "BLOCKED_{signal.signal_type}"`). **Wniosek:
backtest replikuje blokadę SCOUT_MODE→HIGH_RISK_ADD identycznie z produkcją —
to NIE podważa wiarygodności backtestu dla równoległego zadania D1 ATR
(filtr `chore/phase3-4-d1-atr-exhaustion-filter`).** Audyt czysty na tym punkcie.

**Drobne (kosmetyczne) znalezisko przy okazji A4, naprawione:** `reason` string
zwracany przy tej blokadzie (`analytics.py:431,481`) i docstring
`state_manager.is_high_risk_add_allowed()` (`state_manager.py:313`) mówiły
dosłownie *"leverage x0.1"* / *"leverage 0.1"* — to jest STARY, świadomie
porzucony mechanizm (patrz sekcja C niżej), nie to, co kod dziś robi
(`SCOUT_LEVERAGE=1.0`, redukcja przez `SCOUT_SIZE_MULT=0.3`). Test
`tests/test_integration.py::test_scout_mode_blocks_high_risk_add` asertował
DOSŁOWNIE ten przestarzały string w tej samej funkcji, w której 3 linie wyżej
poprawnie asertuje `state_mgr.leverage == SCOUT_LEVERAGE` — samo-sprzeczność
w jednym pliku testowym, prawdopodobne źródło mylnego modelu mentalnego
opisanego w sekcji C. Naprawione: `reason` → *"HIGH_RISK_ADD blocked in
SCOUT_MODE (reduced position size, not leverage)"*, docstring poprawiony,
test i komentarz w `test_daemon.py:106` zaktualizowane. Czysto tekstowe, zero
zmiany zachowania.

### B) Dyscyplina "closed candle" (ryzyko look-ahead bias)

**B1 — `H1_VALIDATION`/16:05: POTWIERDZONE, NIE ZAIMPLEMENTOWANE — wciąż tylko
wymaganie z dokumentu architektury.** `grep -rniE "H1_VALIDATION|H1_CLOSE_FILTER|
16:05|markowanie|liquidity trap|session.?us"` po całym `src/mms4tk/`, `tests/`,
`task.md`, `docs/` — **zero trafień** poza samym dokumentem architektury.
Checklist §6 tego dokumentu ma ten punkt jako `- [ ]` (nieodhaczony) —
"Implementacja zależności `H1_CLOSE_FILTER`..." — spójne z ustaleniem: to
propozycja, nigdy niezaimplementowana. Nie ma tu rozbieżności backtest≠produkcja,
bo mechanizmu nie ma W OGÓLE w żadnym z nich.

**B2 — ogólna dyscyplina closed-candle w pipeline sygnałowym: POTWIERDZONE
przestrzegana, z jednym potwierdzonym wyjątkiem (patrz Bug #1 niżej).**
`analytics.py:307` (`closed_candles = candles[:-1]`) i `daemon.py:1066/1282/1424`
(analogicznie, `candles[:-1]`/`candles[-2]`) konsekwentnie odrzucają
niezamkniętą ostatnią świecę przed każdą decyzją sygnałową, ATR-em i dedup
znacznikiem czasu (`_last_candle_ts`/`_last_signal_candle_ts`) — potwierdzone
grepem i czytaniem kodu, w tym jawny komentarz `daemon.py:1281` *"candles[-1] is
the active (unclosed) candle, candles[-2] is last closed"*. `use_closed_candles=False`
(`analytics.py:694-706`, param `HigherTimeframeAnalytics.calculate_higher_tf`) —
**zweryfikowane: wywoływane WYŁĄCZNIE z `tests/audit/audit_math_indicators.py`**
(read-only, standalone skrypt diagnostyczny, nazwa pliku nie pasuje do
`test_*.py`/`*_test.py` więc pytest go w ogóle nie zbiera — niekolidujące z
`-m "not slow"`). Jedyny produkcyjny wywołujący, `api.py:736`, woła bez tego
parametru (default `True` = closed-candle). **Czysty, bezpieczny debug/inspect
path — nie używany w pętli produkcyjnej.**

**B3 — off-by-one backtest vs produkcja: ZNALEZIONY I NAPRAWIONY REALNY BUG
(patrz sekcja "Bug znaleziony i naprawiony" niżej).** Poza tym jednym miejscem,
offsety są spójne: sygnał w `backtest_engine.py` (`window = candles[:i+2]` →
`evaluate_signals` robi `closed_candles = window[:-1]` → `curr_c =
closed_candles[-1] = candles[i]`) odpowiada dokładnie temu, co
`daemon.py`'s `evaluate_signals(candles,...)` robi na żywych danych (`candles[-1]`
= świeca w budowie z giełdy, odrzucana identycznie). SL/TP w backteście
sprawdzane są na TEJ SAMEJ świecy `i`, na której został wygenerowany sygnał w
poprzedniej iteracji dla otwartej pozycji — brak lookahead. `full_df["tma"]`/
`tma_slope` w `backtest_engine.py` (komentarz :344-346 "Precompute TMA/slope ONCE
over the whole closed-candle history... no lookahead") liczone kauzalnie po
indeksie `i` — zgodne z tym, co `daemon.py` liczy z `closed_candles` dla ATR-u
(:1066-1072), ale **NIEZGODNE z tym, co `daemon.py` liczyło z `candles` (z
niezamkniętą świecą) dla `tma_mid` w gałęzi Midline Partial TP** — patrz bug
niżej. To jest dokładnie wzorzec z Fazy 2 (V0/V2), tylko wewnątrz samej produkcji
zamiast między backtestem a produkcją: jeden fragment tej samej funkcji
(`execute_symbol_cycle`) łamał dyscyplinę, którą fragment 6 linii wyżej
poprawnie egzekwuje.

### C) Weryfikacja tezy: "SCOUT_MODE = leverage 0.1x, powrót po w pełni zyskownej transakcji netto"

**C1 — "SCOUT_MODE zrzuca leverage do 0.1x": OBALONE. To jest przestarzały model
mentalny odpowiadający ŚWIADOMIE PORZUCONEMU mechanizmowi, nie temu, co kod robi
dziś.** `state_manager.py:35-56` (komentarz nad `SCOUT_LEVERAGE`/`SCOUT_SIZE_MULT`)
to wprost historia tej zmiany, zacytowana w kodzie: *"Risk in SCOUT is cut through
POSITION SIZE, not through leverage. Leverage cannot carry the reduction: OKX only
accepts integer leverage >= 1, so the old 0.1x was silently clamped to 1x on the
exchange while the notional it implied ($0.70 at a $10 cap) fell under the
exchange's minimum order value — the entry was then skipped entirely and the
symbol could never trade its way back to BASE_MODE."* Dzisiejsze defaulty:
`SCOUT_LEVERAGE=1.0` (`MMS4_SCOUT_LEVERAGE`/legacy `MMS4_LEVERAGE_SCOUT` env),
`SCOUT_SIZE_MULT=0.3` (`MMS4_SCOUT_SIZE_MULT`). Efektywna redukcja notional vs
BASE_MODE (profil MODERATE, leverage 3.0): `SCOUT_SIZE_MULT * SCOUT_LEVERAGE /
base_leverage = 0.3 * 1.0 / 3.0 ≈ 0.33` per-jednostka-marginesu, ale ponieważ
BASE_MODE dodatkowo dźwignuje 3x a SCOUT tylko 1x, całkowita redukcja NOTIONAL to
**10x** (komentarz :51 *"a 10x reduction at ANY capital level"*) — to jest
najprawdopodobniej źródło mylącego skojarzenia z "0.1x": liczbowo zbieżne (10x
redukcji ≈ "×0.1" w sensie zagregowanym), ale **mechanicznie to nie jest ustawienie
leverage=0.1** — leverage w SCOUT_MODE dziś wynosi realnie **1.0x** (wartość
w pełni legalna na giełdzie), redukcja niesiona jest przez rozmiar pozycji.
Zweryfikowane też grepem: `SCOUT_LEVERAGE` nigdzie w kodzie nie jest ustawiane na
0.1 — jedyne trzy miejsca z literałem "0.1"/"leverage x0.1" to STARE STRINGI
opisowe (`analytics.py:431,481`, `state_manager.py:313`), same w sobie
przestarzałe i mylące (naprawione w sekcji A4 wyżej) — czyli **teza użytkownika
o "0.1x" ma realne, konkretne źródło w samym kodzie (stare komentarze/reason
stringi), nie jest wymyślona znikąd, ale opisuje mechanizm retired, nie
dzisiejszy.**

**C2 — "powrót do BASE_MODE wyłącznie po zamknięciu w pełni zyskownej transakcji
netto": CZĘŚCIOWO OBALONE — warunek jest subtelnie inny.** `on_take_profit_hit()`
(`state_manager.py:362-373`) resetuje SCOUT_MODE→BASE_MODE na PODSTAWIE FLAGI
`is_take_profit=True` z pełnego (nie-partial) zamknięcia — **funkcja nie
przyjmuje ani nie sprawdza żadnej wartości PnL, ani gross, ani net.** Flaga
`is_take_profit` jest przypisywana przez WOŁAJĄCEGO na podstawie MECHANIZMU
wyjścia (band-touch / "TP_AND_FLIP" — dotknięcie przeciwnej wstęgi TMA), nie na
podstawie zweryfikowanej rentowności tej konkretnej transakcji:
- `daemon.py:1459-1466` liczy `pnl_pct` jako surowy, GROSS `(exit-entry)/entry`
  (bez odjęcia fee/funding) — i sam kod PRZEWIDUJE ujemny wynik (`pnl_str =
  f"+{...}" if pnl_pct >= 0 else f"{...}"`, formatowanie dla obu znaków) —
  po czym **bezwarunkowo** woła `record_trade(..., is_take_profit=True)`
  (`daemon.py:1517`) niezależnie od znaku `pnl_pct`, a TYM BARDZIEJ niezależnie
  od PnL netto (fee/funding nigdzie tu nieliczone w produkcji — netto istnieje
  tylko w modelu P2 backtestu).
- `backtest_engine.py::_trade_record_flags()` (:158-167) replikuje TO SAMO:
  `"TP_AND_FLIP": dict(is_take_profit=True, ...)` przypisywane bezwarunkowo po
  samej nazwie exit_reason, bez sprawdzania `pnl_pct` (`close_position()` też
  nie filtruje po znaku PnL przed wywołaniem `sm.record_trade()`) — **backtest
  wiernie replikuje TĘ SAMĄ (nie węższą, nie szerszą) definicję.**
- **Wniosek:** faktyczny warunek to *"pierwsze pełne zamknięcie zaklasyfikowane
  jako TP_AND_FLIP (dotknięcie przeciwnej wstęgi)"*, NIE *"pierwsze pełne
  zamknięcie ZWERYFIKOWANE jako netto-zyskowne"*. Te dwa warunki pokrywają się w
  większości przypadków (dotknięcie przeciwnej wstęgi zwykle oznacza zysk), ale
  NIE identycznie: transakcja TP_AND_FLIP zamknięta bardzo blisko ceny wejścia
  (mały ruch, duże koszty) może być netto stratna, a mimo to zresetuje
  SCOUT_MODE→BASE_MODE, bo klasyfikacja opiera się na TYPIE wyjścia, nie na
  weryfikacji znaku PnL. To NIE jest bug (kod robi to konsekwentnie identycznie
  w produkcji i w backteście — audyt nie znalazł tu rozbieżności backtest≠
  produkcja), ale to jest odrębne od tezy "w pełni zyskowna transakcja netto" —
  ta ostatnia to niepoprawny model mentalny, poprawiony tu wprost do zapisu.

### 🐛 Bug znaleziony i naprawiony (osobno od reszty audytu, punkt 2 deliverable)

**Lokalizacja:** `daemon.py::execute_symbol_cycle`, gałąź Midline Partial Take-Profit
(ok. linii 1062-1078 i 1124-1141, PRZED poprawką).

**Opis:** ATR (`current_atr`, linie 1066-1072) liczony był poprawnie z
`closed_candles = candles[:-1]` — ale **TMA midline (`tma_mid`) liczony był z
surowego `candles`** (`tma_df_all = TMAIndicator.calculate(candles, ind_params)`),
czyli WŁĄCZAJĄC niezamkniętą, wciąż formującą się ostatnią świecę zwracaną przez
`fetch_ohlcv`/OKX (dokładnie ten sam powód, dla którego `closed_candles` istnieje
2 linie wyżej w TEJ SAMEJ funkcji, i dokładnie ta sama dyscyplina, którą
`MMS4SignalEngine.evaluate_signals`/`HigherTimeframeAnalytics.calculate_higher_tf`
egzekwują gdzie indziej). Analogicznie, próg dotyku (`candle_high`/`candle_low`,
linie 1127-1128 PRZED poprawką) czytany był z `candles[-1]` (świeca w budowie),
zamiast z tej samej zamkniętej świecy co `signal.price` (pochodzący z
`closed_candles[-1].close` wewnątrz `evaluate_signals`). Skutek: rolujący TMA
midline mógł "repaintować" WEWNĄTRZ trwającej świecy (przeciwieństwo idei
"closed candle" — próg mógł się przesuwać z każdym tickiem zanim świeca się
zamknęła), a wick na formującej się świecy mógł fałszywie odpalić Midline
Partial TP przed zamknięciem świecy, która go wygenerowała. `backtest_engine.py`
(przez konstrukcję — cała historia w fixture jest już zamknięta) liczy `tma_mid`
kauzalnie wyłącznie z danych domkniętych do indeksu `i` — więc backtest NIGDY nie
mierzył tego konkretnego zachowania produkcji; nie było więc możliwe wykryć tę
rozbieżność backtest≠produkcja z samej analizy backtestu, tylko przez bezpośrednie
czytanie `daemon.py`.

**Status w produkcji: dziś latentny, nie aktywny.** `midline_partial_tp_enabled`
domyślnie `False` od P9.1 (task.md, Faza 0a) — ale to jest kod GOTOWY do
aktywacji przez samego operatora (per-symbol `system_settings`), więc bug jest
realny i czekający, nie hipotetyczny. `run_backtest()`'s `midline_tp_enabled`
też domyślnie `False`, więc żaden dotychczasowy werdykt backtestu (Midline TP
6/6 przegrana, task.md Etap 1) nie jest tym dotknięty — ten filtr i tak
przegrywa niezależnie od tego buga.

**Naprawa:** `tma_df_all` liczone teraz z `closed_candles` (zamiast `candles`);
`candle_high`/`candle_low` czytane z `closed_candles[-1]` (zamiast `candles[-1]`).
Zero zmiany interfejsu, zero zmiany innych gałęzi.

**Test regresyjny:** `tests/test_midline_inactivity_tp.py::
test_midline_tp_uses_closed_candle_not_forming_candle_for_trigger` — buduje
historię świec, której ZAMKNIĘTE dane nie dotykają midline (zweryfikowane
programowo przez `TMAIndicator.calculate`, nie liczone ręcznie), dokleja jedną
"formującą się" świecę z ekstremalnym highem, który PRZED poprawką przekraczał
próg i fałszywie odpalał `MIDLINE_PARTIAL_TP`. **Zweryfikowane empirycznie w obie
strony:** test uruchomiony tymczasowo przeciwko kodowi SPRZED poprawki (przez
chwilowe odłożenie zmian w `daemon.py` na bok i przywrócenie ich zaraz po
weryfikacji) — **FAILuje bez poprawki** (`AssertionError: assert
'MIDLINE_PARTIAL_TP' != 'MIDLINE_PARTIAL_TP'`), **PASSuje z poprawką**. Istniejące
testy w tym pliku niezmienione (używały płaskich, identycznych świec — poprawka
nie zmienia ich wyniku, bo zamknięta i niezamknięta świeca mają tam identyczne
wartości).

### Werdykt zbiorczy

| # | Punkt | Werdykt |
|---|---|---|
| A1 | Maszyna stanów SCOUT_MODE w harnessie | ✅ potwierdzone, wierne |
| A2 | Sizing scout-entry (fractional contracts) | ✅ bezpieczne, udokumentowane uproszczenie |
| A3 | Ścieżka Hermes H4 / scout_override / directional_bias | ⚠️ potwierdzone: NIE modelowana, było ciche → udokumentowane w tej sesji |
| A4 | HIGH_RISK_ADD blokowany w SCOUT_MODE | ✅ potwierdzone, replikowane identycznie (+ 1 kosmetyczny string naprawiony) |
| B1 | `H1_VALIDATION`/16:05 | ⚠️ potwierdzone: NIEzaimplementowane, wciąż tylko wymaganie dok. architektury |
| B2 | Ogólna dyscyplina closed-candle | ✅ przestrzegana (poza Bugiem #1) |
| B3 | Off-by-one backtest vs produkcja | 🐛 1 REALNY BUG znaleziony i naprawiony (Midline TP `tma_mid`/`candle_high`/`candle_low`) |
| C1 | "SCOUT_MODE = leverage 0.1x" | ❌ OBALONE — mechanizm retired, dziś `SCOUT_LEVERAGE=1.0` + `SCOUT_SIZE_MULT=0.3` |
| C2 | "powrót do BASE_MODE = TP netto-zyskowny" | ⚠️ CZĘŚCIOWO OBALONE — warunek to klasyfikacja exit-reason (TP_AND_FLIP), nie weryfikacja PnL netto; backtest replikuje to identycznie (brak rozbieżności) |

**Audyt czysty na:** A1, A2, A4 (poza kosmetyką), B1, B2. **Bug realny znaleziony
i naprawiony:** B3 (Midline TP closed-candle). **Ustalenia korygujące błędny model
mentalny (wartościowe, nie "bug"):** A3 (dodano dokumentację), C1, C2.

**Test suite:** `python3 -m pytest -q -m "not slow"` — 344 passed, 4 deselected
(przed tą sesją) → **345 passed, 4 deselected** (+1 nowy test regresyjny), zero
regresji.
