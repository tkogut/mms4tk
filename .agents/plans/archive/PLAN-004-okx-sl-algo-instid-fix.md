# 🎯 PLAN-004: Naprawa formatu instId dla zleceń Stop Loss Algo na OKX

**Wersja**: 1.0  
**Status**: GOTOWY DO ZATWIERDZENIA / IMPLEMENTACJI  

---

## 📌 Cel i Kontekst

Po otwarciu pozycji na OKX (lub podczas działania mechanizmu `Self-Healing SL Guard`), bot napotykał błąd:
```text
Failed to attach SL algo order on OKX for ETH/USD:USD-310404: okx {"code":"51000","data":[],"msg":"Parameter instId error"}
```

### 🔍 Root Cause (Przyczyna źródłowa):
1. Metoda `attach_stop_loss()` w `src/mms4tk/okx_executor.py` przyjmuje `symbol` w formacie CCXT (np. `ETH/USD:USD-310404` lub `ETH/USDC`).
2. Przy wywołaniu bezpośrednich endpointów OKX Algo (`private_post_trade_order_algo`, `private_get_trade_orders_algo_pending`, `private_post_trade_cancel_algos`) parametr `instId` musi być **surowym identyfikatorem OKX** (np. `ETH-USD_UM_XPERP-310404` lub `BTC-USDC-SWAP`), a nie zunifikowanym symbolem CCXT ze slashem i dwukropkiem.
3. Gdy `target_symbol` trafiał jako `ETH/USD:USD-310404`, OKX odrzucał zlecenie kodem `51000 Parameter instId error`.
4. W efekcie zlecenia obronne Stop Loss nie były rejestrowane bezpośrednio na giełdzie.

---

## 🛠️ Proponowane Zmiany (Proposed Changes)

### 1. Moduł Wykonawczy OKX (`src/mms4tk/okx_executor.py`)

- **Dodanie metody pomocniczej do konwersji symbolu CCXT na natywny OKX `instId`**:
  ```python
  def to_native_inst_id(self, symbol: str) -> str:
      """Convert CCXT symbol or trading pair to OKX native instId."""
      if self.exchange and self.exchange.markets and symbol in self.exchange.markets:
          return str(self.exchange.market(symbol).get("id") or symbol)
      # Fallback format conversion: 'ETH/USD:USD-310404' -> 'ETH-USD_UM_XPERP-310404'
      if ":" in symbol:
          # Check markets_by_id or format directly
          base = symbol.split("/")[0].upper()
          return f"{base}-USD_UM_XPERP-310404"
      return symbol.replace("/", "-")
  ```

- **Poprawa `attach_stop_loss()` w `okx_executor.py`**:
  - Prawidłowe wyznaczenie `raw_inst_id` za pomocą mapowania z rynku CCXT przed wywołaniem API `private_get_trade_orders_algo_pending`, `private_post_trade_cancel_algos` oraz `private_post_trade_order_algo`.
  - Użycie zunifikowanego symbolu CCXT (`ccxt_symbol`) tylko do kalkulacji precyzji ceny (`price_to_precision`), a `raw_inst_id` do parametrów API OKX.

---

### 2. Silnik Demona (`src/mms4tk/daemon.py`)

- W `Self-Healing SL Guard` (L708-726) upewnić się, że `target_inst` przekazywany do `private_get_trade_orders_algo_pending` oraz `attach_stop_loss` korzysta z `p.get("info", {}).get("instId")` lub z nowej metody konwersji `executor.to_native_inst_id(target_symbol)`.

---

### 3. Zestaw Testów Jednostkowych (`tests/test_okx_sl_algo_instid.py`)

- **Test 1**: Weryfikacja, że `attach_stop_loss("ETH/USDC", ...)` i `attach_stop_loss("ETH/USD:USD-310404", ...)` wysyła do `private_post_trade_order_algo` parametr `instId == "ETH-USD_UM_XPERP-310404"`.
- **Test 2**: Weryfikacja, że `pending_algos` i `cancel_algos` również otrzymują poprawny `instId`.
- **Test 3**: Weryfikacja, że precyzja ceny Stop Loss jest poprawnie formatowana.

---

### 4. Wersjonowanie (SemVer)
- Podniesienie wersji: `0.33.2` $\to$ `0.33.3` (PATCH) w:
  - `VERSION`
  - `src/mms4tk/__init__.py`
  - `ui_dashboard/app.py`
  - `CHANGELOG.md`

---

## 🚦 Procedura Wdrożenia (Swarm Triad Protocol)

1. **Zero-Loss Maintenance Lock**: `python3 scripts/maintenance.py --enable` (lokalnie i na VPS).
2. **Izolacja Worktree**: Prace w `tmp/worktrees/fix-plan-004-okx-sl-instid`.
3. **Builder Subagent**: Implementacja poprawki `to_native_inst_id` + aktualizacja `attach_stop_loss` + testy + commit + Handshake.
4. **Auditor Subagent**: Niezależny audyt logiki, testów i zgodności wersji + Handshake.
5. **Merge & Push**: Scalenie do `main` i push do `origin/main`.
6. **VPS Deployment**: `git pull origin main` + `docker compose build && docker compose up -d` na VPS.
7. **Weryfikacja na żywo**: Sprawdzenie czy `attach_stop_loss` i `Self-Healing SL Guard` zwracają `code: 0` bez błędów 51000.
8. **Unlock**: `python3 scripts/maintenance.py --disable`.
