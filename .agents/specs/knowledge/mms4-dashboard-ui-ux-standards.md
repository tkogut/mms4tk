---
tags: ["#NotebookLM", "#ExpertKnowledge"]
date_synced: "2026-09-17"
source: "raw_notes/Kompendium Inżynieryjne: Standardy UI/UX i Refaktoryzacja Dashboardu Algotradingowego MMS4.md"
---

# Kompendium Inżynieryjne: Standardy UI/UX i Refaktoryzacja Dashboardu Algotradingowego MMS4

Kompendium Inżynieryjne UI: System MMS4 i Framework Pracy z Agentami AI
Niniejszy dokument definiuje standardy inżynieryjne dla systemu MMS4, koncentrując się na ekstremalnej wydajności interfejsów tradingowych oraz rygorystycznej współpracy z agentami AI (Agentic Workflow). Jako Lead Architect, wymagam bezwzględnego przestrzegania poniższych protokołów w celu utrzymania płynności 60 FPS oraz integralności kontraktowej kodu.
1. Transformatywny Model Pracy z Agentami AI (Agentic Workflow)
W systemie MMS4 odchodzimy od nieustrukturyzowanego czatu na rzecz modelu kontraktowego. Agenci (Cursor, Claude Code, Windsurf) muszą operować wewnątrz  Model Context Protocol (MCP) , co pozwala na bezpieczną i przewidywalną interakcję z systemem plików i narzędziami zewnętrznymi.
1.1. Od Czatu do Kontraktu (Task Specs)
Każda zmiana musi być poprzedzona "Kontraktem Zadania". Agent ma obowiązek przyjąć rolę  Inżyniera Implementacyjnego , walidując architekturę przed zapisem.Szablon Promptu Systemowego / Task Spec:
### ROLE: Implementation Engineer (MMS4 System)
### GOAL: [Opisz precyzyjnie cel, np. Refaktoryzacja tabeli zleceń]
### CONTEXT:
- Stack: React 18, TypeScript, Tailwind CSS, TanStack Table v8.
- Performance: Maximize render efficiency, 60fps target.
### CONSTRAINTS:
- No global state for high-frequency ticks.
- Use `useRef` for direct DOM/Canvas updates.
- Adhere to WCAG 2.1 accessibility standards.
### SCOPE:
- Read: [ścieżki do plików]
- Modify: [ścieżki do plików]
### ACCEPTANCE CRITERIA:
1. Zero layout shift (CLS) during data bursts.
2. Type safety validation (npx tsc).
3. Receipt generation post-task.


1.2. Kontrola Granic i Zapobieganie Goal-Driftowi
Agent musi wymusić "Zero-Tolerance Policy" dla modyfikacji  out-of-scope . Przed edycją plików, agent musi wywołać procedurę walidacji istniejącej architektury, aby uniknąć redundancji i naruszenia wzorców (np. dublowanie logiki walidacji Zod).
1.3. System Raportowania „Final Receipt”
Każda sesja kończy się wygenerowaniem protokołu weryfikacyjnego.| Kryterium | Wymóg | Status/Wynik || ------ | ------ | ------ || Technologia | TypeScript + TanStack Table / Zod | OK/Fail || Static Analysis | npx tsc --noEmit & npm run lint | Log Output || Bundle Impact | Przyrost < 5kB (minified/gzipped) | +X.X kB || Accessibility | Walidacja JAWS / Axe Core | Score || Performance | Brak rerenderów przy tickach (useRef/Memo) | Confirmed |
2. Architektura UI/UX „Hedge-Fund Terminal” & Mobile-First PWA
Interfejs MMS4 to „High-Density UI”. Każdy piksel musi służyć przekazywaniu danych bez wprowadzania szumu wizualnego.
2.1. Precyzja Danych i Eliminacja Jumping Layout
Wszystkie dane finansowe muszą używać font-mono oraz tabular-nums z wyrównaniem text-right. Aby wyeliminować  Cumulative Layout Shift (CLS)  podczas burstów danych WebSocket, nakazuje się:
Stosowanie aspect-ratio dla kontenerów wykresów i kart.
Użycie contain: strict w CSS dla komponentów o stałym wymiarze.
Zarezerwowanie minimalnej wysokości (min-h-...) dla dynamicznych list.
2.2. Matrix High-Density Layout
Porównanie standardów UI z rygorem MMS4:| Element | Standard UI | MMS4 High-Density UI | Klasy Tailwind || ------ | ------ | ------ | ------ || Padding | p-4 (1rem) | p-1.5 (0.375rem) | p-1.5 || Gap | gap-4 | gap-1 lub gap-2 | gap-1.5 || Font Size | text-base (16px) | text-11px / text-xs | text-11px leading-tight || Scrollbars | Visible Default | Hidden Horizontal Swipe | scrollbar-hide |
2.3. Wydajność Renderowania: Direct DOM vs Virtual DOM
Zabrania się używania useState do obsługi ticków cenowych o częstotliwości > 10Hz.
Mechanizm:  Dane z WebSocket powinny trafiać do useRef. Aktualizacja UI następuje poprzez bezpośrednią manipulację innerText lub Canvas (np. Lightweight Charts) wewnątrz pętli requestAnimationFrame.
Wirtualizacja:  Długie listy (historia zleceń)  muszą  implementować wirtualizację (TanStack Virtual), aby utrzymać stałą liczbę węzłów DOM niezależnie od wielkości datasetu.
2.4. Semantyka Wizualna i Directionality
Statusy:  Zielony (Trade/Ok), Żółty (Margin Guard/Warning), Czerwony (Risk/Stop).
Price Directionality:  Wprowadź mikro-interakcję "Flash":
Cena up: krótki błysk tła bg-green-500/20 (max 300ms).
Cena down: krótki błysk tła bg-red-500/20.
3. Specyfikacja Refaktoryzacji Komponentów MMS4
3.1. MonitoringTab.jsx – Operational Command Center
Market Grid:  Wykorzystaj CSS Grid: grid-cols-repeat(auto-fill,minmax(120px,1fr)). Karty skompresowane o 50%, obsługujące horyzontalny swipe.
MarginGuardBar:  Komponent typu "breathing bar". Animacja szerokości: transition-all duration-300 ease-in-out.
<60%: Emerald-500.
60-80%: Amber-500.
80%: Rose-600.
Przycisk KILLALL:  Implementacja Force-Hold Button. Wymagane przytrzymanie przez 1.5s (progress bar wokół przycisku) lub szybki double-tap (<300ms) z natychmiastowym feedbackiem haptycznym.
3.2. AnalyticsTab.jsx – Telemetria Hermes H4
Hermes H4 Header:  Event-Stream Health Monitor. Wyświetla: ping_ms, ws_status (Connected/Reconnecting), event_lag_ms (różnica między server_ts a client_ts).
Equity Curve:  Integracja Lightweight Charts (TradingView) z osadzoną obsługą resizeObserver dla pełnej responsywności (w-full h-320px md:h-450px).
3.3. SettingsTab.jsx – Konfiguracja Ryzyka
Zod Integration:  Każdy formularz (Leverage, Margin Cap) musi być walidowany przez schemat  Zod  zintegrowany z React Hook Form.
Optimistic UI:  Przełączniki (Toggle Switch) muszą reagować natychmiastowo wizualnie, z ewentualnym wycofaniem stanu (rollback) w przypadku błędu API (status 4xx/5xx).
4. Procedura Weryfikacyjna i Kontrola Jakości
Każdy Pull Request generowany przez agenta AI musi przejść przez "Definition of Done" (DoD).
4.1. Statyczna i Dynamiczna Kontrola Jakości
Typowanie:  npx tsc --noEmit – zero błędów any w logice finansowej.
Linting:  npm run lint – zgodność z regułami eslint-plugin-react-hooks.
Automatyzacja E2E:  Uruchomienie npx playwright test dla krytycznych ścieżek (np. proces składania zlecenia).
4.2. Testy Mobilne i Dostępność
Touch Targets:  Wszystkie elementy interaktywne minimum 44x44px.
Overlap Check:  Weryfikacja braku nakładania się tekstu przy ekstremalnych wartościach numerycznych (np. saldo > 1M USD).
WCAG 2.1:  Kontrast tekstu minimum 4.5:1 dla kluczowych metryk (użyj narzędzia Lighthouse).
4.3. Instrukcja Generowania Raportu (Agent Output)
Na koniec zadania, agent  musi  wygenerować blok kodu Markdown z nagłówkiem ## FINAL RECEIPT, zawierający tabelę zmian, wyniki testów npx tsc oraz potwierdzenie braku błędów w konsoli przy użyciu console-check (np. przez Playwright).
END OF COMPENDIUM - SYSTEM MMS4 READY FOR IMPLEMENTATION#
FINAL RECEIPT TEMPLATE GENERATED

#NotebookLM #ExpertKnowledge
