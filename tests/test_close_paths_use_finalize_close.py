"""Guard: every way a position is booked goes through `MMS4StateManager.finalize_close`.

A close path that calls `record_trade` / `on_take_profit_hit` / `on_stop_loss_hit` directly
can disagree with the shared reason table (and with the backtest) or skip the SCOUT/BASE
state machine entirely - the class of gap behind #107. Only `state_manager.py` itself may
call them (`finalize_close` -> `record_trade` -> hooks).
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "mms4tk"
FORBIDDEN = {"record_trade", "on_take_profit_hit", "on_stop_loss_hit"}


def _calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            yield name, node.lineno


def test_no_module_outside_state_manager_books_a_close_directly():
    offenders = [
        f"{p.name}:{line}:{name}"
        for p in sorted(SRC.glob("*.py"))
        if p.name != "state_manager.py"
        for name, line in _calls(p)
        if name in FORBIDDEN
    ]
    assert offenders == [], f"book closes via finalize_close(), not directly: {offenders}"


def test_daemon_books_every_close_path_through_finalize_close():
    # reconciliation, midline partial TP, TP+flip, and the stale helper that serves
    # STALE_SPLIT_50 / FORCE_CLOSE / FORCE_CLOSE_BE / BE_ESCAPE
    n = sum(1 for name, _ in _calls(SRC / "daemon.py") if name == "finalize_close")
    assert n >= 4, f"expected the daemon close paths to call finalize_close, found {n}"


def test_stale_close_paths_are_wired_to_the_booking_helper():
    calls = [name for name, _ in _calls(SRC / "daemon.py")]
    # the executed SPLIT_50 branch and the FORCE_CLOSE*/BE_ESCAPE branch
    booked = calls.count("_book_stale_close") + calls.count("_book_stale_close_if_executed")
    assert booked >= 2
