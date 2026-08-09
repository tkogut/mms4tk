# PLAN 006: TASK 06 - Integracja Sieciowa i Narzędziowa mms4tk z Agentem Hermes (Traefik Proxy)

## Status: COMPLETE
**Worktree:** `tmp/worktrees/task_06`  
**Branch:** `task_06`  

---

## 1. CEL
Wdrożenie pełnej integracji sieciowej oraz narzędziowej pomiędzy kontenerami `hermes-agent` oraz `antigravity_core` w sieci `traefik-proxy` na serwerze VPS Hostinger:
1. **Zewnętrzna sieć Docker (`traefik-proxy`)**:
   - Podpięcie `antigravity_core` pod wspólną sieć `traefik-proxy` w `docker-compose.yml`.
   - Weryfikacja komunikacji kontener-kontener pod adresem `http://antigravity_core:8000/api/v1/health`.
2. **Udostępnienie narzędzi Function Calling dla Hermesa**:
   - Generowanie pliku specyfikacji OpenAPI `.agents/swarm/openapi.json` oraz instalacja dedykowanych reguł/narzędzi dla Hermesa w `/docker/hermes-agent/data/tools/mms4tk_tools.json`.
3. **Testy Integracyjne Kontenerów**:
   - Wykonanie testów HTTP bezpośrednio z wnętrza `hermes-agent` do `antigravity_core`.
4. **Handshake & Raport**:
   - Aktualizacja `.agents/swarm/handshake.json` oraz `.agents/swarm/task_06_report.md`.

---

## 2. ETAPY WYKONANIA (BUILDER)
- [x] Aktualizacja `docker-compose.yml` (dodanie sieci `traefik-proxy`)
- [x] Stworzenie specyfikacji narzędzi Hermesa `src/mms4tk/hermes_tools.py`
- [x] Utworzenie testu integracyjnego `tests/test_hermes_network.py`
- [x] Wdrożenie w środowisku VPS Hostinger (`docker compose up -d`)
- [x] Weryfikacja połączenia z kontenera `hermes-agent`
- [x] Wygenerowanie pliku `.agents/swarm/handshake.json` oraz `.agents/swarm/task_06_report.md`

---

## 3. AUDYT & HANDSHAKE
- [x] Wszystkie testy jednostkowe z wynikiem PASS (`pytest`: 21 passed).
- [x] Pomyślna odpowiedź HTTP z `hermes-agent` do `http://antigravity_core:8000/api/v1/health` (status 200 OK).
- [x] Handshake: Plan-Alignment and Inter-Container Connectivity checked.

