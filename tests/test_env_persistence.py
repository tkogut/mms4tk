import pytest

from mms4tk.api import persist_env_config


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Run inside an empty tmp dir so persist_env_config only ever touches test files
    (it also probes '/app/.env' and '../.env', which harmlessly no-op when absent)."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_persist_env_config_preserves_inline_comment(isolated_cwd):
    """Updating a key must not swallow a trailing '# ...' comment on its line."""
    (isolated_cwd / ".env").write_text(
        "OKX_API_KEY=abc123\n"
        "MMS4_ENGINE_MODE=CLASSIC # inline comment explaining mode\n"
        "MMS4_ADAPTIVE_VR_HIGH=1.6\n"
    )

    persist_env_config({"MMS4_ENGINE_MODE": "ADAPTIVE"})

    content = (isolated_cwd / ".env").read_text()
    assert "MMS4_ENGINE_MODE=ADAPTIVE # inline comment explaining mode" in content
    assert "OKX_API_KEY=abc123" in content
    assert "MMS4_ADAPTIVE_VR_HIGH=1.6" in content


def test_persist_env_config_preserves_standalone_comments_and_unrelated_keys(isolated_cwd):
    (isolated_cwd / ".env").write_text(
        "# Header comment\n"
        "OKX_API_KEY=abc123\n"
        "MMS4_ENGINE_MODE=CLASSIC\n"
        "# a trailing comment block\n"
    )

    persist_env_config({"MMS4_ENGINE_MODE": "ADAPTIVE", "MMS4_NEW_KEY": "foo"})

    lines = (isolated_cwd / ".env").read_text().splitlines()
    assert "# Header comment" in lines
    assert "OKX_API_KEY=abc123" in lines
    assert "MMS4_ENGINE_MODE=ADAPTIVE" in lines
    assert "# a trailing comment block" in lines
    assert "MMS4_NEW_KEY=foo" in lines


def test_persist_env_config_handles_missing_trailing_newline(isolated_cwd):
    """A .env whose last line has no trailing newline must not get glued to an append."""
    (isolated_cwd / ".env").write_text("MMS4_ENGINE_MODE=CLASSIC")  # no trailing \n

    persist_env_config({"MMS4_NEW_KEY": "foo"})

    content = (isolated_cwd / ".env").read_text()
    assert "MMS4_ENGINE_MODE=CLASSICMMS4_NEW_KEY=foo" not in content
    lines = content.splitlines()
    assert "MMS4_ENGINE_MODE=CLASSIC" in lines
    assert "MMS4_NEW_KEY=foo" in lines


def test_persist_env_config_handles_missing_trailing_newline_on_update(isolated_cwd):
    """Same corruption risk applies when the last line is itself the one being updated."""
    (isolated_cwd / ".env").write_text("MMS4_ENGINE_MODE=CLASSIC")  # no trailing \n, no other keys

    persist_env_config({"MMS4_ENGINE_MODE": "ADAPTIVE", "MMS4_NEW_KEY": "foo"})

    lines = (isolated_cwd / ".env").read_text().splitlines()
    assert lines == ["MMS4_ENGINE_MODE=ADAPTIVE", "MMS4_NEW_KEY=foo"]


def test_persist_env_config_rejects_newline_injection_in_value(isolated_cwd):
    """A value containing a newline must never be able to inject a new env line."""
    (isolated_cwd / ".env").write_text("MMS4_TIME_LOCK_START=13:30\n")

    persist_env_config({"MMS4_TIME_LOCK_START": "13:30\nINJECTED_KEY=evil"})

    lines = (isolated_cwd / ".env").read_text().splitlines()
    assert "INJECTED_KEY=evil" not in lines
    assert any(l.startswith("MMS4_TIME_LOCK_START=13:30") for l in lines)
