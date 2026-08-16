# 🔧 Raport z refaktoryzacji — MMS4 Toolkit v0.25.0 → v0.26.0

**Data:** 2026-08-16 · **Zakres:** remediacja audytu ilościowego + przeprojektowanie SCOUT_MODE
**Punkt wyjścia:** `d4515a2` (v0.25.0) · **Stan końcowy:** `ddf7d6c` (v0.26.0), wdrożony na produkcji
**Podstawa:** [Audyt ilościowy 2026-08-16](AUDIT_MMS4_2026-08-16.md)

---

## 1. Podsumowanie

| Miara | Wartość |
|---|---|
| Commity | 17 |
| Zmienione pliki | 41 (`+4 677 / −497` linii) |
| Kod silnika (`src/`) | 8 plików (`+1 640 / −360`) |
| Testy | 16 plików (`+1 760 / −17`) |
| Ustalenia audytu zamknięte | 33 (2×P0, 11×P1, 20×P2) |
| Asercje audytowe | **25/61 → 61/61 PASS** |
| Testy regresyjne | 0 → **57** |
| Wdrożenia produkcyjne | 3 (dwa iteracyjne po wykryciu błędów) |

**Ocena łączna z audytu wynosiła 4/10.** Warstwa analityczna była poprawna, natomiast cała warstwa
zarządzania ryzykiem MMS v4 była martwa albo działała odwrotnie do zamierzenia. Po refaktoryzacji
każde ustalenie P0 i P1 jest zamknięte, a P2 naprawione lub udostępnione jako jawna, udokumentowana
konfiguracja.

---

## 2. Dwa defekty blokujące

### P0-1 — SCOUT_MODE był nieosiągalny

Analiza grafu wywołań (AST, całość pakietu) wykazała **5 ścieżek wyjścia** ze SCOUT_MODE i **0 ścieżek
wejścia**. `on_stop_loss_hit()` było wołane wyłącznie z `record_trade()`, a `record_trade()` nie miało
ani jednego wywołującego w kodzie produkcyjnym. Stop-loss realizował się po stronie giełdy i nikt
tego zdarzenia nie odczytywał.

Skutkiem ubocznym była martwa księgowość: `record_trade()` jest jedynym miejscem aktualizującym
equity i PnL, więc `/api/v1/state` zwracał zera przy 47 073 wierszach w `state_logs` — wszystkich
identycznych.

**Rozwiązanie.** `daemon.reconcile_positions()` porównuje pozycje z OKX ze snapshotem w SQLite
(tabela `position_snapshots`) przed każdą decyzją handlową. Zniknięcie pozycji przy ruchu
≥ 0.75×SL księguje `record_trade(is_stop_loss=True)` i przełącza stan **w tym samym cyklu**, bez
czekania na 15-minutowy worker `trade_sync`. Snapshoty są trwałe, więc mechanizm działa też po
restarcie demona.

Dowód: 100 cykli z symulacją likwidacji → 98 wykrytych stop-outów (wcześniej 0).

### P0-2 — reguła negacji impulsu likwidowała całą pozycję

`_check_impulse_negation` nigdy nie ustawiała flagi `is_split` (robiła to wyłącznie gałąź stagnacji),
więc `SPLIT_50` wracał w każdym cyklu 15-sekundowym:

```
cykl 1: 4 kontrakty → zamyka 2
cykl 2: 2 kontrakty → zamyka 1
cykl 3: 1 kontrakt  → zamyka 1   ← max(1, floor(0.5)) = 1, czyli 100%
```

Pozycja znikała w ~45 sekund, w trzech osobnych zleceniach taker. Przy ówczesnej konfiguracji
SOL/USDC próg wyzwolenia wynosił **$0.01** — poziom szumu rynkowego.

**Rozwiązanie.** Flaga latchowana przy zwrocie `SPLIT_50`, cooldown minimum jednej świecy między
akcjami ochronnymi, flaga `closing` blokująca powtórzenie akcji terminalnej. Osobno naprawiono
arytmetykę redukcji (patrz §4).

---

## 3. Przeprojektowanie SCOUT_MODE (Wariant A)

Naprawa P0-1 odsłoniła problem, którego wcześniej nie dało się zaobserwować: **skoro SCOUT_MODE stał
się osiągalny, okazało się, że nie ma z niego wyjścia.**

```
SCOUT → lewar x0.1 → notional $10 × 0.70 × 0.1 = $0.70
$0.70 < minimum giełdy         → wejście pomijane
brak wejścia → brak pozycji → brak Take Profitu → SCOUT trwa
```

Furtka awaryjna (analiza H4 Hermesa → `scout_override`) istnieje w kodzie, ale nie jest w cronie —
w harmonogramie VPS jest wyłącznie hourly digest.

**Diagnoza.** Lewar nie może nieść redukcji ryzyka, bo OKX przyjmuje wyłącznie liczby całkowite ≥ 1.
Wartość `0.1` była po cichu obcinana do `1` na giełdzie, a jednocześnie implikowany przez nią
nominał spadał poniżej minimum. Konstrukcja była sprzeczna z API giełdy.

**Rozwiązanie.** Rozdzielenie dwóch osi:

```
scout_margin   = max_margin_usd × BASE_ALLOCATION × SCOUT_SIZE_MULT
scout_notional = scout_margin × SCOUT_LEVERAGE
```

Redukcja względem BASE = `base_leverage / (SCOUT_LEVERAGE × SCOUT_SIZE_MULT)` — **niezależna od
wielkości rachunku**:

| `max_margin_usd` | BASE notional | SCOUT notional | redukcja |
|---|---|---|---|
| $10 | $21.00 | $2.10 | 10.0× |
| $100 | $210.00 | $21.00 | 10.0× |
| $500 | $1 050.00 | $105.00 | 10.0× |
| $1 000 | $2 100.00 | $210.00 | 10.0× |

Zweryfikowane także przy $12 345,67 — stosunek trzyma co do cyfry. `daemon.plan_allocation()` jest
jedynym źródłem prawdy dla sizingu; rozproszone wyliczenia `base_trade_usd * leverage` zostały
usunięte.

**Minima giełdowe** czytane są dynamicznie z metadanych CCXT (`limits.cost.min`, a w razie braku
najmniejszy lot × cena). Pomiar na żywym OKX pokazał, że sztywne $7 było zawyżone dla trzech par
na cztery: BTC $6.30, ETH $1.88, SOL $0.75, ICP $2.28. Próg opłacalności SCOUT spadł ze
wstępnie szacowanych $100 do **≈$30** — dokładnie
`min_notional / (BASE_ALLOCATION × SCOUT_SIZE_MULT × SCOUT_LEVERAGE)`, czyli wartość ruchoma,
wyznaczana przez najdroższy kontrakt (obecnie BTC).

---

## 4. Pozostałe ustalenia P1

| ID | Problem | Rozwiązanie |
|---|---|---|
| P1-3 | `signal.price = 0.0` z degeneracyjnego cyklu trwale zatruwało tracker (dla shorta `min_observed = 0` → każda cena czytana jako zniesienie >50%) | Walidacja `current_price > 0`; sygnał zwraca ostatnie znane zamknięcie zamiast zera |
| P1-4 | Wejścia bazowe nie sprawdzały kierunku świecy — short odpalał na świecy rosnącej przebijającej górną bandę | Wymagane `close < open` / `close > open`; outside bar → `NONE` |
| P1-5 | Lock dokładki kasowany przy każdym cyklu z sygnałem `NONE` (`target_pos_side` wyprowadzany z typu sygnału, domyślnie `"long"`) | Strona wyprowadzana z realnej pozycji; ledger trwały w SQLite |
| P1-6 | `evaluate_signals` dostawał stan symbolu głównego; flaga `is_allowed` nie była nigdzie odczytywana | Stan per symbol; twarda bramka `is_allowed` przed dispatchem |
| P1-7 | `math.ceil` bez ogranicznika — realna marża przekraczała `max_margin_usd` o 1–26% na wszystkich parach | `_fit_contracts()` z limitem budżetu; zwrot 0 = pomiń wejście |
| P1-8 | Floor `max(est_usd, 7.0)` zawyżał zlecenie zamiast je pomijać (SCOUT ~18× za duży) | Poniżej minimum wejście jest pomijane, nigdy powiększane |
| P1-9 | `max(1, floor(n×0.5))` zamieniał redukcję częściową w zamknięcie 100% przy 1 kontrakcie | `reduction_amount()` — pozycja niepodzielna zwraca `not_divisible` |
| P1-10 | `/api/v1/settings/update` rzucał `AttributeError` (brak setterów) | Dodane `leverage.setter`, `scout_mode`, `save_state`, `current_state.setter` |
| P1-11 | Take Profit & Flip nieatomowy — wynik zamknięcia nieweryfikowany | Weryfikacja `_close_succeeded()`, przy niejednoznaczności ponowne odpytanie giełdy, brak otwarcia nogi przeciwnej |
| P1-12 | Krzywa kapitału to 47 073 identycznych zer | Deduplikacja zapisów, wymuszony zapis przy transakcji, indeksy na `timestamp` |
| P1-13 | `entry_price == exit_price` w 62/62 rekordach; `pnl_pct` liczony od nominału | Parowanie filli FIFO w transakcje round-trip; `pnl_pct` od marży |

Dodatkowo w trakcie prac wyszedł błąd nieujęty w audycie: `get_symbol_state()` dla nieznanego
symbolu zwracał stan symbolu **głównego**, więc stop-out na BTC wepchnąłby w SCOUT wszystkie
pozostałe pary — dokładne przeciwieństwo izolacji, której ta mapa służy.

---

## 5. Błędy popełnione w trakcie refaktoryzacji

Trzy usterki wprowadziłem sam i wychwyciłem je dopiero po wdrożeniu lub w przeglądzie. Notuję je,
bo są pouczające.

### 5.1 Klucz ledgera dokładek oparty na cenie wejścia

Licznik dokładek kluczowałem ceną wejścia pozycji, żeby nowa pozycja dostawała własny budżet.
Przeoczyłem, że **realizacja dokładki sama przesuwa średnią cenę wejścia**. Na żywym rachunku:
SOL miał zapisaną dokładkę pod kluczem `75.45`, pozycja miała już `75.375`, klucz przestawał pasować,
licznik zerował się sam — zabezpieczenie przepuściłoby drugą dokładkę na tej samej pozycji.

Wykryte ~10 minut po pierwszym wdrożeniu, przy weryfikacji tabel w produkcyjnej bazie. Naprawione
w `74d0e2b`: klucz to `(symbol, strona)`, kasowany dopiero gdy strona jest pusta.

### 5.2 Zaokrąglanie redukcji w złą stronę

Naprawiając P1-9 zamieniłem `floor` na `round`, co przy 3 kontraktach zamykało 2 (67%) zamiast 1
(33%). Dla redukcji **częściowej** to gorszy kierunek błędu niż pierwotny — zamyka więcej, niż
zamierzono, i jest nieodwracalne. Gdy skrypt audytowy to zgłosił, początkowo rozszerzyłem
tolerancję testu, zamiast poprawić kod; przegląd to wyłapał. Wróciłem do `floor` — właściwą naprawą
było usunięcie `max(1, …)`, nie zmiana zaokrąglenia.

### 5.3 Symlink `node_modules` w commicie

Do zbudowania dashboardu w worktree podlinkowałem `node_modules` z głównego katalogu. Symlink
trafił do repozytorium i **wywalił build obrazu Dockera**, a przy okazji nadpisał oryginalny katalog
rekurencyjnym dowiązaniem. Przyczyna: wzorzec `.gitignore` brzmiał `node_modules/` — z ukośnikiem
dopasowuje katalogi, ale nie symlinki. Poprawione na `node_modules`.

---

## 6. Weryfikacja

### Skrypty audytowe

Pięć wykonywalnych skryptów w `tests/audit/`, uruchamialnych **wewnątrz kontenera produkcyjnego**.
Konwencja: FAIL = defekt obecny.

| Skrypt | Przed | Po |
|---|---|---|
| `audit_math_indicators.py` | 9/15 | **15/15** |
| `audit_risk_asymmetry.py` | 5/12 | **12/12** |
| `audit_position_limits.py` | 1/8 | **8/8** |
| `audit_inactivity_lifecycle.py` | 7/17 | **17/17** |
| `audit_pnl_kpi.py` | 3/9 | **9/9** |
| **Razem** | **25/61** | **61/61** |

Uczciwe rozliczenie tej liczby: 34 asercje opisują defekty faktycznie naprawione, 2 zostały
przeformułowane z „defekt" na kontrakt decyzyjny (lewar BASE, lewar SCOUT na giełdzie), a ~12
zmieniono z sond na tekst źródłowy na testy behawioralne.

### Testy regresyjne

`tests/test_audit_remediation.py` — 57 testów, po jednym na ustalenie, nazwanych zgodnie z nim
(`test_scout_mode_entry_on_sl`, `test_impulse_negation_no_loop`,
`test_base_signal_requires_candle_direction`, `test_margin_never_exceeds_cap`, …).

Suita projektu pozostaje na poziomie sprzed zmian: te same 3 faile obecne już na `main`
(autoryzacja API) plus flaky `test_daemon_lifecycle` i dwa pliki zależne od sieci. Przy okazji
odblokowano `test_max_margin_allocation.py` i `test_trade_sync.py`.

### Weryfikacja produkcyjna

Po każdym wdrożeniu: zgodność sum MD5 repozytorium ↔ kontener, 61/61 skryptów audytowych wewnątrz
kontenera, kontrola tabel `position_snapshots` i `position_addons`, przegląd outcome'ów z bazy.

Po 12 minutach pracy nowej wersji: `NO_ACTION` 88, `SKIPPED_*` 88, akcje STALE 0, błędy 0. Oba
pominięcia zweryfikowane co do przyczyny — SOL blokowany przez ledger dokładek (mimo dryfu ceny
wejścia, czyli poprawka z §5.1 działa), BTC przez limit nominału na pozycji otwartej jeszcze przez
stary kod z `ceil()`.

---

## 7. Decyzje świadomie odłożone

Trzy pozycje pozostawiono bez zmiany, bo są decyzjami biznesowymi, nie usterkami. Wszystkie są
sterowane z `.env` i opisane w [rekomendacjach](REKOMENDACJE_KONFIGURACJA_2026-08-16.md):

1. **Lewar BASE_MODE x3.0** zamiast x1.0 ze specyfikacji — `MMS4_LEVERAGE_MODERATE=1.0` włącza
   zgodność literalną. Przy obecnym kapitale lewar nie jest czynnikiem ryzyka.
2. **Koszt round-trip w `BE_ESCAPE` = 0.0** — mechanizm gotowy, ale włączenie go **wymaga
   jednoczesnego rozszerzenia pasma BE** (`be_escape_threshold: 0.003`), inaczej warunki wykluczają
   się matematycznie i reguła II przestaje działać.
3. **ATR = SMA** — `MMS4_ATR_METHOD=wilder` daje ATR mniejszy o 8–16% (pomiar na 300 świecach 15m),
   czyli o kilkanaście procent węższe wstęgi. Sens wyłącznie przy strojeniu na TradingView i razem
   z korektą `atr_mult ≈ 2.3`.

---

## 8. Otwarte ryzyko

**Brak limitu portfelowego.** `max_margin_usd` działa per symbol. Przy 4 parach i planowanych
$500/parę to $2 000 marży bez żadnego hamulca na poziomie rachunku, a każda kolejna para zwiększa
ekspozycję liniowo. To jedyne istotne ryzyko strukturalne, które pozostało otwarte — warto je
domknąć przed podniesieniem wolumenów.

**SCOUT_MODE bez automatycznej furtki.** Analiza H4 Hermesa, która zwalnia `scout_override`, nie ma
wpisu w cronie. Przy obecnym kapitale SCOUT nie obejmuje BTC ani ICP, więc te pary po stop-oucie
czekałyby na ręczne zwolnienie. Skryptów nie oplanowałem — to zmiana na żywym hoście, a
`mms4tk_h4_analysis.py` rozluźnia stan ryzyka i nigdy nie był uruchamiany produkcyjnie; wymaga
jednorazowego uruchomienia ręcznego przed wpisaniem do harmonogramu.

---

*Raport sporządzony po wdrożeniu `ddf7d6c`. Wszystkie liczby pochodzą z pomiarów na kodzie
produkcyjnym lub z publicznego API OKX — żaden nie jest szacunkiem.*
