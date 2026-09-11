---
title: "Architektura Systemowa mms4tk: Przekład Strategii Masterminda na Logikę Agenta (Raport 10.09.2026)"
tags: ["#NotebookLM", "#ExpertKnowledge"]
date_synced: "2026-09-11"
source: "raw_notes/Architektura Systemowa mms4tk: Przekład Strategii Masterminda na Logikę Agenta (Raport 10.09.2026).md"
status: "ExpertKnowledge"
---

# 🧠 Architektura Systemowa mms4tk: Przekład Strategii Masterminda na Logikę Agenta (Raport 10.09.2026)

> **Źródło**: [raw_notes/Architektura Systemowa mms4tk: Przekład Strategii Masterminda na Logikę Agenta (Raport 10.09.2026).md](file:///home/tkogut/projects/mms4tk/raw_notes/Architektura%20Systemowa%20mms4tk:%20Przek%C5%82ad%20Strategii%20Masterminda%20na%20Logik%C4%99%20Agenta%20%28Raport%2010.09.2026%29.md)  
> **Status wiedzy**: `#NotebookLM` `#ExpertKnowledge` (Posiada priorytet nad lokalną dokumentacją roboczą)

---

## 1. Metadane i Kontekst Analizy

| Parametr | Wartość |
| :--- | :--- |
| **Data analizy** | 10.09.2026 |
| **Źródła danych** | Wpisy Mastermind (16:18, 17:23, 18:29) |
| **Identyfikator systemu** | `mms4tk` |
| **Główne paradygmaty** | Mean-Reversion, Iron Execution |

Celem niniejszego raportu jest translacja rynkowej intuicji Masterminda na deterministyczne parametry techniczne komponentów systemu `mms4tk`. Dokument standaryzuje logiczne zachowanie modułów **Hermes Agent** oraz **Inactivity Manager**, konwertując opisowe sygnały o „ładowaniu toreb” i „markowaniu interwałów” na konkretne filtry egzekucyjne i warunki wyjścia.

---

## 2. Mechanizm Ochronny: Walidacja Interwału i „Markowanie 16:05”

System `mms4tk` implementuje rygorystyczny moduł walidacji wejścia oparty na analizie anomalii płynnościowych na początku sesji USA.

* **Trigger Anomalii (16:05)**: Gwałtowny wzrostowy ruch ceny w okolicach godziny 16:05 jest klasyfikowany jako „ładowanie toreb” (zbieranie płynności przed właściwym ruchem). Wykrycie tej anomalii aktywuje filtr bezpieczeństwa `H1_VALIDATION`.
* **Filtr H1 Candle Closure**: W przypadku aktywacji triggera 16:05, system nakłada bezwzględną blokadę na otwieranie pozycji Short przed zamknięciem świecy H1 (godz. 17:00).
* **Cel Inżynieryjny**: Oczekiwanie na pełne zamknięcie interwału zapobiega uwięzieniu agenta w pułapkach płynnościowych („markowanie interwałów”). Sygnał wejścia jest uznawany za poprawny dopiero po potwierdzeniu trwałej struktury podażowej na interwale H1.

---

## 3. Zarządzanie Ekspozycją: Moduł Hermes Agent i D1 ATR

Logika ekspozycji systemu opiera się na koncepcji „podażowego rendżu D1”, który musi być traktowany jako dynamiczny obszar operacyjny kampanii.

* **Kalibracja ATR_LOOKBACK**: Parametr `ATR_LOOKBACK` w module Hermes Agent zostaje ustawiony na przedział **3–5 dni** (default: `4`). Taka konfiguracja pozwala uwzględnić „multi-dniową” charakterystykę kampanii podażowej, o której wspomina Mastermind, mapując realny zasięg możliwego wyciskania płynności.
* **Zasada „Iron Execution” (Żelazna Egzekucja)**:
  * **Priorytet Ekspozycji**: Algorytm przedkłada utrzymanie pozycji nad matematyczną precyzję wejścia. Raz zidentyfikowany kierunek wymusza utrzymanie ekspozycji, dopóki rynek nie zrealizuje zasięgu.
  * **Determinizm Systemowy**: Operator ma zakaz ingerencji w parametry zlecenia („nie kombinuje”). Ostateczne rozstrzygnięcie kampanii pozostaje w gestii zmienności rynkowej.
* **Techniczna Blokada Operatora**: W trybie Iron Execution system musi aktywnie chronić integralność `OrderID`. Wymagana jest implementacja mechanizmu, który w momencie otwarcia pozycji automatycznie cofa każdą manualną próbę zmiany poziomów SL/TP lub (jeśli API brokera na to pozwala) tymczasowo ogranicza uprawnienia zapisu dla kluczy operatora w zakresie edycji parametrów aktywnych zleceń.

---

## 4. Zabezpieczanie Poziomów: Dual-Stage TP i Inactivity Manager

Logika wyjścia zostaje rozdzielona na dwie fazy, sterowane przez moduł Inactivity Manager z uwzględnieniem formacji V-kształtnych.

* **Definicja Dual-Stage Take Profit**:
  * **Stage 1 (`Local Level SPLIT_50`)**: Pierwszy cel techniczny na lokalnym wsparciu/bazie. Służy do zabezpieczenia kapitału.
  * **Stage 2 (`D1 Range Target`)**: Docelowy poziom wyjścia z całości pozycji, wynikający z pełnego rendżu D1.
* **Logika SPLIT_50 i V-Shape**:
  * Wykrycie formacji „V-ki” (dynamiczne zejście do bazy i natychmiastowy powrót) wymusza aktywację flagi `SPLIT_50`.
  * W momencie dotknięcia lokalnego poziomu, system zamyka 50% pozycji, a pozostała część zostaje zabezpieczona na Break Even (BE).
* **Ryzyko Podwójnego Retestu**: Jeśli cena po dotknięciu lokalnego poziomu utrzyma się powyżej niego (zamknięcie świecy 5-minutowej powyżej poziomu wsparcia), Inactivity Manager musi natychmiast zamknąć pozostałe 50% wolumenu. Utrzymanie ceny powyżej lokalnego poziomu jest interpretowane jako ustanowienie nowego wsparcia i unieważnienie pierwotnego setupu podażowego.

---

## 5. Model Bazowy: Uzasadnienie Mean-Reversion

> [!NOTE]
> Trwałość i szerokość „podażowego rendżu D1” stanowi fundament dla architektury `mms4tk`. Utrzymywanie nastawienia typu Mean-Reversion (powrót do średniej) jest uzasadnione technicznie przez mechanizm zbierania płynności powyżej lokalnych maksimów. System zakłada, że po fazie dystrybucji nastąpi powrót do konsolidacji wewnątrz bazy, co potwierdza poprawność modelu powrotu do średniej w kolejnych cyklach płynnościowych sesji.

---

## 6. Instrukcje Implementacyjne (Techniczne Wytyczne)

Wdrożenie powyższych zmian wymaga od integratora odhaczenia następującej listy kontrolnej:

- [ ] **Moduł Wejścia**: Implementacja zależności `H1_CLOSE_FILTER` od wystąpienia anomalii cenowej w oknie czasowym 16:05 – 16:10.
- [ ] **Hermes Agent**: Konfiguracja zmiennej `ATR_LOOKBACK` na wartość `4` (default) z zakresem 3–5 sesji do mapowania D1 Range.
- [ ] **Inactivity Manager**: Implementacja triggera `V_SHAPE_DETECTION` opartego na dynamice powrotu do bazy, wymuszającego tryb `SPLIT_50`.
- [ ] **Zabezpieczenie Wyjścia**: Skrypt monitorujący zamknięcia świec 5M powyżej „lokalnego poziomu” w celu natychmiastowej terminacji pozostałej ekspozycji (*Double Retest Protection*).
- [ ] **Hard-Lock**: Aktywacja automatycznego nadpisywania (*revert*) manualnych zmian SL/TP w trakcie trwania kampanii podażowej.

---

#NotebookLM #ExpertKnowledge