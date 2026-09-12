# 📋 PLAN 009: Dedykowane Web UI (Centrum Dowodzenia Mastermind) & Moduł Analityczny KPI

**Wersja:** 1.0.0 (AGENTS-OS v6.5 Swarm Edition)  
**Status:** W trakcie realizacji  
**Data:** 2026-08-12  

---

## 🎯 Cele Planu
Zbudowanie i wdrożenie dedykowanego Web UI (Centrum Dowodzenia Mastermind) jako osobnego mikroserwisu Streamlit (`ui_dashboard`), połączonego z nowym workerem synchronizacji transakcji (`src/mms4tk/trade_sync.py`), zaawansowanym modułem analitycznym Plotly & KPI oraz pełną responsywnością mobilną PWA (manifest, custom CSS, mobile meta viewport).

---

## 🧱 KROKI WYKONAWCZE (Execution Breakdown)

### Phase 1: Baza Danych & Worker Synchronizacji Transakcji (`trade_sync.py`)
- [ ] **Step 1.1**: Dodanie tabeli `closed_trades` w `src/mms4tk/logger_db.py` dla persystowania historii transakcji OKX.
- [ ] **Step 1.2**: Implementacja modułu synchronizacyjnego `src/mms4tk/trade_sync.py` (pobieranie `orders-history` z OKX API v5 co 15 minut, wyliczanie zysków/strat USD i procentowych, upsert do SQLite).
- [ ] **Step 1.3**: Testy jednostkowe dla workera w `tests/test_trade_sync.py`.

### Phase 2: Interfejs Web UI w Streamlit & Wsparcie PWA Mobilne (`ui_dashboard/`)
- [ ] **Step 2.1**: Utworzenie struktury katalogu `ui_dashboard/` z plikami `Dockerfile`, `requirements.txt` oraz `static/manifest.json`.
- [ ] **Step 2.2**: Implementacja wstrzykiwania nagłówków HTML (PWA Meta Injections: viewport, theme-color, manifest.json) w `ui_dashboard/app.py`.
- [ ] **Step 2.3**: Stworzenie dedykowanego arkusza CSS (`ui_dashboard/style.css`) ukrywającego domyślne nagłówki Streamlit (`#MainMenu`, `header`, `footer`), wymuszającego layout Mobile-First oraz duże obszary dotykowe (touch-friendly buttons/cards).

### Phase 3: Implementacja 4 Zakładek Centrum Dowodzenia
- [ ] **Step 3.1: Tab 1 (Monitoring)**
  - Pozycje OKX Europe na żywo.
  - Metryki maszyny stanów (`BASE_MODE` / `SCOUT_MODE`, lewar 1x - 10x).
  - Integracja z Kanbanem Hermesa (`kanban.db` / `logs.db`) i obsługa zgód HITL.
- [ ] **Step 3.2: Tab 2 (Analytics & Plotly)**
  - Kafelki KPI: Win Rate (dla LONG, SHORT i łącznie), Profit Factor ($>1.0$), Max Drawdown (MDD %), Average Risk/Reward (R:R).
  - Wykres 1: Interaktywna Krzywa Kapitału (Equity Curve) z Plotly.
  - Wykres 2: Benchmark vs Buy & Hold (Skumulowane PnL bota vs kurs Bitcoina).
  - Wykres 3: Kalendarzowa Heatmapa zysków/strat (dni/miesiące).
- [ ] **Step 3.3: Tab 3 (Settings)**
  - Bezpieczny edytor parametrów środowiskowych `.env` (np. zmiana `MMS4_MAX_MARGIN_USD` z $10 na $100, `MMS4_LOG_LEVEL`) z atomowym zapisem i automatyczną kopią `.env.bak`.
- [ ] **Step 3.4: Tab 4 (Symbols Manager)**
  - Menedżer dodawania/usuwania aktywnych par handlowych (`ACTIVE_SYMBOLS`) z automatyczną walidacją instrumentów X-Perp na OKX Europe (`discover_eea_xperp_symbol()`).

### Phase 4: Wdrożenie Docker & Audit Handshake
- [ ] **Step 4.1**: Dodanie serwisu `ui_dashboard` do `docker-compose.yml` z konfiguracją sieci `internal_swarm_net`, `traefik-proxy` i etykietami Traefik HTTPS / Cloudflare Zero Trust.
- [ ] **Step 4.2**: Przeprowadzenie pełnego audytu (Auditor Handshake: testy jednostkowe, linting, math-consistency, CSS/Z-index).
- [ ] **Step 4.3**: Commit na GitHub, pull i rebuild na VPS, weryfikacja działania na żywo.
