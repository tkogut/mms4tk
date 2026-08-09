# PLAN 005: TASK 05 - Główna Pętla Asynchroniczna (Daemon) oraz Procedura Wdrożenia Produkcyjnego

## Status: COMPLETE
**Worktree:** `tmp/worktrees/task_05`  
**Branch:** `task_05`  

---

## 1. CEL
Implementacja produkcyjnej pętli asynchronicznej (daemon) dla `mms4tk` oraz przygotowanie podręcznika wdrożeniowego dla VPS Hostinger:
1. **Główna Pętla Asynchroniczna (`src/mms4tk/daemon.py` / `src/mms4tk/main.py`)**:
   - Pętla działająca w tle (async loop), cyklicznie pobierająca świece z OKX via `OKXExecutor`.
   - Przetwarzanie świec przez `MMS4SignalEngine`.
   - Odczyt stanu z `MMS4StateManager` i egzekucja decyzji handlowych (`BASE_SHORT`, `BASE_LONG`, `HIGH_RISK_ADD`).
   - Respektowanie trybu `SCOUT_MODE` (lewar x0.1) oraz twardej ochrony SL 2% / 1%.
   - Zapis zdarzeń do bazy `logger_db.py`.
2. **Integracja Lifespan z FastAPI (`src/mms4tk/api.py`)**:
   - Uruchamianie/zatrzymywanie daemona w pętli zdarzeń FastAPI (Lifespan Context Manager).
3. **Instrukcja Wdrożenia Produkcyjnego (`docs/DEPLOYMENT_GUIDE.md`)**:
   - Kompletny przewodnik krok-po-kroku dla VPS Hostinger (Docker, `.env`, `docker-compose up -d`, komendy inspekcji logów, procedura bezpieczeństwa R-SEC-01).
4. **Testy Jednostkowe & Integracyjne (`tests/test_daemon.py`)**:
   - Weryfikacja cyklu pracy pętli asynchronicznej oraz obsługi błędu braku kluczy API (mock mode).
5. **Handshake & Raport**:
   - Utworzenie `.agents/swarm/handshake.json` oraz `.agents/swarm/task_05_report.md`.

---

## 2. ETAPY WYKONANIA (BUILDER)
- [x] Stworzenie `src/mms4tk/daemon.py` (Async Market Loop Daemon)
- [x] Rozbudowa `src/mms4tk/main.py` jako punktu wejścia aplikacji i CLI
- [x] Zintegrowanie Lifespan w `src/mms4tk/api.py`
- [x] Utworzenie `docs/DEPLOYMENT_GUIDE.md`
- [x] Utworzenie `tests/test_daemon.py`
- [x] Wygenerowanie pliku `.agents/swarm/handshake.json` oraz `.agents/swarm/task_05_report.md`

---

## 3. AUDYT & HANDSHAKE
- [x] Wszystkie testy jednostkowe z wynikiem PASS (`pytest`: 13 passed).
- [x] Weryfikacja bezbłędnego uruchamiania i zatrzymywania pętli daemona.
- [x] Handshake: Plan-Alignment and Execution-Consistency checked.

