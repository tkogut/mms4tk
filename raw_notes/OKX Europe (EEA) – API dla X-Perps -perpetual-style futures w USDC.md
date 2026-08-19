# OKX Europe (EEA) – API dla X-Perps / perpetual-style futures w USDC

**Dokument do bazy RAG**  
**Stan wiedzy:** 12 sierpnia 2026  
**Zakres:** użytkownik OKX Europe z Polski/EEA, regulowany europejskim modelem usług; programowe składanie zleceń na X-Perps (Expiry Perps) przez OKX API v5; USDC jako dostępna waluta margin/settlement.  
**Źródło nadrzędne:** aktualna dokumentacja i materiały OKX Europe oraz OKX API v5.

---

## 1. Executive summary

Dla użytkownika z EEA korzystającego z OKX Europe należy przyjąć następującą architekturę API:

1. **Regionalna domena API:** `https://eea.okx.com`.
2. **Nie należy zakładać dostępu do globalnego `SWAP` typu `BTC-USDT-SWAP`.** Dla europejskiego użytkownika właściwym produktem perpetual-style jest **X-Perp / Expiry Perp**, występujący w API jako instrument typu `FUTURES` i identyfikowany przez `ruleType = xperp`.
3. **USDT nie jest walutą transakcyjną dla EEA na OKX Europe.** Od lipca 2026 OKX Europe nie udostępnia handlu USDT użytkownikom EEA; USDT można w określonych warunkach zdeponować i jednostronnie przekonwertować do USDC. USDC jest używane jako aktywo rozliczeniowe oraz może być używane jako margin dla X-Perps.
4. **Nie należy kodować na stałe `instId`.** Prawidłowa implementacja pobiera instrumenty dla aktualnego konta przez `GET /api/v5/account/instruments?instType=FUTURES`, następnie wybiera instrument z `ruleType=xperp`, `state=live` i odpowiednią walutą rozliczeniową/margin.
5. **Zlecenie składa się przez standardowy endpoint:** `POST /api/v5/trade/order`. Dla X-Perps API używa tych samych endpointów co dla standardowych expiry futures.
6. **Przed handlem należy spełnić warunki dostępu:** KYC, appropriateness assessment oraz zaakceptowanie X-Perp risk disclosure w interfejsie web/app. OKX wskazuje, że API trading dla X-Perps zostaje aktywowany po zaakceptowaniu disclosure; sub-accounty muszą zaakceptować je oddzielnie.
7. **USDC nie oznacza, że `instId` musi zawierać `USDC`.** Aktualna architektura OKX używa nazw w rodzaju `BTC-USD_UM_XPERP-040431`, podczas gdy waluta rozliczeniowa jest określana w metadata kontraktu i w kontekście konta.

---

## 2. Regulacyjne i produktowe tło

### 2.1. MiCA a X-Perps / MiFID

OKX Europe działa w europejskim reżimie regulacyjnym. Stablecoinowe ograniczenia dla EEA wynikają z MiCA. Natomiast sam produkt X-Perps jest przez OKX opisany jako instrument pochodny podlegający wymaganiom MiFID. Przed dostępem do X-Perps OKX wymaga appropriateness assessment dotyczącego wiedzy i doświadczenia w zakresie złożonych instrumentów pochodnych.

To rozróżnienie jest ważne dla bazy RAG:

- **MiCA** – istotne m.in. dla dostępności i dopuszczalności stablecoinów, w tym USDT.
- **MiFID / regulacja instrumentów pochodnych** – istotna dla X-Perps, appropriateness assessment, klasyfikacji retail/professional i warunków dostępu do produktu.

### 2.2. X-Perp nie jest klasycznym perpetu­alem

X-Perp jest przez OKX opisany jako expiry perpetual / expiry futures. Kontrakt ma określoną datę rozliczenia, zazwyczaj około 5 lat od emisji. Jednocześnie posiada mechanizm funding rate, bieżące margining oraz charakter handlu zbliżony do perpetual futures. Z tego powodu dla aktywnego tradera zachowuje się funkcjonalnie jak perpetual, ale technicznie jest instrumentem `FUTURES` z terminem wygaśnięcia.

### 2.3. Aktualna lista crypto-linked X-Perps w EEA

OKX publikuje specyfikację kontraktów crypto-linked X-Perps dla EEA. W aktualnej specyfikacji znajdują się m.in.:

- BTC
- ETH
- SOL
- DOGE
- XRP
- ZEC
- PEPE
- HYPE
- SUI
- PUMP
- BNB

Dostępność konkretnego instrumentu dla konkretnego konta może zależeć od regionu, statusu konta i bieżącego rollout produktu. Dlatego **źródłem prawdy dla bota powinno być API konta**, a nie statyczna lista z dokumentacji.

---

## 3. USDT vs USDC w EEA

### 3.1. Status USDT

OKX Europe podaje, że USDT nie jest już aktywem handlowym dla użytkowników EEA. USDT może być zdeponowane, ale nie może być użyte do zwykłego handlu; dla kwalifikowanych użytkowników EEA dostępna jest jednostronna konwersja `USDT -> USDC`.

W praktyce dla bota EEA:

```text
USDT = nie używać jako normalnego quote/margin asset
USDC = preferowana waluta operacyjna
```

### 3.2. USDC jako margin / settlement

OKX podaje, że USDC jest używane jako aktywo rozliczeniowe w order books oraz może być używane jako margin dla X-Perps i Futures Bots.

Dla X-Perps USD-margined OKX opisuje rozliczenie jako USD-equivalent, np. USDC, USDG lub USD, zależnie od jurysdykcji i dostępności. Dla celu tej implementacji należy przyjąć, że **konto EEA ma być prowadzone w USDC**, ale przed wysłaniem zlecenia trzeba zweryfikować z API konkretnego konta, jakie wartości `settleCcy` / margin są faktycznie dostępne.

---

## 4. Regionalna domena API – krytyczne wymaganie

Dla użytkowników UE zarejestrowanych w `my.okx.com` OKX wymaga używania:

```text
https://eea.okx.com
```

a nie:

```text
https://openapi.okx.com
https://www.okx.com
```

Dokumentacja OKX wskazuje, że użycie niewłaściwej domeny może powodować m.in. błąd `50119 API key doesn't exist`, ponieważ klucz API należy do innego regionu.

### Zalecana konfiguracja

```python
BASE_URL = "https://eea.okx.com"
```

Przykład końcówki:

```text
POST https://eea.okx.com/api/v5/trade/order
```

---

## 5. API key

Do składania zleceń API key musi posiadać uprawnienie **Trade**. W praktyce warto ograniczyć klucz do:

- `Read`
- `Trade`

oraz stosować IP allowlist.

Nie należy nadawać `Withdraw`, jeżeli bot go nie potrzebuje.

OKX wskazuje także, że klucze bez IP allowlist z uprawnieniami transakcyjnymi lub wypłat mogą zostać usunięte po 14 dniach nieaktywności; użycie prywatnych endpointów oraz składanie zleceń liczy się jako aktywność.

---

## 6. Warunki aktywacji X-Perps dla EEA

Przed próbą składania zleceń należy zweryfikować:

1. KYC zakończone.
2. Appropriateness assessment zakończony pozytywnie.
3. X-Perps dostępny dla danego konta.
4. X-Perp risk disclosure zaakceptowany w web/app.
5. API key ma `Trade` permission.
6. API używa `eea.okx.com`.
7. Konto ma dostępny USDC margin.

Ważne: OKX podaje, że API users muszą zaakceptować X-Perp risk disclosure w interfejsie web/app przed aktywacją tradingu przez API. Dla sub-account disclosure należy zaakceptować oddzielnie.

---

## 7. Jak identyfikować instrument X-Perp przez API

### 7.1. Preferowany endpoint

```http
GET /api/v5/account/instruments?instType=FUTURES
```

Jest to prywatny endpoint zwracający instrumenty dostępne dla **aktualnego konta**.

To jest preferowany mechanizm dla bota EEA, ponieważ publiczna lista instrumentów może zawierać instrumenty, których dane konto nie może handlować.

### 7.2. Kryteria wyboru

Minimalny filtr powinien wyglądać koncepcyjnie tak:

```python
xperps = [
    x for x in instruments
    if x.get("ruleType") == "xperp"
    and x.get("state") == "live"
]
```

Następnie należy filtrować po underlying/family, np. BTC.

Nie należy zakładać, że symbol jest:

```text
BTC-USDC-SWAP
```

lub:

```text
BTC-USDC-PERP
```

Aktualny przykład OKX wygląda jak:

```text
BTC-USD_UM_XPERP-040431
```

### 7.3. Przykładowe pola metadata

Bot powinien traktować co najmniej następujące pola jako dane wejściowe do walidacji zlecenia:

```text
instId
instType
instFamily
uly
settleCcy
ctVal
ctType
lotSz
minSz
tickSz
state
ruleType
expTime
lever
```

Nie wolno zakładać konkretnych wartości bez odczytania metadata.

---

## 8. X-Perp jako `FUTURES`, nie `SWAP`

To jest jedna z najważniejszych reguł implementacyjnych.

### Globalny model, którego NIE należy kopiować bezpośrednio dla EEA

```text
BTC-USDT-SWAP
instType = SWAP
```

### Model X-Perp dla EEA

```text
instType = FUTURES
ruleType = xperp
settlement = USD-equivalent (np. USDC)
```

W konsekwencji należy używać endpointów futures, nie budować logiki na założeniu, że europejski perpetual zawsze będzie `SWAP`.

---

## 9. Złożenie zlecenia

### Endpoint

```http
POST /api/v5/trade/order
```

Endpoint przyjmuje m.in.:

```text
instId
instType context through instrument
tdMode
ccy
clOrdId
side
posSide
ordType
sz
px
reduceOnly
```

Dla futures `tdMode` może być `cross` albo `isolated` zależnie od trybu konta/pozycji.

### Minimalny konceptualny request – LONG, market

```json
{
  "instId": "BTC-USD_UM_XPERP-040431",
  "tdMode": "cross",
  "side": "buy",
  "ordType": "market",
  "sz": "10"
}
```

**Uwaga:** powyższy `instId` jest przykładem dokumentacyjnym, nie wartością, którą bot powinien mieć na stałe zakodowaną.

### Minimalny konceptualny request – SHORT, market

```json
{
  "instId": "BTC-USD_UM_XPERP-040431",
  "tdMode": "cross",
  "side": "sell",
  "ordType": "market",
  "sz": "10"
}
```

W `net` position mode `buy` / `sell` interpretuje się względem bieżącej pozycji. W `long/short` mode wymagany jest `posSide`.

---

## 10. Net mode vs long/short (hedge) mode

OKX obsługuje dwa sposoby prowadzenia pozycji futures:

### Net mode

Jedna pozycja netto na instrument.

Przykład:

```text
buy  -> zwiększa LONG / zmniejsza SHORT
sell -> zwiększa SHORT / zmniejsza LONG
```

Dokładny efekt zależy od istniejącej pozycji i order parameters.

### Long/short mode

Można utrzymywać osobno:

```text
long
short
```

W tym trybie `posSide` jest wymagane i ma wartość:

```text
long
short
```

Przed składaniem zleceń bot powinien pobrać konfigurację konta:

```http
GET /api/v5/account/config
```

i ustalić:

```text
posMode
acctLv
```

Nie należy zgadywać trybu konta.

---

## 11. Dźwignia

Przed zleceniem warto pobrać aktualną konfigurację leverage:

```http
GET /api/v5/account/leverage-info
```

Do ustawiania leverage:

```http
POST /api/v5/account/set-leverage
```

Przykład koncepcyjny:

```json
{
  "instId": "BTC-USD_UM_XPERP-040431",
  "lever": "5",
  "mgnMode": "cross"
}
```

Zakres dostępnej dźwigni zależy od instrumentu i konta. Aktualna specyfikacja EEA dla crypto-linked X-Perps podaje do 10x dla wymienionych kontraktów, ale **bot powinien odczytywać aktualną wartość z API/metadata zamiast hard-code**.

---

## 12. Rozmiar zlecenia – `sz` jest krytyczny

Dla kontraktów `FUTURES` parametr `sz` oznacza **liczbę kontraktów**, a nie bezpośrednio wartość w USDC.

Przykład z dokumentacji X-Perp BTC:

```text
Contract size = 0.0001 BTC
```

Zatem nominalna ekspozycja jednego kontraktu jest związana z `ctVal` / specification instrumentu.

Bot powinien pobrać:

```text
ctVal
lotSz
minSz
```

i zaokrąglić żądaną liczbę kontraktów do `lotSz` oraz sprawdzić `minSz`.

Dodatkowo przed wysłaniem zlecenia warto wywołać:

```http
GET /api/v5/account/max-size
```

a następnie upewnić się, że:

```text
requested_sz <= maxBuy / maxSell
```

oraz że request nie przekracza limitów pozycji/instrumentu.

---

## 13. Maksymalny rozmiar i ryzyko position limits

Aktualne API zwraca również ograniczenia pozycji i może odrzucać nowe zlecenia, jeśli użytkownik lub platforma osiągnęli limit open interest / pozycji.

W szczególności API dokumentuje pola takie jak:

```text
posLmtAmt
posLmtPct
maxPlatOILmt
```

Bot nie powinien zakładać, że sama dostępność marginu wystarcza do otwarcia pozycji.

---

## 14. Funding rate i charakter X-Perp

X-Perps mają funding mechanism, podobnie jak perpetual futures. OKX opisuje funding jako okresową wymianę pomiędzy stronami pozycji; w materiałach dla X-Perps podawane są okresy co 8 godzin.

Jednocześnie X-Perp ma odległą datę expiry, zazwyczaj około 5 lat od emisji.

Konsekwencje dla bota:

- pozycja nie powinna być traktowana jako kontrakt bez terminu wygaśnięcia;
- bot powinien przechowywać `expTime`;
- w pobliżu wygasania należy uwzględnić możliwość rotacji na nowy kontrakt;
- funding należy uwzględnić w PnL i strategii;
- przy długich pozycjach może wystąpić basis risk wynikający z długiego terminu kontraktu.

---

## 15. Generowanie nowych kontraktów X-Perp

OKX podaje, że pierwszego dnia miesiąca wygaśnięcia generowany jest nowy, odległy kontrakt. Przez okres przejściowy stary i nowy kontrakt mogą być jednocześnie dostępne.

Wniosek dla bota:

```text
NIE:
BTC_INST_ID = "BTC-USD_UM_XPERP-040431"

TAK:
1. Pobierz aktywne FUTURES.
2. Filtruj ruleType=xperp.
3. Filtruj underlying BTC.
4. Wybierz właściwy aktywny kontrakt na podstawie expTime/state/rodziny.
5. Zapisz aktualny instId.
```

---

## 16. Public instruments vs account instruments

Dostępne są dwa istotne źródła metadata:

### Public

```http
GET /api/v5/public/instruments?instType=FUTURES
```

Służy do pobierania publicznej konfiguracji instrumentów.

### Private – preferowane dla bota tradingowego

```http
GET /api/v5/account/instruments?instType=FUTURES
```

Zwraca instrumenty dostępne dla bieżącego konta.

**Rekomendacja:** dla decyzji „czy mogę tym kontem handlować?” używać `account/instruments`.

---

## 17. Ticker / cena / market data

Do aktualnej ceny można wykorzystać m.in.:

```http
GET /api/v5/market/ticker?instId=<INST_ID>
```

Do strategii warto również wykorzystywać:

- index price,
- mark price,
- funding rate,
- order book,
- candles/OHLCV,
- open interest.

Liquidation jest oparta na Mark Price, a nie Last Price.

Dla systemu risk management należy więc unikać liczenia liquidation risk tylko z ceny ostatniej transakcji.

---

## 18. Autoryzacja REST API

Prywatne endpointy OKX wymagają nagłówków:

```text
OK-ACCESS-KEY
OK-ACCESS-SIGN
OK-ACCESS-TIMESTAMP
OK-ACCESS-PASSPHRASE
```

a dla produkcji:

```text
OKX-SIMULATED-TRADING: 0
```

Podpis HMAC-SHA256 jest tworzony z:

```text
timestamp + HTTP method + requestPath + body
```

Następnie wynik jest kodowany Base64.

Dla `GET` body jest pustym stringiem, a query string należy uwzględnić w `requestPath`.

### Wzór

```python
prehash = timestamp + method.upper() + request_path + body
signature = base64.b64encode(
    hmac.new(
        secret_key.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256
    ).digest()
).decode()
```

### Timestamp

OKX podaje, że różnica czasu klient-serwer powinna być utrzymywana w granicach około 30 sekund. W praktyce bot powinien okresowo synchronizować czas z:

```http
GET /api/v5/public/time
```

---

## 19. Python – minimalny klient REST dla EEA

Poniższy kod jest szkieletem technicznym. Nie zawiera automatycznego doboru konkretnego X-Perp, ponieważ ten wybór powinien być wykonany z aktualnego `account/instruments`.

```python
import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

import requests


class OKXEEA:
    BASE_URL = "https://eea.okx.com"

    def __init__(self, api_key: str, secret_key: str, passphrase: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.session = requests.Session()

    def _timestamp(self) -> str:
        # ISO-8601 UTC with milliseconds
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    def _sign(self, timestamp: str, method: str,
              request_path: str, body: str = "") -> str:
        prehash = timestamp + method.upper() + request_path + body
        digest = hmac.new(
            self.secret_key.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode()

    def request(self, method: str, path: str,
                params: Optional[Dict[str, Any]] = None,
                body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        method = method.upper()

        if params:
            from urllib.parse import urlencode
            query = urlencode(params)
            request_path = f"{path}?{query}"
        else:
            request_path = path

        body_text = json.dumps(body, separators=(",", ":")) if body else ""
        timestamp = self._timestamp()
        sign = self._sign(timestamp, method, request_path, body_text)

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "OKX-SIMULATED-TRADING": "0",
        }

        response = self.session.request(
            method,
            self.BASE_URL + request_path,
            headers=headers,
            data=body_text if body else None,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error: {data}")

        return data

    def get_futures_instruments(self):
        return self.request(
            "GET",
            "/api/v5/account/instruments",
            params={"instType": "FUTURES"},
        )["data"]

    def get_account_config(self):
        return self.request(
            "GET",
            "/api/v5/account/config",
        )["data"]

    def get_positions(self, inst_id: Optional[str] = None):
        params = {"instId": inst_id} if inst_id else None
        return self.request(
            "GET",
            "/api/v5/account/positions",
            params=params,
        )["data"]

    def get_xperps(self, underlying: Optional[str] = None):
        instruments = self.get_futures_instruments()

        result = [
            x for x in instruments
            if x.get("ruleType") == "xperp"
            and x.get("state") == "live"
        ]

        if underlying:
            result = [
                x for x in result
                if x.get("uly", "").upper().startswith(underlying.upper())
                or x.get("instId", "").upper().startswith(underlying.upper())
            ]

        return result

    def set_leverage(self, inst_id: str, leverage: str,
                     mgn_mode: str = "cross"):
        body = {
            "instId": inst_id,
            "lever": leverage,
            "mgnMode": mgn_mode,
        }
        return self.request(
            "POST",
            "/api/v5/account/set-leverage",
            body=body,
        )

    def place_order(self, inst_id: str, side: str, size: str,
                    td_mode: str = "cross",
                    ord_type: str = "market",
                    pos_side: Optional[str] = None,
                    reduce_only: Optional[bool] = None,
                    cl_ord_id: Optional[str] = None):
        body = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": size,
        }

        if pos_side is not None:
            body["posSide"] = pos_side

        if reduce_only is not None:
            body["reduceOnly"] = "true" if reduce_only else "false"

        if cl_ord_id:
            body["clOrdId"] = cl_ord_id

        return self.request(
            "POST",
            "/api/v5/trade/order",
            body=body,
        )
```

### Ważna uwaga do produkcji

Powyższy kod jest szkieletem. W produkcji warto dodać:

- synchronizację czasu z serwerem OKX;
- retry dla błędów przejściowych;
- idempotencję przez `clOrdId`;
- kontrolę rate limits;
- sprawdzanie statusu order po timeoutach;
- oddzielny mechanizm `close-position`;
- WebSocket do aktualizacji pozycji i orderów;
- walidację `lotSz/minSz/tickSz`;
- kontrolę `posMode`;
- kontrolę `settleCcy`/margin;
- dziennik audytowy wszystkich requestów i odpowiedzi;
- circuit breaker po serii błędów API.

---

## 20. Zalecany workflow bota

### Start bota

```text
1. Ustaw BASE_URL = https://eea.okx.com
2. Pobierz server time
3. Sprawdź API credentials
4. GET /api/v5/account/config
5. GET /api/v5/account/balance
6. GET /api/v5/account/instruments?instType=FUTURES
7. Znajdź ruleType=xperp + state=live
8. Wybierz instrument BTC/ETH/SOL itd.
9. Zweryfikuj settleCcy / margin = dostępny USDC
10. Pobierz leverage info
11. Ustaw leverage, jeżeli wymagane
12. Pobierz max-size
13. Oblicz sz w kontraktach
14. Złóż order
15. Potwierdź ordId
16. Monitoruj position/order przez WebSocket
```

### Nie należy

```text
1. hard-code BTC-USDT-SWAP
2. używać USDT jako margin dla EEA
3. zakładać, że każdy FUTURES jest X-Perp
4. zakładać stałego instId na lata
5. obliczać sz bez ctVal/lotSz/minSz
6. zakładać net mode
7. zakładać dostępności instrumentu tylko dlatego, że istnieje publicznie
```

---

## 21. Zamknięcie pozycji

Dla automatycznego bota preferowany jest mechanizm zgodny z aktualnym position mode.

OKX udostępnia także endpoint zamknięcia pozycji przez market order:

```http
POST /api/v5/trade/close-position
```

Parametry obejmują m.in.:

```text
instId
mgnMode
posSide
ccy
autoCxl
```

W `net` mode `posSide` może być pominięte/ustawione zgodnie z dokumentacją; w `long/short` trzeba wskazać `long` lub `short`.

Do bota należy preferować jednoznaczne zamykanie pozycji i dodatkowo potwierdzać rzeczywisty stan przez `GET /api/v5/account/positions`.

---

## 22. Błędy, które mogą wystąpić w implementacji

### 50119 – API key doesn't exist

Najczęstsza przyczyna dla EEA: zła domena regionalna.

Rozwiązanie:

```text
https://eea.okx.com
```

### 50102 – timestamp expired

Synchronizuj zegar z serwerem OKX i utrzymuj małą różnicę czasu.

### 50101 – API key does not match current environment

Sprawdź produkcja vs demo oraz:

```text
OKX-SIMULATED-TRADING
```

### 51000 – posSide error

Sprawdź `posMode` przez:

```http
GET /api/v5/account/config
```

Nie wysyłaj `posSide=long/short` do net mode.

### 51121 – order quantity must be a multiple of lot size

Zaokrąglaj `sz` zgodnie z `lotSz`.

### 51010 – request unsupported under current account mode

Sprawdź account mode i czy dany typ tradingu jest obsługiwany w aktualnym trybie konta.

---

## 23. Minimalne reguły walidacyjne przed POST /trade/order

Przed wysłaniem zlecenia bot powinien sprawdzić:

```python
assert base_url == "https://eea.okx.com"
assert instrument["instType"] == "FUTURES"
assert instrument["ruleType"] == "xperp"
assert instrument["state"] == "live"
assert settle_currency_available
assert size >= min_size
assert size % lot_size == 0
assert size <= max_size
assert leverage <= allowed_leverage
assert side in {"buy", "sell"}
assert order_type in {"market", "limit", "post_only", "fok", "ioc"}
```

W praktyce dla wartości decimal należy stosować `Decimal`, nie `float`.

---

## 24. Obsługa USDC w architekturze bota

Zalecany model danych:

```python
ACCOUNT_CURRENCY = "USDC"

SUPPORTED_SETTLEMENT = {
    "USDC"
}
```

Ale implementacja powinna pozostać konfigurowalna, ponieważ OKX dokumentuje X-Perps jako USD-margined i wskazuje także USDG/USD jako możliwe USD-equivalent settlement currencies dla określonych produktów/jurysdykcji.

Przy wyborze instrumentu bot powinien preferować:

```python
settleCcy == "USDC"
```

a jeżeli instrument ma inne dopuszczalne rozliczenie, nie powinien automatycznie przełączać się na nie bez jawnej konfiguracji.

---

## 25. Ważne rozróżnienie: `BTC-USD_UM_XPERP` a `BTC-USDC`

Nazewnictwo instrumentu może sugerować USD, ale nie oznacza to, że użytkownik musi posiadać fizyczne USD.

OKX używa modelu **USD-margined / USD-equivalent settlement**. Aktualny przykład BTC X-Perp:

```text
BTC-USD_UM_XPERP-040431
```

ma rozliczenie określone w specyfikacji jako USDC/USDG/USD.

Dlatego poprawna logika bota to:

```text
symbol/family -> z metadata instrumentu
settlement/margin -> z metadata + konta
```

nie:

```text
symbol contains USDC -> assume settlement is USDC
```

---

## 26. Wymagane dane do RAG dla późniejszego generowania kodu

Jeżeli RAG ma później odpowiadać na pytania programistyczne, warto indeksować następujące fakty jako osobne fragmenty:

### Fakt A

```text
EEA OKX REST API domain = https://eea.okx.com
```

### Fakt B

```text
EEA X-Perps are API-accessible and use the standard expiry-futures API endpoints.
```

### Fakt C

```text
X-Perp instrument type = FUTURES
X-Perp identifier = ruleType=xperp
```

### Fakt D

```text
USDT is not tradeable on OKX Europe for EEA users in 2026.
USDT can be one-way converted to USDC for eligible EEA users.
```

### Fakt E

```text
USDC can be used as margin for X-Perps and Futures Bots.
```

### Fakt F

```text
Do not hard-code xperp instId. Discover it from account instruments.
```

### Fakt G

```text
GET /api/v5/account/instruments?instType=FUTURES
returns instrument information available to the current account.
```

### Fakt H

```text
POST /api/v5/trade/order is the order placement endpoint for futures.
```

### Fakt I

```text
For FUTURES contract orders, sz is the number of contracts.
Use ctVal/lotSz/minSz to calculate and validate sz.
```

### Fakt J

```text
Before API trading of X-Perps, the user must satisfy account eligibility requirements and accept the X-Perp risk disclosure in the OKX web/app interface.
```

---

## 27. Rekomendowana implementacja dla konkretnego bota

Najbardziej odporna architektura:

```text
OKXEEAClient
   |
   +-- Auth / Signing
   |
   +-- Account
   |     +-- config
   |     +-- balance
   |     +-- instruments
   |     +-- leverage
   |     +-- positions
   |     +-- max-size
   |
   +-- Market
   |     +-- ticker
   |     +-- mark price
   |     +-- index price
   |     +-- funding
   |     +-- candles
   |
   +-- Trading
   |     +-- place order
   |     +-- amend order
   |     +-- cancel order
   |     +-- close position
   |
   +-- XPerpSelector
   |     +-- filter ruleType=xperp
   |     +-- select underlying
   |     +-- select settleCcy=USDC
   |     +-- select state=live
   |     +-- select valid expTime
   |
   +-- RiskEngine
         +-- leverage
         +-- max size
         +-- position limits
         +-- liquidation/mark price
         +-- funding cost
```

---

## 28. Sources / materiały źródłowe

1. OKX API Guide / V5 API documentation  
   https://www.okx.com/docs-v5/

2. OKX API FAQ – Europe  
   https://www.okx.com/en-eu/help/api-faq

3. OKX X-Perps Guide – Europe  
   https://www.okx.com/en-eu/help/x-perps-guide

4. What are X-Perps (Expiry Perps)? – EEA  
   https://www.okx.com/en-eu/help/okx-x-perps-eea-what-are-expiry-perps

5. Who can access X-Perps in the EEA?  
   https://www.okx.com/en-eu/help/okx-x-perps-eea-regional-availability-eligibility

6. How do Stock and Commodity X-Perps work? – API access details  
   https://www.okx.com/en-eu/help/how-do-stock-and-commodity-x-perps-work

7. X-Perps Contract Specifications – EEA  
   https://www.okx.com/es-es/help/x-perps-contract-specifications

8. USDT One Way Convert FAQ – Europe  
   https://www.okx.com/en-eu/help/usdt-one-way-convert

9. Why USDT Is Not Tradeable on OKX in Europe  
   https://www.okx.com/en-us/learn/usdt-not-tradeable-okx-europe-stablecoin-guide

10. OKX API Agreement – Europe  
    https://www.okx.com/en-eu/help/okx-api-agreement

---

## 29. Status dokumentu

Ten dokument odzwierciedla stan materiałów OKX dostępnych w internecie na **12 sierpnia 2026**. OKX może zmieniać dostępność instrumentów, nazwy `instId`, limity dźwigni, settlement currencies, eligibility rules, endpoint schemas i rate limits. Z tego powodu baza RAG powinna traktować niniejszy dokument jako **wersjonowaną wiedzę integracyjną**, a nie niezmienny kontrakt API.

Najważniejsza zasada implementacyjna:

> **Dla konta OKX Europe nie zgaduj instrumentu ani waluty rozliczeniowej na podstawie globalnych przykładów. Odczytaj dostępne instrumenty z prywatnego API EEA, wybierz `FUTURES` + `ruleType=xperp`, zweryfikuj USDC jako dostępny settlement/margin, a następnie składaj zlecenie przez `POST /api/v5/trade/order`.**
