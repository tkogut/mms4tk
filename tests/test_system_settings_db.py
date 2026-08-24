import os
import pytest
import tempfile
from fastapi.testclient import TestClient
from mms4tk.logger_db import LoggerDB
from mms4tk.api import app, logger_db


def test_logger_db_system_settings():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = LoggerDB(db_path=db_path)
        
        # Test empty
        assert db.get_setting("non_existent") is None
        assert db.get_setting("non_existent", "default_val") == "default_val"
        
        # Test set and get
        db.set_setting("engine_mode", "ADAPTIVE", updated_by="test_suite")
        assert db.get_setting("engine_mode") == "ADAPTIVE"
        
        # Test bulk set
        db.set_settings_bulk({
            "adaptive_vr_high": "1.25",
            "adaptive_vr_low": "0.75",
            "time_lock_start": "12:00",
            "time_lock_end": "13:00"
        }, updated_by="test_suite")
        
        all_s = db.get_all_settings()
        assert all_s["engine_mode"] == "ADAPTIVE"
        assert all_s["adaptive_vr_high"] == "1.25"
        assert all_s["adaptive_vr_low"] == "0.75"
        assert all_s["time_lock_start"] == "12:00"
        assert all_s["time_lock_end"] == "13:00"
        
        # Test persistence across re-instantiation
        db2 = LoggerDB(db_path=db_path)
        assert db2.get_setting("engine_mode") == "ADAPTIVE"
        assert db2.get_setting("adaptive_vr_high") == "1.25"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_api_settings_endpoints_with_sqlite():
    client = TestClient(app)
    api_key = os.getenv("MMS4_API_KEY", "test_key")
    headers = {"X-MMS4-API-KEY": api_key}
    
    orig_env = {
        "MMS4_ENGINE_MODE": os.getenv("MMS4_ENGINE_MODE", "CLASSIC"),
        "MMS4_ADAPTIVE_VR_HIGH": os.getenv("MMS4_ADAPTIVE_VR_HIGH", "1.6"),
        "MMS4_ADAPTIVE_VR_LOW": os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8"),
    }
    
    try:
        # 1. Update settings via POST
        post_res = client.post(
            "/api/v1/settings/update",
            json={
                "engine_mode": "ADAPTIVE",
                "adaptive_vr_high": 1.45,
                "adaptive_vr_low": 0.70,
                "time_lock_start": "11:30",
                "time_lock_end": "12:30"
            },
            headers=headers
        )
        assert post_res.status_code == 200
        data = post_res.json()
        assert data["status"] == "success"
        assert data["engine_mode"] == "ADAPTIVE"
        assert data["adaptive_vr_high"] == 1.45
        assert data["adaptive_vr_low"] == 0.70
        assert data["time_lock_start"] == "11:30"
        assert data["time_lock_end"] == "12:30"
        
        # 2. Verify settings via GET
        get_res = client.get("/api/v1/settings")
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["engine_mode"] == "ADAPTIVE"
        assert get_data["adaptive_vr_high"] == 1.45
        assert get_data["adaptive_vr_low"] == 0.70
        assert get_data["time_lock_start"] == "11:30"
        assert get_data["time_lock_end"] == "12:30"
    finally:
        # Restore original state
        for k, v in orig_env.items():
            os.environ[k] = v
        if logger_db:
            logger_db.set_settings_bulk({
                "engine_mode": orig_env["MMS4_ENGINE_MODE"],
                "adaptive_vr_high": orig_env["MMS4_ADAPTIVE_VR_HIGH"],
                "adaptive_vr_low": orig_env["MMS4_ADAPTIVE_VR_LOW"],
            }, updated_by="test_cleanup")
