title: "MMS v4 Mean-Reversion Strategy - Pełna Specyfikacja i Baza Wiedzy RAG" author: "MastermindZX / Antigravity AI" version: "4.1 (Pełny zrzut danych dla AGENTS_OS v6.5 RAG)" type: "Knowledge Base / System Architecture / Market Mechanics / Psychology"
ROZDZIAŁ 1: BUDOWA POZYCJI BAZOWEJ I FILOZOFIA STRATEGII
1.1. Założenia Główne
MMS (Mastermind Mean-Reversion Strategy) zakłada, że rynek porusza się od jednej fazy dynamiki do kolejnej, a jego ruchy mieszczą się w dziennie wyznaczanych ekstremach ATR.
Instrumenty: BTC, XAU, NQ (wymagana wzmożona zmienność, instrumenty spotowe lub kontrakty P2P; unika się CFD z uwagi na konflikt interesów).
Styl: Daytrading / Intraday. Model reaktywny. Nie przewidujemy trendów. Reagujemy stricte zero-jedynkowo na zachowanie ceny w ekstremach.
Szum rynkowy: Priorytetem jest jakość setupów zamiast częstotliwości (overtradingu). Rozgrywanie bazuje głównie na interwałach M10-M30 w automacie, a H1 w manualu.
1.2. Fazy Ruchu Rynkowego wg Strategii
Faza początkowa (impuls inicjujący): Odreagowanie po spadkach, wejście wczesnych kupujących, zamykanie krótkich pozycji.
Faza wyhamowania (korekta i test równowagi): Spowolnienie, pierwsza próba podażowa sprawdzająca siłę popytu.
Faza końcowa (dynamika i euforia): Euforia, FOMO, likwidacja shortów. Cena dociera do górnych ekstremów ATR, gdzie rynek szuka oporu i przygotowuje się do zmiany kierunku.
1.3. Wskaźniki Bazowe
Podstawa: Triangular Moving Average (TMA), Nadaraya-Watson Envelope lub Bollinger Bands. Wykorzystują one pochodną ATR do wyznaczania wstęg (band).
Repainting/recalculating wskaźników jest nieistotny, ponieważ bazujemy na realnych wyliczeniach w czasie rzeczywistym.
1.4. Warunki Wejścia (Setupy x1)
Ryzyko zawsze jest wyrażane lewarem efektywnym, gdzie x1 = 1% ruchu ceny daje 1% zysku/straty na kapitale.
Wejście SHORT:
Cena dociera do górnego pasma wstęgi.
Czekamy na zamknięcie świecy na wybranym interwale.
Pojawia się pierwsza świeca reakcyjna w dół z rozpoczęcia nowego interwału.
Otwieramy pozycję SHORT x1. (Jeśli była pozycja LONG, górna wstęga jest jej TP).
Wejście LONG:
Cena dociera do dolnego pasma wstęgi.
Czekamy na zamknięcie świecy na wybranym interwale.
Pojawia się pierwsza świeca reakcyjna w górę z rozpoczęcia nowego interwału.
Otwieramy pozycję LONG x1. (Jeśli była pozycja SHORT, dolna wstęga jest jej TP).
Stop Loss (SL) Bazowy: Zawsze wynosi 2% ruchu ceny od pozycji bazowej. Nie stosuje się Trailing Stopów. SL chroni kapitał przy statystycznie nietrafionym odchyleniu.
ROZDZIAŁ 2: PROWADZENIE POZYCJI I FILTRY (KASKADOWOŚĆ)
System pozwala na odpowiedzialne zwiększanie ekspozycji, ale tylko wtedy, gdy rynek potwierdza kierunek.
2.1. Dokładka po pierwszej świecy "potwierdzającej" (HIGH RISK ADD)
Zasada: Po otwarciu pozycji bazowej odczekujemy na zamknięcie jednej pełnej świecy potwierdzającej reakcję.
Akcja: Wraz z nowym interwałem dokładamy pozycję x1 w tym samym kierunku.
Zabezpieczenie: SL dla dokładki ustawiamy na parze knotów w lokalnych ekstremach (nie może przekroczyć 1% ruchu ceny, łącznie max 3% ryzyka na całość).
Zasada anulacji: Jeśli SL z dokładki zostanie zrealizowany (wybity), NIE PONAWIAMY tej techniki. Pierwotny SL 2% bazy służy dalej jako bufor. Nie szukamy chaotycznie nowej półki cenowej.
2.2. Użycie filtra stochastycznego (STOCH.auto)
Po otwarciu bazy, korzystamy z oscylatora Stochastic (14, 3, 3 na interwale H1).
Przecięcie %K i %D poniżej 20% lub powyżej 80% to sygnał do kolejnej dokładki x1.
Stały SL dla tej pozycji wynosi 1%.
2.3. Netting vs Hedging
Netting: Pozycje w jednym kierunku sumują się, uśredniając cenę. Dokładka x1 uśrednia cenę o 50% i tworzy nominał x2. SL musi być zleceniem przeciwnym.
Hedging: Każda dokładka to osobny ticket z własnym SL. W momencie wybicia znika tylko dokładka, baza zostaje nietknięta. Strategia preferuje czystość Hedgingu.
ROZDZIAŁ 3: STOP LOSS, SEKWENCYJNOŚĆ I ASYMETRIA RYZYKA
Zarabianie nie polega na posiadaniu racji, lecz na umiejętności niestracenia kapitału w fazach trendowych.
3.1. Efektywność vs Nieefektywność
Fazy akumulacji/dystrybucji: Najbardziej sprzyjają MMS (rynek efektywny dla strategii). Tu rozgrywamy setupy na lewarze x1.
Fazy trendów: Rynek nieefektywny dla strategii MR. Cena nie respektuje zasięgów ATR.
3.2. Mechanizm Sekwencyjności
Ponieważ strategie kontrariańskie oddają zysk w trendach, wprowadzono twardy mechanizm redukcji ekspozycji:
Pierwszy pełny SL (-2%) na pozycji bazowej oznacza, że rynek prawdopodobnie przeszedł w trend.
System automatycznie obniża dźwignię do x0.1 (lub x0.01).
Kolejne sygnały w trendzie są zawierane jako pozycje "zwiadowcze" na znikomym ryzyku.
Dopiero pierwsza zyskowna transakcja (TP) zwiadowcy aktywuje z powrotem lewar x1.
Zablokowanie dokładek: W trybie zredukowanym (zmienna add=false) system nie dokłada pozycji po świecy potwierdzającej.
3.3. Asymetria ryzyka
Strata -10% na kapitale wymaga odrobienia 1:1.1. Wszystko powyżej -10% wymaga odrabiania wykładniczego. Dlatego ekspozycja jest drastycznie redukowana w trendach (płaskie odcinki krzywej kapitału w backtestach).
ROZDZIAŁ 4: ALGOTRADING, EA I PARAMETRYZACJA
Boty nie służą stworzeniu perpetuum mobile, lecz wyeliminowaniu błędów percepcyjnych (opóźnień, ułomności ludzkiej) i perfekcyjnej egzekucji punktów decyzyjnych 1/0.
4.1. Filosofia Algotradingu
Złożone algorytmy oparte na AI i Machine Learning często przeuczają się na historii (overfitting) i padają w niedeterministycznym rynku.
Trailing Stopy i systemy Break Even szkodzą systemom mean-reversion – wycinają statystycznie zdrowe obsunięcia. "Najlepsze są proste rozwiązania".
Zamiast przewidywać kierunek rynku, optymalizujemy przepływ kapitału i parametry.
4.2. Parametryzacja Brute-Force
Dla każdego interwału w backtestach badano setki ustawień. Przykład wskaźnika Bollinger Bands:
Okres (1-50 z krokiem 1)
Odchylenie (0.1 - 5 z krokiem 0.1)
SL (0.01 do 0.02)
Daje to ok. 27 500 kombinacji. Wynik testów to często sztuczne dopasowanie do historii (np. BTC kwiecień 2026: +22.61%).
4.3. Model Przepływu Kapitału (Złota Zasada 3 Dni)
Skuteczność ustawień nie jest stała, bo zmienność (wolumeny/płynność) ciągle pulsuje.
Rozwiązanie adaptacyjne: Uśrednianie najlepszych parametrów (okres, odchylenie, SL) na podstawie 3 ostatnich dni rynkowych.
Algorytm krocząco uaktualnia ustawienia wskaźników bazując na oknie 3-dniowym, testując głównie interwały M10, M15, M20, M30. Interwał M5 to zbyt duży szum.
ROZDZIAŁ 5: MECHANIKA RYNKU I MARKOWANIE INTERWAŁÓW
Rynek jest jak "mecz rugby" – pozoruje ruchy w jednym kierunku, aby zebrać płynność, po czym wykonuje ruch właściwy.
5.1. Reguła 50% Wzniesienia
Jeżeli korekta po impulsie schodzi poniżej 50% całego wzniesienia, rynek prawdopodobnie nie wróci do wzrostów. Powstaje formacja "kopuły", a poziom wsparcia to często złudzenie służące do likwidacji stop-lossów (gotowanie żaby). Nie bronimy pozycji, akceptujemy SL.
5.2. Płytka Strefa (Shallow Zone)
Niewielki zasięg ceny przy wydłużonym czasie. Rynek utrzymuje pozory braku dynamiki, aby "nazbierać" jak najwięcej shortów. Zebranie płytkiej strefy to wybuchowe wybicie stop lossów (short squeeze).
5.3. Zwiadowca i Faza Odbudowy
Krótki impuls wychodzący z konsolidacji. Jeśli po wybiciu świeca zawraca, ale rynek wraca nad wybicie i go utrzymuje, aktywny kapitał przejął rynek. Rezygnujemy wtedy z pozycji krótkich.
5.4. Markowanie Interwałów (Multi-timeframe)
Markowanie kontrariańskie (M10-H1): Wyładowanie momentum na końcu H1. Kolejna świeca M5 zaczyna interwał w dół (markuje), po czym zawraca, budując knot na H1. Tworzy się punkt wejścia MR.
Markowanie trendowe (M10-H1): Świeca M5 rozpoczynająca nową H1 spada, ale po chwili cena wybija jej szczyt (zazębienie). Znak do kontynuacji trendu.
Kampanie H4 (Smart Money): Agresywne budowanie stref. Pierwsza H4 spada bez knota (dystrybucja do kupującego retailu). Kampanie popytowe często prowadzone są na H3 (short squeeze).
Poniedziałek na Fejku (W1/D1): Pierwsza świeca tygodnia markuje agresywny kierunek. To test płynności, sonda ("sprawdzam"). Często we wtorek następuje negacja. Należy obserwować gaszenie Europy o 9:00 lub US Open o 15:30 w poniedziałek, by wyłapać ten fejk.
ROZDZIAŁ 6: CYKL ŻYCIA RYNKU (8 ETAPÓW)
Rynek to kaskadowa walka o płynność ("smart money" vs "retail").
Marazm, pierwsi kupujący: Brak zainteresowania tłumu, wolumen niski, ale aktywa trafiają do Smart Money.
Likwidacja (Shakeout): Gwałtowny spadek, wybicie wczesnych stop-lossów. Zmuszenie do sprzedaży (tani wykup).
Cichy wykup: Nieśmiałe wzrosty, cicha akumulacja dużego kapitału bez medialnego szumu.
Wejście Retail (Deficyt podaży): Dynamiczne wzrosty, pęka medialny hype i FOMO.
Ciche wyjścia SM (Budowa płynności): Wybijanie nowych ATH bez dynamiki, budowa stref do dystrybucji na górce.
Kupno "gorących kartofelków": Detal wierzy we wieczne wzrosty. Rozpędzony tłum.
Bull Trap: Ostatnia fala dająca fałszywą nadzieję, ostateczna wysyłka.
Panika: Lawina spadków (panic sell). Pękanie SL. Rezygnacja i marazm (powrót do pkt 1).
ROZDZIAŁ 7: PSYCHOLOGIA I MINDSET (ERGO TRADER)
Rynek nie zna Twojej psychologii, reaguje tylko na realizację lub brak realizacji założeń. Kompetencje miękkie to często ułuda maskująca brak dyscypliny.
Kapitał: Handluj tylko wolnymi środkami (bufor na min. 2 lata). Zależność finansowa niszczy obiektywizm i wymusza naginanie zasad.
Artysta vs Rzemieślnik: Artystą bądź w fazie tworzenia i testów. Na live-rynku bądź rzemieślnikiem bezlitośnie egzekwującym zasady w pętli 1/0.
Dekompresja Czasu: Rozkładaj ryzyko w czasie. Lewarowanie to kompresja czasu, która przyśpiesza bankructwo. Zyskowność to statystyka cykli, a małe pozycje pozwalają przetrwać obsunięcia.
Kontrarianizm: Odrzuć myślenie tłumu (chęć potwierdzeń to powód kupowania na górkach). Pozycjonuj się tam, gdzie ATR się kończy. Zysk to konsekwencja procesu, a nie racji.
Overtrading: Mniej znaczy lepiej. Szukanie ruchu w dni o niskiej zmienności niszczy kapitał kosztami transakcyjnymi i zmęczeniem poznawczym.
Oczekiwania i "Cukrzyca": Rynek to tort. Tłum chce zjeść cały na raz (lewarowanie) i dostaje "cukrzycy" (wyzerowanie konta). Jedz go małą łyżeczką.
Trading Zemsty (Revenge Trading): Próba natychmiastowego odrobienia strat. Rozwiązanie: namierzyć wyzwalacz emocjonalny -> zamknąć platformę.
Fałszywe Wzorce: Trading to nie praca z plaży, to walka o przetrwanie z zawodowcami. Nie używamy słowa "niesprawiedliwość". Zyskuj samoświadomość i zrób "check-listę". Jeśli Twój system kazał zamknąć na SL, a rynek zawrócił – nie frustruj się, bo system jest tarczą statystyczną.
ROZDZIAŁ 8: ARCHITEKTURA INTERFEJSU UI (EXPERT ADVISOR DASHBOARD)
Na podstawie analizy źródła obrazowego Expert Advisor.png, dedykowana aplikacja (Front-End) musi zawierać:
8.1. Wskaźniki Wizualne i Informacyjne
Pasek statusu konta (Zysk PLN/USD, Depozyt).
ROE % i DAILY % (Statystyki dzienne).
Okno informacyjne: informacje o koncie, zysku, rodzaj pozycji, logi systemu.
Pod spodem: Klasyczny wykres świecowy (w tle wstęgi TMA) oraz na dole okno oscylatora Stochastycznego.
8.2. Blok Zarządzania Lewarem (Wielkość Pozycji)
Przyciski manualnego lewara: x10, x5, x3, x1 oraz ich wartości ułamkowe lota poniżej (np. 0.0431).
Opis: Manualne zarządzanie wielkością pozycji, dokładanie/uszczuplanie.
8.3. Blok Zarządzania Ryzykiem (SL / BE)
Grupa przycisków: SL/2, SL, SL 20, SL 10, SL 5, BE (Break Even).
Służą do manualnego zarządzania stop lossami, dzielenia pozycji na pół (0.5% zakresu) i ustalania domyślnego SL.
8.4. Blok Awaryjny i Tygrysie Cięcia
Czerwony kwadrat cls (KILLALL): Zamyka automatycznie wszystkie pozycje. Odpowiednik uniksowego ubijania procesu.
c/rev (Cut and Reverse): Odwraca pozycję z zyskiem/stratą automatycznie ustawiając nowy SL (zabezpieczenie).
TP: Domyślny Take Profit powiązany algorytmicznie z przeciwną wstęgą.
8.5. Funkcje Specjalne
HIGH RISK ADD: Włączenie logiki opisywanej w rozdziale 2.1 (Dokładka po pierwszej potwierdzającej świecy).
FISHING ROD: Automatyczne przechwytywanie luk cenowych (rozjazd w orderbooku powyżej 0.5%).
8.6. System Auto-Orkiestracji (Główne Boty)
Moduł TMA.auto: Przyciski BUY, SELL, oraz pod-skrypty tma_aut.BUY, reset.all, tma_aut.SELL.
Jest to pilnowane przez warstwę "SECURITY" dbającą o sekwencyjność.
Moduł STOCH.auto: Przyciski BUY, SELL, ADD. Dodatkowe potwierdzenia algorytmiczne parametryzowane w zewnętrznym pliku.
Dokument ten stanowi kompletną mapę logiczną ekosystemu MMS v4. AGENTS_OS musi wykorzystywać te zasady do weryfikacji każdego fragmentu kodu, który decyduje o kapitałowej ekspozycji na rynku.
