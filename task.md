# TASK 01: Inicjalizacja rdzenia mms4tk (OKX + FastAPI + State Machine)

## Cel
Zbudowanie szkieletu aplikacji `mms4tk` (MMS4 Toolkit), która będzie pełnić rolę modułu egzekucyjnego strategii MMS v4, udostępniającego interfejs API dla agenta nadrzędnego (Hermes).

## Kontekst i Wymagania Architektoniczne
- **Giełda:** OKX (tryb zunifikowany, Perpetual Futures, pary USDC, wymuszony Hedge Mode).
- **Technologia:** Python 3.11+, `FastAPI`, `ccxt` (async), `pydantic`.
- **Środowisko:** Kontener Docker na VPS (plik `.env` przekazywany lokalnie).

## Zadania dla subagenta The Builder:
1. **Struktura katalogów `src/mms4tk/`:**
   - Utwórz odpowiednią strukturę pakietów pod projekt FastAPI.
2. **Maszyna Stanów (`state_manager.py`):**
   - Zaimplementuj klasę `MMS4StateManager`.
   - Zdefiniuj stany: `BASE_MODE` (lewar x1.0) oraz `SCOUT_MODE` (lewar x0.1).
   - Zaimplementuj twardą logikę: Przejście z `BASE` do `SCOUT` następuje wyłącznie po zaliczeniu twardego SL wynoszącego 2%. Przejście ze `SCOUT` do `BASE` po pierwszej zyskownej transakcji (TP).
   - Użyj rygorystycznego typowania (Type Hinting). Brak funkcji kroczącego stop lossa (NO trailing stops).
3. **Moduł Giełdowy (`okx_executor.py`):**
   - Zaimplementuj asynchroniczne połączenie z OKX przy użyciu `ccxt.async_support`.
   - Wdróż regułę R-SEC-01: Klucze API (Key, Secret, Passphrase) muszą być pobierane wyłącznie ze zmiennych środowiskowych. Zablokuj ich logowanie w konsoli.
4. **Warstwa API (`api.py`):**
   - Wystaw instancję FastAPI z podstawowym endpointem awaryjnym (np. `POST /api/v1/emergency/killall`), który w przyszłości przyjmie polecenie od Hermesa.
5. **Zarządzanie Zależnościami:**
   - Wygeneruj plik `requirements.txt`.

## Oczekiwany Rezultat (Handshake):
Zapisz raport z wykonania bazy (opis struktury klas i implementacji reguły sekwencyjności) do pliku w katalogu `.agents/swarm/` zgodnie z protokołem AGENTS-OS.