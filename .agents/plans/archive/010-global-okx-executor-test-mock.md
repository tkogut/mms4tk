# PLAN 010: Globalny mock sieciowy dla `OKXExecutor` w całym test suite

## Status: TODO — do zlecenia agentowi wykonawczemu
**Worktree (do stworzenia przez wykonawcę):** `tmp/worktrees/feature/global-okx-test-mock`
**Branch (do stworzenia przez wykonawcę):** `feature/global-okx-test-mock`

---

## 0. Dla agenta wykonawczego — jak czytać ten dokument

Ten plik jest **samodzielną specyfikacją**. Nie masz dostępu do rozmowy, w której
powstał — wszystko, czego potrzebujesz (dokładne numery linii, dokładny kod, dokładne
kształty zwracanych wartości, dokładna lista plików do zmiany i plików do NIEruszania)
jest tutaj. Jeśli coś się nie zgadza ze stanem repo w chwili wykonania (linie mogły się
przesunąć), potraktuj numery linii jako punkt startowy do `grep`, nie jako twardą prawdę
— ale kształty API, nazwy metod i logika opisana niżej są zweryfikowane względem
rzeczywistego kodu i powinny być stabilne.

Wykonaj zgodnie z SDLC z `CLAUDE.md` w korzeniu repo: worktree → implementacja →
atomowe commity → handshake → QA Gate → PR. Nie commituj bezpośrednio na `main`.

---

## 1. CEL

`src/mms4tk/okx_executor.py::OKXExecutor.__init__` **zawsze** tworzy prawdziwego,
nieautoryzowanego klienta `ccxt.okx(...)`, niezależnie od tego, czy są skonfigurowane
klucze OKX (`OKX_API_KEY`/`OKX_SECRET`/`OKX_PASSPHRASE`). "Mock Mode" oznacza tylko
brak autoryzacji, **nie** brak sieci. 7 jego metod robi realne, bezwarunkowe
wywołania sieciowe do REST API OKX. W środowisku bez (lub z zawodnym) dostępem do
sieci to się wiesza lub jest wolne i niedeterministyczne — potwierdzone w praktyce: w
poprzedniej sesji audytowej znaleziono ten problem osobno w `test_hermes_network.py` i
`test_multi_symbol.py`, łatane każdorazowo lokalnym `monkeypatch.setattr(instance, ...)`
— rozwiązanie, które **nie generalizuje się**, bo `OKXExecutor()` jest tworzony w wielu
miejscach niezależnie od siebie (patrz §3).

Cel: **jeden globalny, autouse fixture w nowym `tests/conftest.py`**, patchujący
`OKXExecutor` na poziomie KLASY (nie instancji), tak by żaden test — niezależnie od
tego, skąd bierze swój `executor` — nigdy nie dotykał prawdziwej sieci. Bez utraty
pokrycia testowego: te same 143 testy muszą dalej przechodzić, z identycznym zestawem
asercji.

---

## 2. Diagnoza — dokładnie które metody wymagają patcha

Wszystko w `src/mms4tk/okx_executor.py`. Numery linii przybliżone wg stanu w chwili
pisania tego planu — jeśli się przesunęły, `grep -n "async def <nazwa>"` znajdzie je od razu.

### 2a. Metody robiące REALNE wywołania sieciowe BEZWARUNKOWO (wymagają patcha)

| Metoda | Linia | Co robi | Zwraca |
|---|---|---|---|
| `initialize()` | ~132 | Buduje osobny, nieautoryzowany `ccxt.okx({...})` i woła `await public_ex.load_markets()` — realne zapytanie HTTP, bez żadnego guarda na credentials | `None` |
| `close()` | ~149 | `await self.exchange.close()` — zamyka realną sesję ccxt (istnieje zawsze, bo `__init__` zawsze tworzy `self.exchange`) | `None` |
| `verify_symbol_with_okx(symbol)` | ~154 | Do 6 kandydatów zapisu symbolu × (próba autoryzowana + fallback publiczny) = do ~12 realnych zapytań `fetch_ticker` | `Dict[str, Any]`: `{"symbol", "okx_verified", "autotrade_permitted", "okx_status", "last_price", "reason"}` |
| `fetch_candles(symbol, timeframe="1m", limit=100)` | ~225 | `self.exchange.fetch_ohlcv(...)` lub fallback publiczny `ccxt.okx({...})` | `List[Candle]` (Candle = pydantic model z `analytics.py`: `timestamp, open, high, low, close, volume`) |
| `discover_eea_xperp_symbol(symbol="BTC")` | ~263 | Realne `publicGetPublicInstruments(...)` gdy markets nie są załadowane (nigdy nie są gwarantowane, bo `initialize()` nie zawsze jest wołane przed tym) | `Optional[str]` |
| `execute_order(symbol, side, amount, stop_loss_pct, leverage, price)` | ~449 | Woła `discover_eea_xperp_symbol(...)` **bezwarunkowo** na linii ~467, ZANIM jakikolwiek check `has_credentials` — to jest realne wywołanie sieciowe nawet w pełnym mock mode | `Dict[str, Any]` |
| `reduce_position(symbol, side, reduction_pct, reason)` | ~770 | To samo bezwarunkowe wywołanie `discover_eea_xperp_symbol` na linii ~798 | `Dict[str, Any]` |

### 2b. Metody JUŻ bezpieczne — NIE wymagają patcha

`fetch_balance()` (~599), `fetch_positions(symbols)` (~688), `set_leverage(leverage, symbol)`
(~698), `cancel_all_orders(symbol)` (~723), `set_hedge_mode(symbol)` (~582) — każda z nich
jako **pierwszą instrukcję** sprawdza `self.has_credentials` / `self.exchange is None` i
zwraca bezpieczną wartość mock, zanim cokolwiek dotknie sieci. W środowisku testowym
(brak zmiennych `OKX_*`) `has_credentials` jest zawsze `False`, więc te ścieżki są już
dziś w 100% bez-sieciowe. **Nie patchuj ich** — dodałoby to tylko martwy kod do
utrzymania bez żadnej korzyści (uzasadnienie w §5).

Metody synchroniczne, czysto lokalne (bez I/O): `calculate_order_amount`,
`min_notional_for`, `reduction_amount`, `_fit_contracts` — nie dotyczy ich ten plan.

---

## 3. Kluczowy insight architektoniczny — DLACZEGO patch musi być na poziomie klasy

`OKXExecutor()` jest instancjonowany w wielu, niezależnych od siebie miejscach:

**A. Współdzielony singleton** — `src/mms4tk/api.py` (linia ~53): `executor = OKXExecutor()`
tworzony **przy imporcie modułu**, potem wstrzykiwany do `daemon = MMS4Daemon(executor=executor, ...)`
i `trade_sync_worker = OKXTradeSyncWorker(executor=executor, ...)`. Pliki testowe
dotykające tego singletona (przez `TestClient(app)` lub bezpośredni import
`executor`/`daemon` z `mms4tk.api`): `test_adaptive_tactics.py`, `test_api_auth.py`,
`test_hermes_integration.py`, `test_hermes_network.py`, `test_hermes_webhook.py`,
`test_integration.py`, `test_multi_symbol.py` (poza jednym testem).

**B. Świeże, niezależne instancje** budowane wprost przez kod testowy, z pominięciem
singletona z `api.py`:
- `test_daemon.py` — 3 miejsca, realny `OKXExecutor()` wstrzykiwany do świeżego
  `MMS4Daemon(executor=executor, ...)`.
- `test_multi_symbol.py::test_multi_symbol_daemon_cycle` — `MMS4Daemon(symbol="BTC/USDC")`
  **bez** `executor=` → `MMS4Daemon.__init__` (daemon.py: `self.executor = executor or OKXExecutor()`)
  buduje świeży, realny executor sam.
- `test_portfolio_margin_guard.py` — 5× `MMS4Daemon()` z zerem argumentów, każdy buduje
  własny realny executor (metody testowane tam go dziś nie wołają, ale instancja i tak
  istnieje).

**Konsekwencja**: patch na poziomie **instancji** (`monkeypatch.setattr(some_instance, "fetch_candles", fn)`
— dokładnie to, co dziś robią lokalne fixture'y w `test_hermes_network.py` i
`test_multi_symbol.py`) chroni WYŁĄCZNIE tę jedną instancję. Nie chroni żadnej z
instancji z grupy B, bo są tworzone PO fixture i nigdy nie dostają patcha.

**Rozwiązanie**: patch na poziomie **klasy** — `monkeypatch.setattr(OKXExecutor, "fetch_candles", fn)`,
patchując `mms4tk.okx_executor.OKXExecutor` bezpośrednio. To działa dla OBU grup:
- Dla singletona z grupy A: mimo że instancja już istnieje (stworzona przy imporcie
  `api.py`, zanim jakikolwiek fixture pytest zdąży się uruchomić), Python najpierw
  szuka atrybutu na instancji, a gdy go nie znajdzie, spada do klasy — więc patch klasy
  zaaplikowany PO utworzeniu instancji nadal działa.
  Uwaga: **nie chodzi tu o "keszowanie" instancji** — to jest zwykły, standardowy
  mechanizm wyszukiwania atrybutów Pythona (`instance.__dict__` → `type(instance).__dict__`),
  nic specjalnego do zaimplementowania, tylko trzeba wiedzieć, że patch klasy załapie
  też stare instancje.
- Dla świeżych instancji z grupy B: automatycznie dziedziczą patch klasy, bez względu
  na to, kiedy i gdzie zostały utworzone.

---

## 4. KRYTYCZNA pułapka implementacyjna — `self` w fake'ach

Ponieważ patch jest na klasie, a nie na instancji, przypisana funkcja jest dostępna
przez protokół deskryptora Pythona — przy wywołaniu `some_instance.fetch_candles(...)`
Python automatycznie zwiąże `some_instance` jako pierwszy argument (`self`). To znaczy:

**Fake przypisany na KLASIE musi mieć `self` jako pierwszy parametr:**
```python
async def _fake_fetch_candles(self, symbol=None, timeframe="1m", limit=100):
    ...
```

To jest ODWROTNA konwencja niż istniejące fake'y instancyjne w `test_hermes_network.py`
(które poprawnie POMIJAJĄ `self`, bo podmieniają atrybut konkretnej instancji
bezpośrednio, nie przechodząc przez klasę):
```python
async def _noop(*args, **kwargs):   # instancyjny — bez self, bo to inny mechanizm
    return None
monkeypatch.setattr(shared_executor, "initialize", _noop)
```

Pomylenie tych dwóch wzorców to najłatwiejszy sposób na cichy błąd — kod się
zaimportuje bez problemu, testy mogą nawet przejść, jeśli dana metoda nigdy nie zostanie
faktycznie wywołana w danym teście, i dopiero pierwsze realne wywołanie rzuci
`TypeError: _fake_fetch_candles() takes 3 positional arguments but 4 were given` (albo
`missing 1 required positional argument`, zależnie od kierunku pomyłki). **Sprawdź to
explicite podczas implementacji** — uruchom pełny suite, nie tylko pojedynczy plik.

---

## 5. Czy patchować też metody z §2b (`fetch_balance` itd.)?

**Nie.** Uzasadnienie:
- Wszystkie pięć już sprawdza `has_credentials`/`self.exchange is None` jako pierwszą
  instrukcję i zwraca bezpieczną wartość PRZED dotknięciem sieci. W środowisku
  testowym (brak `OKX_*` w env) to jest już dziś w 100% bez-sieciowe — nie ma nic do
  naprawienia.
- Patchowanie ich mimo to byłoby czystą nadmiarowością: kolejne miejsce do
  synchronizowania z kształtem prawdziwej metody, zero korzyści behawioralnej ani
  wydajnościowej.
- Jedyny scenariusz, w którym patch miałby znaczenie — test świadomie ustawiający
  fałszywe klucze OKX przez zmienne środowiskowe, żeby przetestować gałąź "z
  credentials" jednej z tych metod — dziś nie istnieje nigdzie w suite. Gdyby taki
  test kiedyś powstał, powinien patchować TĘ JEDNĄ metodę lokalnie
  (`monkeypatch.setattr(OKXExecutor, "fetch_balance", ...)`, ten sam wzorzec co niżej)
  albo użyć markera z §7 — a nie być cicho zablokowany przez globalny fixture, co
  zniweczyłoby sens takiego testu.

---

## 6. Dokładna treść `tests/conftest.py` (nowy plik)

```python
"""
Globalne fixture'y pytest dla test suite MMS4.

Polityka na poziomie całej sesji: żaden test nigdy nie robi realnego wywołania
sieciowego do REST API OKX. OKXExecutor.__init__ zawsze tworzy prawdziwego klienta
ccxt.okx(...) niezależnie od tego, czy skonfigurowano klucze OKX, a kilka jego metod
robi realne wywołania sieciowe nawet w tym "Mock Mode" (patrz docstringi w
okx_executor.py). W środowisku bez niezawodnego dostępu do sieci te wywołania wieszają
się do wyczerpania własnego łańcucha retry/timeout ccxt, blokując kolejne testy jeden
po drugim.

Ten fixture patchuje KLASĘ mms4tk.okx_executor.OKXExecutor, nie żadną konkretną
instancję, ponieważ OKXExecutor() jest budowany w wielu niezależnych miejscach w
całym suite: współdzielony singleton na poziomie modułu w api.py (zbudowany, zanim
ten fixture zdąży się uruchomić) ORAZ świeże, budowane wprost instancje w
poszczególnych plikach testowych (test_daemon.py, test_multi_symbol.py, itd.).
Patchowanie tylko jednej instancji (wzorzec używany wcześniej lokalnie w
test_hermes_network.py i test_multi_symbol.py) nie chroni pozostałych. Patch klasy
chroni wszystkie — również instancje utworzone PRZED uruchomieniem tego fixture'a,
bo wyszukiwanie atrybutu na instancji spada do klasy, gdy nie ma nadpisania na
poziomie instancji.
"""
import time
from typing import Any, Dict, List, Optional

import pytest

from mms4tk.okx_executor import OKXExecutor, _generate_mock_candles


# ---------------------------------------------------------------------------
# Fake'i. Każdy jest przypisywany na KLASIE, więc każdy MUSI przyjmować `self`
# jako pierwszy parametr — Python wiąże go automatycznie przez protokół
# deskryptora przy wywołaniu przez instancję (np. `some_executor.fetch_candles(...)`).
# Pominięcie `self` tutaj rzuci `TypeError` dopiero przy pierwszym realnym
# wywołaniu danej metody w jakimś teście.
# ---------------------------------------------------------------------------

async def _fake_initialize(self) -> None:
    return None


async def _fake_close(self) -> None:
    return None


async def _fake_verify_symbol_with_okx(self, symbol: str, *args, **kwargs) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "okx_verified": True,
        "autotrade_permitted": True,
        "okx_status": "AUTOTRADE_READY",
        "last_price": 100.0,
        "reason": "stubbed by tests/conftest.py — no live OKX call",
    }


async def _fake_fetch_candles(
    self,
    symbol: Optional[str] = None,
    timeframe: str = "1m",
    limit: int = 100,
) -> List[Any]:
    # Reużywa istniejącego generatora z okx_executor.py — to dokładnie to, co
    # prawdziwa metoda i tak zwraca dziś w gałęzi except po nieudanym realnym
    # zapytaniu, więc testy oczekujące "jakichś" sensownych świec zachowują ten
    # sam efektywny kształt danych, tylko bez realnego (i wolnego/zawodnego)
    # zapytania sieciowego przed nim.
    return _generate_mock_candles(count=limit)


async def _fake_discover_eea_xperp_symbol(self, symbol: str = "BTC") -> Optional[str]:
    return None


async def _fake_execute_order(
    self,
    symbol: str,
    side: str,
    amount: float,
    stop_loss_pct: float = 0.02,
    leverage: float = 1.0,
    price: Optional[float] = None,
) -> Dict[str, Any]:
    # Ten sam kształt co wbudowana gałąź "brak credentials" w prawdziwej metodzie
    # (okx_executor.py, execute_order, ~linia 519-530) — te same klucze, ten sam
    # status "executed"/flaga mock — więc callerzy w daemon.py czytający
    # .get("id")/.get("status") zachowują się identycznie jak dziś bez kluczy OKX.
    return {
        "id": f"mock_order_test_{int(time.time() * 1000)}",
        "symbol": symbol,
        "side": side.lower(),
        "amount": amount,
        "leverage": leverage,
        "stop_loss_pct": stop_loss_pct,
        "status": "executed",
        "mock": True,
    }


async def _fake_reduce_position(
    self,
    symbol: str,
    side: str,
    reduction_pct: float = 0.50,
    reason: str = "STALE_SPLIT",
) -> Dict[str, Any]:
    # Odzwierciedla to, co prawdziwa metoda i tak zwraca dziś w tym środowisku:
    # fetch_positions() jest zabezpieczone credentials-gate'em i już dziś zwraca
    # [] bez kluczy OKX, więc matching_contracts zawsze wychodzi 0, a prawdziwy
    # reduce_position spada do dokładnie tego samego wyniku "no_position"
    # (okx_executor.py, ~linia 824). Jedyne, co ten fake realnie usuwa, to jedno
    # bezwarunkowe wywołanie sieciowe discover_eea_xperp_symbol() poprzedzające
    # tę logikę. daemon.py::_close_succeeded() traktuje "no_position" jako False
    # — poprawnie, bo żadna pozycja nie została faktycznie zamknięta.
    return {"status": "no_position", "symbol": symbol, "side": side.lower()}


_PATCHED_METHODS = {
    "initialize": _fake_initialize,
    "close": _fake_close,
    "verify_symbol_with_okx": _fake_verify_symbol_with_okx,
    "fetch_candles": _fake_fetch_candles,
    "discover_eea_xperp_symbol": _fake_discover_eea_xperp_symbol,
    "execute_order": _fake_execute_order,
    "reduce_position": _fake_reduce_position,
}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_real_okx_network: wyłącza dla tego testu autouse'owy fixture "
        "_no_real_okx_network (tests/conftest.py). Używać WYŁĄCZNIE dla świadomego, "
        "prawdziwego testu integracyjnego na żywo — taki test będzie robił realne "
        "wywołania sieciowe do OKX i może wisieć/failować w środowiskach bez sieci.",
    )


@pytest.fixture(autouse=True)
def _no_real_okx_network(request, monkeypatch):
    """
    Domyślne dla całej sesji: blokuje każdą instancję OKXExecutor przed realnymi
    wywołaniami sieciowymi do OKX, patchując mms4tk.okx_executor.OKXExecutor na
    poziomie klasy. Stosuje się automatycznie do każdego testu.

    Escape hatch: oznacz test `@pytest.mark.allow_real_okx_network`, żeby pominąć
    to patchowanie dla świadomego testu integracyjnego na żywo.
    """
    if request.node.get_closest_marker("allow_real_okx_network"):
        return
    for name, fake in _PATCHED_METHODS.items():
        monkeypatch.setattr(OKXExecutor, name, fake)
```

**Uzasadnienie scope fixture'a (function-scoped `autouse=True`, standardowy `monkeypatch`,
NIE session-scoped):**
- `monkeypatch` jest z definicji function-scoped w pytest; próba użycia go z
  fixture'a session-scoped skończy się `ScopeMismatch`.
- Patch na poziomie klasy działa tak samo skutecznie bez względu na czas życia
  samego fixture'a — cel (`OKXExecutor` jako klasa) jest jeden, współdzielony przez
  wszystkie instancje, niezależnie od tego, czy patch trwa jeden test czy całą sesję.
- Automatyczny revert `monkeypatch` po każdym teście jest tu POŻĄDANY: gwarantuje
  zero przecieku między testami — test, który sam nadpisze jedną z tych metod
  lokalnie na potrzeby konkretnego scenariusza, dostaje czysty powrót do fake'a z
  conftest po swoim zakończeniu, nie do prawdziwej metody sieciowej.
- Ręczne zarządzanie przez `unittest.mock.patch.object(...).start()/.stop()` w
  fixture session-scoped byłoby więcej kodu bez żadnej korzyści.

---

## 7. Dokładne usunięcia w istniejących plikach

### 7a. `tests/test_hermes_network.py`

Obecny fixture (całość, do znalezienia przez `grep -n "def client" tests/test_hermes_network.py`):
```python
@pytest.fixture
def client(monkeypatch):
    """..."""
    from mms4tk.api import executor as shared_executor

    async def _noop(*args, **kwargs):
        return None

    async def _empty_list(*args, **kwargs):
        return []

    monkeypatch.setattr(shared_executor, "initialize", _noop)
    monkeypatch.setattr(shared_executor, "close", _noop)
    monkeypatch.setattr(shared_executor, "fetch_candles", _empty_list)
    monkeypatch.setattr(shared_executor, "fetch_positions", _empty_list)

    with TestClient(app) as test_client:
        yield test_client
```

**Usuń:** import `from mms4tk.api import executor as shared_executor`, definicje
`_noop`/`_empty_list`, wszystkie 4 linie `monkeypatch.setattr(shared_executor, ...)`.
W pełni zastąpione przez globalny fixture na poziomie klasy.

**ZACHOWAJ bez zmian:** `with TestClient(app) as test_client: yield test_client`.
To jest kluczowe — ta forma kontekstowa uruchamia prawdziwy lifespan ASGI (startuje
tło daemona), co jest istotą tego, co ten plik testuje. Nowy globalny fixture NIE
zastępuje tej części, tylko usuwa potrzebę ręcznego stubowania sieci wokół niej.

Wynik po edycji:
```python
@pytest.fixture
def client():
    """
    FastAPI TestClient fixture dla testów tras Hermes network.

    Użycie TestClient jako context managera uruchamia prawdziwy lifespan ASGI, co
    startuje pętlę tła współdzielonego daemona na współdzielonym singletonie
    `executor`. Realne wywołania sieciowe OKX z tego singletona są zablokowane
    globalnie przez autouse fixture _no_real_okx_network w tests/conftest.py.
    """
    with TestClient(app) as test_client:
        yield test_client
```
(Jeśli po usunięciu ciała fixture'a parametr `monkeypatch` przestaje być używany —
usuń go z sygnatury.)

Reszta pliku (wszystkie funkcje `test_*`, stałe modułowe `API_KEY`/`AUTH_HEADERS`,
`import os`) — bez zmian.

### 7b. `tests/test_multi_symbol.py`

Obecny fixture:
```python
@pytest.fixture(autouse=True)
def _no_real_okx_verification(monkeypatch):
    """..."""
    async def _fake_verify(symbol: str, *args, **kwargs):
        return {
            "symbol": symbol,
            "okx_verified": True,
            "autotrade_permitted": True,
            "okx_status": "AUTOTRADE_READY",
            "last_price": 100.0,
            "reason": "stubbed for test — no live OKX call",
        }

    monkeypatch.setattr(executor, "verify_symbol_with_okx", _fake_verify)
```

**Usuń cały ten fixture.** Globalny `_fake_verify_symbol_with_okx` w conftest.py ma
identyczny kształt zwrotu, zaaplikowany na poziomie klasy — zero zmiany zachowania dla
`test_get_trading_symbols_api` i `test_add_remove_trading_symbol_api` (jedyne dwa
testy w tym pliku, które pośrednio wołają `verify_symbol_with_okx` przez
`/api/v1/symbols` i `/api/v1/symbols/add`).

**Przytnij import:** `from mms4tk.api import app, configured_symbols_store, executor`
→ `from mms4tk.api import app, configured_symbols_store` (po usunięciu fixture'a
`executor` nie jest już nigdzie w pliku używany).

**Nic więcej w tym pliku nie wymaga zmiany.** W szczególności `test_multi_symbol_daemon_cycle`
(buduje `MMS4Daemon(symbol="BTC/USDC")` bez `executor=`) to dokładnie przypadek z §3
grupa B, którego stary fixture nigdy nie chronił — nowy globalny fixture chroni go
automatycznie, bez żadnej dodatkowej zmiany w tym teście.

---

## 8. Czego NIE ruszać (explicit exclusions)

Te wzorce istnieją w innym celu — dostarczają deterministyczne, domenowo-specyficzne
dane testowe (konkretne kształty świec, konkretne wyniki zleceń), nie tylko blokują
sieć. Usunięcie ich "przy okazji" zepsułoby konkretne asercje tych testów:

- `sizing_executor()` w `tests/test_audit_remediation.py` (`MagicMock(spec=OKXExecutor)`
  z konkretnymi `flat_candles(60)` itp.) — zostaw bez zmian.
- Wszystkie override'y `AsyncMock` w `tests/test_take_profit.py` — zostaw bez zmian.
- `MagicMock(spec=OKXExecutor)` / plain `MagicMock()` w `tests/test_position_sizing.py`,
  `tests/test_max_margin_allocation.py`, `tests/test_trade_sync.py` — zostaw bez zmian.
- Pliki `tests/audit/audit_*.py` — to samodzielne skrypty (`if __name__ == "__main__"`),
  **nie są w ogóle zbierane przez pytest** (potwierdzone: `pytest --collect-only -q`
  daje 143 testy, żaden z `tests/audit/`). Nie dotyczy tego planu, nie ruszać.

---

## 9. Sekwencja wykonania (kolejność, w jakiej wykonawca powinien to robić)

1. Stwórz `tests/conftest.py` z treścią z §6.
2. Uruchom cały suite (`python -m pytest -q --durations=20`, jeszcze BEZ usuwania
   starych lokalnych fixture'ów z §7 — ich nadmiarowe `monkeypatch.setattr` na już
   spatchowanym atrybucie jest nieszkodliwe, po prostu patchuje ponownie tą samą
   funkcją niżej w łańcuchu). Potwierdź, że wszystko przechodzi i jest szybkie.
3. Zastosuj usunięcia z §7a i §7b.
4. Uruchom suite ponownie — potwierdź identyczny wynik (te same 143 testy, ten sam
   status pass/fail) jak w kroku 2.
5. Dodaj smoke-testy z §10b (dowód, że fixture faktycznie łapie zarówno singleton,
   jak i świeżą instancję).
6. Uruchom finalny bounded regression-guard z §10c i potwierdź exit code 0.

---

## 10. Weryfikacja end-to-end

### 10a. Wszystkie 143 istniejące testy przechodzą identycznie

```bash
# przed zmianą (dla porównania — jeśli środowisko ma zawodną sieć, oczekuj
# zawieszenia/timeoutu na plikach z §3, to jest properties problem który ten plan
# naprawia, nie błąd w weryfikacji)
python -m pytest --collect-only -q | tail -1     # zanotuj: "143 tests collected"

# po zmianie
timeout 60 python -m pytest -q --durations=20
python -m pytest --collect-only -q | tail -1     # musi dalej być "143 tests collected"
```
Dowód braku regresji w zbieraniu testów: porównaj też same nazwy (nie tylko liczbę) —
`pytest --collect-only -q` przed i po, `diff`, powinno wyjść puste poza ew. kolejnością.

### 10b. Fixture realnie łapie ZARÓWNO singleton, JAK I świeżą instancję

Dodaj (rekomendacja: nowy plik `tests/test_conftest_network_guard.py` — stały,
wartościowy regression-guard, warto zostawić na stałe):

```python
import asyncio
import time

from mms4tk.okx_executor import OKXExecutor
from mms4tk.api import executor as shared_executor


def test_fresh_executor_is_patched_and_returns_instantly():
    """Świeży OKXExecutor(), nigdy nie dotknięty żadnym fixture'em specyficznym dla
    pliku, musi mimo to być zablokowany przed realną siecią przez globalny fixture
    z conftest.py."""
    ex = OKXExecutor()
    start = time.monotonic()
    asyncio.run(ex.initialize())
    candles = asyncio.run(ex.fetch_candles("BTC/USDC", limit=10))
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, "fetch_candles/initialize dotknęło prawdziwej sieci"
    assert len(candles) == 10


def test_shared_singleton_is_also_patched():
    """Singleton executor zbudowany przez mms4tk.api przy imporcie modułu (a więc
    ZANIM jakikolwiek fixture pytest zdążył się uruchomić) musi być objęty tym samym
    patchem na poziomie klasy."""
    start = time.monotonic()
    result = asyncio.run(shared_executor.verify_symbol_with_okx("BTC/USDC"))
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert result["okx_verified"] is True
    assert result["reason"].startswith("stubbed")
```

### 10c. Brak zawieszeń, spadek czasu wykonania

```bash
timeout 60 python -m pytest -q
echo "exit code: $?"   # 0 = OK w budżecie czasowym; 124 = timeout, regresja
```
60s to hojny sufit — po usunięciu realnej sieci 143 testy (bez sporych I/O-bound
opóźnień poza celowym `asyncio.sleep(0.7)` w `test_daemon_lifecycle`) powinny kończyć
się w pojedynczych sekundach; 60s zostawia duży margines dla wolniejszego sprzętu CI,
wciąż łapiąc odtworzenie zawieszenia jako twardy fail (kod 124), a nie ciche wiszenie
w nieskończoność.

Jeśli w repo istnieje CI (`.github/workflows/*.yml`) — sprawdź i rozważ dodanie tego
samego `timeout`-owanego wywołania jako osobny krok/guard, żeby przyszła regresja
(reintrodukcja realnego wywołania sieciowego gdziekolwiek w `OKXExecutor`) failowała
build natychmiast, zamiast wieszać CI w nieskończoność.

---

## 11. Kryteria akceptacji (checklist dla wykonawcy)

- [ ] `tests/conftest.py` istnieje z treścią z §6 (7 fake'i + marker + fixture)
- [ ] `tests/test_hermes_network.py` — usunięte zbędne stubowanie, zachowany
      `with TestClient(app) as test_client`
- [ ] `tests/test_multi_symbol.py` — usunięty cały `_no_real_okx_verification` +
      przycięty import
- [ ] Nic z §8 nie zostało ruszone
- [ ] `pytest --collect-only -q` pokazuje dalej 143 testy, te same nazwy
- [ ] `timeout 60 python -m pytest -q` kończy się exit code 0
- [ ] Dodane 2 smoke-testy z §10b, oba przechodzą
- [ ] Atomowe commity (osobno: nowy conftest.py; osobno: usunięcia w obu plikach
      testowych; osobno: nowe smoke-testy) — zgodnie z CAVEMAN STANDARD z `CLAUDE.md`
- [ ] Handshake JSON wygenerowany (`scripts/generate-handshake.py`)
- [ ] QA Gate: `py_compile` PASS, `validate-handshakes.py` PASS
- [ ] PR otwarty do `main`, **niezmergowany** bez wyraźnej zgody użytkownika
