# SPEC: Analiza wykonalności i plan wdrożenia — „Audyt Kwantytatywny i Plan Naprawczy mms4tk”

**Data:** 2026-10-14 · **Baza:** `main` @ `4049bc9` (VERSION `0.48.14`)
**Źródło:** `docs/Audyt Kwantytatywny i Plan Naprawczy mms4tk.md` (dalej „RFC”)
**Status:** DRAFT — wymaga decyzji właściciela (sekcja 7) przed jakąkolwiek implementacją zmieniającą zachowanie na produkcji.

---

## 0. TL;DR

RFC trafnie wskazuje dwa realne defekty, ale opisuje kod w wersji v0.33–v0.44.6, a nie obecny stan v0.48.14. Część zaleceń jest już wdrożona, część zawiera błędy rachunkowe, a część wprost wznawia hipotezy już odrzucone w `DECISIONS.md` §5.

| # | Twierdzenie RFC | Werdykt | Wykonalność | Ryzyko |
|---|---|---|---|---|
| 1 | „Expectancy killers” w zarządzaniu pozycją | **Częściowo prawdziwe.** Mechanizmy istnieją; Midline TP już wyłączony; brak „T2” | Wysoka (flaga, domyślnie OFF) | Średnie: cofa ochrony z #67–79 (incydent XAG) |
| 2a | SCOUT = 0.3× zamiast 0.1× | **Fałszywe co do efektu.** Efektywny nominał SCOUT wynosi już 0.1× BASE | — (nic do zmiany) | — |
| 2b | Reset SCOUT→BASE na „symbolicznym” zysku | **Prawdziwe, poważniejsze niż w RFC** — `TP_AND_FLIP` resetuje nawet przy **ujemnym** PnL | Wysoka | Niskie (to bugfix) |
| 2c | Paper Scout | **Nie istnieje; rozwiązuje realny problem** (symbol poniżej minimum utyka w SCOUT na zawsze) | Średnia | Niskie–średnie |
| 3 | Alokacja 70/30 + SL dokładki 1.25% | 70/30 prawdziwe; **SL dokładki 1.25% nie jest używany od P3 V2** — dokładka nie ma własnego SL | Wysoka (alokacja = env) | Zmiana ryzyka per sygnał |
| 4 | Brak izolacji SL dokładki (netting) | **Prawdziwe.** Jeden stop `closeFraction=1` na całą pozycję; tryb konta OKX niezweryfikowany | Średnia–niska | **Wysokie** (logika zleceń na giełdzie) |
| 5a | TMA lag / filtr nachylenia | TMA nie jest centrowana (zgodnie z RFC); filtr nachylenia istnieje, ale w %/świecę, nie znormalizowany ATR; **H4 gate już działa na wszystkich 4 symbolach** | Wysoka (nowy tryb) | Zakaz `DECISIONS.md` §1 |
| 5b | Filtr D1 overextension 2.5×ATR | **Odrzucony wcześniej** (0/16 okien, efekt zerowy) | — (nie wdrażać) | — |
| 5c | Rolling WFO | Brak w kodzie; **metodologicznie zgodny** z warunkiem z `task.md` (N→N+1 OOS) | Średnia (wymaga wydajności harnessu) | Okna IS 3–14 dni są statystycznie za małe |

**Rekomendacja:**
1. Najpierw dostarczyć narzędzia pomiaru: wierność harnessu + WFO.
2. Potem bugfix resetu SCOUT.
3. Mechanizmy „Pure v4” wdrażać wyłącznie jako flagi domyślnie OFF, z ablacją.
4. Nic nie włączać na produkcji bez decyzji właściciela, bo „Profitability Workstream” jest zamknięty (`DECISIONS.md` §1).

---

## 1. Kontekst i ograniczenia nadrzędne

1. **Zamknięty „Profitability Workstream”** (`DECISIONS.md:17`). Obowiązuje zakaz wdrażania nowej logiki sygnałowej testowanej na tej samej 24-miesięcznej historii (`*_swap_24m`, `*_max`) bez jakościowo nowego dowodu.
   - Zadania 4 i 5 RFC oraz test A/B na oknie 610-dniowym używają dokładnie tych fixture'ów.
   - PR #84 jest udokumentowanym precedensem naruszenia tego zakazu.
   - **Ta specyfikacja wymaga jawnej zgody właściciela na pomiar** (decyzja D1).
2. **Decyzja #3:** handel pod nadzorem. Wyłączenie ochron pozycji (Zadanie 1) jest zmianą profilu ryzyka, a nie tylko „poprawką”.
3. **Wierność backtestu** (`DECISIONS.md` §3). Harness nie modeluje dwóch mechanizmów:
   - ratchetu SL po SPLIT_50 (#75),
   - wewnątrzświecowego Profit Giveback.

   Każde A/B „baseline vs Pure v4” bez tej poprawki porównuje Pure v4 z baseline'em sprzed #75, a nie z produkcją.
4. **Luka dowodowa XAU/XAG.** Cały materiał eksperymentalny dotyczy BTC/ETH. Wniosków z ablacji nie wolno automatycznie przenosić na metale.
5. **Interpretacja „−23%”.** To mediana **na okno 122-dniowe** (5 okien, łącznie 610 dni, `task.md:1004-1040`, `scripts/ab_engine_mode_analysis.py`), a nie wynik za całe 610 dni. Bramka H4 poprawiła 10/10 okien (+6.2 pp średnio), ale wynik zbiorczy nadal jest głęboko ujemny (−218% → −156% sumarycznie).

---

## 2. Weryfikacja twierdzeń RFC względem kodu

### 2.1 Błąd #1 — mechanizmy wczesnego wyjścia

Stan faktyczny (`src/mms4tk/inactivity_manager.py`, `_update_and_check_impl`, kolejność ewaluacji):

| Mechanizm | Wyzwalacz | Flaga | Lokalizacja |
|---|---|---|---|
| Profit Giveback | peak ≥ 1.0 ATR i bieżący zysk ≤ 50% peak → SPLIT_50 | brak | `inactivity_manager.py:466-475` |
| Profit Stall Gate *(nie wspomniany w RFC)* | zysk ≥ 0.6 ATR, brak nowego szczytu przez 4 świece → SPLIT_50 | brak | `:476-496` |
| Impulse negation | retrace 50% (38.2% przy impulsie ≥ 0.8 ATR) → SPLIT_50, potem FORCE_CLOSE | `impulse_negation_enabled` (domyślnie True) | `:501-507`, `:580-627` |
| Stagnation ladder | 12 świec w ±0.5 ATR → SPLIT_50; +5 świec → FORCE_CLOSE_BE | brak (progi nadpisywalne) | `:515-540` |
| BE_ESCAPE | stagnacja + cena w ±0.2% od wejścia + PnL ≥ koszt | brak | `:547-566` |
| Ratchet SL po SPLIT_50 (#75) | przesuwa stop giełdowy na poziom negacji | brak | `daemon.py:1718-1760` |
| Midline Partial TP | linia środkowa TMA | `midline_partial_tp_enabled`, **domyślnie False, wyłączony na produkcji** od 2026-09-02 (odrzucony 6/6) | `analytics.py:161`, `daemon.py:1517-1658` |

**Korekty do RFC:**
- Midline TP jest już wyłączony; nie znajduje się w `inactivity_manager.py`, tylko w `daemon.py`.
- „T2 Opposite Band TP” nie istnieje jako osobny mechanizm. Normalne TP to sygnał na przeciwległej wstędze → `TP_AND_FLIP` (`daemon.py:1899-1963`). Nie ma zlecenia TP po stronie giełdy.
- Z `system_settings` nadpisywalne są tylko 3 klucze: `stale_candles_threshold`, `stale_atr_threshold`, `impulse_negation_enabled` (`daemon.py:1100-1112`).
- Uwaga: TP na przeciwległej wstędze może zostać zablokowany przez veto/maintenance/cooldown po SL (`daemon.py:1840-1882`). W „Pure v4” pozycja wychodziłaby wtedy wyłącznie przez SL. To trzeba rozdzielić (sekcja 4, PR-3).

**Wniosek:** teza o asymetrii R:R jest spójna z dowodem z Midline TP (6/6). Dla pozostałych mechanizmów nie ma pomiaru ablacyjnego. Pomiar jest wykonalny, bo backtest modeluje wszystkie mechanizmy poza ratchetem (`backtest_engine.py:680-685`, `:868-893`).

### 2.2 Błąd #2 — SCOUT_MODE

**2a. Mnożnik — RFC się myli.** Wielkość pozycji w SCOUT liczy się tak:

```
margin   = max_margin × base_ratio × SCOUT_SIZE_MULT(0.3)
notional = margin × SCOUT_LEVERAGE(1.0)
```

Dla BASE przy MODERATE: `notional = margin × 3.0`. Stosunek SCOUT/BASE = (0.3 × 1.0) / 3.0 = **0.10×** (`daemon.py:355-365`, `:424-425`). RFC jako wartość docelową podaje dokładnie 0.1×. Ustawienie `SIZE_MULT=0.1` przy obecnej dźwigni dałoby ~0.033×.

Osobna kwestia z RFC to dźwignia bazowa 3.0× (SL 2% ≈ 6% marży na pozycję). To decyzja o profilu ryzyka (D4), a nie defekt.

**2b. Reset SCOUT→BASE — defekt potwierdzony i poważniejszy, niż opisuje RFC.** Ścieżki resetu w `state_manager.py`:

| Ścieżka | Warunek PnL | Lokalizacja |
|---|---|---|
| Pełny TP (`TP_AND_FLIP`, `CLOSED_EXTERNAL_TP`, `D1_RANGE_TP`, `MIDLINE_FULL_TP`) | **brak warunku** | `state_manager.py:145-157`, `:634-645`, `:690-694` |
| `TP_AND_FLIP` | PnL liczony z `signal.price` (cena świecy, nie fill); **może być ujemny i nadal resetuje** | `daemon.py:1955-1969` |
| `CLOSED_EXTERNAL_TP` | brutto > 0 — np. ratchetowany SL zamknięty z minimalnym zyskiem | `daemon.py:780-781` |
| `SCOUT_EXPIRED_K` | 3 kolejne „czyste” FORCE_CLOSE (brutto > koszt) | `:759-796` |
| Ręcznie (`relax_scout_mode` / `set_scout_mode`) | — | `:504-511`, `:561-565` |

Pozostałe ustalenia:
- BASE→SCOUT następuje wyłącznie przy `STOP_LOSS` z `sl_percent >= 0.02` (`state_manager.py:617`). SL < 2% nigdy nie przełącza trybu.
- `BE_ESCAPE` i `FORCE_CLOSE_BE` **nie** zmieniają stanu (`:776-777`), wbrew obawie RFC.
- Maszyna stanów dostaje tylko PnL brutto; opłaty istnieją w `closed_trades.fee_usd` / `pnl_net_usd`, ale nie są przekazywane do `finalize_close`.
- Dodatkowy defekt: `is_high_risk_add_allowed()` czyta stan symbolu **głównego**, a nie docelowego (`state_manager.py:587`, używane w `api.py:821`), co zgłoszono już w audycie z 2026-09-08.

**2c. Paper Scout — brak.** Brak tabel wirtualnych pozycji i trybu shadow/paper (`logger_db.py:94-338`). Obecnie wejście SCOUT poniżej minimum notional jest **pomijane** (`okx_executor.py:552-558`, `:703-713`; `daemon.py:2024-2029`). Symbol w takim stanie może wyjść ze SCOUT tylko ręcznie. Właśnie to wykrywa alert `SCOUT_STALE` (#119), ale go nie naprawia. Paper Scout usuwa tę martwą pętlę i ma wartość niezależnie od reszty RFC.

### 2.3 Błąd #3 — dokładka

| Element | RFC | Kod |
|---|---|---|
| Alokacja | 70/30 | Zgadza się: `MMS4_BASE_ALLOCATION=0.70`, `ADDON=1-BASE` (`state_manager.py:99-100`) — zmienialne przez env bez kodu |
| SL dokładki | sztywne 1.25% | **Nieaktualne.** `MMS4_ADDON_SL_PCT` jest martwy od P3 V2. Dokładka idzie z `stop_loss_pct=0.0` (`daemon.py:2155-2170`), a jedynym stopem jest 2% od `base_entry_price` na całą pozycję |
| Blokada ponownej dokładki | `addon_used_in_cycle` | Istnieje odpowiednik: tabela `position_addons` (symbol, side), czyszczona dopiero przy zamknięciu całej strony (`logger_db.py:196-204`, `:1472-1511`) |
| SL na knotach ≤ 1% | nowe | Nie istnieje. **Zbliżone hipotezy odrzucone:** „SL na knocie świecy sygnałowej” (`DECISIONS.md:75`, dla bazy, bez bezpośredniego testu) oraz #82 jest owner-gated |

Rachunek RFC („dokładka = 42.8% bazy”) jest poprawny dla 70/30. Natomiast twierdzenie „ryzyko dokładki = połowa ryzyka bazy” zakłada SL ≤ 1% na dokładce. Obecnie dokładka ryzykuje tyle, ile wynosi dystans od ceny dokładki do stopu 2% bazy. Po potwierdzeniu w kierunku pozycji jest to **więcej** niż 2% od ceny dokładki, a nie mniej.

### 2.4 Błąd #4 — izolacja SL na OKX

Potwierdzone, z dodatkowym ryzykiem:
- SL to jedno zlecenie algo `ordType=conditional`, `closeFraction="1"` (cała pozycja), bez `sz` i bez `reduceOnly` (`okx_executor.py:889-899`).
- Przed postawieniem nowego SL kod **anuluje wszystkie** oczekujące zlecenia warunkowe dla danego `posSide` (`okx_executor.py:871-885`). Każdy re-attach (self-healing `daemon.py:1382`, po Midline TP `:1568`, ratchet `:1729-1753`) skasowałby osobny SL dokładki. Implementacja wymaga tagowania zleceń (`algoClOrdId`) i selektywnego anulowania.
- Pozycja to jeden rekord (`position_snapshots`, jeden wiersz na symbol+side, `logger_db.py:214-233`), bez nóg base/addon. SPLIT_50 i FORCE_CLOSE działają na całości (`okx_executor.py:1244-1267`).
- **Tryb konta niezweryfikowany.** Kod wysyła `posSide` long/short (hedge), ale `set_hedge_mode()` nie jest nigdzie wywoływany (`okx_executor.py:906-921`). Z kolei `docs/AUDIT_MMS4_PROFIT_OPTIMIZATION_2026-09-08.md:17` twierdzi, że konto **nie** jest w trybie hedge. To prerekwizyt każdej pracy nad izolacją (D5).
- Backtest również nie modeluje nóg: dokładka uśrednia wejście, a SL jest jeden (`backtest_engine.py:925-968`).

### 2.5 Błąd #5 — TMA, bramki, metodologia

- **TMA:** `SMA(SMA(close, ceil(L/2)), ceil(L/2))`, trailing (bez `center`), L=20, ATR14 SMA, mnożnik 2.0 (`analytics.py:107-110`, `:219-242`). Nie repaintuje, bo świeca formująca się jest odrzucana (`:325-336`). Teza o opóźnieniu fazowym jest poprawna co do konstrukcji.
- **Rozjazd interwałów (nieopisany w RFC):** live używa `timeframe` z konfiguracji symbolu z fallbackiem **`15m`** (`daemon.py:1201-1214`), a backtest i kalibracja działają **wyłącznie na H1** (`backtest_engine.py:107-108`). Każdy wniosek z backtestu dotyczy więc H1. Przed jakimkolwiek wdrożeniem trzeba potwierdzić interwał produkcyjny symboli (D6).
- **Filtr nachylenia:** `tma_trend_filter_enabled` już istnieje (`analytics.py:129-131`, `:261`, `:558`, `:650`), ale liczy slope jako `pct_change(lookback=5)` z progiem 0.002, czyli **bez normalizacji ATR**. Na produkcji jest OFF dla wszystkich 4 symboli. Przegrał OOS na 90 dniach; na pełnej historii nie był testowany (`DECISIONS.md` §4). Wariant `Slope_norm = ΔTMA / (k·ATR)` z RFC to mała zmiana: nowy tryb tego samego filtra.
- **Bramka H4 (kontekst HTF):** `htf_directional_gate_enabled` = **ON dla wszystkich 4 symboli** (`DECISIONS.md:35`). Postulat RFC „filtrowanie kierunku przez H4” jest więc już zrealizowany.
- **D1 overextension (2.5×ATR):** zaimplementowany w backteście (`backtest_engine.py:351-423`) i **odrzucony**: 0/16 okien zmienionych (`DECISIONS.md:76`). Nie wdrażać ponownie.
- **WFO:** brak. Jedyny OOS to dwukierunkowy podział 50/50 (`backtest_engine.py:1045-1132`). Rolling rekalibracja TMA/ATR (`scripts/rolling_tma_atr_recalibration.py`) została odrzucona jako efekt selekcji in-sample. `task.md:2145-2180` wprost zaleca powtórkę tylko w układzie N→N+1 OOS, a WFO to właśnie ten układ.
  - **Bariera wydajności:** `evaluate_signals` przelicza całe rosnące okno w każdym kroku, czyli O(n²). Stąd chunking po 122 dni. WFO z setkami okien jest niewykonalne bez wektoryzacji wskaźników.
  - **Bariera statystyczna:** okno IS 3–14 dni na H1 to 72–336 świec, a więc zwykle < 30 transakcji. Kryterium `≥30 closes` z `train_test_validate` jest wtedy nieosiągalne. Proponuję IS ≥ 60 dni, OOS 7–14 dni, krok = OOS (do decyzji D7).

---

## 3. Wymagania (po korekcie)

| ID | Wymaganie | Źródło | Charakter |
|---|---|---|---|
| R1 | Reset SCOUT→BASE tylko przy pełnym TP z **netto** PnL > 0 liczonym z faktycznego fill | RFC #2 (zaostrzone) | Bugfix |
| R2 | `is_high_risk_add_allowed(symbol)` czyta stan symbolu docelowego | audyt 09-08 | Bugfix |
| R3 | Harness modeluje ratchet SL po SPLIT_50 oraz intra-candle giveback (aproksymacja high/low) | `DECISIONS.md` §3 | Wierność pomiaru |
| R4 | Harness: wskaźniki liczone raz (wektorowo) i odczytywane po indeksie; wynik bit-for-bit zgodny z obecnym | wydajność | Refaktor |
| R5 | Runner WFO N→N+1 z raportem per okno OOS i agregatem | RFC #5 | Narzędzie |
| R6 | Per-symbol flaga `pure_v4_execution` (domyślnie OFF), wyłączająca: Giveback, Profit Stall, Impulse negation, Stagnation ladder, BE_ESCAPE, ratchet, Midline TP — w daemonie **i** backteście | RFC #1 | Feature flag |
| R7 | Paper Scout: gdy SCOUT i wolumen < min notional, wirtualna pozycja w SQLite, wycena na żywych świecach, symulowane opłaty; zamknięcie przez ten sam `finalize_close` | RFC #2 | Feature flag |
| R8 | Tryb nachylenia `tma_slope_mode ∈ {pct, atr}` (domyślnie `pct`); `atr` = ΔTMA/(k·ATR14) | RFC #4 | Feature flag |
| R9 | Izolacja SL dokładki: osobne zlecenie warunkowe `sz=Q_add`, `reduceOnly`, tagowane `algoClOrdId`; selektywne anulowanie; noga dokładki w `position_addons` | RFC #3/#4 | Feature flag, wysokie ryzyko |
| R10 | SL dokładki na parze knotów z limitem 1% (blokada albo cap — konfigurowalne) | RFC #3 | Feature flag, wymaga R9 |
| ~~R11~~ | ~~Filtr D1 overextension~~ | RFC #4.3 | **Wykluczone** (odrzucone 0/16) |

**Poza zakresem:** zmiana domyślnego `MMS4_SCOUT_SIZE_MULT` (efekt docelowy 0.1× jest już osiągnięty); zmiana `MMS4_LEVERAGE_MODERATE` i alokacji 50/50 na produkcji. Oba to parametry env, ustawiane decyzją właściciela, bez kodu.

---

## 4. Plan wdrożenia (PR-y)

Kolejność wynika z zależności: najpierw pomiar, potem bugfixy, potem flagi, a decyzje produkcyjne na końcu. Każdy PR jest atomowy, ma testy, przechodzi QA gate z `.ai/agentic.config.json` i zmienia wersję w `CHANGELOG.md`.

### Faza A — fundament pomiarowy (bez wpływu na produkcję)

**PR-1: Wierność i wydajność harnessu (R3, R4)**
- Pliki: `src/mms4tk/backtest_engine.py`, `tests/test_backtest_engine*.py`.
- Precompute wskaźników (TMA/ATR/VR/slope/H4 bias) raz na serię; pętla świecowa czyta po indeksie.
- Ratchet: po SPLIT_50 SL = `max/min(anchor_2%, compute_negation_level())` — ta sama funkcja co `daemon.py:1729-1753`, wydzielona do współdzielonej funkcji czystej.
- Intra-candle giveback: test na high/low świecy (konserwatywnie: najpierw adverse extreme).
- Flaga parametru `model_ratchet=True` (domyślnie True), żeby dało się odtworzyć stare wyniki.
- **AC:**
  - Przy `model_ratchet=False` i wyłączonym giveback-intracandle wyniki 5×122d są bit-for-bit identyczne z obecnymi (test regresyjny na fixture).
  - Czas przebiegu 122d spada ≥ 5×.
  - Test jednostkowy ratchetu zgodny z daemonem.

**PR-2: Runner WFO (R5)**
- Nowy `scripts/walk_forward.py` + funkcja `walk_forward()` w `backtest_engine.py`.
- Parametry: `is_days`, `oos_days`, `step_days`, siatka; selekcja na IS, ocena na następnym OOS, bez zaglądania w przyszłość.
- Raport: tabela per okno OOS, agregat (suma netto, MaxDD, R:R, liczba transakcji), stabilność wyboru parametrów, porównanie z „frozen baseline” na tych samych OOS.
- **AC:** test syntetyczny dowodzi braku look-ahead (OOS nie widzi danych po swoim końcu); okno IS z < N transakcji jest raportowane jako `INSUFFICIENT`, a nie wybierane.

### Faza B — bugfixy maszyny stanów (ryzyko niskie, wdrażalne bez zmiany profilu)

**PR-3: Reset SCOUT tylko na netto-dodatnim pełnym TP (R1, R2)**
- `state_manager.finalize_close(...)` dostaje `fee_usd` / `pnl_net` (opcjonalne; brak → estymata kosztu z `MMS4_SCOUT_MIN_GROSS_PNL_PCT`).
- Pełny TP resetuje SCOUT tylko przy `pnl_net > 0`.
- `TP_AND_FLIP` w daemonie: PnL z faktycznego fill zamknięcia (pobrany jak w `trade_sync`), z fallbackiem na `signal.price` + log WARNING.
- `CLOSED_EXTERNAL_TP` z ratchetu: wymóg `pnl_net > 0` (domyślnie). Do decyzji D3, czy ratchet w ogóle ma liczyć się jako „pełny TP”.
- `is_high_risk_add_allowed(symbol)`: parametr symbolu + poprawka wywołania w `api.py:821`.
- Backtest korzysta z tego samego `finalize_close`, więc zmiana przenosi się automatycznie.
- **AC:**
  - Testy: TP_AND_FLIP z ujemnym netto → SCOUT zostaje; dodatnim → BASE; CLOSED_EXTERNAL_TP poniżej kosztu → SCOUT zostaje.
  - R2: stan symbolu B nie wpływa na dokładki symbolu A.
  - Wpis w CHANGELOG z opisem zmiany semantyki.

### Faza C — mechanizmy RFC jako flagi (domyślnie OFF)

**PR-4: `pure_v4_execution` (R6)**
- Klucz per-symbol w `system_settings` (wzorem `htf_directional_gate_enabled`), czytany w `daemon.py` obok `:1100-1112`.
- Jeden punkt bramkowania w daemonie: początek bloku inactivity `daemon.py:1661` (obejmuje ratchet) + Midline `:1535`.
- W backteście: parametr `pure_v4=True` pomija `track_position`/`update_and_check` (`:868-893`, `:918`, `:986`) i Midline.
- Tracking pozycji dla telemetrii (MFE/MAE) zostaje.
- Rozdzielenie veto: przy `pure_v4` opposite-band TP nie może być blokowany przez veto kierunkowe ani cooldown po SL (`daemon.py:1840-1882`) — blokada dotyczy tylko nowego wejścia.
- **AC:** test, że przy fladze żaden z 7 mechanizmów nie emituje akcji; test, że TP na przeciwległej wstędze działa mimo veto; flaga OFF → zero zmian (testy regresyjne istniejące przechodzą).

**PR-5: Tryb nachylenia ATR (R8)**
- `analytics.py`: `tma_slope_mode` (`pct` | `atr`), `tma_slope_atr_threshold` (np. 0.25). Ta sama logika blokowania BASE_LONG/BASE_SHORT (`:558`, `:650`) i flip re-entry w backteście (`:977-980`).
- Siatka kalibracji `train_test_validate` rozszerzona o tryb `atr` z θ ∈ {0.15, 0.25, 0.35, 0.50}.
- **AC:** dla `mode=pct` wyniki niezmienione; test jednostkowy formuły na danych syntetycznych (stały trend → stały slope_norm).

**PR-6: Paper Scout (R7)**
- `logger_db.py`: tabela `virtual_scout_positions` (`id, symbol, side, entry_ts, entry_price, notional_usd, sl_price, status, exit_ts, exit_price, exit_reason, fee_usd_sim, pnl_net_usd_sim`) + migracja addytywna.
- Daemon: w SCOUT, gdy `BelowMinimumNotional` / amount = 0 i flaga `paper_scout_enabled` → otwarcie wirtualnej pozycji.
- Wycena w istniejącej pętli (SL 2% od wejścia na high/low świecy; TP = sygnał przeciwległej wstęgi). Opłaty taker × 2 ze stałej harnessu (0.10003% RT).
- Zamknięcie przez `finalize_close(..., is_virtual=True)` → te same reguły PR-3.
- Wirtualne transakcje są **wyłączone** z KPI dashboardu i `closed_trades` (osobna tabela), ale widoczne w panelu SCOUT i w alertach Telegram.
- Wyłącznie jedna wirtualna pozycja na symbol; brak dokładek.
- **AC:** test pełnego cyklu: SCOUT → wirtualne wejście → wirtualne TP netto > 0 → BASE; wirtualny SL → zostaje SCOUT; restart daemona przywraca otwartą wirtualną pozycję.

### Faza D — izolacja dokładki (najwyższe ryzyko, warunkowo)

**PR-7a (prerekwizyt, read-only): weryfikacja trybu konta**
- Przy starcie: `GET /api/v5/account/config` → `posMode`; log + alert, jeśli ≠ `long_short_mode`; ekspozycja w `/health`.
- Nie zmienia trybu automatycznie (Decyzja #3).

**PR-7b: Noga dokładki i osobny SL (R9, R10)** — tylko jeśli D5 = „realizujemy”
- `okx_executor`:
  - `place_stop(..., sz=None|Q, tag="base"|"addon")` z `algoClOrdId` = `mms4-{symbol}-{side}-{tag}-{ts}`;
  - anulowanie wyłącznie zleceń z tym samym tagiem (zastępuje `:871-885`).
  - Bazowy stop zmienia `closeFraction=1` na `sz=Q_base`, żeby nie zamknąć niczego ponad bazę po wybiciu dokładki. Wymaga to aktualizacji `sz` przy SPLIT_50.
- `position_addons`: kolumny `qty`, `entry_price`, `sl_price`, `algo_cl_ord_id`, `status`.
- Reconcile rozpoznaje zamknięcie częściowe = wybity SL dokładki → status `STOPPED`; blokada ponownej dokładki w cyklu zostaje (istniejąca semantyka tabeli).
- SL knotowy: `min(Low[t-1], Low[t-2]) - tick` (LONG) / `max(High...) + tick` (SHORT); Δ > 1% → `addon_sl_cap_mode ∈ {block, cap}`.
- Backtest: dwie nogi z niezależnymi SL; wybicie SL dokładki nie zmienia stanu SCOUT (SL dokładki < 2%, zgodnie z `state_manager.py:617`).
- **AC:**
  - Testy na mocku OKX: re-attach bazowego SL nie kasuje SL dokładki; wybicie SL dokładki zostawia `Q_base` z oryginalnym stopem.
  - Test integracyjny na **OKX demo/sandbox** (`okx_executor.py:155`) przed produkcją.
  - Runbook rollbacku: flaga OFF → przywrócenie pojedynczego stopu `closeFraction=1`.

### Faza E — badanie ablacyjne (deliverable pomiarowy)

**PR-8: `scripts/pure_v4_ablation.py` + raport w `.ai/analysis/`**

Warianty, kumulatywnie:

| Wariant | Konfiguracja |
|---|---|
| S0 | Baseline produkcyjny (H4 gate ON, z ratchetem) |
| S1 | S0 + R1 (reset netto) |
| S2 | S1 + `pure_v4` |
| S3 | S2 + slope `atr` θ ∈ {0.15, 0.25, 0.35, 0.50} |
| S4 | S3 + 50/50 i izolacja dokładki (jeśli PR-7b) |

Paper Scout jest w backteście tożsamy z realnym SCOUT, bo harness nie ma minimum notional, więc nie jest osobnym wariantem.

- Ocena:
  - S0–S2: 5×122d (porównywalność z historią) **oraz** WFO OOS.
  - S3: **wyłącznie** WFO OOS, bo wybór θ to optymalizacja.
- Metryki: suma i mediana netto per okno, MaxDD, R:R, liczba transakcji, odsetek okien z poprawą + test znaków (p), osobno okna trendowe (|zwrot okna| > X%).
- Kontrola: wyniki na `*_max` (realne USDC) jako replikacja; XAU/XAG raportowane osobno jako „bez wniosku” przy < 30 transakcjach.
- **Kryterium rekomendacji wdrożenia wariantu:** poprawa w ≥ 8/10 okien (p < 0.05) **i** brak pogorszenia MaxDD **i** replikacja kierunku na `*_max`. Wynik jest rekomendacją dla właściciela, a nie automatycznym włączeniem.

---

## 5. Kolejność i zależności

```
PR-1 ──► PR-2 ──┐
PR-3 ───────────┼──► PR-8 (ablacja) ──► decyzja właściciela ──► włączenie flag per symbol
PR-4 ───────────┤
PR-5 ───────────┘
PR-6 (niezależny po PR-3)
PR-7a ──► [D5] ──► PR-7b ──► (S4 w PR-8)
```

PR-1, PR-3, PR-4, PR-5 i PR-7a mają rozłączne obszary i mogą iść równolegle. PR-4 i PR-5 dotykają `backtest_engine.py` w innych miejscach, ale merge po PR-1 jest bezpieczniejszy.

| PR | Szac. rozmiar | Ryzyko prod. |
|---|---|---|
| PR-1 | M | brak (backtest) |
| PR-2 | M | brak |
| PR-3 | S–M | niskie (zmienia semantykę wyjścia ze SCOUT) |
| PR-4 | S | brak przy OFF |
| PR-5 | S | brak przy `pct` |
| PR-6 | M | niskie przy OFF |
| PR-7a | S | brak (read-only) |
| PR-7b | L | **wysokie** |
| PR-8 | M | brak |

---

## 6. Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| Overfitting na tej samej historii (powtórka porażki PLAN-008) | WFO jako jedyna ocena dla parametrów; replikacja na `*_max`; kryterium 8/10 + p |
| `pure_v4` usuwa ochrony dodane po incydencie XAG (#67–79) — pozycja trzyma do −2% w każdej stagnacji | Flaga per symbol, OFF; włączanie najpierw na 1 symbolu; SL 2% pozostaje na giełdzie |
| PR-3 wydłuża pobyt w SCOUT (mniej resetów) | To intencja; PR-6 i istniejący `SCOUT_EXPIRED_K` dają ścieżkę wyjścia; alert `SCOUT_STALE` monitoruje |
| PR-7b: osierocone/skasowane zlecenia SL | Tagowanie, reconcile, sandbox, rollback; prerekwizyt PR-7a |
| Rozjazd 15m (live) vs H1 (backtest) | D6 przed jakimkolwiek wnioskiem produkcyjnym |
| XAU/XAG bez backtestu | Raport osobny; brak przenoszenia wniosków |

---

## 7. Decyzje właściciela (blokujące)

| ID | Pytanie | Rekomendacja |
|---|---|---|
| D1 | Czy zgoda na pomiar (PR-8) na zakazanych fixture'ach `*_swap_24m` / `*_max` w ramach zamkniętego workstreamu? | Tak, ale tylko pomiar + WFO; wdrożenie dopiero po decyzji na podstawie raportu |
| D2 | Czy PR-3 (reset netto) wdrażać od razu jako bugfix? | Tak — to korekta poprawności, nie nowa logika sygnałowa |
| D3 | Czy zamknięcie ratchetowanego SL w zysku (`CLOSED_EXTERNAL_TP`) ma w ogóle resetować SCOUT? | Nie — traktować jak ochronne wyjście, nie pełny TP |
| D4 | Dźwignia bazowa MODERATE 3.0× — zostaje? | Bez zmian do wyników PR-8; to parametr env |
| D5 | Realizować izolację nóg dokładki (PR-7b, ryzyko wysokie)? | Odłożyć do wyniku S2/S3; najpierw PR-7a |
| D6 | Jaki jest faktyczny `timeframe` symboli na produkcji (fallback 15m vs backtest H1)? | Zweryfikować w `system_settings` przed PR-8 |
| D7 | Parametry WFO: IS ≥ 60d / OOS 7–14d zamiast 3–14d / 1–3d z RFC? | Tak — krótsze IS nie daje ≥ 30 transakcji |
| D8 | Odrzucenie R11 (D1 overextension) i zmiany `SCOUT_SIZE_MULT` | Potwierdzić odrzucenie |

---

## 8. Odpowiedź na „Zadanie na teraz” z RFC §5

1. **A/B baseline vs Pure v4 na 610 dniach:** wykonalne po PR-1 (bez tego baseline nie odpowiada produkcji) + PR-3 + PR-4. Wymaga D1.
2. **Siatka TMA Slope Gate θ ∈ {0.15…0.50}:** wykonalne po PR-5. Zgodnie z historią PLAN-008 sensowne tylko w ocenie OOS (PR-2), nie na pełnym oknie.
3. **Ablation study (MaxDD, wynik, okna trendowe):** PR-8, z kryteriami z sekcji 4E.
