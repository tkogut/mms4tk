# 📋 TASK BACKLOG: Etap 0 — P9 (Higiena) + P7 (Naprawa parametrów silnika rekalibracji)

**Status:** DRAFT — do omówienia architektury, **kod jeszcze nie napisany**
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

#### TICKET-P9.1: `midline_partial_tp_enabled` default → `False`
- **Pliki:** `src/mms4tk/analytics.py:146`, `src/mms4tk/api.py:911`, `src/mms4tk/api.py:1045`, `ui_dashboard/src/components/SettingsTab.jsx:18`
- **Dowód:** audyt §1.6 — funkcja odrzucona 6/6 (CHANGELOG 0.42.1) jest dziś fail-open: świeży deploy bez jawnego wpisu w `system_settings` włącza ją z powrotem. Wszystkie inne odrzucone filtry mają default `False`.
- **Zakres:** zmiana jednej wartości domyślnej w 4 miejscach, spójnie.

#### TICKET-P9.2: Portfolio Margin Guard — ustawić `total_max_portfolio_margin_usd`
- **Pliki:** dane operacyjne w `system_settings` (nie kod — `daemon.py:150-173`/`api.py:941-949` już poprawne i kompletne, w tym zamknięcie race'u)
- **Dowód:** audyt §4 poz. 21 — dziś `total_max_portfolio_margin_usd = 0.0` → guard jest de facto wyłączony. Przy notional 2×($105+$45)=$300 na $100 equity próg rzędu 300-400 USD nic dziś nie blokuje, ale jest twardym hamulcem przy skalowaniu konta.
- **⚠️ Otwarte pytanie do architektury:** to nie jest zmiana kodu, tylko wartości konfiguracyjnej — czy ustawiamy ją operacyjnie (API/DB), czy zmieniamy fabryczny default w `daemon.py:135`? Audyt nie podaje jednej twardej liczby (mówi "300-400"), więc dokładną wartość i miejsce ustawienia trzeba uzgodnić przed implementacją.

#### TICKET-P9.3: Naprawić `/api/v1/emergency/reverse` (dziś pusty stub)
- **Plik:** `src/mms4tk/api.py:457-467`
- **Dowód:** audyt §1.9f — endpoint dziś zwraca `"success"` i nic nie robi. To "najgroźniejszy rodzaj buga" — raportowanie nieprawdy o zamknięciu pozycji w ścieżce awaryjnej.
- **Zakres (do wyboru na dyskusji):** (a) zaimplementować faktyczne odwrócenie pozycji, albo (b) zwracać `501 Not Implemented` do czasu implementacji. Ten sam problem dotyczy `killall` — sprawdzić analogicznie: albo zamyka pozycje, albo przestaje to twierdzić.

#### TICKET-P9.4: Rozszerzyć `HigherTimeframeResponse` o pola volatility
- **Plik:** `src/mms4tk/api.py` (response model), `src/mms4tk/analytics.py` (już liczy te wartości)
- **Pola do dodania:** `volatility_zone`, `is_volatility_expansion`, `atr_d1_expansion_threshold`
- **Dowód:** audyt §1.3 pkt 1 — dane są liczone w `analytics.py` i dziś odfiltrowywane przez `response_model` w `api.py:509`. Zero wpływu na egzekucję, ale prerekwizyt dla P5 (krok 0) z dalszej kolejności audytu.

#### TICKET-P9.5: Podnieść `limit=100` → `limit=150` w `daemon.py:733`
- **Plik:** `src/mms4tk/daemon.py:733`
- **Dowód:** audyt §1.9h — VR (Volatility Ratio) ma dziś mianownik liczony z ~86 okresów zamiast 100 (bo pobierane jest tylko 100 świec, a część idzie na rozgrzewkę wskaźnika), co przesuwa punkt przecięcia progów 1.6/0.8 właśnie wykalibrowanych w innym miejscu audytu.

#### TICKET-P9.6: Usunąć martwe `lev` z `analytics.py:532-537`, `:619-624`
- **Plik:** `src/mms4tk/analytics.py`
- **Dowód:** audyt §1.9b / §4 poz. 19 — wartości nieużywane w praktyce (`daemon.py` bierze lewar ze `state_manager`, nie stąd), a układ `AGGRESSIVE(2.0) < MODERATE(3.0)` jest mylący dla każdego przyszłego czytelnika, bo sugeruje odwrotną hierarchię ryzyka niż faktycznie obowiązująca.

#### TICKET-P9.7: `high_risk_add` — wołać `_record_addon`, gate per-symbol, nie łykać wyjątków
- **Plik:** `src/mms4tk/api.py:470`
- **Dowód:** audyt §1.9g — dzisiejsza implementacja omija Single Addon Guard i raportuje `executed: True` nawet gdy w środku wystąpił wyjątek połknięty przez `except`.

---

### 🔹 FAZA 0b: P7 — Naprawić parametry silnika rekalibracji 🟠 (SZYBKA WYGRANA, WYSOKI PRIORYTET)

#### TICKET-P7.1: `train_test_validate` ma czytać VR z `system_settings`, nie z hardcoded defaultów
- **Plik:** `src/mms4tk/backtest_engine.py:457` (defaulty), `:549` (`run_calibration_job`, wchodzi w default bez `params`)
- **Dowód:** audyt §1.5 / P7(a) — hardcoded `adaptive_vr_high=1.0, adaptive_vr_low=0.75` to progi już zresetowane na produkcji w tej sesji audytowej. Silnik rekalibracji dziś symuluje strategię, która **już nie istnieje**.
- **Mechanizm (P7(b)):** nie podnosi PnL bezpośrednio — przywraca wiarygodność jedynego mechanizmu chroniącego strategię przed overfittingiem. Bez tego każdy przyszły werdykt PLAN-010 jest bezwartościowy.
- **Wzorzec implementacyjny (P7(c)):** czytać `engine_mode`/`adaptive_vr_*`/`time_lock_*` z `system_settings` przez `LoggerDB.get_all_settings()` — dokładnie tak jak `daemon._get_indicator_params` (`daemon.py:678-690`, gotowy wzorzec do skopiowania/reużycia).
- **Test wymagany:** dwa uruchomienia z jawnie różnymi progami VR muszą dać różne `full_closes` — to jedyny dowód, że parametr faktycznie dotarł do silnika. Dodatkowo assert: `train_test_validate` wywołany bez `params` **nie może** cicho spaść na literały.

**Przy tej samej okazji, ten sam plik (P7 "same file" follow-ons — do decyzji czy wchodzą w ten ticket czy osobny):**
- Dodać **gate na liczbę transakcji** (nie świec) przed werdyktem `VALIDATED` — dziś jedyne zabezpieczenie to próg 48 świec (`:541`). Rekomendacja audytu: min. 30 `full_closes` per połowa okresu (H1/H2).
- Zredukować siatkę parametrów do zatwierdzonych **4×4** (zgodnie z `TICKET-3` w starym `task.md`/PLAN-010) zamiast dzisiejszych **64 komórek** — 4× nadmiarowa ekspozycja na multiple testing, albo dodać wymóg płaskowyżu parametrów.
- Dodać model **Post-SL Cooldown (3 świece)** — audyt §2.1.
- **Szacowany wysiłek całości P7 wg audytu: ~3h.**

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

## ✅ Następny krok

**Brak kodu w tym commicie.** Ten plik + nowy worktree służą wyłącznie do rozmowy o
architekturze (kolejność ticketów, granice P7 "same file" follow-onów, decyzja
TICKET-P9.2/P9.3). Implementacja zaczyna się dopiero po wyraźnej zgodzie.
