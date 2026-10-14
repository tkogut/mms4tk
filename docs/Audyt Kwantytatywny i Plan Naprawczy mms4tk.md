# 🛸 RFC: Audyt Kwantytatywny i Plan Naprawczy Systemu `mms4tk` (MMS v4)

**Autor:** MMS4 Antigravity AI (Quantitative Architect & Market Mechanics Reviewer)  
**Cel dokumentu:** Analiza przyczyn ujemnej wartości oczekiwanej (mediana $-23\%$ na oknie 610-dniowym) w repozytorium `tkogut/mms4tk` (v0.33.1 – v0.44.6) oraz specyfikacja techniczna poprawek dla zespołu agentów (Claude / Hermes Swarm).  
**Status:** WYMAGA NATYCHMIASTOWEJ IMPLEMENTACJI (Faza 3)

---

## 📋 1. Podsumowanie Wykonawcze (Executive Summary)

Z punktu widzenia inżynierii oprogramowania architektura `mms4tk` (asynchroniczny daemon, persystencja SSOT w SQLite `logs.db`, separacja warstw wykonawczych i nadzór Hermes Agent) jest skonstruowana wzorcowo. 

Jednak z punktu widzenia **mechaniki rynku i matematyki strategii Mean-Reversion (MMS v4)**, obecna implementacja zawiera **5 krytycznych błędów konstrukcyjnych ("Expected Value Killers")**. System systematycznie łamie fundamentalne prawa statystyczne oryginalnej strategii MMS4: **bierze na siebie pełne, szerokie 2% Stop-Lossy podczas silnych trendów, ale w fazach konsolidacji przedwcześnie ucina zyskowne pozycje** (poprzez `inactivity_manager.py`, `BE_ESCAPE` oraz `Midline T1 TP`) oraz **zbyt słabo redukuje ekspozycję w trybie ochronnym (`SCOUT_MODE` = 0.3x zamiast 0.1x)**.

Poniżej znajduje się szczegółowa diagnoza każdego z 5 problemów oraz gotowe wytyczne architektoniczne do wdrożenia w kodzie.

---

## 🔴 2. Diagnoza Krytycznych Błędów (Expected Value Killers)

### Błąd #1: Złamanie "Żelaznej Reguły" braku BE i przedwczesnego ucinania pozycji
W oficjalnej specyfikacji MMS v4 obowiązuje **całkowity zakaz stosowania Trailing Stopów oraz wczesnego przesuwania na Break-Even (BE)**. Backtesty i statystyka rynkowa dowodzą, że w strategiach Mean-Reversion mechanizmy te drastycznie obniżają wartość oczekiwaną ($E[V]$), ponieważ wycinają pozycje podczas naturalnych retestów i kompresji zmienności przed właściwym ruchem powrotnym.

* **Stan obecny w `mms4tk`:** Wprowadzono rozbudowaną logikę *Position lifecycle protection* (`inactivity_manager.py`):
  * `Impulse negation`: redukcja `SPLIT_50`, a następnie `FORCE_CLOSE` przy cofnięciu impulsu o 50%.
  * `Stagnation ladder`: zamknięcie `SPLIT_50` $\rightarrow$ `FORCE_CLOSE_BE`, gdy cena stoi w miejscu przez określoną liczbę świec.
  * `Break-even escape`: automatyczna ucieczka na `BE_ESCAPE`.
  * `Dual-Stage TP (T1 Midline TP)`: zamykanie części pozycji już na środkowej linii TMA.
* **Skutek matematyczny:** Asymetria zysku do ryzyka ($R:R$) zostaje zniszczona. Kiedy rynek trenduje, bot uderza w pełny Stop-Loss ($-2.0\%$). Kiedy rynek wchodzi w konsolidację i powoli wraca do średniej, bot sam obcina sobie potencjał zysku o $50\%$ lub zamyka pozycję na $0\%$ (BE) z powodu "stagnacji". W strategii Mean-Reversion czas nie jest wrogiem – liczy się ekspozycja od bandy do bandy.

### Błąd #2: Wadliwa parametryzacja Maszyny Stanów (`SCOUT_MODE` = 0.3x zamiast 0.1x)
Sekwencyjność (`MMS4StateManager`) to główna tarcza anty-trendowa strategii. Jej celem jest **spłaszczenie krzywej kapitału (flat equity curve)** podczas silnych impulsów kierunkowych poprzez redukcję lewara o **90% (z $1.0\times$ na $0.1\times$)** lub **99% ($0.01\times$)**.

* **Stan obecny w `mms4tk` (`.env`):**
  ```ini
  MMS4_SCOUT_LEVERAGE=1.0
  MMS4_SCOUT_SIZE_MULT=0.3
  MMS4_LEVERAGE_MODERATE=3.0
  ```
* **Skutek matematyczny:**
  1. Lewar bazowy $3.0\times$ przy SL $2.0\%$ oznacza aż $6.0\%$ straty kapitału alokowanego na pojedynczym wybiciu.
  2. Redukcja `SIZE_MULT` do `0.3` ($30\%$ pozycji bazowej) nie chroni kapitału w trendzie! Podczas silnego impulsu na BTC/ETH (gdzie cena generuje 5–8 fałszywych sygnałów powrotu pod rząd), tracenie po $30\%$ normalnej stawki na każdym kroku kumuluje się do głębokiego obsunięcia kapitału (stąd wynik $-23\%$ na 610 dniach).
  3. **Ryzyko przedwczesnego resetu:** Nawet po wdrożeniu poprawki z v0.42.0, należy bezwzględnie upewnić się, że wyjście na `T1 Midline TP` lub `BE_ESCAPE` z symbolicznym zyskiem netto ($+\$0.01$) **nie resetuje** trybu `SCOUT_MODE` z powrotem do `BASE_MODE` w środku trwającego trendu.

### Błąd #3: Zniekształcenie proporcji kapitału i ryzyka przy dokładkach (Alokacja 70/30 vs 1:1)
* **Specyfikacja MMS v4:** Pozycja bazowa ma wagę **$1\times$** (SL $2.0\%$). Pierwsza dokładka na świecy potwierdzającej ma wagę **$1\times$** (łączny lewar $2\times$, proporcja **50/50**), a jej SL jest ustawiany ciasno pod lokalnym ekstremum (parą knotów) i **nie może przekraczać $1.0\%$**. Dzięki temu ryzyko dokładki ($1.0\% \times 1$) stanowi połowę ryzyka bazy ($2.0\% \times 1$), a środek ciężkości całej pozycji przesuwa się agresywnie na korzyść tradera.
* **Stan obecny w `mms4tk`:** Sztywny podział `70/30` (`MMS4_BASE_ALLOCATION=0.70`, `MMS4_ADDON_ALLOCATION=0.30`) ze sztywnym `MMS4_ADDON_SL_PCT=0.0125` ($1.25\%$).
* **Skutek matematyczny:** Dokładka stanowi zaledwie $\sim 42.8\%$ wielkości pozycji bazowej ($\$3$ do $\$7$). Gdy pojawia się właściwy sygnał odwrócenia z potwierdzeniem, dokładka ma zbyt małą wagę, by wygenerować asymetryczny zysk pokrywający wcześniejsze straty. Z kolei sztywne $1.25\%$ SL na dokładce ignoruje mikrostrukturę świec (knoty).

### Błąd #4: Problem konta Nettingowego na OKX (Brak izolacji SL dla dokładki)
Na kontraktach OKX Perpetual Swap w trybie *Net* (oraz w ramach tego samego kierunku na subkoncie), giełda uśrednia cenę wejścia (`avgPx`) dla całej pozycji.
* **Zagrożenie w architekturze:** Zgodnie z regułami MMS4, jeśli ciasny SL dokładki ($\le 1.0\%$) zostanie wybity, zamykana jest **wyłącznie dokładka**, a pozycja bazowa musi pozostać w grze ze swoim oryginalnym, nienaruszonym $2.0\%$ SL liczonym od ceny wejścia bazy. Jeśli daemon zarządza pozycją jako zintegrowaną bryłą (np. poprzez regułę `Impulse negation -> SPLIT_50 then FORCE_CLOSE`), wybicie dokładki pociąga za sobą przedwczesną likwidację pozycji bazowej.

### Błąd #5: Statyczne parametry TMA i opóźnienie fazowe (Lag) w silnych trendach
* **Problem opóźnienia TMA:** Klasyczna, niecentrowana *Triangular Moving Average* ($\text{SMA}(\text{SMA}(\text{Close}, N), N)$) charakteryzuje się ogromnym opóźnieniem fazowym (lagiem). Kiedy na rynku krypto rozpoczyna się silny trend, niecentrowana wstęga TMA podąża za ceną bardzo wolno. Cena przebywa poza wstęgą przez dziesiątki świec, a silnik co chwilę generuje sygnały kontrariańskie (SHORT w silnej hossie / LONG w krachu), które są seryjnie wybijane przez Stop-Loss.
* **Brak rekalibracji kroczącej:** Testowanie jednego, zamrożonego zestawu parametrów TMA/ATR na oknie 610 dni jest sprzeczne z naturą rynków nie-stacjonarnych. Specyfikacja MMS4 zakłada krótkie, **3-dniowe (lub 7-dniowe) okna kroczące (Rolling Walk-Forward)** do bieżącej adaptacji szerokości wstęg oraz bezwzględne filtrowanie kierunku przez wyższe interwały (H4/D1).

---

## 🛠️ 3. Specyfikacja Techniczna Poprawek dla Agentów (Action Plan – Faza 3)

Zalecam podział prac dla agentów Claude / Hermes na **5 konkretnych zadań inżynieryjnych (PRs)**:

### Zadanie 1: Deaktywacja "Expectancy Killers" w `inactivity_manager.py`
1. Wprowadzić flagę konfiguracyjną `MMS4_PURE_V4_EXECUTION=true`.
2. Gdy flaga jest aktywna:
   * Całkowicie wyłączyć `Stagnation ladder`, `Impulse negation` oraz `BE_ESCAPE`.
   * Wyłączyć częściową realizację zysków na linii środkowej (`T1 Midline TP` = $0\%$, `T2 Opposite Band TP` = $100\%$).
   * Pozycja bazowa może zostać zamknięta **wyłącznie** przez: (a) dotknięcie przeciwległej wstęgi TMA (100% TP), lub (b) twardy Stop-Loss $2.0\%$ od ceny wejścia bazy.

### Zadanie 2: Refaktoryzacja `state_manager.py` i wdrożenie "Paper Scout Mode"
Aby pogodzić wymóg redukcji ekspozycji do **$0.1\times$** w `SCOUT_MODE` z minimalnym progiem zlecenia na giełdzie OKX (`MMS4_MIN_NOTIONAL_USD=7.0`) przy małych budżetach testowych (np. $\$10$ USD):

1. Zmienić domyślny `MMS4_SCOUT_SIZE_MULT` z `0.3` na **`0.1`** (lub `0.01`).
2. Wprowadzić mechanizm **Paper Scout (Virtual Shadow Tracking)** w SQLite (`logs.db`):
   * Jeśli system znajduje się w `SCOUT_MODE` i wyliczony wolumen zlecenia $V_{\text{scout}} = V_{\text{base}} \times 0.1 < \text{MIN\_NOTIONAL\_OKX}$, silnik wykonawczy **nie wysyła fizycznego zlecenia do API OKX**, lecz otwiera wirtualną pozycję zwiadowczą w tabeli `virtual_scout_positions`.
   * Daemon monitoruje tę pozycję w cyklu 15-sekundowym na żywych świecach OKX.
   * Przejście `SCOUT_MODE` $\rightarrow$ `BASE_MODE` następuje **wyłącznie wtedy**, gdy pozycja zwiadowcza (fizyczna lub wirtualna) dotknie pełnego TP na przeciwległej wstędze TMA i zamknie się z dodatnim wynikiem netto (po uwzględnieniu symulowanych prowizji Taker/Maker).

#### Wzorcowa implementacja logiki maszyny stanów:
```python
from enum import Enum
from dataclasses import dataclass

class TradingState(str, Enum):
    BASE_MODE = "BASE_MODE"     # Lewar efektywny: 1.0x | Dokładki: DOZWOLONE
    SCOUT_MODE = "SCOUT_MODE"   # Lewar efektywny: 0.1x | Dokładki: ZABLOKOWANE

@dataclass(frozen=True)
class TradeCloseEvent:
    hit_opposite_tma_band: bool  # True TYLKO przy pełnym TP na przeciwległej wstędze
    hit_base_hard_sl: bool       # True przy wybiciu 2.0% SL pozycji bazowej
    realized_net_pnl: float      # PnL netto po odjęciu prowizji FIFO

class MMS4StateManager:
    def __init__(self, initial_state: TradingState = TradingState.BASE_MODE) -> None:
        self._state: TradingState = initial_state

    @property
    def state(self) -> TradingState:
        return self._state

    @property
    def size_multiplier(self) -> float:
        return 1.0 if self._state == TradingState.BASE_MODE else 0.1

    @property
    def can_add_on(self) -> bool:
        """Bezwzględna blokada dokładek w trybie SCOUT_MODE."""
        return self._state == TradingState.BASE_MODE

    def process_trade_close(self, event: TradeCloseEvent) -> TradingState:
        if self._state == TradingState.BASE_MODE:
            # Każde wybicie 2% SL bazy natychmiast zrzuca system do SCOUT_MODE
            if event.hit_base_hard_sl or event.realized_net_pnl < 0.0:
                self._state = TradingState.SCOUT_MODE

        elif self._state == TradingState.SCOUT_MODE:
            # Żelazna Reguła: TYLKO pełny TP na przeciwległej wstędze resetuje stan do BASE_MODE
            if event.hit_opposite_tma_band and event.realized_net_pnl > 0.0:
                self._state = TradingState.BASE_MODE

        return self._state
```

### Zadanie 3: Korekta mechaniki dokładek (`HIGH_RISK_ADD`) i izolacja SL na OKX
1. **Zmiana alokacji kapitału:** Zmieńcie proporcję `MMS4_BASE_ALLOCATION` / `MMS4_ADDON_ALLOCATION` z `0.70 / 0.30` na **`0.50 / 0.50`** (relacja 1:1 zgodna ze specyfikacją MMS4: $1\times$ baza + $1\times$ dokładka).
2. **Strukturalny SL dokładki (Wick-Based SL $\le 1.0\%$):**
   * Zamiast sztywnego `MMS4_ADDON_SL_PCT=0.0125`, obliczajcie SL dokładki na podstawie lokalnego ekstremum z pary świec (świeca wejścia bazy $t-2$ oraz pierwsza świeca potwierdzająca kierunek $t-1$):
     $$\text{SL}_{\text{add, LONG}} = \min(\text{Low}_{t-1}, \text{Low}_{t-2}) - \text{tick\_size}$$
     $$\text{SL}_{\text{add, SHORT}} = \max(\text{High}_{t-1}, \text{High}_{t-2}) + \text{tick\_size}$$
   * Obliczcie dystans procentowy:
     $$\Delta_{\text{SL}} = \frac{|\text{Entry}_{\text{add}} - \text{SL}_{\text{add}}|}{\text{Entry}_{\text{add}}}$$
   * Jeśli $\Delta_{\text{SL}} > 0.01$ ($1.0\%$), dokładka jest **blokowana** (zbyt duża zmienność świecy potwierdzającej) lub SL dokładki jest sztywno przycinany do maksymalnie $1.0\%$.
3. **Izolacja wolumenu na koncie Nettingowym OKX:**
   * Po otwarciu dokładki o wolumenie $Q_{\text{add}}$, daemon wystawia osobne zlecenie warunkowe `reduceOnly=True` na dokładną ilość kontraktów $Q_{\text{add}}$ po cenie $\text{SL}_{\text{add}}$.
   * Wybicie $\text{SL}_{\text{add}}$ redukuje pozycję o $Q_{\text{add}}$, pozostawiając wolumen bazowy $Q_{\text{base}}$ z jego pierwotnym zleceniem obronnym na poziomie $-2.0\%$ od ceny wejścia bazy. Flaga dokładki dla bieżącego cyklu zostaje zablokowana (`addon_used_in_cycle = True` – zakaz ponawiania dokładki po jej wybiciu).

### Zadanie 4: Reaktywacja Bramki Trendu (TMA Slope Gate / PLAN-010 + Kontekst H4/D1)
Jak udowodniły testy Fazy 2 na 610 dniach, najczęstszą przyczyną strat jest seryjne otwieranie pozycji SHORT na górnej wstędze podczas silnego trendu wzrostowego (lub LONG podczas krachu). Należy bezwzględnie wdrożyć i przetestować na oknie 610-dniowym **znormalizowany filtr nachylenia TMA (TMA Slope Gate)**:

1. Obliczajcie znormalizowane względem zmienności nachylenie linii środkowej TMA na przestrzeni ostatnich $k$ świec (np. $k = 5$):
   $$\text{Slope}_{\text{norm}}(t) = \frac{\text{TMA}_{\text{mid}}(t) - \text{TMA}_{\text{mid}}(t - k)}{k \cdot \text{ATR}_{14}(t)}$$
2. **Reguła Bramki Kierunkowej (Directional Bias Gate):**
   * Jeśli $\text{Slope}_{\text{norm}}(t) > +\theta_{\text{slope}}$ (silny impuls wzrostowy), system ustawia blokadę `BLOCK_SHORT = True` (dozwolone wyłącznie szukanie pozycji LONG na dolnej wstędze).
   * Jeśli $\text{Slope}_{\text{norm}}(t) < -\theta_{\text{slope}}$ (silny impuls spadkowy), system ustawia blokadę `BLOCK_LONG = True` (dozwolone wyłącznie szukanie pozycji SHORT na górnej wstędze).
3. **Filtr Wyczerpania D1 (Overextension Gate):**
   * Jeśli dzienny zasięg świecy D1 ($\text{High}_{\text{D1}} - \text{Low}_{\text{D1}}$) przekracza $2.5 \times \text{ATR}_{14}(\text{D1})$, system natychmiast blokuje dokładki (`HIGH_RISK_ADD = False`).

### Zadanie 5: Zmiana metodyki walidacji na Rolling Walk-Forward Optimization (WFO)
Testowanie jednego, stałego zestawu parametrów (`tma_period`, `atr_multiplier`) przez 610 dni na rynku krypto jest błędem metodologicznym.
1. Przebudować harness testowy tak, aby wspierał **Rolling Walk-Forward Optimization (WFO)**.
2. Zamiast optymalizacji globalnej, zastosować krótkie okno kroczące:
   * **Okno kalibracji (In-Sample):** np. od 3 do 14 dni (dobór optymalnego mnożnika ATR i okresu TMA zapewniającego stabilne odbicia w bieżącej strukturze zmienności).
   * **Okno wykonawcze (Out-of-Sample):** kolejne 1–3 dni handlu na zamrożonych parametrach, po czym okno przesuwa się do przodu.

---

## 📊 4. Tabela Kontrolna dla Agentów (Macierz Zgodności z MMS v4)

| Komponent | Stan obecny w `mms4tk` (Błędny) | Docelowy stan w Fazie 3 (Zgodny z MMS v4) | Priorytet |
| :--- | :--- | :--- | :--- |
| **Zarządzanie pozycją (`inactivity_manager`)** | `Stagnation ladder`, `Impulse negation`, `BE_ESCAPE`, `Midline T1 TP`. | **WYŁĄCZONE.** Brak BE, brak cięcia w połowie. Czysta gra od wstęgi do wstęgi lub 2% SL. | 🔴 **P0 (Bloker)** |
| **Mnożnik w `SCOUT_MODE`** | `0.3x` (`MMS4_SCOUT_SIZE_MULT=0.3`), lewar bazowy `3.0x`. | **`0.1x`** (lub **Paper Scout** w SQLite poniżej minimum OKX), lewar bazowy **`1.0x`**. | 🔴 **P0 (Bloker)** |
| **Reset `SCOUT_MODE` $\rightarrow$ `BASE`** | Ryzyko resetu przy częściowym zamknięciu lub `BE_ESCAPE`. | Reset **wyłącznie** po 100% TP na przeciwległej wstędze TMA i $\text{PnL}_{\text{net}} > 0$. | 🔴 **P0 (Bloker)** |
| **Filtr Trendu (Pre-Trade)** | Wyłączony (odrzucony wcześniej na niereprezentatywnym oknie 90d). | **Wdrożony TMA Slope Gate ($\text{Slope}_{\text{norm}}$)** + filtr H4/D1 blokujący grę pod silny impuls. | 🔴 **P0 (Bloker)** |
| **Alokacja Baza / Dokładka** | `70% / 30%`, sztywny SL dokładki `1.25%`. | **`50% / 50%` (1:1)**, dynamiczny SL dokładki pod parą knotów **$\le 1.0\%$** (`reduceOnly`). | 🟠 **P1 (Wysoki)** |
| **Metodyka Backtestu (610d)** | Statyczne parametry przez 24 miesiące. | **Rolling Walk-Forward (WFO)** z cykliczną rekalibracją wstęg TMA/ATR. | 🟠 **P1 (Wysoki)** |

---

## 💬 5. Pytania do przedyskutowania z Agentami (Prompt startowy na spotkanie)

> **Instrukcja dla Agentów (Claude / Hermes):**  
> Przeanalizujcie powyższy audyt RFC. Nasze dotychczasowe testy na 610 dniach (mediana $-23\%$) badały system z włączonymi mechanizmami ucinania zysków (`inactivity_manager`, `Midline TP`) oraz zbyt wysokim mnożnikiem w trybie ochronnym (`SCOUT_SIZE_MULT=0.3`), bez aktywnego filtra nachylenia wstęgi (`TMA Slope Gate`).  
>  
> **Zadanie na teraz:**  
> 1. Przygotujcie test A/B na 610-dniowym oknie w harnessie V2, porównujący obecny baseline z konfiguracją **Pure MMS v4** (wyłączony `inactivity_manager`, brak `Midline TP`, `SCOUT_SIZE_MULT=0.1` z twardym resetem dopiero na przeciwległej wstędze).  
> 2. Na tej czystej bazie uruchomcie siatkę testową dla **TMA Slope Gate (PLAN-010)** ($\text{Slope}_{\text{norm}} \in \{0.15, 0.25, 0.35, 0.50\}$), który blokuje otwieranie pozycji przeciwko silnemu nachyleniu wstęgi.  
> 3. Przedstawcie raport wpływu każdego z tych dwóch kroków (Ablation Study) na maksymalne obsunięcie kapitału (MaxDD) oraz wynik końcowy w oknach silnie trendowych.