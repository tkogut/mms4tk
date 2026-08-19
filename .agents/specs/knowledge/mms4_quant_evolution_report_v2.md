---
title: "Kompleksowy Raport Ewolucji Kwantowej i Integracji Techniczno-Regulacyjnej MMS4 (v2)"
author: "Quant Architecture & AGENTS-OS v6.5"
date_synced: "2026-08-19"
tags: ["#NotebookLM", "#ExpertKnowledge", "#MMSv4", "#WFO", "#HMM", "#RegimeDetection", "#RiskManagement", "#OKX_EEA"]
target: "Hermes Agent & MMS4 Core Engine"
source: "raw_notes/mms4-quant-evolution-report-v2.md"
---

# Kompleksowy Raport Ewolucji Kwantowej i Integracji Techniczno-Regulacyjnej MMS4 (v2)

Raport przedstawia twarde, inżynieryjne fakty, matematyczne wzory, pułapki architektoniczne oraz bezpośrednie wnioski użyteczne dla Quant Developera przy rozbudowie bota `mms4tk` (antigravity). Treść opiera się wyłącznie na załączonych dokumentach badawczych i specyfikacjach giełdowych.

---

## 1. Metodologia Walk-Forward Optimization (WFO) i Zarządzanie Zmiennością

### Struktura Podziału IS / OOS (In-Sample / Out-of-Sample)
*   **Optymalny Podział Danych i Kryteria Liczbowe:** Klasyczne podziały to chronologiczne rezerwowanie najnowszego bloku danych rzędu 20–30% jako zbioru testowego (OOS), zgodnie z analizami w [The GT-Score: A Robust Objective Function for Reducing Overfitting in Data-Driven Trading Strategies](https://arxiv.org/pdf/2602.00080). Jednak kluczowym kryterium statystycznym jest **liczba zamkniętych transakcji** w okresie testowym – pojedynczy blok OOS musi zawierać ich minimum **od 30 do 100**, aby wygenerować wiarygodną statystykę i uniknąć losowości, co wykazano w badaniach nad stabilnością testów out-of-sample w [A novel approach to trading strategy parameter optimization using double out-of-sample data and walk-forward techniques](https://arxiv.org/abs/2602.10785).
*   **Optymalizacja Długości Okien (WFO):** W badaniach nad intraday tradingiem Bitcoina na częstotliwościach od 1 do 60 minut przetestowano **81 kombinacji długości okien kroczących** (w zakresach od 1 do 28 dni) na 19-miesięcznym okresie treningowym (IS), weryfikując je na 21-miesięcznym okresie testowym (OOS) [A novel approach to trading strategy parameter optimization](https://arxiv.org/abs/2602.10785). Wykazano wysoką zależność wyników od doboru długości okien. Model kroczący (Rolling Window) cechuje się stałą długością okna treningowego $T_{IS}$ i przesunięciem o stały krok $T_{OOS}$, co szczegółowo omówiono w [Walk-Forward Optimization: Anchored vs. Rolling Windows and When Each Fails](https://www.susanpotter.net/quant/walk-forward-optimization/) oraz w podręcznikach [َWhat Is Walk Forward Optimisation in Trading? - Aron Groups](https://arongroups.co/forex-articles/walk-forward-optimisation-in-trading/). Model rozszerzający się (Anchored Window) posiada stały punkt startowy, a $T_{IS}$ rośnie z każdym krokiem, co zwiększa stabilność parametrów, lecz opóźnia adaptację do zmian strukturalnych, co przedstawia [Walk-Forward Optimization - What It Is, Examples, Benefits - WallStreetMojo](https://www.wallstreetmojo.com/walk-forward-optimization/).
*   **Przeciwdziałanie Data Leakage (Purging & Embargo):** 
    *   **Purging:** Aby zapobiec przeciekowi informacji na granicy IS/OOS (szczególnie przy stosowaniu cech kroczących), należy usunąć bufor obserwacji między oknami, co zaleca [Walk-Forward Optimization: Anchored vs. Rolling Windows and When Each Fails](https://www.susanpotter.net/quant/walk-forward-optimization/). Szerokość oczyszczania (purge width) musi przekraczać czas znoszenia autokorelacji stóp zwrotu (np. przy autokorelacji do 5. rzędu oczyszcza się min. 5 okresów) lub długość lookbacku użytych wskaźników (np. przy 20-okresowej średniej kroczącej, oczyszczanie musi wynosić minimum 20 okresów). W przypadku etykiet predykcyjnych wybiegających w przód (np. "czy cena wzrośnie za 5 okresów"), szerokość oczyszczania musi być przynajmniej równa horyzontowi etykiety.
    *   **Embargo:** Zapobiega wyciekowi wzorców z końca treningu do początku walidacji poprzez blokowanie oceny pierwszych $K$ obserwacji okna OOS.
*   **Druga Pętla Przeuczenia (Second-Order Overfitting):** Wielokrotne testowanie różnych konfiguracji meta-parametrów WFO (długości okien IS/OOS, kroków) i wybór tej o najwyższym Sharpe Ratio prowadzi do overfittingu na samym procesie walidacji, przed czym ostrzega [Model Overfitting — Machine Learning Concept | LuxAlgo Library](https://www.luxalgo.com/library/concept/model-overfitting/). Standardem weryfikacji jest zamrożenie całego potoku przed passami testowymi i wdrożenie tzw. *double out-of-sample* [A novel approach to trading strategy parameter optimization](https://arxiv.org/abs/2602.10785) lub testów stabilności parametrów w przestrzeni gridu (wybór parametrów ze stabilnych płaskowyżów, a nie izolowanych maksimów) [Parameter Stability — Performance, Backtesting & Validation Concept | LuxAlgo Library](https://www.luxalgo.com/library/concept/parameter-stability/).

### Filtry Zmienności i Dynamiczne Sizingowanie
*   **Stosunek Krótkoterminowej do Długoterminowej Zmienności:** Wskaźnik ATR (Average True Range) służy do mapowania dziennych ekstremów zmienności, co opisano w [ATR Indicator: What It Measures and How to Use It | FundedFast](https://fundedfast.com/learn/indicators/atr-indicator) oraz [ATR: Technical Indicator - FTMO Academy](https://academy.ftmo.com/lesson/atr-technical-indicator/). Dynamiczny wskaźnik Volatility Ratio (stosunek bieżącego ATR do jego średniej kroczącej, np. $VR = \frac{ATR_{current}}{SMA(ATR, n)}$) powyżej 1.5 sygnalizuje silną ekspansję zmienności sprzyjającą strategiom trendowym (breakout) [Using ATR to Adjust Position Size: Volatility-Based Risk Management for Dynamic Markets](https://quantstrategy.io/blog/using-atr-to-adjust-position-size-volatility-based-risk/). Wartości poniżej 1.0 wskazują na kompresję zmienności i marazm (stagnację), faworyzując powrót do średniej (mean-reversion).
*   **Zarządzanie Ryzykiem Portfelowym (Sizing & Limits):**
    *   **Złudzenie Niskiej Zmienności (ATR-Sizing Trap):** Klasyczna formuła wielkości pozycji oparta na ATR dąży do nieskończoności, gdy zmienność rynkowa drastycznie spada [Using ATR to Adjust Position Size](https://quantstrategy.io/blog/using-atr-to-adjust-position-size-volatility-based-risk/).
    *   **Zabezpieczenie 1 (Portfolio Heat Ceiling):** Sztywny limit całkowitego ryzyka otwartego portfela (np. całkowita suma ryzyka wszystkich aktywnych pozycji ograniczona do 5% kapitału konta), co stanowi standardową regułę ochrony kapitału opisaną w [Volatility-Based Position Sizing Explained - For Traders](https://fortraders.com/blog/volatility-based-position-sizing-explained) oraz [How to Use Volatility-Based Position Sizing when Trading with CFDs - ActivTrades](https://www.activtrades.com/en/news/how-to-use-volatility-based-position-sizing-when-trading-with-cfds).
    *   **Zabezpieczenie 2 (Capital Caps / Notional Limits):** Maksymalny dopuszczalny kapitał ulokowany w pojedynczej transakcji, zdefiniowany parametrem $\kappa \in [0.05, 0.15]$ (odpowiadający limitom 5% - 15% NAV) [Volatility-Adjusted Position Sizing - ZitaPlus](https://zitaplus.com/blog/risk-management/volatility-adjusted-position-sizing/).
    *   **Zabezpieczenie 3 (Volatility-Targeted Leverage Scaling):** Skalowanie lewarowania w oparciu o poziom zmienności realizowanej [Dynamic Position Sizing Across Volatility and Risk Regimes - Aron Groups](https://arongroups.co/forex-articles/dynamic-position-sizing/). Spadek ATR poniżej krytycznego progu (np. 1% ceny) dopuszcza maksymalną dźwignię (np. do unijnego limitu 10x na X-Perps), natomiast skok zmienności (np. ATR > 3% ceny) zmusza do natychmiastowego obniżenia dźwigni efektywnej do bazowego poziomu 1x [Volatility-Based Position Sizing Explained - Singapore Investment Blog | Collin Seow](https://collinseow.com/volatility/).

---

## 2. Detekcja Reżimów Rynkowych (Modele ML i Statystyczne)

### Charakterystyka Stacjonarnych Cech Wejściowych (Features)
Modele takie jak Hidden Markov Models (HMM) czy Gaussian Mixture Models (GMM) wymagają bezwzględnej stacjonarności danych wejściowych pod rygorem rozbieżności matematycznej [What Is Market Regime Detection with Hidden Markov Models? - Cube Exchange](https://www.cube.exchange/what-is/market-regime-detection-with-hidden-markov-models). Najwyższą skuteczność inżynieryjną wykazują:
1.  **Logarytmiczne Stopy Zwrotu (Log-Returns):** Wyliczane jako $r_t = \ln(P_t / P_{t-1})$ [Market Regime Detection in Bitcoin Time Series Using K- Means Clustering and Hidden Markov Models](https://jdmdc.com/index.php/JDMDC/article/download/57/37). Stabilizują wariancję i usuwają trendy z poziomów cenowych.
2.  **Historyczna Zmienność Realizowana (Realized Volatility):** Liczona w oknie kroczącym (np. 20-dniowym standardowym odchyleniem stóp zwrotu) [Market Regime Detection in Bitcoin Time Series](https://jdmdc.com/index.php/JDMDC/article/download/57/37).
3.  **Znormalizowany Wolumen (Volume Momentum):** Stosunek bieżącego wolumenu do średniej kroczącej, zapobiegający dominacji surowych jednostek wolumenowych w obliczeniach kowariancji, co opisano w projekcie [RegimeSense - Building an Adaptive Market Regime Detection System](https://moh1tt.vercel.app/blog/RegimeSense).
4.  **Zannualizowany Wskaźnik Absorpcji (Absorption Ratio - AR):**
    Wyliczany z ruchomej macierzy kowariancji $\Sigma_t$ o wymiarach $N \times N$ (okno bazowe $\omega = 60$ dni), zgodnie z [Identifiable Regime Detection in Pension-Fund Networks via Sticky Hidden Markov Models](https://www.mdpi.com/2227-7390/14/14/2463) oraz preprintem [Identifiable Regime Detection in Pension Fund Networks via Sticky Hidden Markov Models](https://www.preprints.org/manuscript/202606.0111):
    $$AR_t = \frac{\lambda_1^{(t)}}{\text{Tr}(\Sigma_t)}$$
    Gdzie $\lambda_1^{(t)}$ reprezentuje największą (wiodącą) wartość własną macierzy kowariancji, a $\text{Tr}(\Sigma_t)$ to jej ślad (suma wszystkich wartości własnych, czyli całkowita wariancja systemu). Wzrost wskaźnika AR bezpośrednio i monotonicznie odzwierciedla wzrost korelacji systemowej (systemic co-movement).

### Filtracja Szumu i Robustness Estymacji Kowariancji
*   **Próg Szumu Marchenko-Pastura (MP Bound):** W celu oddzielenia realnego sygnału od szumu statystycznego w krótkich oknach estymacji (np. przy stopniu niedopróbkowania $\frac{N}{T} = \frac{40}{60} \approx 0.67$) stosuje się filtrowanie spektralne [Identifiable Regime Detection in Pension-Fund Networks](https://www.mdpi.com/2227-7390/14/14/2463). Górna granica szumu spektralnego MP dla standaryzowanej kowariancji o wariancji $\sigma^2 = 1$ wynosi:
    $$\lambda_+ = \sigma^2 \left(1 + \sqrt{\frac{N}{T}}\right)^2 \approx 3.30$$
    Wartości własne poniżej progu $\lambda_+$ są zastępowane ich średnią, aby zachować ślad macierzy przy jednoczesnej eliminacji losowych fluktuacji wiodącego komponentu w krótkich próbach. Empiryczne analizy potwierdzają, że dla rynków finansowych $\lambda_1$ stale przekracza próg $\lambda_+$, co dowodzi, iż sygnał AR niesie realną treść ekonomiczną.

### Pułapki Technologiczne i Redukcja Detection Lag
*   **Efekt Bezwładności Cech (Windowing Effect):** Stosowanie długich okien lookback (40–60 barów) do wyliczania cech wejściowych (zmienność, autokorelacja) opóźnia moment, w którym model HMM zadeklaruje zmianę stanu, co jest przedmiotem dyskusji w społecznościach [Let's talk about regime detection : r/algotrading - Reddit](https://www.reddit.com/r/algotrading/comments/1razsuv/lets_talk_about_regime_detection/) oraz [What Is Market Regime Detection with Hidden Markov Models? - Cube Exchange](https://www.cube.exchange/what-is/market-regime-detection-with-hidden-markov-models). Reżim często ulega wyczerpaniu, zanim model go wykryje.
*   **Niestabilność Baum-Welch (EM) i Multiple Restarts:** Algorytm EM osiąga wyłącznie lokalne maksima funkcji wiarygodności (rough likelihood surface) [What Is Market Regime Detection with Hidden Markov Models?](https://www.cube.exchange/what-is/market-regime-detection-with-hidden-markov-models). Produkcyjne wdrożenie wymaga wielokrotnego restartu algorytmu Baum-Welch (minimum 20–50 losowych inicjalizacji parametrów startowych) w celu uniknięcia rozwiązań zdegenerowanych. Alternatywnie, inicjalizacja centrów klastrów przy pomocy algorytmu K-Means przed krokiem EM znacząco poprawia stabilność zbieżności i dokładność dopasowania (niższy wskaźnik BIC) [Market Regime Detection in Bitcoin Time Series Using K- Means Clustering and Hidden Markov Models](https://jdmdc.com/index.php/JDMDC/article/download/57/37).
*   **Architektura Multi-Scale MS-GARCH:** Aby rozwiązać problem niedopasowania skali (Scale Mismatch) – gdzie rzadkie przejścia makro (1D) kolidują z częstymi mikro-szokami (1H) – wdraża się hierarchię wieloskalową opisaną w [Multi-Scale Markov-Switching GARCH: Volatility Regime Detection in EUR/USD](https://arxiv.org/html/2606.06190v1). Trzy niezależne modele liczone są dla interwałów: Macro (1D), Meso (4H) oraz Micro (1H), a ich wyjścia łączy się w 27-wymiarowy tensor prawdopodobieństwa łącznego.
*   **Time-Varying Transition Probabilities (TVTP):** Statyczna macierz przejść wolno reaguje na szoki. Zastąpienie jej strukturą TVTP (multinomial logit) pozwala prawdopodobieństwom przejść $p_{ij,t}$ dynamicznie reagować na wektor zmiennych stressorów mikrostrukturalnych $z_{t-1}$ (z-score zmienności, spready, pęd wolumenu) [Multi-Scale Markov-Switching GARCH: Volatility Regime Detection in EUR/USD](https://arxiv.org/html/2606.06190v1).
    $$p_{ij,t} = \frac{\exp(a_{ij} + \gamma_{ij} z_{t-1})}{\sum_{k=0}^{K-1} \exp(a_{ik} + \gamma_{ik} z_{t-1})}$$
*   **Bramki Histerezy i Entropia Shannona:**
    *   **Temporal Hysteresis Gate:** Zapobiega migotaniu (boundary thrashing) na granicach stanów. Przekierowanie routingu pozycji następuje dopiero, gdy prawdopodobieństwo nowego stanu utrzyma się przez minimum $k$ kolejnych świec (np. $k = 2$) powyżej poziomu 0.85, zgodnie z kryteriami w [Multi-Scale Markov-Switching GARCH](https://arxiv.org/html/2606.06190v1).
    *   **Shannon Entropy Filter:** Ciągła kalkulacja entropii prawdopodobieństw stanów: $H_t = -\sum_{i=1}^K \pi_{i,t} \ln(\pi_{i,t})$. Przekroczenie progu znormalizowanej entropii (np. $\tilde{H}_t > 0.85$) oznacza stan skrajnej niepewności przejściowej i skutkuje automatycznym zawieszeniem handlu.
*   **Izolacja Wątku Głównego (Analytical Plane vs Operational Plane):** Ciężkie obliczenia Baum-Welch/NUTS blokują pętlę zdarzeń asyncio i wątek WebSocket, co omówiono w [7 Async Patterns for Running Agents Concurrently in Python](https://machinelearningmastery.com/7-async-patterns-for-running-agents-concurrently-in-python/). Architektura musi odsprzęgać krytyczną ścieżkę egzekucji (Operational Plane: asyncio, lekki routing zleceń w Pythonie o niskiej latencji) od asynchronicznego wątku obliczeń ML (Analytical Plane: procesy w tle liczące parametry i zapisujące wagi/prawdopodobieństwa do pamięci Redis).

---

## 3. LLM i Smart Money Concepts (SMC)

### Redukcja Latency i Eliminacja Halucynacji w Czasie Rzeczywistym
*   **Architektura TiMi (Trade in Minutes):** Ponieważ latencja wnioskowania LLM (5–10 s) wyklucza bezpośredni udział modelu w krytycznej ścieżce (Critical Path) niskich interwałów, LLM jest stosowany wyłącznie offline (faza generowania hipotez / factor discovery) [Trade in Minutes! Rationality-Driven Agentic System for Quantitative Financial Trading](https://www.alphaxiv.org/abs/2510.04787). Wygenerowany, ustrukturyzowany kod wskaźników jest kompilowany, automatycznie weryfikowany w piaskownicy (Sandboxed Validation) pod kątem Sharpe Ratio i wdrażany produkcyjnie jako deterministyczny moduł o latencji <15ms [FinRL-DeepSeek](https://arxiv.org/abs/2502.07393).
*   **Asynchroniczny Równoległy Krytyk (Asynchronous Parallel Critic):** Bot handlowy działa w 100% deterministycznie na poziomie silnika operacyjnego, podczas gdy agent LLM w tle, asynchronicznie, analizuje kontekst makro, strukturę orderbooka oraz sentyment rynkowy, zapisując flagi nadzorcze (np. `REDUCE_SIZE`, `OVERRIDE`) bezpośrednio do pamięci Redis [Agentic Trading: When LLM Agents Meet Financial Markets](https://arxiv.org/html/2605.19337v1) oraz [Trade in Minutes!](https://www.alphaxiv.org/abs/2510.04787). Silnik operacyjny odczytuje te flagi w ułamku milisekundy przed wysłaniem zlecenia.
*   **Programowe Bezpieczniki Pętli Agentów:**
    *   *Hard Timeouts:* Maksymalny czas oczekiwania na odpowiedź LLM ograniczony do 3.0s; po jego przekroczeniu następuje automatyczny fallback do tradycyjnej logiki wskaźnikowej [Trade in Minutes!](https://www.alphaxiv.org/abs/2510.04787).
    *   *Termination Guards:* Monitorowanie wyjścia agenta pod kątem powtórzeń (infinite loops) i wymuszone przerwanie pętli wnioskowania [Trade in Minutes!](https://www.alphaxiv.org/abs/2510.04787).
    *   *Instruction Stripping:* Dynamiczne usuwanie preambuł, tagów i zbędnych instrukcji w miarę zapełniania okna kontekstowego, w celu utrzymania maksymalnej precyzji wnioskowania [Trade in Minutes!](https://www.alphaxiv.org/abs/2510.04787).
*   **Zasada Enkapsulacji i Izolacji Danych:** Zakaz podawania do LLM surowych, "płaskich" danych numerycznych OHLCV [Trade in Minutes!](https://www.alphaxiv.org/abs/2510.04787). Zamiast tego preprocesor bota wylicza strefy S/R, poziomy zmienności, i przesyła do LLM ustrukturyzowane, abstrakcyjne deskryptory semantyczne [Trade in Minutes!](https://www.alphaxiv.org/abs/2510.04787). Każda zmiana kodu generowana przez LLM musi przejść automatyczny wsteczny test (backtest) z uwzględnieniem kosztów transakcyjnych i poślizgów (slippage) przed wdrożeniem na produkcję [Comparing 3 LLMs for Generating Profitable Trading Strategies](https://www.insightbig.com/post/comparing-3-llms-for-generating-profitable-trading-strategies).

### Tłumaczenie Koncepcji Wizualnych (SMC) na Wektory Tekstowe
*   **Błąd Dezorientacji Przestrzennej:** Bezpośrednie próby odczytywania wykresów lub podawania surowych cen w celu lokalizacji stref płynności (np. *liquidity sweeps*, *fair value gaps*) prowadzą do halucynacji geometrycznych [LLM-Assisted Semantic Pruning for Genetic Programming-Based Alpha Factor Discovery](https://www.mdpi.com/2076-3417/16/12/6231).
*   **Struktury Markdown-KV i YAML:** Badania nad percepcją danych strukturyzowanych przez modele LLM wykazały, że standardy CSV oraz płaski JSON osiągają najniższą dokładność interpretacji ustrukturyzowanych danych [Markdown vs JSON: Choosing the Right Format for LLM Prompts](https://webcrawlerapi.com/blog/markdown-vs-json-choosing-the-right-format-for-llm-prompts). Najwyższą dokładnością (60.7% poprawności interpretacji) charakteryzuje się format **Markdown-KV (Key-Value)** oraz **YAML**, co potwierdzono w [Which Table Format Do LLMs Understand Best? (Results for 11 Formats)](https://www.improvingagents.com/blog/best-input-data-format-for-llms/).
*   **Semantyczne Przechwytywanie Płynności (Semantic Representation of Sweeps):** Preprocesor bota identyfikuje ekstrema knotów świec w interwałach H1/H4 [Mechanika rynku, patterny](https://x.com/MastermindZX) oraz [Markowanie interwałów](https://x.com/MastermindZX). Następnie tłumaczy te współrzędne na ustrukturyzowany format Markdown-KV podawany jako prompt:
    ```markdown
    ## Analiza Płynności Aktywa BTC-USD_UM
    - Stan_Zmienności: ATR(14)_15m = 1.2% ceny bazowej
    - Lokalny_Wierzcholek_H4: 64200.00 USD (Knot_SMC)
    - Status_Wybicia: Swept (Cena osiągnęła 64280.00 USD, zamknięcie świecy 15m poniżej poziomu wierzchołka na 64150.00 USD)
    - Wolumen_Wybicia: 2.1x średniego wolumenu 20-okresowego (Potwierdzona absorpcja podaży)
    ```
    Model LLM (np. Claude w pętli asynchronicznej) otrzymuje tak przygotowany profil semantyczny, co pozwala na bezbłędną interpretację fałszywych breakoutów bez konieczności przetwarzania surowych wykresów pixelowych [LLM-Assisted Semantic Pruning](https://www.mdpi.com/2076-3417/16/12/6231).

---

## 4. Unijne Ramy Regulacyjne (MiKA/MiFID II) i Integracja API OKX Europe

### Integracja Techniczna Kontraktów X-Perps (EOG)
*   **Europejskie Środowisko Regulacyjne (MiKA & MiFID II):** Rozporządzenie MiKA i wytyczne ESMA klasyfikują standardowe kontrakty perpetual swaps jako instrumenty typu CFD, wprowadzając drastyczne restrykcje [Techniczna i regulacyjna integracja API v5 OKX dla europejskich kontraktów terminowych X-Perps w kontekście wymogów MiKA oraz MiFID II](https://my.okx.com/docs-v5/en/). W ich miejsce OKX Europe Markets Ltd (OEM, nadzorowane przez maltański MFSA) oferuje **X-Perps (Expiry Perps)** [OKX Launches X-Perps in Europe: MiFID Regulated Crypto Derivatives with up to 10x Leverage](https://via.ritzau.dk/pressemeddelelse/14889810/okx-launches-x-perps-in-europe-mifid-regulated-crypto-derivatives-with-up-to-10x-leverage?publisherId=90456&lang=en) oraz [X-Perps. A More Powerful Way to Trade. Built For Europe. - OKX](https://www.okx.com/en-eu/x-perps). Są to **5-letnie (60-miesięczne) kontrakty terminowe** wygasające w zdefiniowanej przyszłości, posiadające identyczny mechanizm finansowania (Funding Rate) naliczany co 8 godzin, co zapewnia zachowanie i płynność tożsamą z klasycznymi perpetualami, co szczegółowo objaśniono w [OKX X-Perps Guide](https://www.okx.com/help/x-perps-guide) i [What are X-Perps (Expiry Perps)? - OKX](https://www.okx.com/help/okx-x-perps-eea-what-are-expiry-perps).
*   **Eliminacja Błędu 50119 (Izolacja EOG):** Klucze API użytkowników z Europejskiego Obszaru Gospodarczego są całkowicie odizolowane bazodanowo ze względów zgodności z RODO [OKX API Key Doesn't Exist? Here Are All the Fixes That Actually Work](https://github.com/nkujvu13/okx-api-key-fixes). Przesłanie żądania na standardowy adres globalny przy użyciu europejskich kluczy skutkuje **błędem 50119 (API key doesn't exist)** [OKX Error 50119: API key doesn't exist · Issue #10967 - GitHub](https://github.com/freqtrade/freqtrade/issues/10967). Wszystkie zapytania dla bota `mms4tk` muszą być kierowane na europejskie punkty końcowe [Connect to OKX - Help Center - Gainium](https://gainium.io/help/connect-to-okx):
    *   **REST API:** `https://eea.okx.com` (lub alias `https://my.okx.com`) [Overview – OKX API guide | OKX technical support | OKX](https://my.okx.com/docs-v5/en/)
    *   **WebSocket Prywatny:** `wss://wseea.okx.com:8443/ws/v5/private`
*   **Wymogi Finansowe i KYC Konta:** Klucze API użytkowników w EOG będą odrzucać zlecenia handlowe, jeśli konto/subkonto nie przeszło weryfikacji KYC Level 2, testu odpowiedniości (Appropriateness Assessment) [Who can access X-Perps in the EEA? - OKX](https://www.okx.com/help/okx-x-perps-eea-regional-availability-eligibility) oraz jeśli saldo aktywów (AUM) na danym subkoncie spadnie poniżej równowartości **100 USD** [Overview – OKX API guide](https://my.okx.com/docs-v5/en/).
*   **Ograniczenie Dźwigni i Wykluczenie USDT:** Maksymalna dopuszczalna dźwignia dla europejskich kontraktów X-Perps wynosi **10x** [What are X-Perps? A Guide for European Traders - OKX](https://www.okx.com/learn/what-are-x-perps-guide-for-european-traders). Pod rygorem przepisów MiKA dotyczących stablecoinów (przepisy EMT), **pary oparte o USDT zostały całkowicie wycofane z EOG**. Wszystkie kontrakty są zabezpieczane i rozliczane w unijnych ekwiwalentach dolara amerykańskiego: **USDC, USDG lub USD** [What are X-Perps? A Guide for European Traders](https://www.okx.com/learn/what-are-x-perps-guide-for-european-traders).

### Rozwiązanie Błędu 54070 (Strukturyzacja TP/SL)
*   **Problem:** Na rynku kontraktów X-Perps w EOG bezpośrednie przesyłanie parametrów zabezpieczających (`tpTriggerPx` / `slTriggerPx`) bezpośrednio w ciele żądania `Place Order` (`POST /api/v5/trade/order`) jest kategorycznie odrzucane z **błędem 54070** (Current function not supported/Use attachAlgoOrds array) [okx package - github.com/tigusigalpa/okx-go](https://pkg.go.dev/github.com/tigusigalpa/okx-go).
*   **Rozwiązanie:** Wszystkie zlecenia zabezpieczające typu Take-Profit i Stop-Loss muszą być bezwzględnie przesyłane wewnątrz dedykowanej struktury tablicowej attachAlgoOrds [okx package - github.com/tigusigalpa/okx-go](https://pkg.go.dev/github.com/tigusigalpa/okx-go).

#### Przykładowa poprawna struktura JSON dla zlecenia Place Order:
```json
{
  "instId": "BTC-USD_UM-310404",
  "tdMode": "isolated",
  "side": "buy",
  "posSide": "long",
  "ordType": "market",
  "sz": "1",
  "attachAlgoOrds": [
    {
      "tpTriggerPx": "63000",
      "tpOrdPx": "-1",
      "tpTriggerPxType": "last",
      "slTriggerPx": "57000",
      "slOrdPx": "-1",
      "slTriggerPxType": "mark"
    }
  ]
}
```
*Uwaga:* Przekazanie wartości `-1` jako `tpOrdPx` / `slOrdPx` instruuje silnik giełdy, aby po przekroczeniu ceny wyzwalającej zrealizował zlecenie natychmiastowo po cenie rynkowej (Market Order) [Jak handlować instrumentami pochodnymi za pomocą Jupyter Notebook? | OKX Europa](https://my.okx.com/pl/help/how-can-i-do-derivatives-trading-with-the-jupyter-notebook). Stosowanie `slTriggerPxType: "mark"` (cena oznaczenia) jest rekomendowane w celu ochrony pozycji przed aktywacją SL na fałszywych, pojedynczych knotach cenowych rynków o niskiej płynności.

### Rozwiązanie Błędu 54092 (TradFi Perps Disclaimer)
*   **Problem:** Przy próbie handlu kontraktami surowcowymi (TradFi X-Perps, takimi jak złoto `XAU-USD_UM` lub ropa naftowa `BZ-USD_UM`) przez API, serwer zwraca **błąd 54092** [OKX X-Perps Guide](https://www.okx.com/help/x-perps-guide).
*   **Rozwiązanie:** Unijne przepisy wymagają wyraźnej akceptacji oświadczenia o ryzyku (Disclaimer) dla instrumentów tradycyjnych osobno dla każdego konta i subkonta. Aby odblokować API, użytkownik musi manualnie zalogować się przez interfejs webowy lub aplikację mobilną OKX, wejść na wykres danego instrumentu TradFi i zaakceptować wyskakujący formularz zgody. Dopiero po tym kroku API autoryzuje zlecenia automatyczne.

---

## 5. Rygor Deweloperski i GitOps (AGENTS-OS v6.5 Swarm Edition)

Pod rygorem błędów krytycznych i paraliżu pętli myślowej agentów, rozwój aplikacji `mms4tk` musi bezwzględnie przestrzegać zasad konstytucji systemu operacyjnego:

1.  **Blokada File Edit Hang (+0 -0):** Wbudowany edytor wizualny Antigravity w wersjach 1.23.2+ po wczesne wydania 2.0 wykazuje krytyczną niestabilność (generuje puste dyfy i zawiesza wątek). **Kategorycznie zabrania się stosowania wbudowanego edytora wizualnego.** Wszystkie pliki źródłowe muszą być tworzone, modyfikowane i nadpisywane wyłącznie za pośrednictwem terminala bash przy użyciu strumieniowych komend zapisu oraz konstrukcji `cat << 'EOF' > ...` (bash heredoc).
2.  **Ochrona przed Stale Worktree Crash:** Osierocone, nieaktywne gałęzie robocze (worktrees) powodują cichy paraliż modelów (silent hang). W pliku `hooks.json` systemu operacyjnego należy zaimplementować twardy automatyczny hook przed wywołaniem modeli (`before_model_call`) o strukturze komendy `git worktree prune`.
3.  **Secrets Sanitization (Bezpieczeństwo Poświadczeń):** Kategoryczny zakaz bezpośredniego wyświetlania lub logowania zawartości plików `.env` oraz tabel bazy danych bez uprzedniego zamaskowania (np. przy użyciu filtrów `grep -v`, `sed` lub `awk`), aby uniemożliwić wyciek kluczy API na konsolę.
4.  **Izolacja Środowiska (Builder Worktrees):** Prace inżynieryjne agenta Builder (Claude) mogą odbywać się wyłącznie w odizolowanych katalogach roboczych `./tmp/worktrees/` przy użyciu natywnego skryptu uruchomieniowego `./os-run-builder <nazwa_gałęzi>`, co zapobiega zanieczyszczeniu głównego repozytorium.
5.  **Protokół Handshake:** Każdy subagent (Builder, Auditor) po zakończeniu prac ma obowiązek zapisać formalny raport weryfikacji matematycznej i zmian w katalogu `.agents/swarm/` pod unikalną nazwą `<conversation_id>_<role>_handshake.json` przed zawnioskowaniem o push do gałęzi głównej.

---
*Opracowano na podstawie dokumentów badawczych i technicznych w bazie wiedzy projektu MMS.*
