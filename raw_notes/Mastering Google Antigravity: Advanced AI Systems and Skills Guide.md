Raport Inżynieryjny: Standardy Layoutu i Refaktoryzacji Dashboardów Algotradingowych
1. Strategia Architektury Przestrzennej: Koncepcja Data-Dense UI
W profesjonalnych systemach klasy Enterprise FinTech, budowanych w hubach technologicznych takich jak Hyderabad czy Bangalore, gęstość informacji (information density) przestała być wyborem estetycznym, a stała się rygorem inżynieryjnym. Przejście z "modern web" na standard "Hedge-Fund Terminal" jest krytyczne dla systemów o wysokim natężeniu ruchu (high-traffic production environments). Wykorzystując stack  Next.js 14, ShadCN/UI oraz Tailwind CSS , musimy projektować interfejsy jako "Event-driven surfaces", gdzie szybkość reakcji operatora jest bezpośrednio skorelowana z efektywnością upakowania danych powyżej linii zgięcia (above-the-fold).
Fundamenty "Hedge-Fund Terminal" i "Data-Dense Editorial"
Zasada "Mission-critical reliability" wymaga całkowitej eliminacji scrollowania w kluczowych widokach operacyjnych. Architektura musi wspierać natychmiastową percepcję bez zbędnych interakcji. W profesjonalnym terminalu każdy piksel musi pracować na zysk – odrzucamy dekoracyjne marginesy na rzecz czytelnej hierarchii informacji, typowej dla systemów zarządzania flotą czy arkuszy tradingowych (np. systemy typu SmartDocs czy Zeromoblt).
Rygorystyczna Kompresja Przestrzeni (Tailwind CSS Configuration)
Wdrażamy system "Zero-Gutter", wymuszając w pliku konfiguracji oraz w komponentach ShadCN następujące zasady:
Marginesy i Dopełnienia:  Bezwzględny limit p-2 / p-3 dla głównych kontenerów danych oraz gap-2 dla siatek grid/flex.
Separacja Liniowa:  Eliminujemy białe znaki (white space) jako separator. Zastępujemy je subtelnymi obramowaniami (border-zinc-800) i kontrastowymi tłami kart, co pozwala na 30-40% gęstsze upakowanie komponentów przy zachowaniu "Data-Dense Editorial".
Wzorce PWA dla Trading Dashboard
Zgodnie z wymogami dla nowoczesnych aplikacji IoT i FinTech (standard PGAGI), dashboardy mobilne muszą ewoluować w stronę płynnych list horyzontalnych:
flex overflow-x-auto no-scrollbar gap-2 – implementacja "płynnej wstążki" dla par walutowych.
Single-line Status Check:  Każda para rynkowa musi posiadać "jednoliniowe podsumowanie" mieszczące się w szerokości viewportu mobilnego (375px-430px), co eliminuje potrzebę skomplikowanej nawigacji pionowej.
2. Typografia Numeryczna i Stabilność Układu (Jumping Layout Prevention)
W systemach tradingowych, gdzie dane przesyłane są przez WebSockets lub  gRPC-Web , mikrosekundowe opóźnienia w odczycie ceny generują realne straty. Wizualna stabilność liczb jest fundamentem zaufania do danych (Ground Truth).
Eliminacja Przesunięć Układu (Layout Shifts)
Dynamiczne zmiany cen nie mogą powodować "pływania" interfejsu. Wymuszamy rygorystyczne reguły typograficzne:
Tabular Figures:  Wszystkie komponenty prezentujące dane (PnL, Wolumen, Cena) muszą korzystać z klas font-mono tabular-nums. Gwarantuje to stałą szerokość znaku, zapobiegając drganiom układu podczas fluktuacji cyfr.
State Optimization:  Wykorzystujemy  Zustand  lub  Redux  zoptymalizowany pod kątem "high-frequency updates", aby minimalizować re-rendery i zapewnić płynność wizualną przy odświeżaniu rzędu 100ms.
Inżynieria Wyrównania i Margin Guard
Wyrównanie numeryczne:  Wszystkie kolumny z danymi ilościowymi muszą posiadać klasę text-right. Pozwala to na błyskawiczne porównywanie rzędów wielkości.
Hero Numbers:  Na kartach KPI stosujemy centrowanie tylko dla wartości statycznych. Dane dynamiczne są zawsze wyrównywane do krawędzi zgodnie z zasadą "Margin Guard", co separuje wizualnie strefy ryzyka od informacji kontekstowych.
3. Specyfikacja Rekonstrukcji Układu dla Kluczowych Widoków
Dashboard projektujemy jako "Mission-critical system", gdzie każda zakładka to wyspecjalizowane narzędzie inżynieryjne.
MonitoringTab.jsx – Centrum Operacyjne (IoT & Live Data)
Kompaktowość:  Redukcja wysokości kart o 50%. Wdrożenie horyzontalnych list swipe-to-action (React Native/PWA).
Risk Zones:  Kolorystyczny "Margin Guard" (Zielony <60%, Żółty 60-80%, Czerwony >80%).
Semi-Auto Controls:  Implementacja dwustopniowego przycisku KILLALL. Przycisk CUT & REVERSE pozostaje nieaktywny (disabled state) do momentu spełnienia warunków płynności, co zapobiega błędom w warunkach wysokiej zmienności.
AnalyticsTab.jsx – Wizualizacja Trendów i Event-Driven Surfaces
Chart Dominance:  Wykresy (TradingView / Lightweight Charts) muszą dominować nad układem (w-full h-450px na desktopie).
Sub-navigation Architecture:  Przeniesienie Equity Curve do dedykowanych sub-zakładek.
Badge Bar:  Wdrożenie paska raportów "Hermes H4" bezpośrednio nad osią czasu wykresu, co integruje sygnały algorytmiczne z analizą wizualną.
SettingsTab.jsx – Multi-tenant SaaS Configuration
Sekcje Zwarte:  Trzy sekcje o stałej wysokości: Lewar/Scout, Margin Guard Cap, Filtry Volatility.
Modern Form Controls:  Zastąpienie checkboxów przez Toggle Switche. Wymagana walidacja typu  Zod  dla wszystkich parametrów wejściowych algorytmu.
4. AI-Native Development i Zarządzanie Długiem Technicznym
W dobie "Agentic AI" (Claude Code, Cursor, MCP), rola Architekta przesuwa się z pisania kodu na definiowanie rygorystycznych ograniczeń (Persistence Rules).
Konstrukcja Promptu Taktycznego (Task Spec)
Podczas pracy z agentami AI (np. w workflow Gradera AI czy Sentiaflow), nie prosimy o "poprawę wyglądu". Stosujemy prompty zorientowane na LLD (Low-Level Design):"Zrekonstruuj strukturę DOM w MonitoringTab.jsx. Zaimplementuj rygorystyczny grid system oparty na Tailwind gap-2. Zredukuj zagnieżdżenie divów. Wdróż tabular-nums dla pól WebSocket. Przeprowadź audyt pod kątem accessibility (WCAG 2.1) i performance budgets."
Refaktoryzacja AI-Generated Legacy Debt
Zgodnie ze standardami "Founding Engineer" (kontekst PencilCrew), każda zmiana wygenerowana przez AI musi zostać poddana audytowi pod kątem ryzyka produkcyjnego. "Data-Dense UI" służy tu jako benchmark – jeśli AI proponuje luźne marginesy p-4 lub nie-monotypiczne fonty w tabelach, jest to sygnał do natychmiastowej korekty architektury.
Persistence Rules – Pliki Konfiguracyjne (.cursorrules / CLAUDE.md)
W plikach konfiguracyjnych agentów wstrzykujemy bezwzględne reguły:
Rule 1:  "Nigdy nie używaj p-4 jeśli p-2 zapewnia czytelność w kontekście Data-Dense".
Rule 2:  "Zawsze stosuj font-mono tabular-nums dla dynamicznych danych numerycznych".
Rule 3:  "Każdy nowy komponent musi posiadać specyfikację LLD (Low-Level Design) i testy jednostkowe (Jest/Vitest) przed wdrożeniem".Ostatecznym celem jest stworzenie interfejsu, który nie jest tylko pasywnym podglądem, ale precyzyjnym narzędziem wspierającym egzekucję strategii w czasie rzeczywistym, zgodnie z najwyższymi standardami inżynieryjnymi globalnych hubów FinTech.

