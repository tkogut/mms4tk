# mms4tk — Obowiązujące decyzje i stan systemu

**Ostatnia weryfikacja: 2026-09-30, main @ `3ed0dea` (v0.48.9), stan flag zweryfikowany bezpośrednio na produkcyjnym `system_settings` przez SSH.**

Ten plik zastępuje `task.md` jako źródło prawdy o bieżącym stanie. `task.md` zostaje jako zarchiwizowany zapis historyczny (rozumowanie, siatki testowe, wyniki eksperymentów sprzed 2026-09-14) — nie jako aktualny opis systemu. Aktualizuj **ten** plik, gdy coś się zmieni; nie dopisuj do `task.md`.

---

## 1. Formalne decyzje w mocy

**Decyzja #3 (2026-09-12): MMS4 handluje pod nadzorem, nie jako w pełni autonomiczny bot 24/7 reagujący na każdy reżim rynkowy.**
Potwierdzona jako nadal obowiązująca i **nienaruszona** przez serię PR #73-79 (naprawa ochrony pozycji po incydencie XAG, 2026-09-28) — wręcz przeciwnie:
- `killall` (#73) wymaga klucza API i teraz faktycznie włącza maintenance lock — to wzmacnia nadzór, nie osłabia.
- Zewnętrzny watchdog (#74) tylko czyta stan i wysyła alert Telegram — nic nie wykonuje automatycznie.
- Ratchet SL (#75) i persystencja trackera (#77) to automatyczna **ochrona** już otwartej pozycji w ramach istniejącej strategii, nie nowa logika sygnałowa ani zmiana reżimu handlu.

**Zamknięty "Profitability Workstream":** zakaz wdrażania nowej logiki sygnałowej testowanej na tej samej 24-miesięcznej historii (fixture'y `*_swap_24m`, `*_max`) bez jakościowo nowego źródła dowodów (nowe dane produkcyjne, długi realny track record, albo zmiana reżimu widoczna na żywo).
- ⚠️ **Precedens naruszenia litery zakazu:** PR #84 (test kaskady EMA jako źródła `directional_bias`) użył tych samych zakazanych fixture'ów. Wynik był negatywny i nic nie trafiło do produkcji, więc szkody praktycznej nie było — ale to naruszenie procesu, nie tylko formalność. Traktuj to jako precedens do unikania, nie do powtarzania.
- `#70` (filtr wejść na zdegenerowanych świecach weekendowych) był **zgodny** z zamknięciem — użył realnych danych produkcyjnych (`closed_trades`), czyli dokładnie tego rodzaju "jakościowo nowego dowodu", który zakaz dopuszcza.
- Otwarte issue `#82` (SL strukturalny wstęga±k·ATR) świadomie ma bramkę "owner-gated" — wymaga Twojej wyraźnej zgody na ponowne otwarcie tego zakazu, zanim ktokolwiek to zmierzy.
- Otwarte issue `#80` (filtr wolumenu): krok 1 (instrumentacja na żywych danych) jest zgodny z zamknięciem; krok 2 (backtest na `*_USDC_1h_max`) **byłby** naruszeniem — jeśli do niego dojdzie, potraktuj to świadomie, nie przypadkiem.

**Odrzucono jako przedwczesne (2026-09-30): druga, alternatywna strategia (trend-following) z automatycznym przełączaniem reżimu.**
Powody: (a) wprost cofa Decyzję #3, (b) architektonicznie to nie jest "druga funkcja sygnałowa" — cały stos zarządzania pozycją (Inactivity Manager, 7 mechanizmów naprawionych w #67-79) wymagałby duplikacji, pozycje nie mają w schemacie tagu "który silnik otworzył", a konto OKX w trybie hedge pozwoliłoby obu silnikom otworzyć przeciwne nogi na tym samym symbolu jednocześnie. Cel ("nie tradować mean-reversion w silnym trendzie") już jest realizowany taniej przez bramkę H4 (patrz niżej).

---

## 2. Stan flag na produkcji (zweryfikowany bezpośrednio, nie z dokumentacji)

| Flaga | Wartość domyślna w kodzie (`analytics.py`) | Stan na produkcji |
|---|---|---|
| `midline_partial_tp_enabled` | `False` | wyłączony (odrzucony 6/6 w backteście 90-dniowym, 2026-09-02) |
| `tma_trend_filter_enabled` | `False` | wyłączony — **status sporny, patrz sekcja 4** |
| `high_vol_base_filter_enabled` | `False` | wyłączony |
| `htf_directional_gate_enabled` | `False` (kod) | **`true` dla WSZYSTKICH 4 aktywnych symboli** (BTC/USDC, ETH/USDC, XAU/USDC, XAG/USDC) — włączane per-symbol ręcznie 2026-09-13/14 |
| `base_sl_pct` | `0.02` (env `MMS4_BASE_SL_PCT`) | 2% — jedyna wartość z jakimkolwiek pomiarem za sobą (patrz sekcja 3) |

Aktywne symbole: **BTC/USDC, ETH/USDC, XAU/USDC, XAG/USDC**. SOL/ICP/XRP zostały usunięte z aktywnego handlu (wcześniejszy commit, przed 2026-09-14).

**Epoka danych** (`data_collection_epoch_start`, filtr do liczników KPI/rekalibracji — nie kasuje historii): zbita **2026-09-28T20:39:00Z** dla wszystkich 4 symboli po wdrożeniu ratchetu SL (#75), bo zmienił rozkład wyników wyjść dla pozycji przechodzących przez split. Transakcje sprzed tej daty należą do starego reżimu przy liczeniu "ile czystych transakcji zebraliśmy pod bieżącym kodem" (cel: ~175/symbol przed jakąkolwiek rekalibracją).

---

## 3. ⚠️ Ważne zastrzeżenie: backtest nie opisuje w pełni obecnej produkcji

`backtest_engine.py` **nie modeluje** dwóch mechanizmów wdrożonych na żywo:
1. **Ratchet SL po SPLIT_50** (#75, `daemon.py`) — w backtescie SPLIT_50 tylko zmniejsza pozycję, nigdy nie przesuwa SL.
2. **Profit Giveback Protection liczony wewnątrz świecy** — backtest wywołuje logikę tylko na zamknięciu świecy, produkcja sprawdza na żywo.

**Konsekwencja:** wszystkie liczby bezwzględne z `sl_grid_analysis.py` (np. "SL 1.5% przegrywa z 2.0% w 9/10 komórek") opisują zachowanie **sprzed** #75 dla pozycji, które przechodzą przez split. Kierunkowy wniosek (2.0% lepsze niż 1.5%) niekoniecznie jest unieważniony, ale nie traktuj tych liczb jako precyzyjnego opisu dzisiejszej produkcji. Jeśli ktoś kiedyś chce ponownie testować siatkę SL (np. przy realizacji #82), harness backtestowy najpierw wymaga aktualizacji o te dwa mechanizmy.

---

## 4. Nierozstrzygnięte sprzeczności do zamknięcia

- **Status filtra TMA slope — sprzeczność rozwiązana (2026-09-30).** Poprzedni zapis w tej sekcji mówił "nikt tego nie rozstrzygnął pomiarem" — to zbyt mocne sformułowanie, historia jest bardziej precyzyjna:
  - **2026-08-29**: filtr (`tma_trend_filter_enabled`, PLAN-008 Faza 3) przeszedł rygorystyczny test train/test-split (nie tylko full-window backtest) — **przegrał out-of-sample na obu symbolach** (BTC: konfiguracja live to artefakt dopasowania do połowy okna, nie działa na drugiej połowie; ETH: przegrywa w obu kierunkach). W konsekwencji wyłączony na `BTC/USDC` i `ETH/USDC` tego samego dnia (`system_settings`, `updated_by=api_dashboard`).
  - **Ale ten test był na tym samym 90-dniowym oknie**, na którym filtr wcześniej wyglądał na wygraną (Faza 1/2, `task.md`) — `task.md` słusznie flagował, że to okno jest niereprezentatywne (spokojny reżim rynkowy, nie obejmuje silnych trendów jak rajd BTC 2024/2025) i że **pełna 24-miesięczna historia nigdy nie została przeliczona** tą metodologią. To pozostaje faktycznie otwarte — nie dlatego że nikt nie mierzył, tylko dlatego że dostępny pomiar dotyczy złego okna.
  - `.agents/MEMORY.md`'s starsze twierdzenie "zrewalidowany na długiej historii" było błędne i zostało już skorygowane w PR #86.
  - **Stan live zweryfikowany bezpośrednio przez SSH (2026-09-30)**: `tma_trend_filter_enabled` = `False` dla **wszystkich 4 symboli** (BTC/ETH: `updated_by=api_dashboard`; XAU/XAG: `updated_by=coordinator`, prawdopodobnie ustawione przez analogię przy wdrożeniu tych symboli 2026-09-14, nigdy nie testowane wprost). Bezpieczny, zachowawczy stan — nic do zrobienia teraz.
  - **Recalibration trigger (`[[plan008-phase3-trend-filter-overfit]]`, uzgodniony 2026-08-29) nadal nie jest spełniony**: wymaga ~150-200 czystych transakcji/symbol pod bieżącą epoką. Zmierzone na żywo 2026-09-30 pod epoką `2026-09-28T20:39:00Z`: BTC=4, ETH=6, XAU=10, XAG=0 — daleko od progu. Dopóki próg nie jest spełniony, nowy pełnohistoryczny test train/test i tak byłby przedwczesny (ten sam błąd co pierwsza porażka filtra — dopasowanie do zbyt małej/niereprezentatywnej próbki). `PLAN-010`'s mechanizm analyze→apply (już wdrożony, v0.37.0-v0.39.0) jest gotowym narzędziem na tę chwilę, gdy próg zostanie osiągnięty — nie trzeba nowego dedykowanego skryptu teraz.
- **Luka zakresu dowodowego:** siatka SL, filtr high-vol i D1 ATR exhaustion nadal dotyczą wyłącznie BTC/ETH — XAU/XAG handlują na żywo od 2026-09-14 bez pomiaru tych trzech, flagi ustawione przez analogię.
  - **Bramka H4 (`htf_directional_gate_enabled`) — ta pozycja zamknięta 2026-10-15** (`.ai/analysis/2026-10-15-htf-gate-xau-xag-backtest.md`), po incydencie XAG/USDC tego samego dnia (wejście przeciw silnemu odreagowaniu po krachu -6,5%, z bramką H4 wymuszającą SHORT_ONLY). Ta sama metodologia co oryginalny pomiar BTC/ETH (okna 122-dniowe, `run_backtest`, sign test): **XAU 4/4 okien poprawa (p=0,125, n za małe na istotność samodzielnie), XAG 2/2 okna poprawa (p=0,500), łącznie XAU+XAG 6/6, p=0,0313 — istotne statystycznie.** Próbka mniejsza niż przy BTC/ETH (XAU/XAG notowane na instrumencie proxy od 2025-04-09, krótsza dostępna historia), ale kierunek w pełni spójny z wynikiem BTC/ETH: bramka redukuje straty (~39-46% w zmierzonych oknach), nie czyni strategii zyskowną. **Decyzja: `htf_directional_gate_enabled=true` zostaje dla XAU/USDC i XAG/USDC** — "ustawione przez analogię" jest teraz retroaktywnie potwierdzone pomiarem, nie tylko założeniem.

---

## 5. Odrzucone hipotezy (nie proponuj ponownie bez nowego dowodu)

| Hipoteza | Wynik | Źródło |
|---|---|---|
| Midline Partial TP | Odrzucony 6/6 okien (backtest 90-dniowy) | `task.md` (archiwum), 2026-09-02 |
| Filtr wejść na zdegenerowanych świecach weekendowych | Odrzucony — niedzielne wejścia wypadły średnio *lepiej* niż w dni robocze | issue #70, dane produkcyjne |
| Kaskada EMA 26/50/144/200 jako źródło `directional_bias` | Odrzucony — bije bramkę H4 tylko w 7/16 rozgrzanych okien, blokuje ~4x więcej wyjść TP-and-flip | PR #84, `scripts/ema_cascade_gate_analysis.py` |
| Druga strategia (trend-following) + auto-przełączanie reżimu | Odrzucony jako przedwczesny | sekcja 1 wyżej |
| Natywny trailing stop OKX (`move_order_stop`) jako zamiennik software'owego | Odrzucony — inna matematyka, ryzyko osieroconych zleceń, niepewne wsparcie EEA/XPERP | audyt ochrony pozycji, 2026-09-28 |
| SL na knocie świecy sygnałowej (wariant z "EMA Alerts") | Odrzucony przed wdrożeniem — własna siatka testowa już pokazuje że ciaśniejszy SL (1.5%) przegrywa z 2.0% | issue #82 |
| D1 ATR exhaustion filter | Odrzucony, efekt zero (0/16 okien bit-for-bit identyczne) | `task.md` (archiwum) |

## 6. Otwarte, niezmierzone (backlog)

- **#80** — filtr wolumenu na wejściach (próg względny, nie absolutny — BTC/ETH i XAU/XAG czytają wolumen z różnych rynków).
- **#81** — martwe pole `SignalResult.stop_loss`, drobny refaktor, zero ryzyka.
- **#82** — SL kotwiony do wstęgi±k·ATR, **wymaga Twojej zgody** na ponowne otwarcie zamkniętego zakazu (sekcja 1).
