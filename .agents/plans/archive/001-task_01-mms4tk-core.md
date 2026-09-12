# PLAN 001: TASK 01 - Inicjalizacja rdzenia mms4tk (OKX + FastAPI + State Machine)

## Status: COMPLETE
**Worktree:** `tmp/worktrees/task_01`  
**Branch:** `task_01`  

---

## 1. CEL
Stworzenie struktury pakietu `src/mms4tk/` z podstawowymi modułami:
- `state_manager.py`: `MMS4StateManager` z przejściami BASE (x1.0) <-> SCOUT (x0.1) na podstawie SL 2% / TP.
- `okx_executor.py`: Klasa `OKXExecutor` korzystająca z `ccxt.async_support.okx` (R-SEC-01 env validation, Hedge mode).
- `api.py`: FastAPI server z routerem `/api/v1` i endpointem `POST /api/v1/emergency/killall`.
- `requirements.txt`: Zależności Python (`fastapi`, `uvicorn`, `ccxt`, `pydantic`, `python-dotenv`).

---

## 2. ETAPY WYKONANIA (BUILDER)
- [x] Utworzenie struktury katalogów `tmp/worktrees/task_01/src/mms4tk/`
- [x] Implem `src/mms4tk/__init__.py`
- [x] Implem `src/mms4tk/state_manager.py` (MMS4StateManager, Enum stany, -2% SL -> SCOUT, 1st TP -> BASE)
- [x] Implem `src/mms4tk/okx_executor.py` (OKX API via env vars, ccxt async)
- [x] Implem `src/mms4tk/api.py` (FastAPI, /api/v1/emergency/killall, /api/v1/state)
- [x] Implem `requirements.txt`
- [x] Raport z wykonania w `.agents/swarm/task_01_report.md`

---

## 3. AUDYT & HANDSHAKE
- [x] Weryfikacja spójności typowania i braku wycieków sekretów (`py_compile` ok, R-SEC-01 ok).
- [x] Handshake: Plan-Alignment and Math-Consistency checked. Ready for Coordinator Push.
