# AUDYT STRATEGII MMS4 (`mms4tk`) — ANALIZA POD KĄTEM OPTYMALIZACJI ZYSKU

**Data:** 2026-09-08 · **Zakres:** `.agents/specs/knowledge/` (5 dok. w całości) vs `src/mms4tk/` (analytics, daemon, inactivity_manager, state_manager, okx_executor, backtest_engine, api, hermes_*) · **Dane:** lokalny `.agents/swarm/logs.db` (tylko odczyt), `git log`, `CHANGELOG.md`, `.agents/plans/`, `docs/STRATEGY_AND_ALGORITHM.md`

**Wykonano przez:** Claude Opus 5 (agent w trybie read-only), zlecone jako głęboka, jednorazowa analiza — bez żadnych zmian w repozytorium.

**Legenda dowodowa:** 🟢 = potwierdzone kodem/danymi w tej sesji · 🟡 = hipoteza wymagająca backtestu

---

## 1. ZGODNOŚĆ IMPLEMENTACJI ZE SPECYFIKACJĄ

### 1.1 🟢 KRYTYCZNE — Netting + kompozytowy SL dokładki niszczy 2% bufor bazy (wątki #1 i #2 łącznie)

To najważniejsze znalezisko audytu. Trzy oddzielne fakty składają się w jeden mechanizm.

**Fakt 1 — konto NIE jest w trybie Hedging, a `set_hedge_mode()` nigdy nie jest wołane.**
`/home/tkogut/projects/mms4tk/src/mms4tk/okx_executor.py:836` definiuje `set_hedge_mode()`. Grep po `src/`, `tests/`, `scripts/`: **zero wywołań** — tylko definicja i wzmianki w dokumentacji. Tymczasem `.agents/specs/knowledge/vps_hermes_okx_architecture.md:12` mówi wprost: *„System musi programowo zweryfikować lub wymusić tryb Hedge Mode"*, a `mms4-stale-positions-report.md:146` wymaga *„Tryb konta Hedging (umożliwia osobne bilety z niezależnymi SL)"*.

**Fakt 2 — SL jest kompozytowy na całej pozycji i kasuje poprzedni.**
`okx_executor.py:765-834` (`attach_stop_loss`): przed złożeniem nowego SL **anuluje wszystkie oczekujące zlecenia `conditional` dla tego `posSide`** (linie 802-817), a nowy składa z `"closeFraction": "1"` (linia 828) — czyli na 100% pozycji. Docstring nie ukrywa tego: *„protect the entire position"*.

**Fakt 3 — dokładka wywołuje ten mechanizm z ciasnym progiem liczonym od CENY DOKŁADKI, nie od średniej.**
`daemon.py:1540-1547`: `execute_order(..., stop_loss_pct=self.addon_sl_pct, price=signal.price)`. `okx_executor.py:750-758` przekazuje `entry_price=current_price` = `signal.price` = **close świecy potwierdzającej**, nie `avgPx`. `daemon.py:1553` sam to nazywa: *„with composite SL=1.25%"*.

**Konsekwencja arytmetyczna** (przykład SHORT, ilustracja mechanizmu):

```
Baza SHORT @ 100.00, SL @ 102.00 (2%)              ← zamysł spec: 2% oddechu
Świeca potwierdzająca zamyka się @ 99.00
Dokładka SHORT @ 99.00 (30% alokacji)
  → OKX netuje: avgPx = (0.7·100 + 0.3·99) / 1.0 = 99.70
  → attach_stop_loss KASUJE SL @102, składa nowy: 99.00 · 1.0125 = 100.24
  → ryzyko całej pozycji od avgPx 99.70 do 100.24 = +0.54%
```

**Po dokładce cała pozycja stoi na stopie ~0.5% powyżej ceny wejścia bazy — to de facto stop Break-Even.** Analogicznie dla LONG (baza 100, dokładka 101 → SL 99.74, avgPx 100.30, ryzyko -0.56%).

To bezpośrednio narusza `mms4_knowledge_base.md:67`: *„Trailing Stopy i systemy Break Even szkodzą systemom mean-reversion – wycinają statystycznie zdrowe obsunięcia"* — oraz „Żelazną Regułę" z waszego własnego `docs/STRATEGY_AND_ALGORITHM.md:144`, która dla Midline TP explicite zabrania przesuwania SL na BE. Ta sama dyscyplina nie została zastosowana do dokładki. **Nikt nigdy nie zmierzył tego w izolacji** — w przeciwieństwie do Midline TP (6/6 okien) i filtra wysokiej zmienności (3/4 okna).

**Skala:** ~59 `EXECUTED_HIGH_RISK_ADD_SHORT` na ~85 `EXECUTED_BASE_SHORT` = **~70% pozycji SHORT trafia w ten mechanizm**.

**Dodatkowo — dwie różne wartości SL zależnie od ścieżki kodu** 🟢: `daemon.py:851-869` (Self-Healing SL Guard) re-attachuje z `entry_price=entry_px` = **`avgPx` z giełdy** (nie z ceny dokładki). W przykładzie: 99.70 · 1.0125 = **100.95**, a nie 100.24. Ta sama pozycja ma inny stop w zależności od tego, która ścieżka odpaliła jako ostatnia.

### 1.2 🟢 KRYTYCZNE — mechanizm Sekwencyjności (SCOUT_MODE) jest obchodzony przy ~70% pozycji

Pochodna 1.1, ale osobna i równie kosztowna. `daemon.py:488`:

```python
sl_threshold = self.base_sl_pct * 0.75      # = 1.5%, BEZ świadomości dokładki
is_stop_out = loss_pct >= sl_threshold
```

Wybicie kompozytowego SL daje `loss_pct ≈ 0.5%` (patrz 1.1) — **daleko poniżej progu 1.5%**. Skutki łańcuchowe, wszystkie potwierdzone kodem:

| Mechanizm | Ścieżka | Efekt |
|---|---|---|
| `state_manager.on_stop_loss_hit` | wymaga `sl_percent >= 0.02` (`state_manager.py:345`) | **SCOUT_MODE nie włącza się** |
| Post-SL Cooldown (3 świece) | `daemon.py:805-806`, warunek `if _rc_evt.get("is_stop_loss")` | **karencja nie włącza się** — natychmiastowe re-wejście |
| Hermes H4 Liquidity Sweep | `emit_scout_mode_event` wołane tylko z `on_stop_loss_hit` | **cała warstwa H4 nie dostaje triggera** |
| Log/Telegram | `daemon.py:574-592` | raportowane jako *„ZAMKNIĘCIE POZYCJI (Manual/TP)"* — operator nie widzi stop-outu |

Czyli reguła, którą `mms_mastermind_rules.md` §1 nazywa *„nadrzędną zasadą przetrwania"*, jest wyłączona dla większości transakcji. `backtest_engine.py:224-228` opisuje to jako *„correctly does NOT retrigger SCOUT_MODE, matching production"* — implementacja jest wierna produkcji, ale **produkcja jest niewierna specyfikacji**. Spec 2.1 mówi: po wybiciu SL dokładki *„Pierwotny SL 2% bazy służy dalej jako bufor"*. W kodzie baza ginie razem z dokładką i nic nie służy jako bufor.

**Ocena: rozbieżność szkodliwa.** W trendzie system nie redukuje ekspozycji, nie ma karencji, wchodzi ponownie z pełnym lewarem 3x. Dokładnie krzywa kapitału, przed którą ostrzega KB 3.3.

### 1.3 🟢 Hermes / warstwa H4-D1 → `directional_bias` jest funkcjonalnie martwa (wątek #3)

Pięć niezależnych blokad, wszystkie w kodzie:

1. **`volatility_zone` nigdy nie wychodzi z API.** `analytics.py:774-785` zwraca `volatility_zone`, `is_volatility_expansion`, `atr_d1_expansion_threshold`. `api.py:509-516` deklaruje `response_model=HigherTimeframeResponse` — schemat **nie zawiera tych trzech pól**, więc FastAPI je odfiltrowuje. Hermes fizycznie nie widzi klasyfikacji reżimu, mimo że jest policzona.
2. **Brak `risk_multiplier`.** `mms_mastermind_rules.md` §4 wymaga: D1 > 2.5× ATR → `risk_multiplier = 0.5` + blokada HIGH_RISK_ADD. `StrategyProfile` (`state_manager.py:81-91`) ma tylko `directional_bias`, `risk_profile`, `scout_override`. **Nie istnieje żadne pole ani ścieżka kodu realizująca `risk_multiplier`.** `htf_expansion_threshold=2.5` jest policzony i nigdzie nie konsumowany przez silnik sygnałów.
3. **Profil jest GLOBALNY, nie per-symbol.** `evaluate_signals` czyta `state_manager.strategy_profile` (`analytics.py:348`) — jedna instancja dla BTC i ETH. Hermes nie ma sposobu wyrazić „ETH w trendzie spadkowym, BTC w konsolidacji".
4. **Endpoint HTF jest zakodowany na symbol podstawowy.** `api.py:526-527`: `fetch_candles(symbol=daemon.symbol, ...)` — tylko BTC/USDC. ETH nigdy nie dostaje własnego kontekstu H4/D1. `hermes_tools.py:178-185` (`mms4_get_higher_tf`) nie przyjmuje parametru `symbol`.
5. **Trigger nie odpala** — patrz 1.2.

**Dowód z danych** 🟢: tabela `strategy_profile` (56 wierszy, 2026-08-19 → 2026-09-08) — **każdy** wpis z `directional_bias != "BOTH"` ma `reason = "Test update via API"`. Zero śladu realnej decyzji Hermesa. Zastrzeżenie metodologiczne: to lokalna baza deweloperska, nie VPS; ale wobec pięciu blokad architektonicznych wyżej brak śladu jest spójny z wnioskiem, nie tylko z brakiem dostępu.

### 1.4 🟢 Ewolucja `InactivityManager` vs prototyp z A.3 — ocena kierunku

Porównanie `mms4-stale-positions-report.md:48-132` z `src/mms4tk/inactivity_manager.py`:

| Zmiana | Ocena |
|---|---|
| Odrzucanie ceny ≤ 0 (`:189-193`) | ✅ **Naprawa realnego bugu.** W prototypie `min_observed_price` = 0 dla shorta trwale zatruwał matematykę impulsu — każda cena czytała się jako >50% zniesienia. |
| Latch `is_split` + cooldown (`:239-244`, `:413-418`) | ✅ **Naprawa realnego bugu.** Prototyp emitował `SPLIT` w każdym cyklu; przy `reduction_amount` zaokrąglającym 1 kontrakt w górę drenowało to całą pozycję osobnymi zleceniami taker. |
| Reset `stale_detected` po wyjściu ze strefy (`:249-257`) | ✅ Bez tego każdy późniejszy powrót do wejścia re-triggerował BE_ESCAPE. |
| Kolejność: drabina PRZED Regułą II (`:300-305`) | ✅ **Dobra korekta.** Pasmo BE (0.2%) leży wewnątrz pasma stagnacji (0.5·ATR), więc sprawdzanie BE pierwsze czyniło drugi szczebel drabiny nieosiągalnym. |
| Bufor prowizji w BE_ESCAPE (`:96`, `:346`) | ✅ Bez niego „break-even" to gwarantowana mała strata. |
| **Fib 38.2% dla impulsu ≥ 0.8 ATR** (`:393`, `:399`) | ⚠️ **Zaostrzenie NIE uzasadnione żadnym dowodem.** Spec zna wyłącznie regułę 50% (`mms4_knowledge_base.md:82`, A.3 §I). 38.2% to nowy parametr wprowadzony przez implementację. |
| **Profit Stall Gate** (`:260-287`) | ⚠️ **Nie istnieje w specyfikacji w ogóle.** Zamyka 50% po 4 świecach bez nowego ekstremum przy uPnL ≥ 0.6 ATR — czyli **ucina zwycięzców**, mechanicznie tożsame z tym, co dyskwalifikowało Midline TP w 6/6 okien. |

**Ocena kierunku:** poprawki obronne (bugi, latche, resety) — jednoznacznie dobre. Nowe reguły odcinające zysk (38.2%, Profit Stall Gate) — **weszły tą samą drogą co Midline TP: bez walidacji przed wdrożeniem.** Ironia jest udokumentowana we waszym własnym CHANGELOG (0.42.0 wprowadza oba + Midline TP jednym PR-em; 0.42.1 wycofuje tylko Midline TP po backteście, dwa pozostałe zostają nietknięte).

### 1.5 🟢 Silnik rekalibracji (PLAN-010) symuluje strategię, która już nie istnieje

`backtest_engine.py:457`:

```python
params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
```

To **stare, błędnie skalibrowane progi VR**, które w tej sesji zresetowano na produkcji na 1.6/0.8. `run_calibration_job` (`:549`) — jedyne produkcyjne wejście, wołane z `api.py:1525` — woła `train_test_validate` **bez `params`**, więc trafia w ten default.

Skutek: przy `VR_HIGH=1.0` zamiast 1.6 backtest blokuje HIGH_RISK_ADD w zdecydowanie większej liczbie świec niż live (`analytics.py:409`, `:459`). Ponieważ od v0.44.0 harness modeluje dokładki, a produkcja ma ~70% addon rate, **populacja transakcji w backteście jest jakościowo inna niż na żywo**. Każdy przyszły werdykt `VALIDATED`/`OVERFIT_REJECTED` będzie wyprodukowany dla innego reżimu bramkowania.

### 1.6 🟢 `midline_partial_tp_enabled` domyślnie **WŁĄCZONY** w kodzie (fail-open)

`analytics.py:146`: `midline_partial_tp_enabled: bool = True`. `daemon.py:688` czyta go przez `_sym_bool(..., defaults.midline_partial_tp_enabled)`. `api.py:911`, `:1045`, `SettingsTab.jsx:18` — wszędzie fallback `"true"`.

Lokalna baza **nie ma tego klucza w `system_settings`** (potwierdzone: 10 wierszy, brak). Czyli w tej instancji funkcja odrzucona w 6/6 okien backtestu **jest aktywna**. Produkcja jest bezpieczna tylko dlatego, że ktoś ręcznie wpisał `false` do VPS-owego `system_settings`. Świeży deploy, reset wolumenu, czysta baza → funkcja wraca cicho. Wszystkie inne odrzucone filtry (`tma_trend_filter_enabled`, `high_vol_base_filter_enabled`) mają poprawnie `default = False`.

### 1.7 🟢 Per-symbol konfiguracja Inactivity Managera to no-op (bezpośrednia odpowiedź na wątek #8)

Łańcuch:

- `api.py:1343-1345` zapisuje `stale_candles_threshold`/`stale_atr_threshold`/`impulse_negation_enabled` do `configured_symbols_store` (pamięć) i `symbols.json`.
- `api.py:1362-1368` (`set_settings_bulk`) persystuje do `system_settings` **tylko 4 klucze trend/high-vol — te trzy NIE**.
- `load_symbols_config()` (`api.py:1119-1124`) przy starcie zwraca SQLite `symbol_configurations`, którego schemat ma **tylko** `symbol, is_active, allocation_usd, base_ratio, add_ratio, timeframe` (potwierdzone zapytaniem do bazy).
- Blok override'ów w `daemon.py:936-951` nigdy nie znajdzie tych kluczy po restarcie.

**Komentarz w `api.py:1354-1359` opisuje dokładnie ten failure mode i naprawia go dla kluczy trend-filtra — a trzy klucze inactivity leżą w tym samym dict-cie tuż wyżej i naprawy nie dostały.** Efekt: Inactivity Manager zawsze działa na `DEFAULT_*` z klasy. Podane progi produkcyjne (12 / 0.5 / true) są prawdziwe **przypadkowo** — bo to są class defaults, nie konfiguracja. Kalibracja per-symbol jest dziś **niemożliwa**, nie tylko niezrobiona.

### 1.8 🟢 `stale_candles_threshold=12` zaprojektowany dla M15, działa na H1

`mms4-stale-positions-report.md:43` definiuje regułę jednoznacznie: *„np. 12 świec na M15 = **3 godziny**"*. Produkcja: interwał 1h, próg 12 → **12 godzin**, plus `DEFAULT_GRACE_CANDLES=5` → **17 godzin** do `FORCE_CLOSE_BE`.

Przy Funding Rate co 8h na OKX X-Perps (co ten sam dokument wskazuje jako główny motyw reguły, §3) pozycja stagnacyjna płaci **do 2 pełnych naliczeń finansowania** przed ewakuacją. Reguła zaprojektowana jako obrona przed fee drag stała się jego źródłem. Ten parametr nigdy nie został przeskalowany przy migracji M15 → H1.

### 1.9 🟢 Pozostałe rozbieżności (niższy priorytet, ale realne)

| # | Rozbieżność | Lokalizacja | Ocena |
|---|---|---|---|
| a | Lewar bazowy **3.0x**, nie x1 | `state_manager.py:30` (`MODERATE=3.0`) | Świadomie udokumentowane jako supersesja. Ale matematyka asymetrii z KB 3.3 (-10% → odrabianie wykładnicze) była wyprowadzona dla x1. 2% SL = 6% marży bazowej. |
| b | `lev` z `analytics.py` jest **martwy** | `analytics.py:532-537`, `:619-624` vs `daemon.py:1395` | Daemon używa `state_manager.get_symbol_leverage()`, nie `signal.leverage`. Skutek: `AGGRESSIVE → lev=2.0` (mniej niż `MODERATE`!) i `CONSERVATIVE → min(lev, 0.5)` nigdy nie działają. Nieszkodliwe, ale mylące. |
| c | Time-Lock **asymetryczny** | `analytics.py:540` (tylko `BASE_SHORT`), komentarz `:627-629` | `BASE_LONG` i **oba HIGH_RISK_ADD** przechodzą przez okno US Open bez filtra. Uzasadnienie („per MMS v4 spec") nie ma pokrycia — `mastermind_weekly_tactics.md` §3 opisuje US Open jako bezkierunkowy generator fałszywych impulsów. |
| d | Warunki wejścia w `docs/` ≠ kod | `docs/STRATEGY_AND_ALGORITHM.md:76-78` | Dokumentacja: `HIGH_RISK_ADD` = „pogłębienie odchylenia o kolejne 1.0×ATR". Kod: świeca kontynuacji, **bez żadnego warunku ATR** (`analytics.py:391`). Dokumentacja opisuje funkcję, której nie ma. |
| e | Siatka 64 komórki vs 16 z planu | `backtest_engine.py:515-516` vs `task.md` TICKET-3 | 8×8 zamiast zatwierdzonych 4×4 → **4× większa ekspozycja na multiple testing**. |
| f | `/api/v1/emergency/reverse` to **pusty stub** | `api.py:457-467` | Zwraca `"status": "success", "Closed existing position, opened reversed position"` i **nie wykonuje niczego**. `killall` (`:449`) anuluje zlecenia, ale nie zamyka pozycji — a raportuje `"position close triggered"`. `c/rev` i `KILLALL` są wymagane przez KB 8.4 i architekturę VPS §4B. |
| g | `high_risk_add` przez API omija ledger | `api.py:470-500` | Nie woła `_record_addon` → Single Addon Guard nie widzi dokładki. Gate `is_high_risk_add_allowed()` (`state_manager.py:315`) czyta `_current_state` = **symbol podstawowy**, nie target. Symbol zakodowany na `daemon.symbol`. Wyjątki zjadane, zwraca `executed: True` zawsze. |
| h | VR liczony na obciętym oknie | `daemon.py:733` (`limit=100`) vs `analytics.py:237` (`rolling(window=100)`) | Mianownik VR to średnia z ~86, nie 100 okresów. Przesuwa punkt przecięcia z 1.6/0.8. Systematyczne, nie losowe. |

---

## 2. ZIDENTYFIKOWANE RYZYKA I LUKI

### 2.1 Metodologiczne (wątek #4)

**🟢 Brak modelu prowizji w backteście — najpoważniejsza luka metodologiczna.** Grep po `backtest_engine.py`: **zero** wystąpień `fee`, `slippage`, `funding`. `_stats()` (`:119-145`) agreguje surowy `pnl_pct`. Tymczasem produkcja ma pełną księgowość netto (PLAN-011, `logger_db.py:339-359`: `fee_usd`, `pnl_net_usd`).

**Decyzje live są oceniane netto, werdykty backtestu — brutto.** Kierunek błędu nie jest losowy:
- Funkcje **zwiększające** obrót (Midline TP, SPLIT_50, Profit Stall Gate) wychodzą w backteście **lepiej** niż w rzeczywistości.
- Filtry **zmniejszające** obrót (trend filter, high-vol base filter) wychodzą **gorzej** niż w rzeczywistości — bo nie zaliczają im się oszczędzone prowizje.

Oba odrzucone filtry należą do drugiej kategorii. Przy ~144 wejściach/miesiąc i round-trip taker ~0.10% (= 5% ruchu 2% SL) to nie jest korekta drugiego rzędu. **Werdykty „szkodliwy" dla trend filtra i high-vol filtra mogą być artefaktem rachunku brutto** 🟡.

**🟢 Post-SL Cooldown nie jest modelowany.** `daemon.py:803-815` blokuje wejścia na 3 świece po stop-oucie. W `run_backtest` po `close_position(..., "STOP_LOSS", i); continue` następna iteracja może wejść natychmiast. Backtest ocenia strategię bez jednego z jej realnych zabezpieczeń.

**🟢 `MIN_FULL_CLOSES_HALF = 12` — ale realna dziura jest w innym miejscu.** A.2 §1 wymaga 30-100 zamkniętych transakcji per blok OOS. Uczciwie: `MEMORY.md` §3 wskazuje operacyjny gate **175 czystych transakcji** (~87/połowę), co spełnia standard. Próg 12 jest podłogą akceptacji komórki, nie triggerem. **Ale**: jedyne zabezpieczenie w `run_calibration_job` to `if len(candles) < MIN_FULL_CLOSES_HALF * 4` (`:541`) = **48 świec = 2 dni na H1**. Nie ma żadnego gate'a na liczbę transakcji. Ktokolwiek uruchomi rekalibrację przedwcześnie dostanie werdykt `VALIDATED` wyprodukowany z 12 transakcji — i UI (`task.md` TICKET-5) wyrenderuje przycisk „Zastosuj".

**🟢 Multiple testing bez korekty.** 64 komórki × 2 kierunki, selekcja `max(total_pnl)` (`:442`), brak Deflated Sharpe / White's Reality Check / testu płaskowyżu parametrów. A.2 §1 wprost tego wymaga (*„wybór parametrów ze stabilnych płaskowyżów, a nie izolowanych maksimów"*). Przy 12-30 transakcjach best-of-64 to praktycznie czysty szum. Zabezpieczeniem jest dwukierunkowy OOS — i on **zadziałał** (poprawnie odrzucił trend filter). To dowód, że ta warstwa działa, nie że problem nie istnieje.

**🟡 Purging/embargo — realny problem jest mniejszy niż sugeruje A.2.** Uczciwa ocena: `full_df` w `run_backtest` liczony jest osobno dla każdego okna (`h1`/`h2` niezależnie), a `warmup = max(20,14) + lookback + 2` (`:214`) pomija pierwsze ~36 barów okna testowego. To **de facto embargo**. Brak jawnego purge'a przy braku etykiet wybiegających w przód jest niskiego ryzyka. **Priorytet niski** — nie inwestowałbym tu wysiłku przed naprawą prowizji.

### 2.2 Architektoniczne

| Ryzyko | Dowód | Ocena |
|---|---|---|
| Preferencja Hedgingu z A.1/A.3 jest **nieosiągalna na OKX** dla dokładek w tym samym kierunku | OKX `long_short_mode` pozwala na jednoczesny long+short, ale w obrębie jednego `posSide` **zawsze netuje do `avgPx`** | Dryf jest **częściowo wymuszony przez giełdę** — ale nigdzie nie zapisano tego jako decyzji, a jego skutek (1.1/1.2) jest nieudokumentowany i szkodliwy. Osiągalne obejścia: sub-konta per leg, albo wirtualny menedżer SL po stronie bota. |
| Detekcja reżimu = jeden filtr TMA-slope, który nie przeszedł walidacji (wątek #5) | A.2 §2 opisuje HMM/GMM/Absorption Ratio/TVTP/histerezę | `volatility_zone` (COMPRESSION/NORMAL/EXPANSION) **już jest policzony** w `analytics.py:765-772` i **nikt go nie konsumuje** — najtańszy krok pośredni jest już w 80% zbudowany. |
| Portfolio Margin Guard wyłączony (wątek #6) | `total_max_portfolio_margin_usd = 0.0` (potwierdzone w `system_settings`) | Kod jest **kompletny i poprawny**, w tym zamknięcie race'u przez `reserve_portfolio_margin` + `asyncio.Lock` (`daemon.py:223-260`) pod `asyncio.gather`. Przy $100 to niski priorytet, ale koszt włączenia ≈ 0. |
| Brak logiki sesyjnej Friday/makro (wątek #7) | Grep `weekday|friday|isoweekday|CPI|FOMC|macro` w `src/`: **zero trafień** | A.2/A.4 opisują konkretne wzorce. Nieobecne w całości. |
| SL jest **price-relative**, nie ATR-relative | `okx_executor.py:790-792`: `entry_price * (1 ± pct)` | Patrz propozycja P4 — moim zdaniem główny mechaniczny podejrzany za ETH. |

---

## 3. PROPOZYCJE OPTYMALIZACJI ZYSKU

Uporządkowane wg (oczekiwany efekt × pewność dowodu) ÷ wysiłek. Każda ma **konkretny test empiryczny** — lekcja z filtra wysokiej zmienności jest respektowana: żadna z nich nie jest gotowa do wdrożenia bez przejścia własnego testu.

---

### P1 — Odseparować SL dokładki od SL bazy 🔴 PRIORYTET NAJWYŻSZY

**(a) Dowód:** §1.1 + §1.2. `closeFraction="1"` + kasowanie poprzednich algo + `entry_price = cena dokładki` = de facto stop BE na ~0.5% na całej pozycji, przy ~70% pozycji. Narusza KB 4.1, waszą własną „Żelazną Regułę" z `STRATEGY_AND_ALGORITHM.md:144` oraz spec 2.1 („SL 2% bazy służy dalej jako bufor"). Nigdy nie mierzone w izolacji.

**(b) Mechanizm wpływu:** przywraca bazie statystyczne pole oddechu, którego wymaga mean-reversion. Ubocznie reaktywuje SCOUT_MODE, Post-SL Cooldown i trigger Hermes H4 dla ~70% pozycji, które dziś ich nie dostają.

**(c) Walidacja — 3 warianty, ten sam harness, ta sama siatka okien co Midline TP (BTC H1/H2/full, ETH H1/H2/full = 6 okien):**

`run_backtest` ma już wszystko: `position["sl_pct_override"] = ADDON_SL_PCT` (`:370`) to jedna linia do sparametryzowania.

- **V0 (baseline):** obecne zachowanie — kompozyt 1.25% od `avgPx`.
- **V1:** kompozyt **2.0%** od `avgPx` (spec-owe „max 3% na całość" ≈ 2% na zmieszanej pozycji).
- **V2:** SL bazy zostaje na `entry_bazy · 1.02` (nietknięty), dokładka bez własnego SL — najbliższe intencji specyfikacji, osiągalne przez `closeFraction` proporcjonalny do legu zamiast `"1"`.

Metryki: **R:R i total_pnl NETTO (patrz P2)**, dodatkowo `full_closes`, częstość stop-outów, oraz — kluczowe — **liczba przejść do SCOUT_MODE** (dziś ≈ 0 dla pozycji z dokładką; wzrost jest oczekiwanym efektem, nie regresją).

Gate wdrożenia: V1 lub V2 bije V0 na R:R **i** total_pnl netto w **min. 5 z 6 okien**. Poniżej — zostaje V0 i dokumentujemy dryf jako świadomy.

**(d) Priorytet: KRYTYCZNY. Wysiłek: harness ~1h (parametryzacja `sl_pct_override`), analiza ~3h, produkcja ~4h (`attach_stop_loss` musi rozróżniać leg bazowy od dokładki).**

---

### P2 — Dodać model prowizji i fundingu do `_stats()` 🔴 PREREKWIZYT DLA WSZYSTKIEGO

**(a) Dowód:** §2.1. Zero `fee`/`funding`/`slippage` w `backtest_engine.py`, przy pełnej księgowości netto na produkcji (PLAN-011). Kierunek błędu jest systematyczny i **przeciwny dla filtrów niż dla funkcji odcinających zysk**.

**(b) Mechanizm:** to nie jest optymalizacja PnL sama w sobie — to **naprawa przyrządu pomiarowego, na którym oparto już trzy decyzje produkcyjne** (Midline TP off, trend filter off, high-vol filter off). Bez tego P1, P3, P5 mierzą złą rzecz.

**(c) Walidacja:**
1. Dodać `round_trip_cost_pct` (0.10% taker×2) odejmowany od każdego `pnl_pct` w `trades`, oraz `funding_cost = ceil(candles_held / 8) · 0.01%` (H1 → co 8 świec).
2. **Test regresji na już rozstrzygniętych decyzjach** — to jest właściwy sposób walidacji nowego przyrządu: przepuścić ponownie Midline TP (6 okien) i high-vol base filter (4 okna) przez wersję netto.
   - Jeśli Midline TP nadal przegrywa 6/6 → model prowizji jest spójny z poprzednim wnioskiem, **przyrząd wykalibrowany**.
   - Jeśli high-vol filter lub trend filter **odwraca werdykt** → mamy potwierdzoną 🟢 przyczynę wcześniejszych odrzuceń i dwa filtry wracają do kolejki jako kandydaci.
3. Kalibracja `round_trip_cost_pct` na realnych danych: `logger_db.get_closed_trades()` ma `fee_usd` per transakcja — nie zgadywać, wyprowadzić z produkcji.

**(d) Priorytet: KRYTYCZNY (blokujący). Wysiłek: implementacja ~2h, re-walidacja historycznych decyzji ~3h.**

---

### P3 — Przywrócić rozróżnianie stop-outu z dokładką w rekoncyliacji 🟠

> ✅ **STATUS (2026-09-10): ZAIMPLEMENTOWANE i wdrożone na produkcję** (PR #31,
> v0.44.3). Finalne rozwiązanie (V2) poszło dalej niż ten opis: zamiast liczyć SL
> dokładki jako osobny, świadomy procent, dokładka w ogóle przestała mieć własny
> SL — cała pozycja jest odtąd chroniona wyłącznie przez SL bazy, zakotwiczony na
> stałe w `base_entry_price`. Historyczna analiza poniżej pozostaje niezmieniona
> jako zapis diagnozy z tamtego dnia; pełny opis wdrożonego rozwiązania i historia
> 3 rund niezależnej recenzji — `task.md`, sekcja "P3".

**(a) Dowód:** §1.2. `daemon.py:488` — `sl_threshold = base_sl_pct * 0.75` bez świadomości dokładki. Ledger `position_addons` **już zawiera** potrzebną informację i jest odczytywany 40 linii wyżej (`daemon.py:851`).

**(b) Mechanizm:** przywraca redukcję ekspozycji (KB 3.2 — *„nadrzędna zasada przetrwania"*) i 3-świecową karencję dla ~70% pozycji. Odblokowuje też trigger H4 dla Hermesa.

**(c) Walidacja — dwustopniowa, i tu ważne zastrzeżenie:**
1. **Najpierw pomiar bezkosztowy:** SCOUT_MODE jest już modelowany wiernie w harnessie od v0.43.0. Uruchomić 6 okien z i bez świadomego progu dokładki, **porównując wyłącznie liczbę wejść w SCOUT_MODE i wynik netto**. To zmiana czysto obronna — jeśli poprawia PnL, świetnie; jeśli jest neutralna lub lekko ujemna w PnL, **nadal ją rekomenduję**, bo jej celem jest ograniczenie ogona strat, nie średnia.
2. **Uwaga na sprzężenie z P1:** jeśli P1 wybierze V1 (kompozyt 2%) lub V2 (SL bazy nietknięty), problem klasyfikacji **znika sam** — `loss_pct` przy stopie wróci powyżej 1.5%. **P3 ma sens tylko jeśli P1 zdecyduje się zostać przy V0.** Nie implementować obu równolegle.
3. Metryka ogona: max drawdown i najdłuższa seria strat — bo tego właśnie broni sekwencyjność, a nie średniej.

**(d) Priorytet: WYSOKI, warunkowy na wyniku P1. Wysiłek: ~2h kod + ~2h analiza.**

---

### P4 — Zbadać SL relatywny do ATR (lub per-symbol) — hipoteza dla problemu ETH 🟠

**(a) Dowód — arytmetyczny, nie empiryczny** 🟡: SL jest price-relative (`okx_executor.py:790-792`), a wstęga jest ATR-relative (`analytics.py:226-227`: `TMA ± 2·ATR`). Skutek w jednostkach, które faktycznie się liczą:

```
Wejście na wstędze, TP = wstęga przeciwna = ~4·ATR
ETH:  ATR ≈ 1.0% ceny  →  SL 2% = 2.00 · ATR       ← ~50% szerokości pasma
BTC:  ATR ≈ 0.6% ceny  →  SL 2% = 3.33 · ATR       ← ~83% szerokości pasma
```

**Stop ETH jest ~40% ciaśniejszy w jednostkach zmienności.** To spójne z obydwoma diagnozami z waszej sesji jednocześnie: „odwrócony stosunek TP:SL względem BTC" **i** „szersza wstęga ATR względem ceny niż BTC" — jedna przyczyna, dwa objawy. Nie potrafiłem tego zmierzyć w tej sesji (`ohlcv_cache` pusty, brak sieci), więc **liczby ATR% są ilustracyjne — to hipoteza, nie ustalenie.**

Konflikt ze doktryną jest jawny: KB 1.4 i `mastermind_weekly_tactics.md` §1 mówią *„Niezmiennie 2.0%"*. Ale te 2% wyprowadzono dla BTC/XAU/NQ na M10-M30. Ten sam nominalny procent na ETH/H1 to **inne ryzyko**, nie to samo.

**(b) Mechanizm:** stop przestaje być wybijany przez normalny szum pasma; wyrównuje profil ETH do BTC bez zmiany logiki sygnału.

**(c) Walidacja — ostrożna, bo to zmiana najgłębiej sięgająca w doktrynę:**
1. **Krok 0 (pomiar, nie zmiana):** policzyć na 90 dniach rozkład `ATR/cena` i `SL_distance/ATR` per symbol. **Jeśli różnica BTC vs ETH okaże się mała, hipoteza upada i tu kończymy.** Ten krok jest tani i musi być pierwszy.
2. **Wariant konserwatywny (rekomendowany do testu):** `base_sl_pct` **per-symbol**, siatka {2.0%, 2.5%, 3.0%} × {BTC, ETH}, pełny 2-way OOS. Zachowuje stały procent jako pojęcie — mniejsze odejście od doktryny.
3. **Wariant agresywny (tylko jeśli #2 pokaże wyraźny sygnał):** `SL = k · ATR`, k ∈ {1.5, 2.0, 2.5, 3.0}, 2-way OOS. Znosi „niezmiennie 2%" — wymaga jawnej zgody właściciela strategii, nie tylko dobrego backtestu.
4. **Sprzężenie do rozstrzygnięcia zanim cokolwiek wdrożymy:** `on_stop_loss_hit` ma twardy próg `>= 0.02` (`state_manager.py:345`). Przy SL 2.5% lub ATR-relative ten próg musi być przeliczony, inaczej albo SCOUT_MODE przestanie działać, albo zacznie odpalać na małych stratach. **To nie jest detal implementacyjny — to zmiana definicji „pełnego SL" w maszynie stanów.**

**(d) Priorytet: WYSOKI (największy potencjał na ETH). Wysiłek: krok 0 ~2h; wariant #2 ~6h (harness + analiza) + ~3h produkcja z przeliczeniem progu SCOUT.**

---

### P5 — Reżim rynkowy: skonsumować `volatility_zone`, który już jest liczony (wątek #5) 🟡

**(a) Dowód:** §1.3 pkt 1-2. `analytics.py:765-772` produkuje 3-stanową klasyfikację reżimu (COMPRESSION/NORMAL/EXPANSION) z D1 ATR; `api.py:509-516` ją **odfiltrowuje** przez `response_model`; żadna ścieżka jej nie konsumuje. A.2 §2 opisuje HMM/GMM/AR/TVTP — ale tu jest lekki krok pośredni **już zaimplementowany w 80%**.

**(b) Mechanizm:** MMS zarabia w akumulacji/dystrybucji i oddaje w trendach (KB 3.1). 3-stanowa bramka na D1 rozdziela te reżimy bez ryzyka overfittingu, na które padł TMA-slope: **zero nowych wolnych parametrów** — `htf_expansion_threshold=2.5` pochodzi wprost z `mms_mastermind_rules.md` §4, nie z grid searcha. To jest jej główna zaleta wobec trend filtra.

**(c) Walidacja — i tu jestem szczególnie ostrożny, bo to koncepcyjnie ten sam gatunek pomysłu, który już dwa razy zawiódł:**
1. **Krok 0 — obserwacja bez działania (obowiązkowy, min. 2 tygodnie):** rozszerzyć `HigherTimeframeResponse` o 3 brakujące pola i **logować** `volatility_zone` przy każdym wejściu. Zero wpływu na egzekucję. Potem policzyć R:R warunkowo per strefa na realnych transakcjach.
   **Jeśli R:R w EXPANSION nie jest istotnie gorsze niż w NORMAL — hipoteza upada i nie idziemy dalej.** To jest test, którego zabrakło przy TMA-slope.
2. Krok 1 (tylko przy pozytywnym #0): backtest 4 warianty — (i) baseline, (ii) blokada HIGH_RISK_ADD w EXPANSION, (iii) `size × 0.5` w EXPANSION, (iv) pełna blokada wejść w EXPANSION. 2-way OOS, metryki netto.
3. **Histereza obowiązkowa** (A.2 §2, *Temporal Hysteresis Gate*): przejście strefy dopiero po `k=2` kolejnych świecach D1, inaczej dostaniemy boundary thrashing na progu 2.5.
4. Wdrożenie: **default OFF, opt-in per-symbol** — dokładnie wzorem `high_vol_base_filter_enabled`.

**(d) Priorytet: ŚREDNI (wysoki potencjał, ale najdłuższa droga dowodowa). Wysiłek: krok 0 ~3h + 2 tyg. zbierania; krok 1 ~6h.**

---

### P6 — Przeskalować progi Inactivity na H1 i uczynić je faktycznie konfigurowalnymi 🟠

**(a) Dowód:** §1.7 (per-symbol config to no-op — potwierdzony bug persystencji) + §1.8 (12 świec projektowane jako 3h, działa jako 12h; 17h do FORCE_CLOSE_BE = do 2 naliczeń fundingu).

**(b) Mechanizm:** ewakuacja z martwej pozycji następuje **w oknie czasowym, dla którego regułę zaprojektowano**, zwalniając kapitał i ucinając fee drag — czyli realizując faktyczny cel reguły z A.3 §3.

**(c) Walidacja:**
1. **Naprawa buga jest bezwarunkowa i nie wymaga backtestu** — dodać 3 klucze inactivity do `set_settings_bulk` w `api.py:1362`, dokładnie tak jak zrobiono dla trend-filtra, i czytać je w `daemon.py` z `system_settings` per-symbol. Dziś UI kłamie operatorowi; to naprawa poprawności, nie optymalizacja.
2. **Dopiero potem** dobór wartości: siatka `stale_candles_threshold` ∈ {3, 6, 9, 12, 18} × `grace_candles` ∈ {2, 3, 5} × {BTC, ETH}, 2-way OOS, metryki netto **z modelem fundingu z P2** (bez niego ten test nie ma sensu — funding jest tu głównym kanałem efektu).
3. Kontrola przeciwna, żeby nie wpaść w pułapkę Midline TP: sprawdzić, ile transakcji zamkniętych przez `SPLIT_50`/`FORCE_CLOSE_BE` **osiągnęłoby przeciwną wstęgę**, gdyby dano im dłużej. Zbyt agresywna ewakuacja to ten sam błąd co ucinanie zwycięzców.

**(d) Priorytet: WYSOKI dla naprawy buga (niski wysiłek, jasna poprawność), ŚREDNI dla doboru wartości. Wysiłek: bug ~2h; kalibracja ~5h.**

---

### P7 — Naprawić params silnika rekalibracji 🟠 SZYBKA WYGRANA

**(a) Dowód:** §1.5. `backtest_engine.py:457` — hardcoded `adaptive_vr_high=1.0, adaptive_vr_low=0.75`, czyli progi zresetowane na produkcji w tej sesji. `run_calibration_job:549` wchodzi w ten default.

**(b) Mechanizm:** nie zwiększa PnL bezpośrednio — **przywraca wiarygodność jedynego mechanizmu, który tę strategię chroni przed overfittingiem.** Bez tego każdy przyszły werdykt PLAN-010 jest wyprodukowany dla nieistniejącej konfiguracji.

**(c) Walidacja:** czytać `engine_mode`/`adaptive_vr_*`/`time_lock_*` z `system_settings` przez `LoggerDB.get_all_settings()` (dokładnie tak, jak `daemon._get_indicator_params` — ta funkcja jest gotowym wzorcem, `daemon.py:678-690`). Test: dwa uruchomienia z jawnie różnymi progami VR muszą dać różne `full_closes` — to dowodzi, że parametr faktycznie dotarł. Dodatkowo assert w testach: `train_test_validate` bez `params` **nie może** cicho użyć literałów.

**Przy tej samej okazji, ten sam plik:**
- Dodać **gate na liczbę transakcji** (nie świec) przed werdyktem `VALIDATED` — dziś jedyne zabezpieczenie to 48 świec (`:541`). Rekomendacja: min. 30 `full_closes` per połowa, zgodnie z A.2 §1; przy operacyjnym progu 175 czystych transakcji nic to nie kosztuje.
- Zredukować siatkę do zatwierdzonych 4×4 (`task.md` TICKET-3) lub dodać wymóg płaskowyżu parametrów (A.2 §1) — 64 komórki to 4× nadmiarowa ekspozycja na multiple testing.
- Dodać model Post-SL Cooldown (3 świece) — §2.1.

**(d) Priorytet: WYSOKI (bardzo tani, odblokowuje zaufanie do P1/P4/P5/P6). Wysiłek: ~3h łącznie.**

---

### P8 — Sesyjność: Friday Close Sweep i daty makro (wątek #7) 🟡 NAJPIERW POMIAR

**(a) Dowód:** §2.2. Zero logiki weekday/makro w `src/`. A.4 §2-3 opisuje konkretne wzorce.

**(b) Mechanizm:** unikanie okien, w których cena wykonuje ruchy pozorowane („ubieranie" detalu) — a to sytuacje, w których MR wchodzi pod właściwy ruch.

**(c) Walidacja — i tutaj rekomendacja jest wyraźnie ostrożniejsza niż przy pozostałych, właśnie z powodu lekcji z filtra wysokiej zmienności:**

**Nie budować filtra.** Zrobić najpierw **czystą analizę warunkową na istniejących danych** — bez linijki kodu produkcyjnego:
1. Pogrupować zamknięte transakcje z 90-dniowego fixture'a po (dzień tygodnia × godzina UTC), policzyć R:R netto i win-rate per komórka.
2. **Gate wejścia do dalszej pracy: komórka musi mieć ≥ 30 transakcji.** Przy ~144 wejściach/miesiąc i 5 dniach × 24 godzinach większość komórek tego **nie osiągnie** — i to jest najbardziej prawdopodobny wynik tej analizy. Wtedy uczciwa odpowiedź brzmi: **danych jest za mało, wątek odłożony**, i to nie jest porażka analizy, a jej rezultat.
3. Time-Lock US Open **już istnieje** i daje darmowy test kalibracji przyrządu: czy analiza per-godzina niezależnie odtwarza sygnał w oknie 13:30-14:30? Jeśli nie odtwarza znanego efektu, nie ma podstaw wierzyć, że wykryje nowy.
4. Daty makro (CPI/FED) wymagają zewnętrznego kalendarza — nowa zależność operacyjna. **Nie rekomenduję** przy obecnej alokacji $100.

**(d) Priorytet: NISKI. Wysiłek: analiza ~4h; implementacja tylko przy zaskakująco mocnym sygnale.**

---

### P9 — Higiena i bezpieczeństwo (wysiłek bliski zeru, poprawność bezwarunkowa) 🟢

Wszystkie potwierdzone kodem, żadna nie wymaga backtestu:

| # | Zmiana | Uzasadnienie |
|---|---|---|
| 1 | `midline_partial_tp_enabled` default → **`False`** (`analytics.py:146`, `api.py:911/1045`, `SettingsTab.jsx:18`) | §1.6 — funkcja odrzucona 6/6 jest fail-open. Wszystkie inne odrzucone filtry mają default `False`. Jedna wartość, eliminuje cichy powrót przy świeżym deployu. |
| 2 | Portfolio Margin Guard: `cap = 300` USD (wątek #6) | Kod kompletny i poprawny (w tym zamknięcie race'u). Przy notional 2×($105+$45) = $300 na $100 equity próg 300-400 nie blokuje nic dziś, a jest twardym hamulcem przy skalowaniu. **Nie wymaga backtestu — to bezpiecznik, nie strategia.** A.4 §4 wymaga go wprost. |
| 3 | Naprawić `/api/v1/emergency/reverse` (`api.py:457-467`) — albo zaimplementować, albo zwracać `501 Not Implemented` | §1.9f — dziś zwraca `"success"` i nie robi nic. Zaraportowanie nieprawdy o zamknięciu pozycji w endpointcie awaryjnym to najgroźniejszy rodzaj buga w tym repo. `killall` analogicznie: albo zamyka pozycje, albo przestaje to twierdzić. |
| 4 | Rozszerzyć `HigherTimeframeResponse` o `volatility_zone`, `is_volatility_expansion`, `atr_d1_expansion_threshold` | §1.3 pkt 1 — dane są liczone i odfiltrowywane. Prerekwizyt P5 kroku 0, zerowy wpływ na egzekucję. |
| 5 | Podnieść `limit=100` → `limit=150` w `daemon.py:733` | §1.9h — VR ma mianownik z ~86 zamiast 100 okresów; przesuwa punkt przecięcia 1.6/0.8, który właśnie z trudem wykalibrowano. |
| 6 | Usunąć martwe `lev` z `analytics.py:532-537`, `:619-624` | §1.9b — `AGGRESSIVE → 2.0 < MODERATE → 3.0`. Kod nieużywany, ale każdy przyszły czytelnik uzna, że tak działa. |
| 7 | `high_risk_add` (`api.py:470`): wołać `_record_addon`, gate per-symbol, nie zjadać wyjątków | §1.9g — obchodzi Single Addon Guard i raportuje `executed: True` przy błędzie. |

---

## 4. PODZIAŁ DOWODOWY

### 🟢 Potwierdzone kodem/danymi w tej sesji

1. `set_hedge_mode()` **nigdy nie jest wołane** — grep po całym `src/`, `tests/`, `scripts/`.
2. `attach_stop_loss` używa `closeFraction="1"` i **kasuje poprzednie algo SL** dla danego `posSide` → SL jest kompozytowy na całej pozycji.
3. SL dokładki liczony od **ceny dokładki** (`daemon.py:1546` → `okx_executor.py:755`), nie od `avgPx` → efektywne ryzyko ~0.5% od średniej = de facto stop Break-Even.
4. Self-Healing SL Guard używa **`avgPx`**, dając **inną** wartość SL niż ścieżka pierwotna dla tej samej pozycji.
5. `sl_threshold = base_sl_pct * 0.75` (1.5%) **bez świadomości dokładki** → SCOUT_MODE, Post-SL Cooldown i trigger Hermes H4 nie odpalają po stopie z dokładką.
6. `volatility_zone`/`is_volatility_expansion` liczone w `analytics.py` i **odfiltrowywane** przez `response_model` w `api.py:509`.
7. **Brak `risk_multiplier`** w `StrategyProfile` — reguła A.5 §4 nie ma ścieżki implementacyjnej.
8. `StrategyProfile` jest **globalny**; endpoint HTF zakodowany na `daemon.symbol`; narzędzia Hermesa nie przyjmują `symbol`.
9. `strategy_profile` (56 wierszy): każdy wpis `!= "BOTH"` ma `reason = "Test update via API"` — zero śladu realnej aktywności Hermesa *(lokalna baza dev, nie VPS)*.
10. `train_test_validate` default = `adaptive_vr_high=1.0, adaptive_vr_low=0.75` — **stare progi**; `run_calibration_job` nie podaje `params`.
11. `backtest_engine.py`: **zero** modelu prowizji/fundingu/poślizgu; **brak** Post-SL Cooldown.
12. Gate `run_calibration_job` = **48 świec**, nie liczba transakcji.
13. Siatka **64 komórki** vs 16 zatwierdzone w `task.md`; selekcja `max(total_pnl)` bez korekty multiple testing.
14. `midline_partial_tp_enabled` **default `True`** w 4 miejscach; brak klucza w lokalnym `system_settings`.
15. `stale_candles_threshold`/`stale_atr_threshold`/`impulse_negation_enabled` **nie są persystowane** (`api.py:1362` pomija je) i **nie są ładowane** (`load_symbols_config` zwraca 6 pól) → UI to no-op po restarcie; komentarz `api.py:1354-1359` opisuje ten bug i naprawia go tylko dla trend-filtra.
16. Próg 12 świec zdefiniowany w A.3 jako *„12 świec na M15 = 3h"*, działa na H1 = 12h (+5 grace = 17h) → do 2 naliczeń fundingu.
17. Profit Stall Gate i Fib 38.2% **nie mają odpowiednika w specyfikacji** i weszły bez walidacji (CHANGELOG 0.42.0 — jeden PR z Midline TP; 0.42.1 wycofuje tylko Midline TP).
18. `/api/v1/emergency/reverse` to **pusty stub**; `killall` nie zamyka pozycji, ale to raportuje.
19. `lev` z `analytics.py` **nieużywane** (daemon bierze lewar ze `state_manager`) → `AGGRESSIVE(2.0) < MODERATE(3.0)`.
20. Time-Lock aplikowany **wyłącznie** do `BASE_SHORT`; `BASE_LONG` i oba `HIGH_RISK_ADD` go nie mają.
21. `total_max_portfolio_margin_usd = 0.0` (odczyt z `system_settings`); kod Guarda kompletny wraz z zamknięciem race'u.
22. **Brak** logiki weekday/Friday/CPI/FOMC w całym `src/`.
23. Lewar bazowy `MODERATE = 3.0x`, nie x1 z KB 1.4 (udokumentowana supersesja w `state_manager.py:24-27`).
24. `daemon.py:733` pobiera 100 świec, `analytics.py:237` chce okna 100 → mianownik VR obcięty.
25. `docs/STRATEGY_AND_ALGORITHM.md:78` opisuje `HIGH_RISK_ADD` jako „pogłębienie o 1.0×ATR" — kod nie ma żadnego warunku ATR.

### 🟡 Hipotezy wymagające walidacji backtestem

1. **Stop BE na bazie po dokładce obniża R:R** — mechanizm potwierdzony 🟢, **wpływ na PnL nie** (P1).
2. **ETH ma ~40% ciaśniejszy stop w jednostkach ATR niż BTC** i to jest główna mechaniczna przyczyna gorszego R:R — arytmetyka spójna z obydwoma waszymi diagnozami, ale **liczby ATR% są ilustracyjne**; wymaga kroku 0 z P4 (`ohlcv_cache` pusty, brak sieci w tej sesji).
3. **Rachunek brutto systematycznie krzywdzi filtry redukujące obrót** — kierunek błędu potwierdzony 🟢, ale **czy odwraca werdykt** trend filtra / high-vol filtra to hipoteza (test regresji w P2).
4. **3-stanowa bramka `volatility_zone` poprawia R:R** — wymaga obowiązkowego kroku 0 (2 tyg. obserwacji) przed jakąkolwiek implementacją (P5).
5. **Krótszy próg stagnacji poprawia wynik netto na H1** — wiarygodne przez kanał fundingu, ale symetrycznie ryzykowne (za agresywna ewakuacja = ten sam błąd co Midline TP) (P6).
6. **Wzorce sesyjne (Friday/makro) są statystycznie istotne** — najsłabiej wsparta hipoteza; najbardziej prawdopodobny wynik analizy to „za mało danych" (P8).

---

## 5. REKOMENDOWANA KOLEJNOŚĆ

Kolejność wynika z zależności, nie z atrakcyjności:

| Etap | Działanie | Dlaczego teraz |
|---|---|---|
| **0** | **P9** (7 poprawek higienicznych) + **P7** (params rekalibracji) | Bez backtestu, niski wysiłek, czysta poprawność. P7 warunkuje zaufanie do całej reszty. |
| **1** | **P2** (model prowizji) + test regresji na Midline TP / high-vol filter | Przyrząd musi być wykalibrowany, zanim zmierzy się nim P1/P4/P6. Może odwrócić dwa istniejące werdykty. |
| **2** | **P1** (SL dokładki, V0 vs V1 vs V2) | Największy potwierdzony mechanizm, ~70% pozycji. Wynik decyduje, czy P3 jest w ogóle potrzebne. |
| **3** | **P4 krok 0** (pomiar ATR%/SL per symbol) + **P6.1** (naprawa buga persystencji) | Tanie, rozstrzygające. Krok 0 może zamknąć wątek ETH w 2h albo otworzyć największą pojedynczą wygraną. |
| **4** | **P3** *(tylko jeśli P1 → V0)*, **P4 wariant #2**, **P6.2** | Warunkowe na etapach 2-3. |
| **5** | **P5 krok 0** (logowanie `volatility_zone`, 2 tyg. obserwacji) | Długi cykl dowodowy — im wcześniej start zbierania, tym lepiej, ale bez wpływu na egzekucję. |
| **6** | **P8** (analiza sesyjna) | Najsłabszy dowód, najprawdopodobniej „za mało danych". |

**Jedna uwaga przekrojowa.** Wzorzec, który zdiagnozowaliście — „sensowna hipoteza okazuje się empirycznie szkodliwa" — nie dotyczy w równym stopniu wszystkich propozycji, i warto to rozróżnić przy planowaniu. P9, P7 i P6.1 to **naprawy poprawności**: kod robi coś innego, niż twierdzi jego własna dokumentacja i UI. Tam nie ma hipotezy do zwalidowania, jest błąd do usunięcia. P1 i P3 to **przywrócenie zapisanej specyfikacji**, którą implementacja cicho porzuciła. Dopiero P4, P5, P6.2 i P8 to nowe hipotezy optymalizacyjne — i tylko one wymagają pełnej ostrożności, jaką wypracowaliście na Midline TP i filtrze wysokiej zmienności. Rozdzielenie tych trzech kategorii wydaje mi się najważniejszą rzeczą, jaką ten audyt może wnieść do kolejnej sesji.

---

**Nie zmodyfikowano żadnego pliku w repozytorium.** Nie utworzono plików, nie wykonano commitów, brancha ani migracji. Dostęp do `.agents/swarm/logs.db` wyłącznie w trybie `mode=ro` przez URI SQLite. Nie odczytywano ani nie logowano zawartości `.env`, kluczy API ani poświadczeń.
