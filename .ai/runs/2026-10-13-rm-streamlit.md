# Execution plan — remove the dead legacy Streamlit dashboard files

Engine: om-auto-create-pr (steps: 7, --loop: no)
Branch: `chore/rm-streamlit` · Base: `main` @ 0ef633a · Refs: #108 (closed by #116) · Role: Builder (SWARM)

## Goal

Delete the legacy Streamlit dashboard leftovers that nothing runs, and drop `ui_dashboard/app.py` from the SSOT version-sync rule, as decided by the owner ("if these files are redundant, clean them up"). **No runtime behavior changes.**

## Context (verified; see the #108 correction and the #116 review)

- Production serves a React/Vite build through nginx (`ui_dashboard/Dockerfile`). The running container has no Python; the Dockerfile never puts `app.py` in the runtime image.
- Only `ui_dashboard/app.py` imports `streamlit`/`plotly`; the root `requirements.txt` has no Streamlit; `README.md` describes the dashboard as React/Vite.
- `ui_dashboard/style.css` is read only by `app.py`. `ui_dashboard/requirements.txt` (streamlit, pandas, plotly, ...) is used by nothing. `ui_dashboard/static/` (`manifest.json`, `sw.js`, icons) is referenced only by `app.py` (`/app/static/...`, Streamlit static serving); the React build takes its icons from the identical `ui_dashboard/public/` and generates its manifest through VitePWA; the tracked `dist/` has no reference to `/app/static`.
- `app.py` is still named in the SSOT version rule (`.agents/MEMORY.md:29-33`: "4-file sync") and in `SDLC.md:48`.

## Scope

1. `git rm`: `ui_dashboard/app.py`, `ui_dashboard/style.css`, `ui_dashboard/requirements.txt`, `ui_dashboard/static/`.
2. Remove `tests/test_ui_dashboard_syntax.py` (its only subject is gone) and drop the stale `ui_dashboard/app.py` mentions from two test docstrings (`tests/test_sqlite_concurrency_pragmas.py`, `tests/test_epoch_kpi.py`).
3. `.agents/MEMORY.md` and `SDLC.md`: the version SSOT becomes a **3-file** sync (`VERSION`, `src/mms4tk/__init__.py`, `CHANGELOG.md`).

## Non-goals

- No version bump (nothing that runs changes), no `src/` change, no change to the React sources, `Dockerfile`, `nginx.conf` or the tracked `dist/`.
- Historical records are left as they are: the old `.agents/swarm/*_handshake.json` that list `ui_dashboard/app.py`, and CHANGELOG entries.
- Does not modify `.agents/plans/`.
- **Needs Coordinator acknowledgement:** it edits Coordinator-owned governance docs (`.agents/MEMORY.md`, `SDLC.md`) to change the version-sync rule.

## Risks

- A reference I missed: mitigated by repo-wide grep (only historical records and the files above remain) and by building the React app from a copy of `ui_dashboard/` with the four items removed.
- Version tooling expecting `APP_VERSION` in `app.py`: no script in `scripts/` or `execution/` reads it (verified), so nothing breaks; a 3-file rule is enough because `api.py` reads `VERSION`.

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` including `slow`, plus `npm ci && npm run build` on a copy of `ui_dashboard/` without the removed files.

## Progress

PR: #117

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Remove the dead files

- [x] 1.1 Remove `ui_dashboard/app.py`, `style.css`, `requirements.txt` and `static/` — 7822a54
- [x] 1.2 Remove the obsolete parse test and the stale mentions in two test docstrings — 7822a54

### Phase 2: Version rule

- [x] 2.1 Change the SSOT version sync from 4 files to 3 in `.agents/MEMORY.md` and `SDLC.md` — a4cb426

### Phase 3: Validate

- [x] 3.1 The React build succeeds from a copy of `ui_dashboard/` without the removed files — a4cb426
- [x] 3.2 Full suite including slow is green — a4cb426
- [x] 3.3 Repo-wide grep: only historical records still mention the removed files — a4cb426
