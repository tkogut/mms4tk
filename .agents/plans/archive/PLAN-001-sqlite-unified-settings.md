# 📋 PLAN-001: Unified SQLite System Settings (Single Source of Truth)

**Goal:** Migrate all dynamic runtime, strategy, risk, and adaptive engine settings to a dedicated `system_settings` SQLite table in `.agents/swarm/logs.db`, establishing a single, ACID-compliant source of truth that is 100% immune to Docker restarts or environment file collisions.

---

## 🏗️ Architecture & Schema Design

### 1. SQLite Table: `system_settings`
```sql
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT DEFAULT 'system'
);
```

### 2. Managed Settings
- **Adaptive Engine:** `engine_mode` (CLASSIC / ADAPTIVE), `adaptive_vr_high`, `adaptive_vr_low`, `time_lock_start`, `time_lock_end`
- **Global Portfolio & Risk:** `total_max_portfolio_margin_usd`, `base_sl_pct`, `addon_sl_pct`, `base_allocation`, `addon_allocation`
- **Symbol Settings:** Synced with `symbol_configurations` table.

---

## 🚀 Implementation Steps

1. **Phase 1: Database Methods (`src/mms4tk/logger_db.py`)**
   - Create table `system_settings` in `_init_db()`.
   - Add seed migration if empty (copying current env/default values).
   - Implement `get_setting(key, default)`, `get_all_settings()`, `set_setting(key, value, updated_by)`, `set_settings_bulk(dict, updated_by)`.

2. **Phase 2: API & Engine Integration (`src/mms4tk/api.py`, `daemon.py`)**
   - Refactor `GET /api/v1/settings` and `POST /api/v1/settings` to read/write directly via `logger_db`.
   - Update `plan_allocation` and `evaluate_signals` to resolve parameters from `logger_db` with fallback.

3. **Phase 3: Unit Testing & Verification**
   - Add unit tests `tests/test_system_settings_db.py`.
   - Execute complete test suite (`pytest tests/ -q`).

4. **Phase 4: Safe Zero-Loss Deployment (Rule G-03)**
   - Bump SemVer to `v0.32.0` across all version manifests.
   - Engage Maintenance Lock on live VPS (`python3 scripts/maintenance.py --enable`).
   - Push and deploy to production VPS (`docker compose up -d`).
   - Verify `GET /api/v1/settings` on live VPS and release Maintenance Lock.
