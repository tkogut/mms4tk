---
title: "Taktyka Tygodniowa: Liquidity Sweep, Session Timing & Portfolio Risk Guard"
author: "MastermindZX & Tomasz Kogut"
date_synced: "2026-08-19"
tags: ["#NotebookLM", "#ExpertKnowledge", "#MMSv4", "#LiquiditySweep", "#SessionTiming", "#RiskManagement", "#PortfolioGuard"]
target: "Hermes Agent & MMS4 Core Engine"
source: "raw_notes/mastermind conclusions.md"
---

# 🧠 Wnioski Taktyczne i Mechanika Sesyjna (MMS v4 / MastermindZX)

Dokument stanowi ustrukturyzowaną destylację bieżących obserwacji rynkowych i reguł egzekucyjnych Masterminda oraz architektonicznych wytycznych zarządzania ryzykiem portfela.

---

## 1. ZARZĄDZANIE RYZYKIEM W STYLU PROP-TRADING (TOPSTEP STANDARD)
* **Priorytet**: Bezwzględna ochrona kapitału i asymetria zysku do ryzyka.
* **Twardy Stop Loss**: Niezmiennie **2.0%** na pozycji bazowej.
* **Czas zaangażowania**: 1–2h dziennie – system musi być zautomatyzowany, powtarzalny i oparty na twardych warunkach brzegowych (brak miejsca na discretionary overtrading).

---

## 2. MECHANIKA SESYJNA: "FRIDAY CLOSE SWEEP" & WEJŚCIE PO REAKCJI
* **Wąska strefa piątkowego zamknięcia (Friday Close Range)**:
  * Wąski zakres cenowy z końcówki sesji piątkowej stanowi naturalną strefę akumulacji zleceń Stop Loss / Breakout dla rynku detalicznego.
* **Protokół Wejścia (Sweep-Then-Reverse Pattern)**:
  1. **Faza 1 (Wybicie)**: Wybicie strefy z otwarciem nowych rynków / sesji (samo wybicie NIE jest sygnałem do wejścia).
  2. **Faza 2 (Zebranie Płynności - Liquidity Sweep)**: Wybicie lokalnych szczytów/dołków i wyczyszczenie płynności.
  3. **Faza 3 (Odrzucenie / Rejection)**: Pojawienie się świecy odrzucenia (knot / wick odrzucający wybicie i powrót korpusu do strefy).
  4. **Faza 4 (Egzekucja)**: Wejście w pozycję dopiero po potwierdzeniu powrotu pod strefę (np. wejście w SHORT po fałszywym wybiciu w górę).

---

## 3. TIMING SESYJNY (SESSION TIMING & MACRO DATES)
* **Sesja Amerykańska (US Open)**:
  * Kluczowy moment kreowania fałszywych impulsów i polowania na płynność przed wejściem właściwego wolumenu.
* **Wydarzenia Makro (np. CPI / FED)**:
  * Rynek regularnie wykonuje ruchy pozorowane mające na celu "ubranie" pozycji detalicznych przed publikacją danych.
  * Automat / Hermes nie powinien gonić wybić bezpośrednio przed danymi makro.

---

## 4. ARCHITEKTURA RYZYKA: GLOBALNY LIMIT MARŻY PORTFELA (PORTFOLIO GUARD)
* **Problem**: `max_margin_usd` działa obecnie per-symbol.
* **Ryzyko kaskady**: Przy 4 aktywnych parach i alokacji $500/parę, łączna ekspozycja marżowa wynosi $2000 bez centralnego limitu rachunku.
* **Wymóg Implementacyjny dla MMS4 Core**:
  * Wprowadzenie `TOTAL_MAX_PORTFOLIO_MARGIN_USD` (twardy hamulec blokujący otwieranie kolejnych par, gdy suma zajętych depozytów przekroczy dopuszczalny próg bezpieczeństwa).
  * Globalny Circuit Breaker na poziomie rachunku OKX.
