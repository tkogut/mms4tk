# HANDOFF — Agent State Transfer Document

> Jeśli czytasz to jako inny agent (np. Antigravity/Coordinator) po przerwaniu
> sesji: to jest zawsze-aktualny wskaźnik "gdzie jesteśmy teraz". Pełny log
> decyzji i wyników: `task.md`, sekcja **"Faza 4 (2026-09-12) — analiza
> strategiczna dokumentu Masterminda"** (na samym końcu pliku).

## Session Info
- **Conversation ID**: `faza4-masterminda-candidates`
- **Role**: Builder (implementacja, testy, atomowe commity — nie merguje/pushuje)
- **Timestamp**: 2026-09-12T00:00:00Z (aktualizowane przy każdym checkpointcie)
- **Status**: `completed`

## Current Task
Backtest dwóch kandydatów strategicznych znalezionych przez analizę dokumentu
Masterminda (`.agents/specs/knowledge/Architektura Systemowa mms4tk...md`):

1. **Protected Stage-1** — Midline Partial TP + ochrona reszty pozycji (BE /
   inwalidacja na zamknięciu poza midline), zamiast dzisiejszego "50% partial,
   reszta na pełnym 2% ryzyku".
2. **D1 Range Target** — jawny cel zysku skalowany wielodniowym zasięgiem D1
   (`ATR_LOOKBACK=4`, ustalone a priori), poprzedzony bramką MFE krok-0.

Pełna specyfikacja metodologii: `task.md`, sekcja "Faza 4", akapit
"Rekomendacja".

- **Branch**: `chore/faza4-masterminda-candidates`
- **Worktree**: `.claude/worktrees/agent-ab159cf9eb4b3dc4e` (izolowany worktree
  przydzielony tej sesji Buildera)

## Progress
- [x] Gap-analysis dokumentu Masterminda vs kod (wynik w `task.md`)
- [x] User zatwierdził test obu kandydatów
- [x] Checkpoint w `task.md` + ten plik zreplikowane do worktree Buildera
      (brakowały w worktree, bo w main checkout były niescommitowane — patrz
      task.md/Faza 4 dla treści źródłowej)
- [x] Krok 0: bramka MFE — **PASS** (pooled mediana +0,992% vs próg 0,10%,
      BTC/ETH osobno >=2x próg) — kandydat #2 idzie do siatki. Pełny wynik:
      `task.md`/Faza 4/"Krok 0".
- [x] Kandydat #2 (D1 Range Target, `ATR_LOOKBACK=4` fixed, `k∈{0.25,0.5}`) —
      **ODRZUCONY**: 0/4 kombinacji k×fixture-set przechodzi bramkę (główny
      grid: k=0.25 tylko ETH 4/5, BTC 2/5; k=0.5 oba 2/5; kontrola USDC: oba k
      prawie same ujemne delty). Pełny wynik: `task.md`/Faza 4/"Kandydat #2".
- [x] Kandydat #1 (Protected Stage-1, 5 wariantów × 10 okien) —
      **NIEJEDNOZNACZNY**: BREAKEVEN/INVALIDATION przechodzą formalną bramkę
      na głównym gridzie (10/10, 9/10) ale kontrola na realnych danych USDC
      NIE potwierdza (BTC płasko/ujemnie). FULL_EXIT jednoznacznie odrzucony.
      Pełny wynik: `task.md`/Faza 4/"Kandydat #1".
- [x] Wyniki wpisane do `task.md` (pełna tabela + werdykt)
- [x] Decyzja: **żaden kandydat nie wdrożony do `daemon.py`** — oba zostają w
      `backtest_engine.py` jako opcjonalne parametry, default OFF/False,
      wyłącznie do odtwarzalności pomiarów.
- [x] Status tego pliku → `completed`

## State Snapshot
### Files Modified
- `task.md`: sekcja "Faza 4" zreplikowana + checkpointy dopisywane po każdym kroku
- `HANDOFF.md`: ten plik

### Pending Actions
1. Krok 0: policzyć MFE dla wyjść Inactivity Manager, zapisać wynik i decyzję
   bramki niezależnie od wyniku.
2. Jeśli bramka przechodzi: siatka kandydata #2 (`k ∈ {0.25, 0.5}`).
3. Kandydat #1: 5 wariantów × 10 okien + kontrola USDC.
4. Commit + wpis do `task.md` po KAŻDYM z powyższych kroków.

### Blockers
- Brak.

## Recovery Instructions
Jeśli ta sesja została przerwana:
1. Przeczytaj `task.md`, sekcja "Faza 4" — tam jest pełny plan i dotychczasowe
   ustalenia (aktualizowane po każdym checkpointcie).
2. Sprawdź commity na branchu `chore/faza4-masterminda-candidates` w tym
   worktree — częściowe wyniki są tam, nie tylko w tym pliku.
3. Kontynuuj od pierwszego niekończonego kroku w liście "Progress" powyżej.
