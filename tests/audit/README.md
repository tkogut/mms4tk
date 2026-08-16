# Skrypty audytowe MMS4

Weryfikacja end-to-end ustaleń audytu z 2026-08-16 (`docs/AUDIT_MMS4_2026-08-16.md`).
Konwencja: **FAIL = defekt obecny**. Asercje opisują właściwość pożądaną.

```bash
for f in tests/audit/audit_*.py; do python3 "$f"; done
```

Stan przed naprawą: 25/61 PASS. Po naprawie: 61/61 PASS.

Skrypty używają wyłącznie mocków i danych syntetycznych — nigdy nie tworzą
`OKXExecutor` z poświadczeniami i nie wysyłają zleceń na giełdę.
