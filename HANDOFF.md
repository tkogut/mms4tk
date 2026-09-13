# HANDOFF — Agent State Transfer Document

> Jeśli czytasz to jako inny agent (np. Antigravity/Coordinator): Profitability
> Workstream jest zamknięty. **Aktywny wątek to Faza 6** (Hermes integration
> hardening) — pełny plan w `task.md`, sekcja "🔹 Faza 6 (2026-09-13) — Hermes
> integration hardening: PLAN, nie zaimplementowane" (na końcu pliku).

## Session Info
- **Conversation ID**: `hermes-integration-hardening`
- **Role**: Coordinator
- **Timestamp**: 2026-09-13T11:00:00Z
- **Status**: `in-progress` — audyt kompletny, **plan spisany, implementacja NIE rozpoczęta**

## Current Task
Audyt integracji Hermes Agent ↔ mms4tk (zlecony przez usera po zamknięciu
Profitability Workstream) znalazł realny, miesiąc działający mechanizm
("H4 Cron", `scripts/hermes/mms4tk_h4_analysis.py` — NASZ własny skrypt, nie
obcy system) który duplikuje i częściowo tłumi wewnętrzną bramkę H4 z Fazy
3.2. Pełne ustalenia + propozycje (C.1.1–C.3.3) + gotowy prompt operacyjny
dla Hermesa: `task.md`/Faza 6.

**Blokujące pytanie, jeszcze nierozstrzygnięte**: user ma zdecydować zakres —
(a) pełna implementacja poprawek kodu, czy (b) tylko wdrożenie promptu
Hermesa teraz, kod później. **Nie zaczynaj Kroku 2 (implementacja) bez tej
decyzji.**

## Progress
- [x] Audyt kanban — wyjaśniony, polityka niezmieniona (user potwierdził)
- [x] Audyt H4 Cron vs wewnętrzna bramka Fazy 3.2 — kompletny, 2 kluczowe
      ustalenia zweryfikowane niezależnie przez orchestrating session
      (istnienie i treść `mms4tk_h4_analysis.py`; brak `allow_mock_fallback=False`
      w `api.py:722`)
- [x] Audyt 12 narzędzi Hermesa — kompletny
- [x] Prompt operacyjny dla Hermesa — gotowy, zero zależności kodowych
- [x] Plan implementacji (C.1.1–C.3.3) spisany w `task.md`/Faza 6
- [ ] **Krok 0**: decyzja usera o zakresie (a)/(b) — NIE PODJĘTA
- [ ] Krok 1: wdrożenie promptu Hermesa (poza repo, można zrobić niezależnie od Kroku 0)
- [ ] Krok 2: implementacja C.1.1–C.3.3 (jeśli wybrano zakres (a))
- [ ] Krok 3: ustalenie jak `mms4tk_h4_analysis.py` trafia na kontener `hermes-agent` (deploy path nieznany, do zbadania PRZED wdrożeniem C.1.1)

## State Snapshot
### Files Modified
- `task.md`: nowa sekcja "Faza 6" (pełny audyt + plan)
- `HANDOFF.md`: ten plik
- **Zero zmian w `src/`** — to wyłącznie planowanie, żadna implementacja się jeszcze nie zaczęła

### Pending Actions
1. Zapytać usera (jeśli sesja wznawia się bez niego pod ręką, zostaw to jako
   pierwsze pytanie) o zakres (a)/(b) z sekcji "Otwarta decyzja" w `task.md`/Faza 6.
2. Jeśli (a): dispatch subagenta z pełną specyfikacją C.1.1–C.3.3 z `task.md`,
   w kolejności: C.1.2 PRZED C.1.1 (zależność), checkpointować po każdym
   podpunkcie (commit + wpis do `task.md`), tak jak w Fazie 4/5.
3. Przed jakąkolwiek zmianą w `scripts/hermes/mms4tk_h4_analysis.py` — ustalić
   Krok 3 (jak ten skrypt faktycznie trafia na `hermes-agent`), inaczej zmiana
   w repo nie dotrze do produkcji.
4. Krok 1 (prompt) można zrobić w każdej chwili, niezależnie — nie czeka na nic.

### Blockers
- Decyzja usera o zakresie (Krok 0) — bez niej nie zaczynać Kroku 2.

## Recovery Instructions
1. Przeczytaj `task.md`, sekcja "Faza 6" w całości — tam jest kompletny audyt
   i plan, nie tylko skrót.
2. Sprawdź czy user w międzyczasie odpowiedział na pytanie o zakres (a)/(b) —
   jeśli to widoczne w historii rozmowy, zacznij od tego. Jeśli nie — zapytaj.
3. Zero kodu jeszcze nie zmieniono — nie ma czego "kontynuować" w git, to
   czysty start od specyfikacji w `task.md`.
