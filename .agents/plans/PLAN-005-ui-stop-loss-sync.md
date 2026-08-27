# 🎯 PLAN-005: Naprawa i ujednolicenie wyświetlania parametrów Stop Loss w UI Dashboard

**Wersja**: 1.0  
**Status**: GOTOWY DO ZATWIERDZENIA / IMPLEMENTACJI  

---

## 📌 Cel i Kontekst

W zakładce **Settings** panelu kontrolnego (`ui_dashboard/src/components/SettingsTab.jsx`) próg Stop Loss inicjalizował się statyczną wartością `1.25%` wraz z mylącym opisem *"Domyślnie 1.25% dla świecy wybijającej"*.

W rzeczywistości w architekturze MMS v4:
- **Pozycje Bazowe (Base, 70% kapitału)**: chronione są przez **Base Stop Loss = `2.00%`** (`daemonData.stop_loss_pct` / `db_settings.base_sl_pct`).
- **Pozycje Dociążające (High-Risk Addon, 30% kapitału)**: chronione są przez stały **Addon Stop Loss = `1.25%`** (`addon_sl_pct`).

Statyczna inicjalizacja `1.25%` w formularzu wprowadzała operatora w błąd, sugerując że aktywny Stop Loss dla nowych zleceń wynosi 1.25%, podczas gdy silnik produkcyjny realizował prawidłowe dla pozycji bazowej 2.00%.

---

## 🛠️ Proponowane Zmiany (Proposed Changes)

### 1. Frontend Dashboard (`ui_dashboard/src/components/SettingsTab.jsx`)

- **Poprawna inicjalizacja stanu `stopLoss`**:
  ```javascript
  const [stopLoss, setStopLoss] = useState(daemonData?.stop_loss_pct || stateData?.stop_loss_pct || 2.0);
  ```
- **Synchronizacja `stopLoss` w `useEffect` przy załadowaniu danych `daemonData`**:
  ```javascript
  if (daemonData?.stop_loss_pct !== undefined) {
    setStopLoss(daemonData.stop_loss_pct);
  }
  ```
- **Czytelne rozróżnienie w UI**:
  - Podpis pola: **Bazowy Stop Loss (%):** (Base Position SL).
  - Dodanie informacji pomocniczej / plakietki informującej o podziale:
    - *Pozycja Bazowa (70%):* `[Wartość z pola]%` (domyślnie 2.00%).
    - *Dociążenie Addon (30%):* `1.25%` (stały dla świecy wybijającej).

---

### 2. Wersjonowanie (SemVer)
- Podniesienie wersji: `0.33.3` $\to$ `0.33.4` (PATCH) w:
  - `VERSION`
  - `src/mms4tk/__init__.py`
  - `ui_dashboard/app.py`
  - `CHANGELOG.md`

---

## 🚦 Procedura Wdrożenia (Swarm Triad Protocol)

1. **Zero-Loss Maintenance Lock**: `python3 scripts/maintenance.py --enable` (lokalnie i na VPS).
2. **Izolacja Worktree**: Prace w `tmp/worktrees/fix-plan-005-ui-stop-loss-sync`.
3. **Builder Subagent**: Modyfikacja `SettingsTab.jsx`, testy, commit, Builder Handshake.
4. **Auditor Subagent**: Niezależny audyt kodu UI, weryfikacja testów i zgodności wersji + Handshake.
5. **Merge & Push**: Scalenie do `main` i push do `origin/main`.
6. **VPS Deployment**: `git pull origin main` + `docker compose build && docker compose up -d` na VPS.
7. **Weryfikacja**: Sprawdzenie endpointów `/api/v1/settings` i interfejsu webowego na porcie `8501`.
8. **Unlock**: `python3 scripts/maintenance.py --disable`.
