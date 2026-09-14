# HANDOFF — Agent State Transfer Document

> Jeśli czytasz to jako inny agent: Faza 6 (Hermes integration hardening) jest
> **zaimplementowana, zmergowana i zdeployowana**. Zostało wyłącznie jedno,
> pasywne zadanie: empiryczna weryfikacja po najbliższym 4h cyklu crona.
> Pełny log: `task.md`, sekcja "🔹 Faza 6 (2026-09-13)".

## Session Info
- **Conversation ID**: `inactivity-giveback-protection`
- **Role**: Coordinator
- **Timestamp**: 2026-09-14T11:45:00Z
- **Status**: `completed` (implementacja, audyt om-auto-review-pr, merge om-approve-merge-pr, v0.45.1 na main)

## Current Task
Wszystkie prace nad incydentem utraty zysku z 2026-09-13 zostały zakończone:
1. Podmiana `current_price=candles[-1].close if candles else signal.price` w `daemon.py` (eliminacja 1h opóźnienia cenowego).
2. Reguła **Profit Giveback Protection** w `inactivity_manager.py` (`peak_profit_atr >= 1.0` i spadek >50% ze szczytu -> natychmiastowy `SPLIT_50`).
3. Nowe testy jednostkowe (427 passed, 4 deselected).
4. PR #60 przeszedł formalny audyt (`om-auto-review-pr 60` -> APPROVED) i został scaliły do `main` (`om-approve-merge-pr 60`).
5. Aplikacja podniesiona do wersji `v0.45.1`.

## Progress
- [x] Diagnoza incydentu z 2026-09-13 (utrata zysku >3×ATR na BTC i ETH)
- [x] Weryfikacja obu luk (Luka A: reset w Rule 0, Luka B: 1h lag w daemonie)
- [x] Implementacja w izolowanym worktree przez Buildera
- [x] Nowe testy jednostkowe (`test_profit_giveback_protection`, `test_daemon_passes_active_candle_close_to_inactivity_manager`)
- [x] Test suite 427/427 PASS
- [x] Otwarcie PR #60
- [x] Niezależny przegląd Open-Mercato (`/om-auto-review-pr 60` -> APPROVED)
- [x] Scalenie squash-merge do `main` (`/om-approve-merge-pr 60`)
- [x] Bump wersji do `v0.45.1` (4 pliki SSOT + CHANGELOG.md)
- [ ] Opcjonalny deploy na VPS (wg uznania usera z maintenance lockiem)
- [ ] Pasywna weryfikacja po najbliższym cyklu crona H4 (sprawdzenie braku nadpisywania directional_bias)
- [ ] Krok 1: wdrożenie promptu Hermesa (poza repo)

## State Snapshot
### Files Modified
- `main` (`d6a00f6`, v0.45.1):
  - `src/mms4tk/daemon.py` (live price feed do Inactivity Managera)
  - `src/mms4tk/inactivity_manager.py` (Profit Giveback Protection + peak_profit_atr)
  - `tests/test_inactivity_manager.py` (nowe testy)
  - `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `ui_dashboard/package.json`, `CHANGELOG.md` (v0.45.1)

### Pending Actions
1. [x] **Inactivity Giveback Protection & Live Price Feed**: Zaimplementowane, zaudytowane i zmergowane w **PR #60** (commit `5365cac`), wersja wydana: `v0.45.1`.
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

