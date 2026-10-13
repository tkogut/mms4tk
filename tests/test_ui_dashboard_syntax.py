"""Every Python file under ui_dashboard/ must at least parse.

`ui_dashboard/app.py` is the legacy Streamlit app: production serves the React/Vite build through
nginx and never runs it, but it is still one of the four SSOT version files (`APP_VERSION`). A stray
duplicated line left it unparseable from 2026-08-19 until #108, and nothing noticed for eight weeks.
Parsing (not importing: Streamlit is not installed here) is enough to catch that class of mistake.
"""
import ast
import os
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[1] / "ui_dashboard"
SKIP_DIRS = {"node_modules", "dist"}


def _python_files():
    """All *.py under ui_dashboard/, pruning node_modules/dist while walking (not after)."""
    found = []
    for root, dirnames, filenames in os.walk(DASHBOARD):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found += [Path(root) / f for f in filenames if f.endswith(".py")]
    return sorted(found)


FILES = _python_files()


def test_dashboard_has_python_files_to_check():
    assert FILES, "expected at least ui_dashboard/app.py"


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(DASHBOARD)))
def test_dashboard_python_file_parses(path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
