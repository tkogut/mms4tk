"""Guard: `daemon.py`'s two STALE_SPLIT_50 / impulse-negation STALE_FORCE_CLOSE
`reduce_position()` call sites must not request `use_post_only=True`.

2026-10-22 (PR #141) wired `use_post_only=True` into both call sites to save the
taker fee on these exits. 2026-10-25: gated back off after review found a real
correctness risk (`_attempt_post_only_reduce`'s partial-fill/cancel-race path can
fall back to a FULL-size market order after only a partial maker fill) that dwarfs
the <$1 total fee savings observed in production — see
`.ai/runs/2026-10-25-post-loss-remediation.md`. The executor's post-only machinery
stays in place (`tests/test_reduce_position_post_only.py` covers it directly) for
when that bug is fixed and the feature is re-enabled; this test only pins the
daemon's wiring, so a future re-enable is a deliberate, visible diff instead of an
accidental revert.
"""
import ast
from pathlib import Path

DAEMON_PY = Path(__file__).resolve().parents[1] / "src" / "mms4tk" / "daemon.py"


def _is_statically_false(expr: ast.AST) -> bool:
    """True for `False`, or an `and`-chain containing a literal `False` (which
    short-circuits the whole expression to False regardless of the other
    operands' runtime value)."""
    if isinstance(expr, ast.Constant) and expr.value is False:
        return True
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
        return any(_is_statically_false(v) for v in expr.values)
    return False


def _reduce_position_use_post_only_sites():
    """For every `reduce_position(...)` call in daemon.py that passes
    `use_post_only=`, yield (is_statically_false, lineno, source)."""
    tree = ast.parse(DAEMON_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "reduce_position":
            continue
        for kw in node.keywords:
            if kw.arg == "use_post_only":
                yield _is_statically_false(kw.value), node.lineno, ast.unparse(kw.value)


def test_no_reduce_position_call_site_requests_post_only():
    sites = list(_reduce_position_use_post_only_sites())
    assert len(sites) >= 2, (
        f"expected to find the SPLIT_50 and FORCE_CLOSE reduce_position call sites "
        f"with an explicit use_post_only=, found {len(sites)}: {sites}"
    )
    offenders = [(line, src) for is_false, line, src in sites if not is_false]
    assert offenders == [], (
        f"daemon.py has reduce_position() call site(s) that can request "
        f"use_post_only=True: {offenders} — this was deliberately gated off on "
        f"2026-10-25 (see .ai/runs/2026-10-25-post-loss-remediation.md); "
        f"re-enabling it requires fixing _attempt_post_only_reduce's partial-fill/"
        f"cancel-race bug first, not a silent revert of this wiring"
    )
