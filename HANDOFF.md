# HANDOFF — Agent State Transfer Document

> Jeśli czytasz to jako inny agent: Faza 6 (Hermes integration hardening) jest
> **zaimplementowana, zmergowana i zdeployowana**. Zostało wyłącznie jedno,
> pasywne zadanie: empiryczna weryfikacja po najbliższym 4h cyklu crona.
> Pełny log: `task.md`, sekcja "🔹 Faza 6 (2026-09-13)".

## Session Info
- **Conversation ID**: `hermes-integration-hardening`
- **Role**: Coordinator
- **Timestamp**: 2026-09-13T11:15:00Z
- **Status**: `completed` (implementacja + deploy) — jedno pasywne zadanie weryfikacyjne otwarte

## Current Task
Wszystkie 7 poprawek (C.1.2→C.1.1→C.1.3→C.2→C.3.1→C.3.2→C.3.3) zaimplementowane
(PR #55, zmergowane), zweryfikowane niezależnie (diff + 425 passed pytest), i
zdeployowane: `antigravity_core` (HEAD=`685112a`, healthy, nowy endpoint
`/api/v1/diagnostics/bias-sync` potwierdzony żywym requestem) ORAZ obie kopie
`mms4tk_h4_analysis.py` na `hermes-agent` (z kopiami zapasowymi, potwierdzone
identyczne, payload już bez `directional_bias`).

**Jedyne pozostałe zadanie — pasywne, nie wymaga akcji teraz**: po najbliższym
uruchomieniu crona (harmonogram `0 */4 * * *`) sprawdzić `strategy_profile`
na produkcji — nowy wiersz "H4 Cron:" powinien mieć `directional_bias`
identyczny jak poprzedni (nie nadpisany). To ostateczne potwierdzenie, że
wdrożenie faktycznie działa na żywo.

**Krok 1 z oryginalnego planu wciąż nie zrobiony**: wdrożenie promptu
operacyjnego dla Hermesa (pełny tekst w `task.md`/Faza 6) do konfiguracji
Hermes Agenta — to dzieje się poza tym repo, user robi to sam lub prosi o
instrukcję krok po kroku. Niezależne od reszty, można w każdej chwili.

## Progress
- [x] Audyt kanban, H4 Cron, 12 narzędzi Hermesa — kompletny
- [x] Prompt operacyjny dla Hermesa — gotowy, pełny tekst w `task.md`
- [x] Plan implementacji — spisany, Krok 0 (zakres) zdecydowany przez usera
- [x] Krok 3 (deploy path skryptu crona) — zbadane, oba miejsca zidentyfikowane
- [x] Implementacja C.1.2→C.3.3 — kompletna, zweryfikowana, zmergowana (PR #55)
- [x] Deploy `antigravity_core` — DONE, healthy, endpoint zweryfikowany
- [x] Deploy `mms4tk_h4_analysis.py` na obie kopie `hermes-agent` — DONE
- [ ] Weryfikacja empiryczna po najbliższym cyklu crona (pasywne, patrz wyżej)
- [ ] Krok 1: wdrożenie promptu Hermesa (poza repo, niezależne, nadal otwarte)

## State Snapshot
### Files Modified
- `main` (`685112a`): pełna implementacja Fazy 6 (`api.py`, `daemon.py`,
  `hermes_tools.py`, `hermes_webhook.py`, `logger_db.py`,
  `scripts/hermes/mms4tk_h4_analysis.py`, testy)
- VPS: `/docker/hermes-agent/data/scripts/mms4tk_h4_analysis.py` i
  `/docker/hermes-agent/data/home/.hermes/scripts/mms4tk_h4_analysis.py`
  zaktualizowane, kopie zapasowe `*.bak_20260913_110823` obok oryginałów

### Pending Actions
1. [x] **Inactivity Giveback Protection & Live Price Feed**: Zaimplementowane i otwarte w **PR #60** (https://github.com/tkogut/mms4tk/pull/60) — czeka na review/merge usera:
   - Podmiana `current_price=candles[-1].close if candles else signal.price` w `daemon.py`
   - Reguła Giveback Protection (`peak_profit_atr >= 1.0` i spadek >50% ze szczytu -> natychmiastowy `SPLIT_50`) w `inactivity_manager.py`
   - Nowe testy jednostkowe (`427 passed, 4 deselected`).
2. Po najbliższym cyklu crona (00/04/08/12/16/20 UTC) — sprawdzić `strategy_profile` na produkcji, potwierdzić brak zmiany `directional_bias` w nowym wierszu "H4 Cron:".
3. Zaproponować/wdrożyć Krok 1 (prompt Hermesa) gdy user będzie gotów.

### Blockers
- Brak.

## ⚠️ Active Finding / Incident Report (2026-09-14)
- **Data incydentu**: 2026-09-13 (ok. 21:00 - 23:00 UTC) na pozycjach LONG BTC/USDC i ETH/USDC.
- **Przebieg**: Pozycje osiągnęły w szczycie zysk >3×ATR (~$5.44 uPnL), po czym w 1 świecy 1H oddały całość zysku do małej straty (zamknięcie przez `STALE_SPLIT_50` z 12 świec dopiero na dnie).
- **Szczegółowa diagnoza**:
  1. `inactivity_manager.py:260-288`: Rule 0 (Profit Stall Gate) chroni tylko przed 4 płaskimi świecami konsolidacji na szczycie (`profit_stall_candles >= 4`). Gdy następuje gwałtowny spadek, `profit_atr < 0.6` resetuje licznik do 0.
  2. `daemon.py:1346` vs `analytics.py:318-337`: Pętla daemona przekazuje cenę `signal.price` (z zamkniętej świecy 1H). Inactivity Manager ma 1-godzinny lag i jest ślepy na zrzuty wewnątrz świecy.

## Recovery Instructions
Dla kolejnego agenta (Claude): Zapoznaj się z sekcją `Active Finding / Incident Report (2026-09-14)` powyżej oraz punktem 1 w `Pending Actions`. Oceń trafność diagnozy i zaproponuj bezpieczne rozwiązanie architektoniczne bez naruszania dyscypliny closed-candle w silniku sygnałów (`evaluate_signals`).

## ✅ Ocena Claude + Propozycja Naprawy (2026-09-14)

**Status: diagnoza z Incident Report zweryfikowana niezależnie, obie luki POTWIERDZONE. Poniżej gotowa specyfikacja implementacyjna dla Buildera — nic w kodzie jeszcze nie zmienione.**

### Weryfikacja Luki A (Rule 0 zeruje licznik)
Potwierdzone empirycznie na żywych świecach 1H z produkcji (nie tylko czytanie kodu):
BTC szczyt zysku = 3,27×ATR (77328,0), ETH szczyt = 2,87×ATR (2512,01) — oba dokładnie
zgodne z `max_observed_price` z `/api/v1/daemon/status`. W obu przypadkach minęła tylko
**1 świeca stagnacji** (nie wymagane 4), po czym ostatnia świeca przebiła całą strefę
≥0,6×ATR w dół jednym ruchem → `inactivity_manager.py:286-287`
(`else: tracker.profit_stall_candles = 0`) wyzerował licznik i Rule 0 nigdy nie odpalił.

### Weryfikacja Luki B (1h lag) — głębsza przyczyna źródłowa
Sprawdzone w `analytics.py`: **wszystkie** (24/24, `grep "price="`) konstrukcje
`SignalResult` w `evaluate_signals()` ustawiają `price=curr_c.close`, gdzie
`curr_c = closed_candles[-1]` (linia 318: `closed_candles = candles[:-1]`) — zawsze
ostatnia ZAMKNIĘTA świeca, nigdy świeca w trakcie formowania. `daemon.py:1346-1351`
karmi tym `InactivityManager.update_and_check(current_price=signal.price, ...)`.
Efekt: manager, którego zadaniem jest łapać ruch WEWNĄTRZ świecy, przez całą godzinę
widzi zamrożoną cenę i "budzi się" tylko na zamknięciu kolejnej świecy — czyli Luka A
miała aż godzinę, żeby się w pełni zmaterializować, zanim manager nawet zauważył ruch.

### Ocena proponowanych napraw — obie TAK, ale muszą wejść RAZEM

**(a) Realna cena do `InactivityManager` — bezpieczne, nie rusza `evaluate_signals`.**
`daemon.py:1340` już nazywa `candles[-1]` "the active (unclosed) candle" — odświeżana
co cykl (15s). Podmiana `current_price=signal.price` → `current_price=candles[-1].close`
(z fallbackiem na `signal.price` gdy `candles` puste) domyka lag z ~1h do ~15s. Zero
zmian w `analytics.py` — silnik sygnałów wciąż liczy WYŁĄCZNIE na zamkniętych świecach.

**(b) Profit Giveback Protection — konieczne NIEZALEŻNIE od (a).** Samo (a) nie
wystarczy: nawet z realną ceną, `else: profit_stall_candles = 0` w Rule 0 wciąż tylko
zeruje licznik przy szybkim spadku, zamiast bronić zysku. Potrzebna osobna reguła w
`inactivity_manager.py`, równoległa do Rule 0 (nie zamiast niej — Rule 0 dalej chroni
przed płaską stagnacją):
- Śledzić nowe pole `peak_profit_atr` (szczyt `profit_atr` w jednostkach ATR,
  niezależne od `last_peak_price`/`max_observed_price`).
- Gdy `peak_profit_atr` kiedykolwiek osiągnął próg zaangażowania (proponowane: 1,0×ATR
  — wyżej niż próg Rule 0 0,6×ATR, żeby nie łapać szumu na małych ruchach), a bieżący
  `profit_atr` cofnął się o >50% tego szczytu → **natychmiastowy `SPLIT_50`**,
  sprawdzane na KAŻDYM ticku (nie tylko `new_candle_closed`), bez czekania na 4 świece.

### Zakres zmian (dla Buildera)
- `src/mms4tk/daemon.py` — 1 linia (podmiana `current_price=` w wywołaniu
  `update_and_check`, ok. linia 1348).
- `src/mms4tk/inactivity_manager.py` — nowe pole `peak_profit_atr` w
  `PositionTracker`, nowa reguła "Giveback Protection" w `update_and_check` (obok
  istniejącego Rule 0, przed lub po nim — do decyzji Buildera, nie mogą się wykluczać).
- **Nie dotykać**: `src/mms4tk/analytics.py` (`evaluate_signals`, closed-candle
  discipline), `scripts/hermes/*` (zdeployowane ręcznie, poza zakresem).
- Wymagane: nowe testy jednostkowe dla obu ścieżek (fast reversal → SPLIT_50 przez
  Giveback; realna cena trafia do `update_and_check` zamiast `signal.price`), pełny
  `pytest -q -m "not slow"` bez regresji.

