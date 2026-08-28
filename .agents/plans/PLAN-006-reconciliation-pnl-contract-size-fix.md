# 🎯 PLAN-006: Naprawa kalkulacji PnL USD w mechanizmie Reconciliacji / Take Profit oraz czyszczenie rekordu w bazie

**Wersja**: 1.0  
**Status**: GOTOWY DO ZATWIERDZENIA / IMPLEMENTACJI  

---

## 📌 1. Diagnoza Problemu (Root Cause Analysis)

W wykazie zamkniętych transakcji (`closed_trades` w `logs.db`) pojawił się błędny rekord:
```text
trade_id: 'recon_BTC/USDC_long_1787926017740'
symbol: 'BTC/USDC'
entry_price: 79542.18
exit_price: 79359.40
amount (contracts): 37.0
pnl_usd: -6763.00 USD  <--- KARDYNALNY BŁĄD MATEMATYCZNY
```

### 🔍 Dlaczego powstał ten błąd?
1. **Błąd jednostki `contracts` vs `contractSize` w `daemon.py:474`**:
   W module `src/mms4tk/daemon.py` w metodzie `reconcile_positions()` oraz w Take Profit (L1037) wzór na PnL w USD wynosił:
   ```python
   # BŁĘDNY KOD:
   pnl_usd = pnl_pct * entry_price * contracts
   ```
   Dla BTC/USD na OKX 1 kontrakt = **`0.0001 BTC`** (`contractSize = 0.0001`).
   Pozycja 37 kontraktów to **`0.0037 BTC`** (Notional $\approx \$294$ USD).
   Wzór w kodzie potraktował 37 kontraktów jako **37 całych BTC** (Notional $\approx \$2,943,000$ USD), co przy spadku o -0.23% wyliczyło fałszywą stratę **`-6,763.00 USD`** zamiast **`-0.67 USD`**!

2. **Zdublowanie wpisu z oficjalną synchronizacją OKX (`OKXTradeSyncWorker`)**:
   - Ta sama transakcja została również pobrana oficjalnie z API OKX jako rekord `3872973361887580160` z poprawnym `pnl_usd = -3.486 USD`.
   - Zastępczy wpis `recon_...` był więc nie tylko błędnie przeliczony o 4 rzędy wielkości, ale również zdublował transakcję w bazie i zniekształcił statystyki KPI panelu.

---

## 🛠️ 2. Zakres Naprawy (Scope of Changes)

### A. Moduł `src/mms4tk/daemon.py`:
1. Dodanie helpera / użycie `ct_size = self._get_contract_size(target_symbol)`:
   ```python
   def _get_contract_size(self, symbol: str) -> float:
       """Return contract size for symbol from executor markets, falling back to 0.0001 for BTC and 1.0 for others."""
       try:
           if getattr(self.executor, "exchange", None) and getattr(self.executor.exchange, "markets", None):
               m = self.executor.exchange.market(symbol)
               if m and m.get("contractSize"):
                   return float(m["contractSize"])
       except Exception:
           pass
       return 0.0001 if "BTC" in symbol.upper() else (0.001 if "ETH" in symbol.upper() else 1.0)
   ```
2. Poprawa kalkulacji PnL USD w `reconcile_positions()` (L474):
   ```python
   ct_size = self._get_contract_size(target_symbol)
   pnl_usd = pnl_pct * entry_price * contracts * ct_size
   ```
3. Poprawa kalkulacji PnL USD w Take Profit (L1037):
   ```python
   ct_size = self._get_contract_size(target_symbol)
   pnl_usd = pnl_pct * active_entry_price * existing_contracts * ct_size
   ```

### B. Usunięcie błędnego rekordu `recon_...` z bazy `logs.db`:
- Wykonanie zapytania usuwającego fałszywy wpis `recon_BTC/USDC_long_1787926017740` (posiadamy oficjalny rekord OKX `3872973361887580160` dla tego zamknięcia).

### C. Testy jednostkowe (`tests/test_reconciliation_pnl.py`):
- Dodanie testów jednostkowych weryfikujących, że reconciliacja pozycji dla kontraktów BTC (`contractSize=0.0001`) i ETH (`contractSize=0.001`) wylicza PnL w USD z uwzględnieniem mnożnika kontraktu, a nie całych monet.

### D. Wersjonowanie (SemVer):
- Bump wersji do **`0.33.5`** (PATCH) w `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `CHANGELOG.md`.

---

## 🚦 3. Procedura Swarm Triad

1. **Zero-Loss Lock**: Włączenie `Maintenance Mode` na czas modyfikacji.
2. **Izolacja Worktree**: Prace w `tmp/worktrees/fix-plan-006-reconciliation-pnl`.
3. **Builder**: Implementacja poprawki `_get_contract_size`, naprawa formuł, testy, commit, Handshake.
4. **Auditor**: Audyt matematyczny (Math-Consistency check) + weryfikacja testów + Handshake.
5. **Merge & Deploy**: Merge do `main`, push do `origin/main`, deploy na VPS.
6. **Baza danych**: Usunięcie zduplikowanego rekordu `recon_...` na VPS.
7. **Unlock**: Wyłączenie `Maintenance Mode`.
