Raport Taktyczno-Techniczny: Zarządzanie Pozycjami Stagnacyjnymi („Kiszącymi się”) w Strategii MMS4
1. Wstęp i Diagnoza Problemu: Natura Marazmu Rynkowego
W strategii Mean-Reversion (MMS4) wejście w pozycję bazową opiera się na założeniu, że cena reaguje na statystycznie skrajne odchylenia zmienności mierzone pochodnymi wskaźnika ATR (np. TMA, Nadaraya-Watson Envelope, Bollinger Bands) [71, 76, 1111]. Zgodnie z tą logiką, prawidłowy setup kontrariański powinien wygenerować dynamiczną reakcję ceny (odbicie) niemal natychmiast (widoczną już w pierwszych świecach nowego interwału) [93, 1131, 1132].

Sytuacja, w której cena po wejściu w pozycję „kisi się w miejscu” (przechodzi w trend boczny o znikomej zmienności przy wydłużonym czasie ekspozycji), oznacza anomalię strukturalną. Sygnalizuje to, że rynek utracił dynamikę Mean-Reversion i wszedł w fazę „gry na timing” lub budowy tzw. „płytkiej strefy” [120, 121].

2. Mechanika Rynkowa Konsolidacji: Pułapki Smart Money
A. Budowa Płytkiej Strefy (Dystrybucja pod Spadki lub Akumulacja pod Wybicie)
Płytka strefa charakteryzuje się minimalnym zasięgiem ruchu ceny w stosunku do czasu trwania konsolidacji [121].

Mechanizm samoregulacji płynności: Jeżeli cena nie realizuje szybko TP na przeciwległej wstędze, oznacza to, że na rynku nie ma jeszcze wystarczającej płynności do wykonania większego ruchu [121, 122].
Znoszenie spóźnionych: Im dłużej rynek utrzymuje pozory słabości lub oporu w wąskim zakresie, tym więcej traderów detalicznych otwiera pozycje w tym samym kierunku, budując ogromną pulę zleceń ochronnych (Stop Loss) tuż nad lub pod tą strefą [121, 122].
Gwałtowny sweep: Faza ta kończy się zazwyczaj dynamicznym, manipulacyjnym wyjściem (ang. liquidity sweep), którego celem jest oczyszczenie orderbooka ze spóźnionych i ciasnych zleceń SL przed właściwym ruchem [108, 122, 123].
B. Zjawisko Złudnego Wspierania
Często po impulsie następuje powrót ceny, ale wsparcie aktywuje się nieco wyżej, dając iluzoryczne wrażenie siły popytu [107, 108]. W rzeczywistości jest to struktura dystrybucyjna służąca Smart Money do przerzucania „gorących kartofelków” na detalistów przed gwałtownym zrzutem [108, 109, 118, 119].

3. Rekomendacje Taktyczne i Zasady Ewakuacji
Aby uniknąć „tradingu modlitewnego” (oczekiwania na zysk bez technicznych przesłanek) oraz degradacji kapitału przez koszty finansowania (Funding Rates pobierane co 8 godzin na OKX X-Perps), wprowadza się cztery żelazne zasady zarządzania pozycjami stagnacyjnymi [114, 1114, 1125, 1131]:

I. Zasada 50% Korekty Wzniesienia (Negacja Impulsu)
Jest to podstawowy taktyczny punkt kontrolny [103].

Jeżeli po otwarciu pozycji (np. SHORT ze wsparcia TMA) cena próbuje odbijać i to odbicie przekracza 50% całego wcześniejszego impulsu, oznacza to, że strona inicjująca ruch traci kontrolę [103, 104, 105].
Działanie: Bezwzględna akceptacja SL lub manualne cięcie pozycji tuż powyżej poziomu 50% korekty [105]. Nie bronimy pozycji za wszelką cenę [105].
LONG SETUP NEGATION:
  Poziom Wejścia (Wsparcie TMA) -------------[Entry x1]
  Poziom 50% Impulsu Wzrostowego -----------[Granica Decyzyjna - Cut/Exit]
  Poziom Pierwotnego SL 2% ------------------[Ostateczna Zapora]
II. Ucieczka przy Pierwszej Okazji (Break-Even Escape)
Jeśli rynek po okresie kiszenia powraca w okolice Twojego poziomu wejścia, dając możliwość zamknięcia pozycji na zero (Break-Even) lub z minimalną stratą – masz rzemieślniczy obowiązek ewakuacji [1114, 1132]. Zmienność i struktura zostały zaburzone, a pierwotny setup stracił statystyczną przewagę [1114, 1121].

III. Dekompresja Czasu (Skalowanie i Splitowanie)
Zgodnie z filozofią dekompresji czasu, mniejsza wielkość pozycji pozwala przetrwać okresy marazmu i niekorzystnych trendów [1122, 1123]. W przypadku kiszenia pozycji stosujemy funkcję Split (dostępną na pulpicie jako przycisk cl s):

Zabezpieczamy 50% pozycji na drobnej stracie/zysku (np. w zakresie 0.5% rozjazdu orderbooka).
Pozostałe 50% pozycji zostaje w grze z pierwotnym, sztywnym SL [1115].
IV. Time-Based Exit (Ewakuacja Czasowa)
Jeśli cena przez określoną liczbę świec interwału roboczego (np. 12 świec na M15 = 3 godziny) nie opuściła strefy wejścia o odległość większą niż 0.5% ATR, algorytm lub trader powinien zainicjować automatyczne zamknięcie pozycji z rynku (Market Order) lub poprzez pasywne zlecenie Limit na Break-Even.

4. Implementacja Techniczna w mms4tk (Python / CCXT)
Poniższy kod w Pythonie implementuje moduł MMS4InactivityManager do automatycznego monitorowania czasu trwania pozycji i jej parametrów zmienności. Może zostać bezpośrednio zintegrowany z Twoim silnikiem antigravity_core [27].

import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("antigravity_core")

class MMS4InactivityManager:
    def __init__(self, max_stale_candles: int = 12, vol_compression_threshold: float = 0.5):
        """
        :param max_stale_candles: Maksymalna liczba świec stagnacji przed ewakuacją.
        :param vol_compression_threshold: Próg kompresji zmienności (stosunek lokalnego zakresu do ATR).
        """
        self.max_stale_candles = max_stale_candles
        self.vol_compression_threshold = vol_compression_threshold
        self.position_trackers: Dict[str, Dict[str, Any]] = {}

    def track_position(self, inst_id: str, entry_price: float, entry_time: float, side: str, atr: float):
        """Inicjuje śledzenie nowo otwartej pozycji bazowej x1."""
        self.position_trackers[inst_id] = {
            "entry_price": entry_price,
            "entry_time": entry_time,
            "side": side,
            "atr": atr,
            "candle_count": 0,
            "max_observed_price": entry_price if side == "long" else float('-inf'),
            "min_observed_price": entry_price if side == "short" else float('inf'),
            "is_split": False
        }
        logger.info(f"Rozpoczęto śledzenie pozycji dla {inst_id}. Cena wejścia: {entry_price}")

    def update_and_check(self, inst_id: str, current_price: float, current_atr: float, new_candle_closed: bool) -> Optional[str]:
        """
        Aktualizuje statystyki pozycji na zamknięciu każdej świecy interwału wykonawczego.
        Zwraca akcję do wykonania: None, 'SPLIT' lub 'FORCE_CLOSE_BE'.
        """
        tracker = self.position_trackers.get(inst_id)
        if not tracker:
            return None

        # Aktualizacja ekstremów ceny w trakcie trwania pozycji
        if tracker["side"] == "long":
            tracker["max_observed_price"] = max(tracker["max_observed_price"], current_price)
        else:
            tracker["min_observed_price"] = min(tracker["min_observed_price"], current_price)

        if new_candle_closed:
            tracker["candle_count"] += 1
            logger.info(f"Pozycja {inst_id} trwa już {tracker['candle_count']} świec.")

        # 1. Test 50% Zniesienia Impulsu (Zasada I)
        if tracker["side"] == "long":
            local_impulse = tracker["max_observed_price"] - tracker["entry_price"]
            if local_impulse > 0.3 * tracker["atr"]: # Jeśli zaistniał zauważalny ruch
                negation_level = tracker["max_observed_price"] - (local_impulse * 0.5)
                if current_price < negation_level:
                    logger.warning(f"Pozycja {inst_id} naruszyła 50% wzniesienia impulsu. Sygnał negacji.")
                    return "SPLIT" if not tracker["is_split"] else "FORCE_CLOSE"
        else:
            local_impulse = tracker["entry_price"] - tracker["min_observed_price"]
            if local_impulse > 0.3 * tracker["atr"]:
                negation_level = tracker["min_observed_price"] + (local_impulse * 0.5)
                if current_price > negation_level:
                    logger.warning(f"Pozycja {inst_id} naruszyła 50% wzniesienia impulsu. Sygnał negacji.")
                    return "SPLIT" if not tracker["is_split"] else "FORCE_CLOSE"

        # 2. Test Stagnacji Czasowej (Time-Based Inactivity - Zasada IV)
        if tracker["candle_count"] >= self.max_stale_candles:
            price_deviation = abs(current_price - tracker["entry_price"])
            max_allowed_deviation = tracker["atr"] * self.vol_compression_threshold

            if price_deviation < max_allowed_deviation:
                logger.warning(f"Wykryto stagnację czasową dla {inst_id} ({tracker['candle_count']} świec bez zmienności).")
                if not tracker["is_split"]:
                    tracker["is_split"] = True
                    return "SPLIT"
                else:
                    return "FORCE_CLOSE_BE"

        return None

    def stop_tracking(self, inst_id: str):
        """Usuwa pozycję z rejestru monitorowania po jej zamknięciu."""
        if inst_id in self.position_trackers:
            del self.position_trackers[inst_id]
            logger.info(f"Zakończono śledzenie pozycji dla {inst_id}.")
5. Integracja z Pulpitem Tradera (Expert Advisor)
Twój manualno-automatyczny pulpit tradera (Expert Advisor) posiada dedykowane fizyczne kontrolery, które idealnie wspierają powyższą dyscyplinę zarządzania stagnacją:

Przycisk cl s (Close Session / Split):
Zastosowanie: Manualne wykonanie częściowego zamknięcia pozycji (Split 50%) w momencie, gdy bot zidentyfikuje wejście ceny w płytką strefę stagnacji.
Działanie: Połowa pozycji jest zamykana natychmiast po cenie rynkowej, a dla drugiej połowy Stop Loss zostaje dociągnięty do lokalnych knotów (redukując ryzyko do poziomu < 0.5%).
Przycisk BE (Break-Even):
Zastosowanie: Aktywacja po wyjściu ceny z fazy marazmu na odległość dającą bufor (np. 0.5% rozjazdu w orderbooku). Zabezpiecza pozycję przed nagłym powrotem i sweepem Smart Money.
Przycisk c/rev (Cut and Reverse):
Zastosowanie: W przypadku, gdy płytka strefa stagnacji zostanie rozbita gwałtownym ruchem wybijającym, który unieważnia strukturę Mean-Reversion i inicjuje silny trend przeciwny. Przycisk ten pozwala na natychmiastowe ucięcie strat i odwrócenie ekspozycji w kierunku nowego pędu rynkowego.
6. Podsumowanie i Zalecenia Implementacyjne
Krok	Działanie	Narzędzie w mms4tk	Uprawnienia na OKX
1	Uruchom pętlę monitorującą MMS4InactivityManager na interwale wykonawczym (M10-M30) [74, 87].	Moduł inactivity_manager.py	Klucz API z uprawnieniami trade [752].
2	W przypadku wykrycia stagnacji trwającej > 12 świec, wywołaj częściowe zamknięcie (Split 50%) poprzez POST /api/v5/trade/order [42, 827].	Metoda OKXEuropeClient.place_order	Tryb konta Hedging (umożliwia osobne bilety z niezależnymi SL) [1115].
3	Przesuń Stop Loss pozostałej części pozycji na poziom lokalnych knotów (SL/2) przy użyciu struktury attachAlgoOrds [42, 809].	Metoda OKXEuropeClient.amend_order	Serwer europejski eea.okx.com w celu zapobiegania błędom 50119 [1175].
4	W trybie manualnym (semi-auto) bezwzględnie korzystaj z przycisku cl s w panelu zamiast czekać na pełne uderzenie w SL [1114].	Fizyczny dashboard tradera	Połączenie WebSocket do subskrypcji pozycji w czasie rzeczywistym [1184].
Złota Zasada Rzemieślnika: Zaakceptowanie ucieczki z kiszącej się pozycji lub uderzenia w kontrolowany Stop Loss to nie porażka – to najwyższy wyraz dyscypliny i główne narzędzie ochrony kapitału, które uwalnia Twoje środki do kolejnych, dynamicznych i statystycznie bezpieczniejszych setupów [1114, 1131, 1150, 1152].