# 📋 PLAN: Audyt i Reset Danych Historycznych KPI (MMS4 Clean Baseline)

**Cel:** Usunięcie zniekształceń metryk KPI (Win Rate, Profit Factor, Max Drawdown, Realized PnL) wywołanych wcześniejszymi błędami pętli (m.in. bug ICP wielokrotnego dobierania 30%), przy jednoczesnym zachowaniu spójności dla 4 obecnie otwartych pozycji na giełdzie OKX.

---

## 1. Analiza Stanu Faktycznego (Pre-Audit)

### A. Stan na Giełdzie OKX (Live)
Obecnie na OKX aktywne są **4 pozycje**:
1. `ICP/USDC` (X-Perp) — **LONG** | 10 kontraktów | Entry: `2.251`
2. `SOL/USDC` (X-Perp) — **SHORT** | 40 kontraktów | Entry: `75.36`
3. `ETH/USDC` (X-Perp) — **SHORT** | 11 kontraktów | Entry: `1879.74`
4. `BTC/USDC` (X-Perp) — **SHORT** | 4 kontrakty | Entry: `63007.26`

### B. Stan Bazy Danych `logs.db`
- `closed_trades`: **124 wpisy** (przeważnie mikro-zamknięcia 0.00 USD i straty z pętli testowych, Suma PnL = `-6.02 USD`, Win Rate = `4.8%`, co zafałszowuje realną ocenę algorytmu).
- `cycle_logs`: **76 407 wpisów** (rutynowe logi odpytywania).
- `state_logs`: **76 404 wpisy**.
- `webhook_events`: **171 wpisów**.

---

## 2. Architektura i Zasady Czyszczenia

```
[ KROK 1: Backup Bezpieczeństwa ]
   └── cp logs.db logs.db.bak_20260815_v0240 (VPS + Local)

[ KROK 2: Selektywne Czyszczenie Tabel SQLite ]
   ├── DELETE FROM closed_trades; (Wyczyszczenie historii zamkniętych transakcji)
   ├── DELETE FROM cycle_logs WHERE timestamp < datetime('now', '-24 hours'); (Pruning)
   ├── DELETE FROM state_logs WHERE timestamp < datetime('now', '-24 hours');
   └── VACUUM;

[ KROK 3: Resynchronizacja StateManagera ]
   ├── realized_pnl = 0.0
   ├── total_trades = 0
   └── Rejestracja 4 aktywnych pozycji w MMS4InactivityManager

[ KROK 4: Weryfikacja KPI na Dashboardzie ]
   ├── Win Rate: 0.0% (Zero-state)
   ├── Profit Factor: 0.00
   ├── Max Drawdown: $0.00
   └── Niezrealizowany uPnL: Aktywny na żywo z 4 pozycji OKX
```

---

## 3. Plan Działań Krok po Kroku

### Faza 1: Kopia Zapasowa (Snapshot)
1. Wykonanie fizycznego backupu pliku `.agents/swarm/logs.db` na VPS do `/docker/mms4tk/.agents/swarm/logs.db.bak_20260815`.
2. Pobranie zrzutu referencyjnego do katalogu lokalnego.

### Faza 2: Skrypt Selektywnego Czyszczenia (`scripts/clean_kpi_history.py`)
1. Utworzenie bezpiecznego skryptu z transakcją SQL (`BEGIN TRANSACTION` / `COMMIT`).
2. Wyczyszczenie tabeli `closed_trades`.
3. Wyczyszczenie przestarzałych webhooków testowych `webhook_events` (pozostawienie tylko nieprzetworzonych).
4. Zachowanie konfiguracji `symbol_configurations` (alokacje, interwały, ustawienia ochrony stagnacji).
5. Wykonanie `VACUUM` w celu odzyskania miejsca i defragmentacji bazy SQLite.

### Faza 3: Resynchronizacja Daemon & Inactivity Manager
1. Zrestartowanie `antigravity_core` na VPS.
2. Inactivity Manager automatycznie rozpozna 4 aktywne pozycje na OKX i rozpocznie ich monitorowanie (liczenie świec od momentu restartu ze świeżym ATR).

### Faza 4: Weryfikacja Wyników
1. Sprawdzenie endpointu `GET /api/v1/stats/pnl`:
   - `realized_pnl`: `0.0`
   - `total_trades`: `0`
   - `win_rate`: `0.0`
   - `profit_factor`: `0.0`
   - `max_drawdown`: `0.0`
2. Sprawdzenie endpointu `GET /api/v1/positions`:
   - Wyświetlenie 4 otwartych pozycji z poprawnym uPnL i Notional USD.
3. Weryfikacja kafelków KPI w Mastermind UI.
