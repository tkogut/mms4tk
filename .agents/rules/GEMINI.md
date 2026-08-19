# PROJECT SPECIFIC RULES (v1.1)
# Sync: ~/.antigravity/GEMINI.md (Global)

- Mode: Caveman Ultra+ active.
- Context: Local Project Context.

## 📌 MANDATORY APPLICATION VERSIONING DIRECTIVE:
- **Version Files**: `VERSION` (root), `src/mms4tk/__init__.py` (`__version__`), `CHANGELOG.md`, `ui_dashboard/app.py`.
- **Commit Rule**: Every code edit, bug fix, or feature commit MUST increment the application version using Semantic Versioning (`MAJOR.MINOR.PATCH`):
  - `PATCH` (+0.0.1) for bug fixes, refactoring, or minor updates (e.g. `0.5.0` -> `0.5.1`).
  - `MINOR` (+0.1.0) for new features, new endpoints, or UI additions (e.g. `0.5.0` -> `0.6.0`).
  - `MAJOR` (+1.0.0) for breaking architectural changes (e.g. `0.5.0` -> `1.0.0`).
- **Forbidden**: Under no circumstances should mismatched, stale version strings, or un-bumped commits remain in the codebase.
