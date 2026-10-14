# Atrybucja strat: STOP-LOSS vs reszta (closed_trades, stan 2026-10-14)

**Autor:** Builder (Claude Code) · **Charakter:** analiza tylko do odczytu, bez zmian w kodzie i produkcji.
**Źródło:** tabela `closed_trades` (odczyt `mode=ro`), 504 wiersze (to nogi/zamknięcia, nie pozycje). API `/api/v1/stats/pnl` raportuje 484 transakcje; różnicy nie badano, sumy brutto zgadzają się (+0,70 USD).
**Kontekst:** niezależna dyskusja z subagentem Opus nad specyfikacją z PR #125 (rozdz. „Wnioski”). Wyniki subagenta są hipotezami, dane poniżej to fakty z bazy.

## 1. Fakty

### 1.1 Wynik wg powodu wyjścia (USD)

| powód | n | brutto | opłaty | netto |
|---|---:|---:|---:|---:|
| STALE_SPLIT_50 | 247 | +17,14 | 13,60 | +3,54 |
| STALE_FORCE_CLOSE | 131 | +12,43 | 12,19 | +0,24 |
| STALE_BE_ESCAPE | 3 | +0,21 | 0,16 | +0,05 |
| BREAK-EVEN | 57 | +0,12 | 1,59 | −1,47 |
| TAKE-PROFIT | 21 | +34,11 | 2,44 | +31,67 |
| CLOSED_EXTERNAL | 10 | +10,59 | 1,35 | +9,24 |
| MIDLINE_PARTIAL_TP | 5 | +1,72 | 0,24 | +1,48 |
| **STOP-LOSS** | **30** | **−75,62** | 4,21 | **−79,83** |
| razem | 504 | +0,70 | 35,66 | −34,88 |

Wyjścia inne niż STOP-LOSS dają łącznie ok. **+45 USD netto**; 30 wyjść STOP-LOSS daje **−79,8 USD**. Wyjścia STALE_* nie są źródłem straty: brutto +29,8, po opłatach ok. +3,8.

### 1.2 Wynik wg symbolu (USD)

| symbol | n | brutto | opłaty | netto | SL (n / USD brutto) |
|---|---:|---:|---:|---:|---|
| BTC | 140 | −17,01 | 12,33 | −29,34 | 14 / −37,60 |
| ETH | 119 | +7,74 | 9,48 | −1,73 | 8 / −23,74 |
| SOL | 86 | −1,87 | 0,60 | −2,47 | 0 |
| XAU | 61 | +7,84 | 7,25 | +0,60 | 0 |
| XAG | 59 | +5,18 | 5,77 | −0,59 | 4 / −12,85 |
| ICP | 32 | −0,70 | 0,23 | −0,92 | 1 / −0,43 |
| XRP | 7 | −0,50 | 0,12 | −0,62 | 3 / −1,00 |

BTC to 84% straty netto, ale też 14 z 30 STOP-LOSS. Bez BTC wynik netto to ok. −5,8 USD na 364 transakcjach.

### 1.3 Charakter pojedynczego STOP-LOSS

- Ruch ceny w chwili SL to **ok. −2,0%** (typowo −1,95…−2,17%), czyli poślizg jest mały. Pełny SL kosztuje **−4,1…−4,5 USD brutto** (+0,21 USD opłaty) przy nominale ok. 210 USD (3× dźwignia). Wcześniejsze sierpniowe SL są mniejsze (inna wielkość pozycji), przez co średnia 30 SL (−2,52 USD) jest niższa niż koszt pełnego SL.
- Wyjaśnia to wcześniejszy „rozjazd” (SL większy niż 2% × ~68 USD): 68 USD było średnim nominałem z kalibracji backtestu, nie z produkcji. XAG: SL −4,2…−5,0 USD odpowiada 2% × ok. 210–250 USD nominału, bez anomalii.
- Czas od wejścia do SL (tam gdzie znany `entry_timestamp`): zwykle **6,6–15 h**, raz 0,4 h. Cena dochodzi do −2% powoli, więc wyjścia STALE_* nie zdążyły zadziałać.
- **Pary BTC+ETH:** 18.09 (ETH 13:39, BTC 13:40, oba SHORT) i 21.09 (BTC 08:36, ETH 08:38, oba SHORT) to dwa pełne SL w ciągu minut. 14–15.09 BTC i ETH LONG też w odstępie godziny. Pozycje BTC i ETH są silnie skorelowane, więc jeden ruch rynku kosztuje ok. 8,5 USD, a nie 4,2.
- Kierunek: większość z 30 SL to pozycje **SHORT** (ok. 22 z 30). To obserwacja, nie wniosek (okres mógł być wzrostowy dla krypto).
- Miesięcznie (SL n / USD brutto): 08.2026 13 / −19,4; 09.2026 12 / −39,8; 10.2026 (do 14.10) 5 / −16,5.
- Ostatnie 133 zamknięcia (ok. od epoki 28.09): 5 wierszy STOP-LOSS (4 pełne ok. −4,2 USD i jeden zamknięty na plusie +0,66 USD po przesunięciu stopu), netto całości −9,74 USD. Bez tych 5 wierszy reszta dawała ok. **+8 USD netto** (szacunek: SL brutto −16,5 USD plus ok. 1 USD opłat). Pojedynczy pełny SL (−4,2) kasuje więc zysk ok. 50 wyjść STALE (ok. +0,08 USD brutto każde).

### 1.4 Statystyka

- Średnia brutto na zamknięcie 0,0014 USD, SD 1,006, SE 0,045. Koszt taker na zamknięcie ok. 0,07 USD.
- 95% CI średniej brutto ≈ [−0,087; +0,090]. Hipoteza „edge brutto = koszt” **nie jest odrzucona** (t ≈ 1,5), strata netto też nie jest istotna statystycznie (t ≈ −1,5).
- Poprawny wniosek: **brak wykazanego edge**, nie „brak edge”. Do rozstrzygnięcia efektu rzędu 0,07 USD potrzeba ok. 800–1600 zamknięć (przy ok. 8/dzień: 3–6 miesięcy). Zamknięcia tej samej pozycji są skorelowane, więc efektywne n jest mniejsze.

## 2. Ograniczenia analizy

- Tabela jest per noga. Grupowanie w pozycje po `(symbol, side, entry_timestamp)` jest niewiarygodne dla starszych wierszy (jedna wartość `entry_timestamp` występuje na 24–30 nogach), więc **nie liczę wyników per pozycja**. Do tego potrzebne jest powiązanie z `position_snapshots`/`position_addons` lub z zleceniami z `okx_orders`.
- Czas trzymania jest znany tylko dla wierszy z poprawnym `entry_timestamp` (od ok. 18.09).
- Nie sprawdzono, czy dokładka (`position_addons`) była aktywna w momencie każdego SL. Wartości SL (−4,2 USD przy nominale ok. 210 USD) sugerują, że dokładka nie jest tu głównym czynnikiem, ale to wnioskowanie.
- Wybór najgorszego symbolu po fakcie (BTC) jest podatny na dopasowanie do danych.

## 3. Wnioski i rekomendacje (do decyzji właściciela)

Niezależna dyskusja z subagentem Opus doprowadziła do wspólnego stanowiska (jego pierwsza teza, że STALE_* „płacą round trip za zero”, została wycofana po tych danych).

1. **Dominujący czynnik: częstość i wielkość ogona STOP-LOSS, nie opłaty ani wyjścia STALE_*.** Zmiana, która zmniejsza ogon, jest wartościowsza niż zmiany wyjść STALE_*.
2. **Zmiany ryzyka, a nie sygnału** (zgodne z `DECISIONS.md`): limit ryzyka w USD na pozycję, niższa dźwignia lub rozmiar, ograniczenie jednoczesnej ekspozycji BTC+ETH (skorelowane pary SL). Do decyzji właściciela.
3. **Egzekucja maker** (post-only) — możliwy zysk ok. 0,04 USD na zamknięcie, wymaga walidacji fill-rate i markoutu na żywo w małej skali.
4. **Kryteria stop/go ustalone z góry**: minimum ok. 800 zamknięć, przegląd co miesiąc, paper albo minimalna skala.
5. **Kontrfaktyka STALE_*** na realnych transakcjach (trzymanie do przeciwległej wstęgi lub SL) jako test tezy RFC bez zakazanych fixture'ów 24m.
6. **PR #125:** zrobić PR-3 (reset SCOUT tylko przy dodatnim netto) i PR-7a (read-only `posMode`); PR-4 jako flaga pod paper; PR-1/PR-2 odłożyć; PR-5, PR-7b i PR-8 na fixture'ach 24m odrzucić. Uwaga: kryterium „≥8/10 okien i p<0,05” w specyfikacji jest wewnętrznie sprzeczne (8/10 daje p=0,055; potrzeba 9/10).
