# PLAN 004: TASK 04 - Wdrożenie Infrastrukturalne (Docker, Compose, R-SEC-01 & Integracja z Hermesem)

## Status: COMPLETE
**Worktree:** `tmp/worktrees/task_04`  
**Branch:** `task_04`  

---

## 1. CEL
Przygotowanie i wdrożenie kompletnej infrastruktury dla kontenera `mms4tk` na serwerze VPS (Hostinger) z integracją dla agenta Hermes (Nous Research):
1. **Dockerfile (Zoptymalizowany)**:
   - Wieloetapowy (multi-stage) lub lekki obraz `python:3.11-slim`.
   - Nieuprawniony użytkownik systemowy (`appuser`, non-root security compliance).
   - Healthcheck wywołujący `GET /api/v1/health`.
2. **docker-compose.yml (Izolacja i Persystencja)**:
   - Dedykowana sieć wewnętrzna `internal_swarm_net` (brak eksponowania portów FastAPI na zewnątrz, R-SEC-01).
   - Montowanie wolumenu dla persystencji bazy danych stanu i logów: `./.agents/swarm:/app/.agents/swarm`.
3. **Bezpieczne Zarządzanie Sekretami (R-SEC-01)**:
   - Odczyt `.env` z sekretami OKX (`OKX_API_KEY`, `OKX_SECRET`, `OKX_PASSPHRASE`).
   - Szablon `.env.example` w repozytorium bez prawdziwych kluczy.
4. **Integracja z Agentem Hermes (OpenAPI & Tooling)**:
   - Generowanie specyfikacji `openapi.json` dla klienta/agenta Hermes.
   - Testowy skrypt integracyjny `tests/test_hermes_integration.py` symulujący wywołania REST/Function Calling Hermesa.
5. **Handshake & Raport**:
   - Aktualizacja `.agents/swarm/handshake.json` i wygenerowanie raportu w `.agents/swarm/task_04_report.md`.

---

## 2. ETAPY WYKONANIA (BUILDER)
- [x] Utworzenie zoptymalizowanego `Dockerfile`
- [x] Utworzenie `docker-compose.yml` z siecią `internal_swarm_net` i wolumenami
- [x] Stworzenie `.env.example` z dokumentacją zmiennych
- [x] Dodanie generatora specyfikacji OpenAPI oraz skryptu integracyjnego `tests/test_hermes_integration.py`
- [x] Utworzenie skryptu generowania specyfikacji `scripts/generate_openapi.py`
- [x] Wygenerowanie pliku `.agents/swarm/handshake.json` oraz `.agents/swarm/task_04_report.md`

---

## 3. AUDYT & HANDSHAKE
- [x] Testowa walidacja budowania kontenera i poprawności syntaxu Docker/Compose.
- [x] Weryfikacja R-SEC-01 (brak wycieku sekretów, non-root user, private network).
- [x] Integracja z Hermesem (OpenAPI endpoints & 9 passed unit/integration tests).
- [x] Handshake: Plan-Alignment and Security-Consistency checked.

