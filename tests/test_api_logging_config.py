"""Guard: importing `mms4tk.api` must leave the ROOT logger with a handler and a
real level, so `logger.info()` calls from every app module (daemon, okx_executor,
inactivity_manager, ...) are not silently dropped in production.

Found 2026-10-25: the real entrypoint (`uvicorn src.mms4tk.api:app`) never runs
`main.py`'s `logging.basicConfig()`. Python's root logger then has no handlers, so
`logging.lastResort` (WARNING+ only, unformatted) was the only thing emitting
anything — every INFO-level log line, including PR #141's own post-only exit
logging, was silently dropped. Run in a fresh subprocess so pytest's own log
capturing (which installs its own root handler) can't mask a regression.
"""
import subprocess
import sys
import textwrap


def _run_in_subprocess(env_extra=None):
    script = textwrap.dedent(
        """
        import logging, os, sys
        sys.path.insert(0, "src")
        os.environ.setdefault("MMS4_API_KEY", "test-api-auth-key-unit-test")
        import mms4tk.api  # noqa: F401 -- the side effect under test
        root = logging.getLogger()
        print(len(root.handlers), logging.getLevelName(root.getEffectiveLevel()))
        """
    )
    env = {**__import__("os").environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=60,
    )


def test_importing_api_configures_the_root_logger():
    result = _run_in_subprocess()
    assert result.returncode == 0, result.stderr
    n_handlers, level = result.stdout.strip().split()
    assert int(n_handlers) >= 1, "root logger has no handler -- INFO logs will be silently dropped"
    assert level == "INFO"


def test_mms4_log_level_env_var_is_honored():
    result = _run_in_subprocess({"MMS4_LOG_LEVEL": "WARNING"})
    assert result.returncode == 0, result.stderr
    _, level = result.stdout.strip().split()
    assert level == "WARNING"
