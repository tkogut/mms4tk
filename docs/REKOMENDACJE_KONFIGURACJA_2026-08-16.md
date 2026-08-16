# Rekomendacje konfiguracyjne po wdrożeniu remediacji

**Data:** 2026-08-16 · **Wersja wdrożona:** `74d0e2b` (main) · **Kontener:** `antigravity_core` (healthy)
**Weryfikacja produkcyjna:** 61/61 asercji audytowych PASS wewnątrz kontenera, 4 pozycje uzbrojone
w snapshoty do wykrywania stop-lossów.

Dotyczy trzech pozycji świadomie niezmienionych w remediacji (sekcja 6 raportu audytowego) oraz
jednego ustalenia, które **ujawniło się dopiero po wdrożeniu**.

---

## 🔴 0. PILNE — SCOUT_MODE stał się stanem pochłaniającym

**To nie było widoczne przed wdrożeniem, bo SCOUT_MODE był nieosiągalny (P0-1).** Teraz, gdy
rekoncyliacja działa, pierwszy stop-loss ≥ 2% realnie przełączy symbol w SCOUT_MODE — i tam utknie.

Łańcuch przyczynowy, każdy element zweryfikowany na produkcji:

```
SCOUT_MODE → lewar x0.1 → notional bazy = $10 × 0.70 × 0.1 = $0.70
$0.70 < $7.00 (minimum OKX)          → wejście POMIJANE (poprawka P1-8)
brak wejścia → brak pozycji → brak Take Profitu
brak TP → on_take_profit_hit() nigdy nie zadziała → SCOUT_MODE trwa
```

Zaprojektowana furtka awaryjna — analiza H4 Hermesa (`SWEEP_WICK` → `scout_override` →
`relax_scout_mode`) — **istnieje w kodzie, ale nie jest uruchamiana**. W cronie na VPS jest
wyłącznie hourly digest:

```
0 * * * * python3 /docker/mms4tk/scripts/hermes/mms4tk_multi_symbol_digest.py
```

`mms4tk_h4_analysis.py` i `mms4tk_event_dispatcher.py` nie mają wpisu. Historia stanów
(`state_history`) potwierdza: **ani jednego przejścia w SCOUT_MODE w całej historii** — mechanizm
nigdy nie był testowany w praktyce, więc luka nie miała jak się ujawnić.

Zmierzone progi kapitału:

| `max_margin_usd` | notional SCOUT | wynik |
|---|---|---|
| $10 (obecnie) | $0.70 | wejście pomijane |
| $50 | $3.50 | wejście pomijane |
| **$100** | **$7.00** | handluje |
| $200 | $14.00 | handluje |

### Rekomendacja (kolejność wdrożenia)

**Najpierw twarde ograniczenie, którego nie da się obejść konfiguracją.** Minimum giełdowe to $7
notional. Baza w BASE_MODE ma $21 notional. Zatem **maksymalna osiągalna redukcja ryzyka w SCOUT przy
`max_margin_usd = $10` wynosi 3× ($21 → $7)**, a nie 10× z opisu strategii. Asymetria x1.0/x0.1
z MMS v4 jest przy tej wielkości rachunku **nieosiągalna** — nie z powodu błędu w kodzie, tylko
dlatego, że giełda nie przyjmie zlecenia mniejszego niż $7.

Trzeba więc wybrać jedno z dwojga:

**Wariant A — redukcja 3×, możliwa dziś (rekomendowany).** SCOUT handluje pełną alokacją bazy przy
lewarze 1.0: notional $7 × 1.0 = **$7.00**, dokładnie na minimum, czyli 3× mniejsza ekspozycja niż
$21 w BASE. Wymaga zmiany kodu: rozdzielenia „lewara SCOUT" od „mnożnika rozmiaru SCOUT", bo dziś
oba są tą samą liczbą (0.1) i to ona schodzi poniżej minimum.

**Wariant B — redukcja 10× zgodna ze specyfikacją, wymaga kapitału.** Notional SCOUT = $7 przy
`max_margin_usd ≥ $100` (bo $100 × 0.70 × 0.1 = $7.00). Uwaga: to dziesięciokrotnie zwiększa też
ekspozycję w BASE_MODE — przy 4 symbolach $400 marży wobec ~$59 wolnego salda. **Przy obecnym
kapitale niewykonalne.**

**Krok doraźny — furtka awaryjna, do rozważenia natychmiast.** Niezależnie od wyboru A/B, SCOUT
potrzebuje zaprojektowanego wyjścia. Skrypty istnieją, ale nie są w cronie:

```bash
*/30 * * * * python3 /docker/mms4tk/scripts/hermes/mms4tk_h4_analysis.py     >> /var/log/mms4tk_h4.log 2>&1
*/10 * * * * python3 /docker/mms4tk/scripts/hermes/mms4tk_event_dispatcher.py >> /var/log/mms4tk_disp.log 2>&1
```

> ⚠️ **Nie dodałem tych wpisów** — uruchomienie nowej automatyki na żywym hoście handlowym to
> osobna decyzja, a `mms4tk_h4_analysis.py` zapisuje `scout_override`, czyli **rozluźnia stan
> ryzyka**. Oba skrypty nigdy nie były uruchamiane w produkcji, więc nie mają żadnego przebiegu.
> Przed wpisaniem do crona uruchomić każdy **ręcznie raz** i sprawdzić wynik:
> `ssh root@… "python3 /docker/mms4tk/scripts/hermes/mms4tk_h4_analysis.py"`.

**Do czasu wdrożenia kroku 1 lub 2:** monitorować `/api/v1/state`. Jeśli symbol wejdzie w SCOUT_MODE,
odblokować ręcznie:
`POST /api/v1/settings/update {"scout_mode": false}` (endpoint naprawiony w P1-10 i działa).

---

## 1. Lewar BASE_MODE x3.0 — **zostawić bez zmian**

Specyfikacja MMS v4 mówi o x1.0, implementacja stosuje x3.0 (profil MODERATE). To nie jest bug,
tylko nadbudowa Two-Tier Adaptive; docstring został poprawiony, a mapa jest sterowalna z env.

**Dlaczego zostawić:**

| | lewar x1.0 | lewar x3.0 (obecnie) |
|---|---|---|
| Notional bazy | $7 | $21 |
| Koszt stop-lossa 2% | $0.14 | $0.42 |
| Strata jako % marży bazy ($7) | 2% | 6% |
| Ekspozycja 4 symboli (baza+dokładka) | $40 | $120 |

Przy wolnym saldzie ~$59 pełna alokacja na 4 symbolach to ~$40 marży (68% salda) i $120 notional,
czyli ok. 2× lewar na poziomie rachunku w trybie cross. To jest wysokie, ale nie likwidacyjne, a
strata pojedynczego stop-lossa to $0.42 — kwota nieistotna wobec salda.

Zejście na x1.0 zmniejszyłoby stratę SL do $0.14, ale też potroiłoby liczbę transakcji potrzebnych
do jakiegokolwiek wyniku. **Przy tej wielkości rachunku lewar nie jest czynnikiem ryzyka** — jest nim
brak limitu zbiorczego.

**Rekomendacja:** utrzymać x3.0. Osobno rozważyć **limit portfelowy** (brak go dziś —
`max_margin_usd` działa per symbol, więc dodanie 5. i 6. pary liniowo zwiększa ekspozycję bez
żadnego hamulca). To jest realne ryzyko, a nie wartość lewara.

Jeśli mimo to chcesz zgodność literalną ze specyfikacją:
```bash
MMS4_LEVERAGE_MODERATE=1.0
```

---

## 2. `MMS4_BE_COST_PCT=0.0012` — **nie ustawiać samodzielnie**

Moja pierwotna sugestia była niekompletna. Zmierzone zachowanie:

```
koszt=0.00%  pasmo_BE=0.10%  -> BE_ESCAPE na świecy 13
koszt=0.12%  pasmo_BE=0.10%  -> BE_ESCAPE NIE ODPALA, wyjście spada na drabinkę (świeca 17)
koszt=0.12%  pasmo_BE=0.30%  -> BE_ESCAPE na świecy 13
koszt=0.12%  pasmo_BE=0.50%  -> BE_ESCAPE na świecy 13
```

**Przyczyna konfliktu:** BE_ESCAPE wymaga jednocześnie `|cena − wejście| ≤ pasmo` **oraz**
`zysk ≥ koszt`. Przy paśmie 0.10% i koszcie 0.12% te warunki wykluczają się matematycznie — pasmo
0.1% nigdy nie pomieści zysku 0.12%. Reguła II cicho przestaje istnieć, a pozycja czeka 4 świece
dłużej na drabinkę.

**Rekomendacja:** ustawiać **parę** wartości, nigdy samego kosztu:

```bash
MMS4_BE_COST_PCT=0.0012        # taker OKX 0.05% × 2 nogi + bufor na funding
```
oraz per symbol w konfiguracji: `be_escape_threshold: 0.003` (0.30%).

Wtedy BE_ESCAPE odpala w przedziale zysku +0.12%…+0.30%, czyli realnie „na zero po kosztach", i
robi to w tym samym momencie co dziś (świeca 13). Bez rozszerzenia pasma zmiana pogarsza sytuację.

**Priorytet: niski.** Obecne domyślne 0.0 oznacza wyjście ze stratą rzędu 0.1% pozycji — przy
notional $21 to ok. $0.02 na transakcję. Warto włączyć przy większym kapitale.

---

## 3. `MMS4_ATR_METHOD=wilder` — **włączyć tylko razem z korektą `atr_mult`**

Pomiar na realnych świecach 15m (300 sztuk, publiczne API OKX, 2026-08-16):

| symbol | ATR (SMA) | ATR (Wilder) | różnica | sygnałów SMA | sygnałów Wilder |
|---|---|---|---|---|---|
| BTC/USDC | 51.5286 | 43.3619 | **−15.8%** | 54 | 55 |
| ETH/USDC | 1.9086 | 1.6476 | −13.7% | 45 | 38 |
| SOL/USDC | 0.0764 | 0.0699 | −8.5% | 50 | 43 |
| ICP/USDC | 0.0053 | 0.0046 | −12.4% | 32 | 33 |

Wynik jest przeciwny do intuicji: Wilder daje ATR **mniejszy** o 8–16%, nie większy. Skutek —
wstęgi TMA są o kilkanaście procent węższe, bo `pasmo = TMA ± atr_mult × ATR`.

Wpływ na liczbę sygnałów jest niejednoznaczny (BTC i ICP bez zmian, ETH −16%, SOL −14%), więc
**sama zmiana wygładzania nie poprawia strategii** — przesuwa jedynie geometrię wstęg i unieważnia
dotychczasowy dobór parametrów.

**Rekomendacja:** przełączać wyłącznie wtedy, gdy strategię stroisz wizualnie na TradingView
(tam ATR to RMA Wildera i dziś wskazania się rozjeżdżają o ~13%). Jeśli tak — zmienić **obie**
wartości naraz, żeby zachować dotychczasową szerokość wstęg:

```bash
MMS4_ATR_METHOD=wilder
# atr_mult 2.0 → ok. 2.3   (2.0 / 0.87, kompensacja średnio 13% mniejszego ATR)
```

Bez korekty `atr_mult` dostaniesz węższe wstęgi, czyli częstsze i płytsze wejścia — zmianę
strategii, nie tylko sposobu liczenia.

**Priorytet: niski.** Jedyny realny zysk to spójność z wykresem, na którym pracujesz.

---

## Podsumowanie priorytetów

| # | Pozycja | Priorytet | Działanie |
|---|---|---|---|
| 0 | SCOUT_MODE bez wyjścia | 🔴 **pilne** | Wariant A: SCOUT = baza przy lewarze 1.0 (redukcja 3×, możliwa dziś). Doraźnie: ręcznie sprawdzić, potem oplanować `h4_analysis` + `event_dispatcher` |
| — | Brak limitu portfelowego | 🟠 średni | Dodać zbiorczy cap marży ponad `max_margin_usd` per symbol |
| 1 | Lewar x3.0 | 🟢 bez zmian | Utrzymać; nie jest czynnikiem ryzyka przy tym kapitale |
| 2 | `MMS4_BE_COST_PCT` | 🟢 niski | Tylko razem z `be_escape_threshold: 0.003`, inaczej wyłącza regułę II |
| 3 | `MMS4_ATR_METHOD=wilder` | 🟢 niski | Tylko razem z `atr_mult ≈ 2.3`; sens wyłącznie przy strojeniu na TradingView |

Żadna z pozycji 1–3 nie wymaga natychmiastowego działania. Pozycja 0 wymaga.

---

## Aneks: stan ledgera dokładek po migracji

Jeden wiersz w `position_addons` powstał jeszcze pod starym kluczowaniem (przed poprawką `74d0e2b`)
i przetrwał do nowego schematu. Zweryfikowano, że zachowuje się poprawnie:

```
ledger:                    [{'symbol': 'SOL/USDC', 'side': 'short', 'addon_count': 1}]
otwarte pozycje:           ['BTC/USDC', 'ETH/USDC', 'ICP/USDC', 'SOL/USDC']
zablokowana dokładka:      ['SOL/USDC']
wolne na pierwszą dokładkę:['BTC/USDC', 'ETH/USDC', 'ICP/USDC']
```

SOL/USDC faktycznie otrzymał dokładkę (40 kontraktów ≈ 27 bazy + 11 dokładki), więc blokada jest
zasadna — i teraz trzyma mimo dryfu średniej ceny wejścia z 75.45 na 75.375, co było istotą błędu
naprawionego w `74d0e2b`. Pozostałe trzy symbole mają czysty ledger, więc ich pierwsza legalna
dokładka nie jest blokowana. **Nie wymaga żadnego działania.**
