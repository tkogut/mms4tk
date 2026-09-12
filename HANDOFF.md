# HANDOFF — Agent State Transfer Document

> Jeśli czytasz to jako inny agent (np. Antigravity/Coordinator) po przerwaniu
> sesji: to jest zawsze-aktualny wskaźnik "gdzie jesteśmy teraz". Pełny log
> decyzji i wyników: `task.md`, sekcja **"Faza 4 (2026-09-12) — analiza
> strategiczna dokumentu Masterminda"** (na samym końcu pliku).

## Session Info
- **Conversation ID**: `faza4-masterminda-candidates-backtest`
- **Role**: Coordinator (orchestrating background builder agents, nie edytuje src/ sam)
- **Timestamp**: 2026-09-12T15:45:00Z (aktualizowane przy każdym checkpointcie)
- **Status**: `in-progress`

## Current Task
Backtest dwóch kandydatów strategicznych znalezionych przez analizę dokumentu
Masterminda (`.agents/specs/knowledge/Architektura Systemowa mms4tk...md`),
zlecony przez usera 2026-09-12 po tym, jak Faza 3 (zmergowana, v0.45.0) nie
zmieniła faktycznej zyskowności strategii:

1. **Protected Stage-1** — Midline Partial TP + ochrona reszty pozycji (BE /
   inwalidacja na zamknięciu poza midline), zamiast dzisiejszego "50% partial,
   reszta na pełnym 2% ryzyku".
2. **D1 Range Target** — jawny cel zysku skalowany wielodniowym zasięgiem D1
   (`ATR_LOOKBACK=4`, ustalone a priori), poprzedzony bramką MFE krok-0.

Pełna specyfikacja metodologii: `task.md`, sekcja "Faza 4", akapit
"Rekomendacja".

- **Issue/branch**: `chore/faza4-masterminda-candidates` (sugerowana nazwa,
  agent mógł ją zmienić — sprawdź `git branch -a | grep faza4` jeśli inna)
- **Worktree**: przydzielony automatycznie subagentowi, ścieżkę zobaczysz przez
  `git worktree list` na hoście
- **Dispatch**: 2026-09-12, agent typu general-purpose, model Sonnet
  (świadomie nie Opus — po wcześniejszej uwadze usera o zużyciu limitów przez
  zbyt wiele agentów Opus w tej sesji), zadanie zawiera w swoim prompcie
  IDENTYCZNĄ instrukcję checkpointowania co ten plik (commit + wpis do
  `task.md` po KAŻDYM kroku: MFE gate → kandydat #2 → kandydat #1), więc nawet
  jeśli TA sesja (Coordinator) padnie, sam subagent (Builder) checkpointuje
  niezależnie na swoim branchu.

## Progress
- [x] Gap-analysis dokumentu Masterminda vs kod (Opus subagent, zakończone,
      wynik w `task.md`)
- [x] User zatwierdził test obu kandydatów
- [x] Checkpoint w `task.md` + ten plik utworzone PRZED dispatchem
- [ ] Subagent zbackrestestował krok 0 (bramka MFE)
- [ ] Subagent zbacktestował kandydata #2 (jeśli MFE przeszła)
- [ ] Subagent zbacktestował kandydata #1
- [ ] Wyniki wpisane do `task.md` (pełna tabela + werdykt)
- [ ] Decyzja: wdrożyć / odrzucić / dalsze badanie
- [ ] Status tego pliku → `completed`

## State Snapshot
### Files Modified
- `task.md`: dodana sekcja "Faza 4" (checkpoint, plan, status wykonania)
- `HANDOFF.md`: ten plik

### Pending Actions
1. Dispatch subagenta (Opus lub Sonnet w zależności od dostępnego limitu w
   danej chwili) z promptem realizującym plan z `task.md`/Faza 4: skrypt
   analityczny wzorowany na `scripts/htf_directional_gate_analysis.py`,
   bramka MFE jako krok 0, potem siatki obu kandydatów, na fixture'ach
   `tests/fixtures/ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv`.
2. Po dispatchu: uzupełnić tu nazwę brancha/worktree i agentId.
3. Po KAŻDYM ukończonym kroku (MFE gate / kandydat #2 / kandydat #1) —
   natychmiast dopisać wynik do `task.md` (nie czekać na koniec całości) i
   zaktualizować checkboxy tutaj.
4. Jeśli sesja zostanie przerwana (limit tygodniowy) zanim subagent skończy:
   sprawdzić czy subagent nadal działa w tle (harness go tak czy inaczej
   dokończy i zapisze wynik do swojego worktree/branch nawet bez nadzorującej
   sesji) — jego branch/commity to źródło prawdy, nie ta rozmowa.

### Blockers
- Brak na chwilę zapisu tego checkpointu (przed dispatchem).

## Recovery Instructions
Jeśli ta sesja została przerwana:
1. Przeczytaj `task.md`, sekcja "Faza 4" — tam jest pełny plan i dotychczasowe
   ustalenia.
2. Sprawdź `git branch -a` i `git worktree list` pod kątem brancha z nazwą
   zawierającą `faza4`/`masterminda`/`protected-stage1`/`d1-range-target` —
   jeśli subagent zdążył scommitować częściowe wyniki, będą tam.
3. Jeśli branch/worktree nie istnieje w ogóle — subagent nigdy się nie
   uruchomił lub padł natychmiast; zacznij od punktu "Pending Actions" 1
   powyżej, z promptem z `task.md`/Faza 4.
4. Nie mergować niczego bez przeglądu diffu i niezależnego ponownego
   uruchomienia testów — standard tej całej sesji roboczej.
