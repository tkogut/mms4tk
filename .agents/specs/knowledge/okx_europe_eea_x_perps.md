---
tags: ["#NotebookLM", "#ExpertKnowledge"]
date_synced: "2026-08-12"
---

# OKX Europe (EEA) – API dla X-Perps / perpetual-style futures w USDC

**Dokument do bazy RAG**  
**Stan wiedzy:** 12 sierpnia 2026  
**Zakres:** Użytkownik OKX Europe z Polski/EEA; programowe składanie zleceń na X-Perps (Expiry Perps) przez OKX API v5; USDC jako dostępna waluta margin/settlement.  
**Źródło nadrzędne:** Aktualna dokumentacja i materiały OKX Europe oraz OKX API v5.

---

## 1. Executive Summary

Dla użytkownika z EEA korzystającego z OKX Europe należy przyjąć następującą architekturę API:

1. **Regionalna domena API:** `https://eea.okx.com` (użycie `www.okx.com` lub `openapi.okx.com` zwraca błąd `50119 API key doesn't exist` lub blokuje dostęp do instrumentów pochodnych dla kluczy EEA).
2. **Brak dostępu do globalnych derivativów `SWAP` (np. `BTC-USDT-SWAP` lub `BTC-USDC-SWAP`).** Dla europejskiego użytkownika właściwym produktem perpetual-style jest **X-Perp / Expiry Perp**, występujący w API jako instrument typu `FUTURES` i identyfikowany przez `ruleType = xperp`.
3. **USDT nie jest walutą transakcyjną dla EEA na OKX Europe (MiCA).** Dostępna jest jedynie jednostronna konwersja `USDT -> USDC`. **USDC** jest używane jako aktywo rozliczeniowe oraz margin dla X-Perps.
4. **Zakaz kodowania na stałe `instId`.** Prawidłowa implementacja pobiera instrumenty dla aktualnego konta przez `GET /api/v5/account/instruments?instType=FUTURES`, następnie wybiera instrument z `ruleType=xperp`, `state=live` i `settleCcy=USDC`.
5. **Składanie zleceń:** Odbywa się przez standardowy endpoint `POST /api/v5/trade/order`.
6. **Warunki aktywacji:** Wymagane KYC, Appropriateness Assessment oraz akceptacja **X-Perp Risk Disclosure** w interfejsie web/app OKX Europe dla konta głównego i sub-accountów.
7. **Identyfikator instId:** W aktualnej architekturze OKX nazwa instrumentu to np. `BTC-USD_UM_XPERP-040431`.

---

## 2. Regulacyjne i Produktowe Tło (MiCA / MiFID)

- **MiCA**: Reguluje stablecoiny w UE (stąd wycofanie USDT i przejście na USDC).
- **MiFID / Instrumenty pochodne**: X-Perp jest klasyfikowany jako instrument pochodny. Wymazywane jest przejdzenie Appropriateness Assessment oraz akceptacja Risk Disclosure.
- **Charakterystyka X-Perp**: Kontrakt posiada odległy termin wygaśnięcia (~5 lat), ale posiada mechanizm Funding Rate (co 8 godzin) i bieżący margining, funkcjonując dokładnie tak jak tradycyjny Perpetual.

---

## 3. Kluczowe Endpointy API dla OKX Europe (EEA)

- **Base URL:** `https://eea.okx.com`
- **Pobranie dostępnych instrumentów dla konta:** `GET /api/v5/account/instruments?instType=FUTURES`
- **Konfiguracja konta (posMode, acctLv):** `GET /api/v5/account/config`
- **Pobranie pozycji:** `GET /api/v5/account/positions`
- **Informacje o dźwigni:** `GET /api/v5/account/leverage-info`
- **Ustawienie dźwigni:** `POST /api/v5/account/set-leverage`
- **Składanie zlecenia:** `POST /api/v5/trade/order`
- **Zamknięcie pozycji:** `POST /api/v5/trade/close-position`

---

## 4. Algorytm wyboru instrumentu X-Perp (Python Pseudo-code)

```python
def discover_xperp_symbol(client, underlying="BTC"):
    # 1. Pobierz instrumenty dostępne dla konta EEA
    instruments = client.request("GET", "/api/v5/account/instruments", params={"instType": "FUTURES"})["data"]
    
    # 2. Przefiltruj X-Perpy w stanie live
    xperps = [
        inst for inst in instruments
        if inst.get("ruleType") == "xperp" 
        and inst.get("state") == "live"
        and inst.get("settleCcy") == "USDC"
    ]
    
    # 3. Znajdź instrument dla danej monety (np. BTC)
    target = [inst for inst in xperps if inst.get("uly", "").startswith(underlying) or inst.get("instId", "").startswith(underlying)]
    if not target:
        raise RuntimeError(f"Brak aktywnego kontraktu X-Perp dla {underlying} na koncie EEA.")
        
    return target[0] # zwraca metadata z instId (np. BTC-USD_UM_XPERP-040431)
```

---

## 5. Błędy API i Diagnostyka

- **`50119 API key doesn't exist` / `API keys are being restricted from trading derivatives`**:
  - Przyczyna: Użycie globalnej domeny `openapi.okx.com` zamiast regionalnej `https://eea.okx.com` LUB brak akceptacji X-Perp Risk Disclosure w panelu WWW OKX LUB próba strzału w `instType=SWAP` zamiast `instType=FUTURES` z `ruleType=xperp`.
- **`51000 posSide error`**:
  - Próba wysłania `posSide` w trybie `net mode` (lub brak `posSide` w `long/short mode`). Sprawdź przez `GET /api/v5/account/config`.
- **`51121 order quantity must be a multiple of lot size`**:
  - Parametr `sz` w kontraktach Futures oznacza **liczbę kontraktów** (gdzie 1 kontrakt ma określoną pojemność `ctVal`, np. 0.0001 BTC). Wymagana jest przeliczenie i zaokrąglenie `sz` zgodnie z `lotSz` i `minSz`.

---

## 6. Status Dokumentu i Tagi

Status: `#NotebookLM #ExpertKnowledge`  
Data synchronizacji: `2026-08-12`  
Nadrzędna domena: EEA OKX Derivatives / X-Perp Integration Guide
