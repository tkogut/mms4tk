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

**Decyzja Kroku 0 podjęta (2026-09-13)**: user powiedział "zacznij prace w
realizacji planu" — interpretowane jako zakres (a), pełna implementacja.

## Progress
- [x] Audyt kanban — wyjaśniony, polityka niezmieniona (user potwierdził)
- [x] Audyt H4 Cron vs wewnętrzna bramka Fazy 3.2 — kompletny, 2 kluczowe
      ustalenia zweryfikowane niezależnie przez orchestrating session
      (istnienie i treść `mms4tk_h4_analysis.py`; brak `allow_mock_fallback=False`
      w `api.py:722`)
- [x] Audyt 12 narzędzi Hermesa — kompletny
- [x] Prompt operacyjny dla Hermesa — gotowy, PEŁNY TEKST persystowany w
      `task.md`/Faza 6 (nie tylko referencja)
- [x] Plan implementacji (C.1.1–C.3.3) spisany w `task.md`/Faza 6
- [x] **Krok 0**: user zdecydował — zakres (a), implementować
- [x] **Krok 3 ZBADANE**: `mms4tk_h4_analysis.py` istnieje jako DWIE
      rozjechane, nie-zsynchronizowane z repo kopie na hoście VPS
      (`/opt/data/scripts/` vs `/opt/data/home/.hermes/scripts/` wewnątrz
      kontenera `hermes-agent`) — pełne szczegóły w `task.md`/Faza 6/Krok 3.
      Deploy zmiany C.1.1 wymaga RĘCZNEGO skopiowania na VPS (`docker cp`)
      + empirycznej weryfikacji która kopia faktycznie się wykonuje —
      **świadomie zarezerwowane dla orchestrating session, nie dla
      subagenta buildera** (zbyt ryzykowne dla już żywej automatyzacji).
- [ ] **Krok 2 W TOKU**: dispatch subagenta (Sonnet, worktree, branch
      sugerowany `chore/faza6-hermes-hardening`, agentId wewnętrzny
      `a9a98c864d8195ed4`) implementującego C.1.2→C.1.1→C.1.3→C.2→C.3.1→
      C.3.2→C.3.3 w tej kolejności (C.1.2 musi być pierwsze — zależność).
      Instrukcja jawnie zakazuje subagentowi dotykać deployu na VPS —
      to repo-side kod + testy tylko.
- [ ] Krok 1: wdrożenie promptu Hermesa (poza repo — user/agent musi wkleić
      tekst z `task.md`/Faza 6 do konfiguracji Hermes Agenta) — wciąż
      nie zrobione, niezależne od Kroku 2, można w każdej chwili
- [ ] Po ukończeniu Kroku 2 przez subagenta: weryfikacja niezależna
      (diff + pytest) przez orchestrating session, push+PR+merge, deploy
      `antigravity_core` (maintenance lock) + ręczny deploy skryptu crona
      na `hermes-agent` (Krok 3, empiryczna weryfikacja)

## State Snapshot
### Files Modified
- `task.md`: sekcja "Faza 6" (pełny audyt + plan + pełny tekst promptu +
  ustalenia deploy path)
- `HANDOFF.md`: ten plik
- **Zero zmian w `src/` na `main`** — implementacja w toku na osobnym,
  niezmergowanym branchu subagenta

### Pending Actions
1. Poczekać na ukończenie subagenta `a9a98c864d8195ed4` (checkpointuje
   commit po każdym C.x.x do swojego brancha + wpisy do `task.md` w swoim
   worktree — scal ustalenia przy weryfikacji, jak w Fazie 4/5).
2. Zweryfikować niezależnie (diff + pytest + spot-check) przed push/PR/merge.
3. Po merge: (a) deploy `antigravity_core` z maintenance lockiem, (b) RĘCZNY
   deploy zaktualizowanego `mms4tk_h4_analysis.py` na `hermes-agent` — patrz
   `task.md`/Faza 6/Krok 3 dla dokładnej ścieżki i planu empirycznej
   weryfikacji (dodać unikalny znacznik, sprawdzić który plik faktycznie
   trafia do `strategy_profile.reason` po najbliższym cyklu crona).
4. Krok 1 (prompt Hermesa) — zrobić niezależnie, w każdej chwili, nie czeka
   na nic z powyższych.

### Blockers
- Brak (decyzja Kroku 0 podjęta, Krok 3 zbadany, implementacja w toku).

## Recovery Instructions
1. Przeczytaj `task.md`, sekcja "Faza 6" w całości — tam jest kompletny audyt
   i plan, nie tylko skrót.
2. Sprawdź czy user w międzyczasie odpowiedział na pytanie o zakres (a)/(b) —
   jeśli to widoczne w historii rozmowy, zacznij od tego. Jeśli nie — zapytaj.
3. Zero kodu jeszcze nie zmieniono — nie ma czego "kontynuować" w git, to
   czysty start od specyfikacji w `task.md`.
