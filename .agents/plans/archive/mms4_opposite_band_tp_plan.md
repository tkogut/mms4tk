# 📋 PLAN: MMS4 Opposite Band Take Profit & Position Flip (v0.25.0)

**Cel:** Wdrożenie automatycznej realizacji zysków na przeciwległej wstędze TMA zgodnie z regułą 1.4 i 8.4 specyfikacji MMS v4.

---

## 1. Analiza i Diagnoza

### Zasada MMS v4:
- Otwarcie pozycji `LONG` przy dolnej wstędze TMA ma swój naturalny cel Take Profit przy górnej wstędze TMA.
- Otwarcie pozycji `SHORT` przy górnej wstędze TMA ma swój naturalny cel Take Profit przy dolnej wstędze TMA.
- Gdy cena dociera do przeciwległej wstęgi i generowany jest sygnał przeciwny (np. `BASE_LONG` przy aktywnym `SHORT`), stary `SHORT` musi zostać zamknięty z rynkowym zyskiem (Take Profit), a system natychmiast otwiera pozycję w nowym kierunku (`LONG`) lub czeka na kolejne potwierdzenie.

### Poprzedni stan w `daemon.py`:
- `opposite_pos` powodował wygenerowanie `BLOCKED_BASE_*` ("Opposite hedging is prohibited"), przez co zyskowna pozycja wisiała bez realizacji zysku.

---

## 2. Architektura Rozwiązania

```
[ Cykl Daemon M15 dla Symbolu ]
   │
   ▼
[ Sygnał: BASE_LONG / BASE_SHORT ]
   │
   ├── Czy istnieje pozycja w tym samym kierunku? ──► [ TAK ] ──► SKIPPED (Single Position Guard)
   │
   ├── Czy istnieje pozycja w PRZECIWNYM kierunku? ──► [ TAK ] (np. sygnał BASE_LONG, aktywny SHORT)
   │     │
   │     ├── KROK 1: reduce_position(symbol, side=opposite, 100%, reason="TAKE_PROFIT_OPPOSITE_BAND")
   │     ├── KROK 2: inactivity_manager.stop_tracking(symbol)
   │     ├── KROK 3: state_manager.on_take_profit_hit(symbol) (Zresetowanie SCOUT_MODE do BASE_MODE)
   │     ├── KROK 4: emit_take_profit_event() -> Kanban + Telegram
   │     └── KROK 5: execute_order(symbol, new_side, amount, SL=2%) (Wejście w nowy kierunek)
   │
   └── [ BRAK POZYCJI ] ──► Otwarcie nowej pozycji bazowej (Standard)
```

---

## 3. Plan Implementacji Krok po Kroku

1. **`src/mms4tk/hermes_webhook.py`**:
   - Dopisanie metody `emit_take_profit_event(...)` wysyłającej alert o realizacji zysku na przeciwległej wstędze.
2. **`src/mms4tk/daemon.py`**:
   - Przebudowa warunku `opposite_pos is not None` z blokady na sekwencję:
     1. Zamknięcie zysku (TP 100%).
     2. Odznaczenie w Inactivity Managerze.
     3. Notyfikacja Take Profit.
     4. Egzekucja nowego zlecenia bazowego.
3. **`tests/test_take_profit.py`**:
   - Test jednostkowy weryfikujący zamknięcie przeciwnej pozycji i otwarcie nowego kierunku.
4. **Wersjonowanie i Wdrożenie**:
   - Podbicie wersji do **`0.25.0`** (`VERSION`, `__init__.py`, `CHANGELOG.md`, `package.json`, `Header.jsx`, `App.jsx`).
   - `npm run build` w `ui_dashboard`.
   - `git commit` & `git push`.
   - Wdrożenie na serwerze VPS (`docker compose build && docker compose up -d`).
