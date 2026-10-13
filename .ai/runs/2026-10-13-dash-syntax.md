# Execution plan — fix the unparseable legacy `ui_dashboard/app.py` (#108)

Engine: om-auto-create-pr (steps: 4, --loop: no)
Branch: `fix/dash-syntax` · Base: `main` @ 2d3dd7d · Issue: #108 · Role: Builder (SWARM)

## Goal

`ui_dashboard/app.py` parses again, and a test keeps it that way. **No production behavior changes**: see the correction on #108.

## Context (corrected assessment, see the #108 comment)

- Production serves a React/Vite build through nginx (`ui_dashboard/Dockerfile` never copies `app.py` into the runtime image; the running container has no Python at all). `app.py` is a legacy Streamlit file nothing runs.
- The defect: a duplicated terminator line `""", unsafe_allow_html=True)` (lines 65-66 of `load_mobile_pwa_assets`), introduced in `d861286` (2026-08-19), makes the file a `SyntaxError`. It has no runtime effect; the earlier claim that a rebuild would break the dashboard was wrong and is withdrawn on #108.
- The file is still one of the four SSOT version files (`APP_VERSION`), so keeping it valid is hygiene, and a tool that bumps the version in it assumes it is intact.

## Scope

1. Remove the duplicated line (CRLF file: one line, nothing else touched).
2. Add `tests/test_ui_dashboard_syntax.py`: parse every `*.py` under `ui_dashboard/` (excluding `node_modules`, `dist`) with `ast`, so this class of regression fails CI-style tests instead of waiting eight weeks.

## Non-goals

- No version bump (nothing that runs changes) and no `src/` change.
- Not retiring the Streamlit files or changing the SSOT version rule (open question on #108; touches Coordinator-owned `.agents/MEMORY.md`).
- Does not modify `.agents/plans/`.

## Risks

- Minimal: one line removed from a file nothing executes; the test only parses. Line endings must stay CRLF (verified by diff stat vs `--ignore-cr-at-eol`).

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` including `slow` (expected: all green, 667 + the new parametrized cases).

## Progress

PR: #116

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Fix and guard

- [x] 1.1 Remove the duplicated `""", unsafe_allow_html=True)` line from `ui_dashboard/app.py` — da10697
- [x] 1.2 Add `tests/test_ui_dashboard_syntax.py` parsing every Python file under `ui_dashboard/` — 0bb114e

### Phase 2: Validate

- [x] 2.1 Full suite including slow: all green; the new test fails on the unfixed file — 0bb114e
- [x] 2.2 Confirm only `ui_dashboard/app.py` (one line) and the new test changed; no `src/` or version change — 0bb114e
