# Przewodnik: Integracja i Handel przez OKX API (Python / Jupyter Notebook)

Niniejszy przewodnik opisuje krok po kroku zasady konfiguracji, uwierzytelniania i prowadzenia handlu za pomocą oficjalnego interfejsu API OKX (zarówno REST, jak i WebSocket) przy użyciu języka Python i biblioteki `python-okx` [6, 41].

---

## 1. Tworzenie i Bezpieczeństwo Kluczy API
Przed rozpoczęciem integracji należy wygenerować klucze API na stronie OKX [42]. Po utworzeniu klucza otrzymasz trzy kluczowe parametry, które należy bezwzględnie zabezpieczyć [42]:
* **API key** (Identyfikator klucza) [42].
* **Secret key** (Klucz prywatny używany do podpisania zapytań) [42].
* **Passphrase** (Hasło klucza określone przez użytkownika podczas jego tworzenia) [42].

### Uprawnienia kluczy (Permissions):
Klucz API może posiadać jedno lub więcej z następujących uprawnień [43]:
1. **Read**: Odczyt danych konta, salda, historii zleceń [43].
2. **Trade**: Składanie i anulowanie zleceń, transfery środków, konfiguracja konta [43].
3. **Withdraw**: Zgłaszanie wypłat zewnętrznych [43].

### Bezpieczeństwo i wygasanie kluczy:
* Ze względów bezpieczeństwa zaleca się powiązanie klucza API z konkretnymi adresami IP (maksymalnie 20 adresów IP dla jednego klucza) [43].
* **Ważne**: Klucze API, które mają uprawnienia do handlu (*Trade*) lub wypłat (*Withdraw*), a **nie są powiązane z żadnym adresem IP, wygasną automatycznie po 14 dniach bezczynności** [44]. Klucze dla środowiska demonstracyjnego (Demo Trading) nie wygasają [44].

---

## 2. Środowisko Produkcyjne vs. Środowisko Demo

OKX udostępnia dwa niezależne środowiska do testowania i handlu [63, 64]. W bibliotece `python-okx` przełączanie między środowiskiem produkcyjnym a demo odbywa się za pomocą parametru `flag` [7, 101]:
* **Produkcja**: `flag = "0"` [79, 101]
* **Demo (Simulated Trading)**: `flag = "1"` [7, 101]

### Punkty dostępowe (Endpoints):
* **Produkcja**:
  * REST: `https://eea.okx.com` [63]
  * WebSocket Private: `wss://wseea.okx.com:8443/ws/v5/private` [63]
* **Demo (Simulated Trading)**:
  * REST: `https://eea.okx.com` [64]
  * WebSocket Private: `wss://wseeapap.okx.com:8443/ws/v5/private` [64]
  * **Ważne**: Przy zapytaniach REST do środowiska Demo wymagane jest dodanie do nagłówka parametru: `x-simulated-trading: 1` [65].

---

## 3. Uwierzytelnianie REST i WebSocket

### A. Sygnowanie zapytań REST (Authentication)
Każde prywatne zapytanie REST musi zawierać następujące nagłówki [45]:
* `OK-ACCESS-KEY`: Twój API Key [45].
* `OK-ACCESS-SIGN`: Wygenerowany podpis Base64 [45].
* `OK-ACCESS-TIMESTAMP`: Znacznik czasu UTC w formacie ISO 8601 (np. `2020-12-08T09:08:57.715Z`) [45]. Serwer odrzuci żądanie, jeśli różni się ono od czasu serwera o więcej niż 30 sekund [45].
* `OK-ACCESS-PASSPHRASE`: Hasło ustawione przy tworzeniu klucza [45].

#### Algorytm generowania podpisu (OK-ACCESS-SIGN):
Podpis jest generowany przez złączenie ciągu znaków: `timestamp + method + requestPath + body` (gdzie body jest pomijane przy zapytaniach GET, o ile nie mają parametrów w ciele) [46, 47]. Następnie ciąg ten jest szyfrowany algorytmem **HMAC SHA256** przy użyciu Twojego `SecretKey` i kodowany do formatu **Base64** [46].

### B. Logowanie do WebSocket (WS Login)
Przed subskrypcją kanałów prywatnych (np. pozycje, zlecenia) należy przesłać ramkę logowania [53, 57]:
```json
{
  "op": "login",
  "args": [
    {
      "apiKey": "Twój_API_Key",
      "passphrase": "Twoje_Passphrase",
      "timestamp": "Unix_Epoch_Time_W_Sekundach",
      "sign": "Sygnatura_Base64"
    }
  ]
}
```
*Sygnatura dla WebSocket powstaje analogicznie: HMAC SHA256 z ciągu `timestamp + "GET" + "/users/self/verify"` przy użyciu SecretKey [55, 56].*

---

## 4. Konfiguracja Konta (Account Modes & Margin)

Do handlu instrumentami pochodnymi (np. Swap Perpetual) uprawniają tylko trzy z czterech dostępnych trybów konta ujednoliconego (Unified Account) [10, 11, 63]:
1. **Single-currency margin mode** (Jednowalutowy depozyt zabezpieczający) [11, 12].
2. **Multi-currency margin mode** (Wielowalutowy depozyt zabezpieczający) [11, 12].
3. **Portfolio margin mode** (Depozyt zabezpieczający portfolio) [11, 12].

*Zwykły tryb spot (Simple mode) nie pozwala na handel pochodnymi [11, 12].*

### Sprawdzanie salda (Jupyter Notebook):
```python
import okx.Account as Account
flag = "1"  # 1 dla konta demo, 0 dla realnego

accountAPI = Account.AccountAPI(api_key, secret_key, passphrase, False, flag)
result = accountAPI.get_account_balance()
print(result)
```
*Kluczowe parametry odpowiedzi to `totalEq` (całkowity kapitał w USD) oraz `mgnRatio` (wskaźnik depozytu zabezpieczającego; wartość ≤ 1.0 oznacza granicę przymusowej likwidacji) [103, 107].*

### Ustawianie dźwigni (Leverage):
Dźwignię finansową dla instrumentów typu SWAP (kontrakty wieczyste Perpetual) można ustawić za pomocą metody `set_leverage` [16, 17, 191]. Poniżej przykłady konfiguracji [17]:

```python
# 1. Dźwignia 5x dla pozycji cross-margin BTC-USDT-SWAP
result = accountAPI.set_leverage(
    instId = "BTC-USDT-SWAP",
    lever = "5",
    mgnMode = "cross"
)

# 2. Dźwignia 5x dla pozycji isolated-margin (tryb pozycji buy/sell)
result = accountAPI.set_leverage(
    instId = "BTC-USDT-SWAP",
    lever = "5",
    mgnMode = "isolated"
)

# 3. Dźwignia 5x dla pozycji isolated-margin LONG (tryb pozycji long/short)
result = accountAPI.set_leverage(
    instId = "BTC-USDT-SWAP",
    lever = "5",
    posSide = "long",
    mgnMode = "isolated"
)
```

---

## 5. Tryby Pozycji i Składanie Zleceń

W handlu kontraktami terminowymi i perpetual (SWAP) na OKX dostępne są dwa tryby pozycji (składania zleceń) [18]:
* **Kupno/Sprzedaż (Net Mode)**: Pozycja danego kontraktu jest po prostu sumą netto kupionych i sprzedanych jednostek [19]. Przy składaniu zlecenia parametr `posSide` nie jest wymagany lub przyjmuje wartość `net` [19, 318].
* **Long/Short (Hedge Mode)**: Pozycje długa i krótka są niezależne od siebie i muszą być zamykane osobno [20]. **W tym trybie parametr `posSide` jest obowiązkowy** i musi przyjmować wartość `long` lub `short` [20, 319].

### Reguły kombinacji Side i PosSide w trybie Long/Short [20, 319]:
* **Otwarcie/Zwiększenie LONG**: `side = "buy"`, `posSide = "long"` [20, 319]
* **Otwarcie/Zwiększenie SHORT**: `side = "sell"`, `posSide = "short"` [20, 319]
* **Zamknięcie/Redukcja LONG**: `side = "sell"`, `posSide = "long"` [20, 319]
* **Zamknięcie/Redukcja SHORT**: `side = "buy"`, `posSide = "short"` [20, 319]

### Przykłady składania zleceń (Jupyter Notebook):

#### A. Zlecenie Limit (Z limitem ceny)
Zakup 100 kontraktów swap BTC-USDT po określonej cenie (np. 19 000 USDT) w trybie izolowanego depozytu netto [21]:
```python
import okx.Trade as Trade

tradeAPI = Trade.TradeAPI(apikey, secretkey, passphrase, False, flag)

result = tradeAPI.place_order(
    instId = "BTC-USDT-SWAP",
    tdMode = "isolated",
    side = "buy",
    posSide = "net",
    ordType = "limit",
    px = "19000",
    sz = "100"
)
print(result)
```

#### B. Zlecenie Market (Po cenie rynkowej)
Zakup 100 kontraktów swap BTC-USDT po aktualnej cenie rynkowej [22]:
```python
result = tradeAPI.place_order(
    instId = "BTC-USDT-SWAP",
    tdMode = "isolated",
    side = "buy",
    posSide = "net",
    ordType = "market",
    sz = "100"
)
print(result)
```

---

## 6. Pobieranie Danych Rynkowych i Zarządzanie Pozycjami

### Pobieranie aktualnej ceny (Tickers):
Aby uzyskać ceny bid/ask oraz statystyki wolumenu dla kontraktów typu SWAP [7, 695]:
```python
import okx.MarketData as MarketData

marketDataAPI = MarketData.MarketAPI(flag = flag)
result = marketDataAPI.get_tickers(instType = "SWAP")
print(result)
```

### Pobieranie otwartych pozycji:
Zwraca szczegółowe zestawienie aktywnych pozycji (w tym ceny wejścia `avgPx`, wskaźniki `upl` - niezrealizowany zysk/strata oraz `liqPx` - szacowaną cenę likwidacji) [123, 125, 129, 130, 131]:
```python
result = accountAPI.get_positions()
print(result)
```

### Anulowanie zlecenia:
Do anulowania aktywnego zlecenia wymagany jest identyfikator instrumentu (`instId`) oraz ID zlecenia (`ordId` lub `clOrdId`) [23, 355]:
```python
result = tradeAPI.cancel_order(
    instId = "BTC-USDT-SWAP", 
    ordId = "505073046126960640"
)
print(result)
```

---

## 7. Stabilność Połączenia i Limity WebSocket

Podczas korzystania z kanałów WebSocket należy przestrzegać restrykcyjnych limitów platformy [49]:
* **Limit nawiązywania połączeń**: Maksymalnie 3 żądania połączenia na sekundę z jednego adresu IP [49].
* **Limity żądań**: Łącznie maksymalnie 480 żądań subskrypcji/odsubskrybowania/logowania na godzinę na jedno połączenie [49].
* **Mechanizm utrzymania połączenia (Keep-Alive / Ping-Pong)**:
  Połączenie zostanie automatycznie przerwane, jeśli w ciągu 30 sekund nie zostaną przesłane żadne dane [50]. Aby utrzymać je w stanie stabilnym, należy zaimplementować timer [50]:
  1. Ustaw timer na $N$ sekund po odebraniu każdej wiadomości (gdzie $N < 30$) [50].
  2. Jeśli timer się skończy (oznacza to brak ruchu przez $N$ sekund), prześlij do serwera ciąg tekstowy `"ping"` [50].
  3. Serwer odeśle odpowiedź `"pong"` [50]. W przypadku braku odpowiedzi w ciągu kolejnych $N$ sekund należy zgłosić błąd i nawiązać połączenie ponownie [50].
