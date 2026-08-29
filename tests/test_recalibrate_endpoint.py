"""PLAN-010 Phase 2 (§4.2/§4.3): the async recalibration endpoints.

Key architectural property under test: `POST /recalibrate` MUST return immediately
(202 + job_id) without blocking on the calibration itself — `api.py` and the live
trading daemon share one asyncio event loop, so an inline multi-minute computation
would stall live reconciliation/self-healing-SL for its full duration (see the
CALIBRATION_EXECUTOR docstring in api.py for the measured timings that motivated this).
"""
import json
import time

import pytest
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from mms4tk.logger_db import LoggerDB
from mms4tk import api


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "recalibrate_test.db")


@pytest.fixture
def client(tmp_db):
    orig_logger_db = api.logger_db
    orig_executor = api.CALIBRATION_EXECUTOR

    test_db = LoggerDB(db_path=tmp_db)
    api.logger_db = test_db
    # A ThreadPoolExecutor (not the real ProcessPoolExecutor) so a monkeypatched
    # `run_calibration_job` in THIS test process is actually observed by the
    # dispatched call — a real subprocess would re-import the module fresh from
    # disk and never see the patch. The property under test (does run_in_executor
    # block the caller) is executor-agnostic; only the isolation-primitive differs.
    api.CALIBRATION_EXECUTOR = ThreadPoolExecutor(max_workers=1)

    yield TestClient(api.app, raise_server_exceptions=False)

    api.CALIBRATION_EXECUTOR.shutdown(wait=True)
    api.logger_db = orig_logger_db
    api.CALIBRATION_EXECUTOR = orig_executor


HEADERS = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}


def _make_eligible(logger_db_instance, symbol="BTC/USDC", n=5):
    """Lower the eligibility bar for a symbol and insert `n` clean closed trades so
    `/recalibrate` doesn't 409 in tests that don't care about the eligibility gate
    itself."""
    logger_db_instance.set_setting(f"trend_filter_eligibility_threshold:{symbol}", str(n))
    for i in range(n):
        logger_db_instance.upsert_closed_trade(
            trade_id=f"t{i}", timestamp=f"2026-08-{16 + i:02d}T09:00:00+00:00",
            symbol="BTC-USD_UM_XPERP-310404", side="SHORT",
            entry_price=80000.0 + i * 1000, exit_price=79000.0 + i * 1000,
            amount=2.0, pnl_usd=20.0, pnl_pct=0.0125, is_win=True,
        )


def test_recalibrate_returns_409_when_not_eligible(client):
    resp = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert "clean_trade_count" in body
    assert "eligibility_threshold" in body


def test_recalibrate_requires_api_key(client):
    resp = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate")
    assert resp.status_code == 401


def test_recalibrate_returns_202_and_job_id_when_eligible(client, monkeypatch):
    _make_eligible(api.logger_db)
    # Avoid a real network fetch / multi-minute grid search in this test — only the
    # dispatch contract (202, job_id, non-blocking) is under test here.
    monkeypatch.setattr(
        "mms4tk.backtest_engine.run_calibration_job",
        lambda job_id, symbol, db_path: None,
    )

    resp = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["symbol"] == "BTC/USDC"
    assert "job_id" in body and body["job_id"]


def test_recalibrate_does_not_block_the_request_even_when_the_job_is_slow(client, monkeypatch):
    """The concrete regression guard for PLAN-010 §4.2's core architectural decision:
    the HTTP response must land almost immediately, well before a slow job finishes."""
    _make_eligible(api.logger_db)

    def slow_job(job_id, symbol, db_path):
        time.sleep(2.0)  # simulates a real multi-second-to-multi-minute calibration

    monkeypatch.setattr("mms4tk.backtest_engine.run_calibration_job", slow_job)

    start = time.monotonic()
    resp = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    assert elapsed < 1.0, f"POST /recalibrate took {elapsed:.2f}s — it must return before the job finishes, not after"


def test_recalibrate_status_transitions_pending_to_done(client, monkeypatch):
    _make_eligible(api.logger_db)
    monkeypatch.setattr(
        "mms4tk.backtest_engine.run_calibration_job",
        lambda job_id, symbol, db_path: api.logger_db.update_calibration_run(
            job_id, status="done", verdict="OVERFIT_REJECTED", result_json=json.dumps({"note": "fake"}),
        ),
    )

    resp = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)
    job_id = resp.json()["job_id"]

    # ThreadPoolExecutor(max_workers=1) processes the submission asynchronously;
    # give it a brief moment to finish (it sleeps for nothing, so this is generous).
    for _ in range(20):
        status_resp = client.get(f"/api/v1/symbols/BTC%2FUSDC/recalibrate/status/{job_id}")
        if status_resp.json()["status"] == "done":
            break
        time.sleep(0.05)

    body = status_resp.json()
    assert body["status"] == "done"
    assert body["verdict"] == "OVERFIT_REJECTED"
    assert body["result"] == {"note": "fake"}


def test_recalibrate_returns_existing_job_instead_of_duplicating(client, monkeypatch):
    """Two POSTs for the same symbol before the first job finishes must return the
    SAME job_id, not enqueue a second one that duplicates the (potentially
    minutes-long) computation."""
    _make_eligible(api.logger_db)
    monkeypatch.setattr(
        "mms4tk.backtest_engine.run_calibration_job",
        lambda job_id, symbol, db_path: time.sleep(1.0),  # still "running" for a bit
    )

    first = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)
    second = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)

    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_recalibrate_status_404_for_unknown_job(client):
    resp = client.get("/api/v1/symbols/BTC%2FUSDC/recalibrate/status/does-not-exist")
    assert resp.status_code == 404


def test_recalibrate_never_writes_trend_filter_settings(client, monkeypatch):
    """`/recalibrate` is analysis-only — it must never touch the live filter
    parameters. Applying a result is a separate call to the existing
    `POST /api/v1/symbols/update`, never automatic here."""
    _make_eligible(api.logger_db)
    monkeypatch.setattr(
        "mms4tk.backtest_engine.run_calibration_job",
        lambda job_id, symbol, db_path: api.logger_db.update_calibration_run(
            job_id, status="done", verdict="VALIDATED",
            result_json=json.dumps({"recommended_params": {"lookback": 5, "threshold": 0.002}}),
        ),
    )

    before = api.logger_db.get_all_settings().get("tma_trend_filter_enabled:BTC/USDC")
    resp = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)
    job_id = resp.json()["job_id"]
    for _ in range(20):
        if client.get(f"/api/v1/symbols/BTC%2FUSDC/recalibrate/status/{job_id}").json()["status"] == "done":
            break
        time.sleep(0.05)

    after = api.logger_db.get_all_settings().get("tma_trend_filter_enabled:BTC/USDC")
    assert before == after


def test_recalibration_history_lists_most_recent_first(client, monkeypatch):
    """PLAN-010 §5.2.3: the 'historia kalibracji' panel needs runs newest-first,
    each with enough summary fields to render without a second round-trip."""
    _make_eligible(api.logger_db)
    monkeypatch.setattr(
        "mms4tk.backtest_engine.run_calibration_job",
        lambda job_id, symbol, db_path: api.logger_db.update_calibration_run(
            job_id, status="done", verdict="OVERFIT_REJECTED", result_json=json.dumps({"note": "x"}),
        ),
    )

    job_ids = []
    for _ in range(3):
        resp = client.post("/api/v1/symbols/BTC%2FUSDC/recalibrate", headers=HEADERS)
        jid = resp.json()["job_id"]
        job_ids.append(jid)
        for _ in range(20):
            if client.get(f"/api/v1/symbols/BTC%2FUSDC/recalibrate/status/{jid}").json()["status"] == "done":
                break
            time.sleep(0.02)

    history = client.get("/api/v1/symbols/BTC%2FUSDC/recalibrate/history").json()
    assert history["symbol"] == "BTC/USDC"
    assert len(history["runs"]) == 3
    assert history["runs"][0]["job_id"] == job_ids[-1]  # most recent first
    assert all(r["verdict"] == "OVERFIT_REJECTED" for r in history["runs"])
    assert "result_json" not in history["runs"][0]  # lightweight summary, not the full blob


def test_recalibration_history_empty_for_symbol_with_no_runs(client):
    history = client.get("/api/v1/symbols/ETH%2FUSDC/recalibrate/history").json()
    assert history["runs"] == []


def test_recalibration_status_exposes_null_epoch_when_unset(client):
    status = client.get("/api/v1/symbols/BTC%2FUSDC/recalibration-status").json()
    assert status["data_collection_epoch_start"] is None


def test_recalibration_status_exposes_epoch_once_set(client):
    """2026-08-29: allocation reduced + trading unlocked -> a symbol's clean count
    should only reflect trades from that point on, and the API must surface WHEN
    that boundary is, not just apply it silently."""
    api.logger_db.set_setting("data_collection_epoch_start:BTC/USDC", "2026-08-29T16:00:00+00:00")
    status = client.get("/api/v1/symbols/BTC%2FUSDC/recalibration-status").json()
    assert status["data_collection_epoch_start"] == "2026-08-29T16:00:00+00:00"
