# 🧠 MMS4TK — PROJECT MEMORY & HANDOFF CONTEXT (v0.48.9, verified 2026-09-30)

## 📌 1. ENVIRONMENT & INFRASTRUCTURE
- **Repository**: `/home/tkogut/projects/mms4tk`
- **Stack**: Python 3.12 (FastAPI, CCXT, Pandas), SQLite3, React/Vite (UI Dashboard).
- **Exchange**: OKX Futures (EEA Multi-Collateral X-Perp: `BTC-USD_UM_XPERP-310404`, `ETH-USD_UM_XPERP-310404`, `XAU-USD_UM_XPERP-310502`, `XAG-USD_UM_XPERP-310509`).
- **VPS Server**: Hostinger Cloud (`srv1490214.hstgr.cloud`, containers `antigravity_core` + `ui_dashboard`).
  - Production DB path: `/app/.agents/swarm/logs.db`.
  - Repo on server: `/docker/mms4tk` (git-tracked, deploy = `git pull` + `docker compose up -d --build`).
  - SSH access: the key at `~/.ssh/tkogut_ssh_key` is passphrase-protected —
    direct `-i` auth without an agent **fails** (`Permission denied
    (publickey)`, re-verified 2026-09-30). A prior version of this note
    claimed direct-key auth worked; it doesn't — don't repeat that claim.
    Use the `vps-ops` skill protocol (`.agents/skills/vps-ops/SKILL.md`):
    ask the user to run `ssh-add ~/.ssh/tkogut_ssh_key` in their own terminal
    (they enter the passphrase there, never in this session) and paste back
    the resulting `SSH_AUTH_SOCK=...` value, then use it for every SSH call
    this session:
    `SSH_AUTH_SOCK=<value> ssh -o StrictHostKeyChecking=no root@srv1490214.hstgr.cloud "docker exec antigravity_core ..."`
    The socket is only valid for that terminal's lifetime — if it stops
    working, ask the user to repeat the `ssh-add` step and give you a fresh one.
- **Test suite**: `MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest -q -m "not slow"` (393+ passed as of Faza 3; grows over time — treat any exact count here as stale, run it fresh).
- **Never run pytest against the production container** (`docker exec antigravity_core pytest ...`) — even with `tests/conftest.py`'s DB/maintenance-lock isolation fix (2026-09-10, see §2), prefer a local checkout or CI. Full context: `git log` for the P3 conftest-hygiene commit.

---

## 🤖 2. SWARM GOVERNANCE & SAFETY RULES
- **Triad Roles**: Coordinator (plans & git push), Builder (atomic edits in isolated worktree `tmp/worktrees/`), Auditor (read-only verification).
- **Mandatory Versioning (SSOT 3-file sync)** — every commit that ships a release MUST increment version identically in:
  1. `VERSION`
  2. `src/mms4tk/__init__.py` (`__version__`)
  3. `CHANGELOG.md`

  (2026-10-13: `ui_dashboard/app.py` (`APP_VERSION`) was dropped from this rule — the legacy Streamlit dashboard was removed; production serves the React/Vite build via nginx.)
- **Zero-Loss Maintenance Directive**: lock before tests/deploy (`python3 scripts/maintenance.py --enable`), release after verification (`--disable`).
- **Handshakes**: Builder handshake in `.agents/swarm/<id>_builder_handshake.json` after each unit of work.
- **Test isolation**: `tests/conftest.py` patches `OKXExecutor` at the CLASS level (autouse) so no test ever makes a real OKX network call — escape hatch `@pytest.mark.allow_real_okx_network` for a deliberate integration test against a faked `.exchange`, never real network.

---

## ⚠️ 3. ŻELAZNE REGUŁY — nieoczywiste, sprawdzone w kodzie (nie zakładać inaczej)

- **SCOUT_MODE nie obniża dźwigni do 0.1x.** To nieaktualny model mentalny z
  wcześniejszej dokumentacji — kod (`state_manager.py`) świadomie porzucił ten
  mechanizm (OKX akceptuje tylko całkowitą dźwignię ≥1, więc 0.1x było po cichu
  clampowane do 1x, permanentnie więżąc symbol w SCOUT_MODE). Dzisiejszy
  mechanizm: `SCOUT_LEVERAGE=1.0` (realna, poprawna dźwignia) + redukcja przez
  **rozmiar pozycji** (`SCOUT_SIZE_MULT=0.3`) — efektywnie ~10x mniejszy notional.
  Powrót SCOUT_MODE→BASE_MODE następuje przy pierwszym zamknięciu sklasyfikowanym
  jako `is_take_profit=True` (mechanizm wyjścia, NIE weryfikacja zysku netto).
- **P3 V2 (SL SSOT, 2026-09-10)**: SL bazy (`base_entry_price`, -2.0%, nigdy nie
  aktualizowany uśrednieniem) jest JEDYNYM stop-lossem całej pozycji (baza+
  dokładka). Dokładka (`HIGH_RISK_ADD`) nie ma własnego SL — `closeFraction="1"`
  na istniejącym algo SL bazy obejmuje już powiększoną pozycję. Backtest
  (`backtest_engine.py`, wariant `addon_sl_variant="V2"`) replikuje to wiernie
  i jest domyślny (poprzedni default `"V0"`, przed-P3, był cichym, wieloletnim
  źródłem rozjazdu backtest↔produkcja — zamknięte, nie odkopywać bez powodu).
- **Dyscyplina closed-candle jest zachowana** w całym pipeline sygnałowym
  (`candles[:-1]` wzorzec w `analytics.py`/`daemon.py`/`backtest_engine.py`) —
  zaudytowane 2026-09-11 (Faza 3.1), jeden realny bug znaleziony i naprawiony
  (Midline TP liczył próg z wciąż formującej się świecy — latentne, bo
  `midline_partial_tp_enabled` domyślnie `False`).
- **`OKXExecutor.fetch_candles` nigdy nie rzuca wyjątku domyślnie** — przy
  błędzie sieciowym cicho zwraca deterministyczne świece mock (z niewielkim,
  systematycznym dryfem w górę), zamiast propagować błąd. Kod polegający na
  faktycznej awarii (np. fail-safe bramek opartych o świeże dane) MUSI wołać
  `fetch_candles(..., allow_mock_fallback=False)` — inaczej fail-safe jest
  martwym kodem (znalezione i naprawione w Fazie 3.2-followup).
- **Pojednanie pozycji używa rzeczywistego fillu z OKX, nie tickera (v0.47.2)**:
  `_reconcile_positions()` w `daemon.py` przy wykryciu zniknięcia pozycji odpytuje
  `OKXExecutor.fetch_last_closing_order()` (`private_get_trade_orders_history`),
  pobierając dokładną cenę wyjścia (`avgPx`), `pnl_usd` i `fee_usd`. Likwiduje to
  błąd fałszywej klasyfikacji Stop-Lossa jako `Manual/TP` (spowodowany odbiciem ceny
  tickera po zamknięciu zlecenia) oraz usuwa rozjazd PnL i dopisek `(szac.)` na Telegramie.
- **Strategia to mean-reversion, wrażliwa strukturalnie na silny trend** — 24-
  miesięczny backtest (Faza 2, 2026-09-11) pokazał medianę okna ≈ −23% netto,
  najgorsze okno to udokumentowany, realny +64% rajd BTC (nie artefakt danych).
  To ograniczenie architektoniczne strategii, nie bug.

Pełna historia dostarczonych planów, zmierzonych i odrzuconych hipotez: `task.md`
(żywy log decyzji, sekcje ✅/ZAMKNIĘTE/ODRZUCONE) i `CHANGELOG.md` (wpis per
wersja). Zarchiwizowane plany operacyjne: `.agents/plans/archive/`.

---

## 📈 4. CURRENT PRODUCTION STATE (v0.48.9, verified 2026-09-30 — see `DECISIONS.md` for the full, living source of truth)
- **Live Trading**: Active na OKX X-Perp (`BTC/USDC`, `ETH/USDC`, `XAU/USDC`, `XAG/USDC`).
- **Capital Allocation**: $100 USD per symbol (BTC, ETH, XAU, XAG).
- **Authoritative Fill & PnL Reconciliation (v0.47.2)**:
  - `OKXExecutor.fetch_last_closing_order(symbol, side)` odpytuje historię zleceń OKX.
  - Precyzyjny fill eliminuje rozjazdy cenowe na Telegramie i fałszywe klasyfikacje SL.
- **Gold (XAU)**: OKX EEA X-Perp `XAU-USD_UM_XPERP-310502` / `XAU/USD:USD-310502`, 0.001 XAU contract size, $100 allocation (70/30 base/addon), 1h timeframe.
- **Silver (XAG)**: OKX EEA X-Perp `XAG-USD_UM_XPERP-310509` / `XAG/USD:USD-310509`, 0.01 XAG contract size, $100 allocation (70/30 base/addon), 1h timeframe.
- **Trend Filter State**: `tma_trend_filter_enabled` — kod: `False` domyślnie. ⚠️ **Status sporny, nierozstrzygnięty** — `task.md` (archiwum) mówi wprost że nigdy nie był testowany na pełnej 24-miesięcznej historii i decyzja pozostaje otwarta; ten wpis wcześniej twierdził "re-zwalidowane w Fazie 3, potwierdzone" bez znalezionego dowodu (żaden skrypt w `scripts/` tego nie testuje). Nie powtarzaj żadnego z tych dwóch twierdzeń jako pewnika — patrz `DECISIONS.md` sekcja 4.
- **H4 Directional-Bias Gate** (`htf_directional_gate_enabled`, Faza 3.2) — **zweryfikowane bezpośrednio na produkcyjnym `system_settings` 2026-09-30: `true` dla WSZYSTKICH 4 aktywnych symboli** (BTC/ETH/XAU/XAG), włączone ręcznie 2026-09-13/14. Ten wpis wcześniej błędnie twierdził "domyślnie OFF dla wszystkich symboli" — nieaktualne od momentu włączenia. Najsilniejszy zmierzony sygnał w historii projektu (10/10 niezależnych okien poprawa, sign test p=0.0020) — redukuje straty w trendzie ~25-30%, nie czyni strategii zyskowną. Alternatywny klasyfikator (kaskada EMA) przetestowany i odrzucony w PR #84 — patrz `DECISIONS.md`.
- **Inactivity Engine (v0.45.1 / PR #60)**:
  - **Live Price Feed**: `daemon.py` przekazuje `candles[-1].close` do `inactivity_manager.update_and_check()`, likwidując 1h lag.
  - **Profit Giveback Protection**: nowa reguła (`peak_profit_atr >= 1.0` i retracement >50% ze szczytu -> natychmiastowy `SPLIT_50` intra-candle).
  - `midline_partial_tp_enabled` — **DISABLED** (odrzucone 6/6 w backteście, P9.1).
- **Fee Accounting**: Round-trip OKX fee via FIFO, `ROUND_TRIP_FEE_PCT=0.0010003` skalibrowane na realnych `closed_trades` (Sesja A1). Net PnL od epoki (`data_collection_epoch_start`) dodatnie, ale statystycznie za mała próbka na twardy wniosek o edge'u.

---

## 📝 5. Decisions & Key Milestones

### 2026-09-17 — /grill-me: Hermes cron "Pamiętnik Masterminda" (@MastermindZX X/Twitter ingest)
- **Cel**: godzinowy cron dla Hermesa, czytający posty @MastermindZX na X, filtrujące do
  `#dzienniktradera`/`#algotrading`, archiwizujące jako czysto informacyjny "pamiętnik".
- **Decyzje z wywiadu (adaptacja protokołu grill-me pod ten task)**:
  1. **Dostęp do X**: brak — ani płatnego X API, ani istniejącego web-browsing tool po
     stronie Hermesa. To jest **blokujący prerequisite**, nie coś do zignorowania w prompt-cie.
  2. **Cel/zakres**: wyłącznie informacyjny log do czytania. **Zero wpływu na żywy trading**
     — nigdy nie podłączać do `mms4_set_strategy_profile`/`directional_bias`/HITL.
  3. **Miejsce przechowywania**: po stronie Hermesa (kanban/własna baza) — **zero zmian w
     repo mms4tk**, zgodnie z istniejącą zasadą "Krok 1 (prompt Hermesa) dzieje się poza
     repo".
  4. **Backfill**: tyle, ile technicznie dostępne w ramach limitu wybranego kanału dostępu
     na pierwszym uruchomieniu, bez sztywnej liczby dni/postów.
- **Status**: prompt dla Hermesa przygotowany (patrz odpowiedź w tej samej sesji) —
  **nie wdrożony**, czeka na rozwiązanie prerequisitu z punktu 1 (wybór i konfiguracja
  kanału dostępu do X) przez usera.

---

## 📡 6. TELEGRAM NOTIFICATIONS & HERMES TRIAD ARCHITECTURE (SSOT for all agents)

### A. Dual Dispatch Architecture:
Powiadomienia w ekosystemie MMS4TK są wysyłane dwoma całkowicie odrębnymi ścieżkami:

1. **Ścieżka bezpośrednia z silnika (Daemon Direct Dispatch):**
   - **Komponenty**: `src/mms4tk/daemon.py` → `src/mms4tk/telegram_queue.py` (`TelegramQueueManager`).
   - **Zakres**: Natychmiastowe alerty o cyklu życia pozycji:
     - `POSITION_OPENED` (otwarcie pozycji bazowej lub dokupki `HIGH_RISK_ADD`),
     - `POSITION_CLOSED` (zamknięcie przez Stop Loss, Take Profit, Midline TP),
     - `STALE_POSITION_FORCE_CLOSE` (zamknięcie z powodu limitu czasu / reguły Stale),
     - `PROFIT_GIVEBACK_SPLIT` (podział pozycji SPLIT_50 przez Giveback Protection),
     - Błędy krytyczne egzekucji OKX.
   - **Mechanizm**: Asynchroniczny wątek daemona (`TelegramQueueWorker`) wykonuje bezpośrednie żądanie HTTP POST do `https://api.telegram.org/bot<TOKEN>/sendMessage`.
   - **Źródło konfiguracji**: Plik środowiskowy kontenera `/docker/mms4tk/.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
   - ⚠️ **KRYTYCZNA REGUŁA DLA AGENTÓW (ZAKAZ UŻYWANIA STAREGO TOKENA)**:
     - W `/docker/mms4tk/.env` token `TELEGRAM_BOT_TOKEN` MUSI być ustawiony na dedykowanego bota **Masterek** (`<REDACTED_ROTATE_IF_NOT_ALREADY>`, `@MMS4_masterek_bot`).
     - **ZAKAZ**: Pod żadnym pozorem nie wpisywać tam tokena bota głównego (`default`), aby alerty tradingowe nie mieszały się z czatem Koordynatora.
     - ⚠️ **ZASADA PRZEŁADOWANIA ŚRODOWISKA DOCKERA**: W Docker Compose polecenie `docker restart antigravity_core` **NIE przeładowuje zmiennych środowiskowych** z pliku `.env` (kontener zachowuje stary proces i stare env w pamięci). Każda modyfikacja pliku `/docker/mms4tk/.env` WYMAGA wykonania:
       ```bash
       cd /docker/mms4tk && docker compose up -d antigravity_core ui_dashboard
       ```

2. **Ścieżka analityczna i okresowa (Hermes Cron & Dispatcher):**
   - **Komponenty**: Skrypty w `/opt/data/scripts/` (lub `scripts/hermes/`) uruchamiane przez cron Hermesa.
   - **Zakres** (lista skryptów zweryfikowana 2026-09-30 przeciw rzeczywistej zawartości `scripts/hermes/` — poprzednia wersja tej listy cytowała nieistniejący `mms4tk_stale_watch.py` i pomijała dwa realne skrypty):
     - `mms4tk H4 Analysis` (`0 */4 * * *`, skrypt `mms4tk_h4_analysis.py`),
     - `mms4tk D1 ATR Monitor` (`8 0 * * *`, skrypt `mms4tk_d1_atr_check.py`),
     - `mms4tk Multi-Symbol Digest` (`30 2,6,10,14,18,22 * * *`, skrypt `mms4tk_multi_symbol_digest.py`),
     - `mms4tk Position Close Monitor`, skrypt `mms4tk_position_close_monitor.py` (harmonogram cron do potwierdzenia u operatora),
     - `mms4tk Daemon Liveness Watchdog` (`* * * * *`, skrypt `mms4tk_daemon_liveness_watchdog.py`, issue #68/PR #74, 2026-09-28) — alert Telegram + sugerowana cena SL gdy `antigravity_core` wygląda na martwy,
     - `mms4tk Event Dispatcher` (`* * * * *`, skrypt `mms4tk_event_dispatcher.py`).
   - **Profil Hermesa**: Wszystkie te 5 zadań należy rejestrować i utrzymywać **wyłącznie w profilu `masterek`** (`hermes -p masterek cron ...`).
   - W profilu `default` zadania mms4tk są usunięte (profil `default` nie ma zadań tradingowych).
   - W skrypcie `mms4tk_event_dispatcher.py` wywołania CLI jawnie przekazują profil: `hermes -p masterek send --to telegram ...`.

### B. Matryca Triady Botów Telegram na VPS:
| Profil | Bot Username | ID Tokena | Port API | Rola w systemie |
| :--- | :--- | :--- | :--- | :--- |
| **`default`** | `@hermes_h257d665t5aa7a2c_bot` | `8858946894` | `8643` | Główny Koordynator, DevOps, nadzór VPS, Docker |
| **`masterek`** | `@MMS4_masterek_bot` | `8851742516` | `8644` | Dedykowany Trading Supervisor MMS v4 (OKX: BTC, ETH, XAU, XAG) |
| **`senti`** | `@Senti_tk_bot` | `8638368161` | `8645` | Dedykowany Analityk GPW & Portfolio Sentinel |

---

## 📚 7. WSKAŹNIK DO POZOSTAŁEJ DOKUMENTACJI
- **Strategia i algorytm (nienaruszalny fundament merytoryczny)**: `docs/MASTERMIND_STRATEGY.md`, `docs/STRATEGY_AND_ALGORITHM.md`, `docs/mms4_mastermind_full_strategy_and_architecture.md`.
- **Reszta dokumentacji human-facing**: `docs/` (jedyny kanoniczny katalog — `.agents/docs/` zlikwidowany 2026-09-12 jako przestarzała, nielinkowana kopia).
- **Wiedza dla agentów / Graph RAG**: `.agents/specs/` (w tym `.agents/specs/knowledge/` dla dystylacji NotebookLM).
- **README.md**: sekcja "Current Development Status" ma zawsze aktualny, jednozdaniowy stan frontu prac.
