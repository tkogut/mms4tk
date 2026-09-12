# HANDOFF — Agent State Transfer Document

> Jeśli czytasz to jako inny agent (np. Antigravity/Coordinator): to jest
> zawsze-aktualny wskaźnik "gdzie jesteśmy teraz". Pełny log decyzji i
> wyników: `task.md`, sekcja **"Faza 5 (2026-09-12) — maker-first (postOnly)
> na 24-mies. danych pod V2 + formalne domknięcie Profitability Workstream"**
> (na samym końcu pliku).

## Session Info
- **Conversation ID**: `faza5-maker-first-execution`
- **Role**: Builder
- **Timestamp**: 2026-09-12T20:00:00Z
- **Status**: `in_progress`

## Current Task
Ostatni test Profitability Workstream: przeliczyć egzekucję maker (`postOnly`)
zamiast taker na 24-mies. danych pod V2, potem formalnie zamknąć workstream
(koniec poszukiwania nowych filtrów sygnałowych). Pełna specyfikacja:
`task.md`/Faza 5.

- **Branch**: `chore/faza5-maker-first-execution` (izolowany worktree)
- **Nie zmergowane, nie pushnięte** — zgodnie z instrukcją usera (NIE push,
  NIE PR, NIE merge w tej sesji).

## Progress
- [x] Krok 1: weryfikacja realnej stawki maker/taker OKX — **DONE**. Brak
      schedule'u w repo (`okx-api-guide.md` × 2 lokalizacje, zero trafień na
      "fee/maker/taker"); użyto publicznego standardowego OKX VIP0 schedule
      (maker 0.02%/taker 0.05% per noga, zweryfikowane WebSearch+WebFetch,
      zgodne co do promila z istniejącą stałą `TAKER_FEE_PCT=0.0005`).
      `MAKER_ROUND_TRIP_FEE_PCT = 0.00040012`, przeliczone tą samą kalibracją
      proporcjonalną co A1 (`ROUND_TRIP_FEE_PCT/2×TAKER_FEE_PCT` ratio).
      Pełne wyprowadzenie: `task.md`/Faza 5/"Krok 1 — wynik".
- [ ] Krok 2: opcjonalny `round_trip_fee_pct_override` param w
      `backtest_engine.py::run_backtest()` (domyślnie `None`, zero zmiany
      zachowania) + testy jednostkowe.
- [ ] Krok 3: przeliczenie 24-mies. (5+5 proxy USDT + kontrola USDC), tabela
      per okno, baseline taker vs maker.
- [ ] Krok 4: formalne domknięcie Profitability Workstream w `task.md`
      (tabela 10 hipotez, 3 decyzje architektoniczne z poprawionym opisem
      SCOUT_MODE, jawne stwierdzenie zamknięcia).
- [ ] Pełny `pytest -q -m "not slow"` + handshake
      (`--conversation-id faza5-maker-first-execution`)

## State Snapshot
### Files Modified (na branchu `chore/faza5-maker-first-execution`)
- `task.md`: Krok 1 wynik dopisany (ta sesja)
- `HANDOFF.md`: ten plik (ta sesja)

### Pending Actions
1. Krok 2 (parametr + testy) → commit.
2. Krok 3 (przeliczenie 24-mies., nowy skrypt w `scripts/`) → commit z pełną
   tabelą w `task.md`.
3. Krok 4 (zamknięcie workstream) → commit.
4. Testy + handshake → commit końcowy.

### Blockers
- Brak.

## Recovery Instructions
Jeśli czytasz to po przerwaniu: sprawdź `git log --oneline` na tym branchu i
checklistę w `task.md`/Faza 5/"Status wykonania" — odhaczone pozycje są
zrobione i zacommitowane, kontynuuj od pierwszej nieodhaczonej.
