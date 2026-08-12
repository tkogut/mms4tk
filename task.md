# TASK 02: Budowa dedykowanego Web UI (Centrum Dowodzenia Mastermind) & Modułu Analitycznego KPI

## Cel
Zbudowanie i wdrożenie dedykowanego Web UI (Centrum Dowodzenia Mastermind) jako mikroserwisu Streamlit (`ui_dashboard`), w oparciu o plan `.agents/plans/009-mastermind-web-ui-analytics.md`.

## Wymagania Architektoniczne:
1. **Worker & Baza Danych (`src/mms4tk/trade_sync.py`)**:
   - Tabela `closed_trades` w SQLite `logs.db`.
   - Pobieranie historii zamkniętych zleceń z OKX API v5 co 15 minut i upsert do bazy.
2. **PWA & Mobile First**:
   - Manifest PWA (`ui_dashboard/static/manifest.json`).
   - Wstrzykiwanie tagów meta viewport i PWA manifest w `app.py`.
   - Dedykowany arkusz CSS (`style.css`) ukrywający menu/header/footer Streamlit i wymuszający responsywność oraz duże przyciski dotykowe.
3. **4 Zakładki Streamlit (`ui_dashboard/app.py`)**:
   - **Tab 1 (Monitoring)**: Pozycje OKX na żywo, tryby BASE/SCOUT, podgląd kart z Kanban (HITL).
   - **Tab 2 (Analytics)**: Kafelki KPI (Win Rate LONG/SHORT, Profit Factor, MDD %, R:R) + 3 interaktywne wykresy Plotly (Equity Curve, Benchmark vs BTC Buy&Hold, Heatmapa) zasilane z `logs.db`.
   - **Tab 3 (Settings)**: Bezpieczna edycja pliku `.env` (np. `MMS4_MAX_MARGIN_USD`).
   - **Tab 4 (Symbols)**: Menedżer dodawania/usuwania par handlowych (`ACTIVE_SYMBOLS`).
4. **Wdrożenie Docker**:
   - Usługa `ui_dashboard` zintegrowana w `docker-compose.yml` z sieciami `internal_swarm_net`, `traefik-proxy` oraz etykietami HTTPS/Cloudflare.

## Oczekiwany Rezultat (Handshake):
Uruchomiony kontener `ui_dashboard`, pozytywne wyniki testów unit, commit na `main`, aktualizacja VPS oraz weryfikacja Auditora.