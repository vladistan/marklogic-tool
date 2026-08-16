"""`config add` stores the password by reference.

A plaintext password on disk outlives the command that wrote it.

The reference must also be readable, so these tests resolve it end to end.
"""

# cspell:ignore cfgtest fmin

import os
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.config import resolve_profile

runner = CliRunner()

SECRET_VALUE = "resolved-from-env-not-disk"  # pragma: allowlist secret


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MARKLOGIC_CONFIG", str(path))
    monkeypatch.delenv("ML_PROFILE", raising=False)
    monkeypatch.delenv("ML_PASSWORD", raising=False)
    monkeypatch.delenv("ML_HOST", raising=False)
    monkeypatch.delenv("ML_USERNAME", raising=False)
    return path


def _add(*extra):
    return runner.invoke(
        app,
        [
            "config",
            "add",
            "--profile",
            "scratch",
            "--host",
            "ml.example.com",
            "--username",
            "admin",
            "--password-ref",
            "env:SCRATCH_SECRET",
            *extra,
        ],
    )


def test_profile_can_be_added_without_hand_editing(config_file):
    result = _add()

    assert result.exit_code == 0
    assert config_file.exists()
    assert "[profiles.scratch]" in config_file.read_text()


def test_no_plaintext_password_is_written(config_file):
    """The checkpoint's grep: only a reference may appear."""
    _add()
    text = config_file.read_text()

    password_lines = [ln for ln in text.splitlines() if "password" in ln.lower()]
    assert password_lines
    for line in password_lines:
        assert "env:SCRATCH_SECRET" in line
    assert SECRET_VALUE not in text


def test_a_literal_password_is_refused(config_file):
    result = runner.invoke(
        app,
        [
            "config",
            "add",
            "--profile",
            "scratch",
            "--host",
            "h",
            "--username",
            "u",
            "--password-ref",
            "hunter2",  # pragma: allowlist secret
        ],
    )

    assert result.exit_code == 2
    assert "literal" in result.stderr.lower()
    assert not config_file.exists(), "nothing may be written on a refusal"


def test_the_literal_is_never_echoed_back(config_file):
    """Refusing a secret must not print it."""
    literal = "hunter2-should-not-appear"  # pragma: allowlist secret
    result = runner.invoke(
        app,
        [
            "config",
            "add",
            "--profile",
            "s",
            "--host",
            "h",
            "--username",
            "u",
            "--password-ref",
            literal,
        ],
    )

    assert result.exit_code == 2
    assert literal not in result.stdout
    assert literal not in result.stderr


def test_the_written_reference_actually_resolves(config_file, monkeypatch):
    """A reference the tool cannot read back would be useless."""
    _add()
    monkeypatch.setenv("SCRATCH_SECRET", SECRET_VALUE)

    profile = resolve_profile(profile_name="scratch")

    assert profile.password.get_secret_value() == SECRET_VALUE
    assert profile.password.get_secret_value() != "env:SCRATCH_SECRET"
    assert profile.host == "ml.example.com"


def test_a_literal_password_in_an_existing_config_still_works(config_file):
    """Existing hand-written configs must not change behaviour."""
    config_file.write_text(
        'default_profile = "legacy"\n\n'
        "[profiles.legacy]\n"
        'host = "old.example.com"\n'
        'username = "admin"\n'
        'password = "a-literal-password"\n'  # pragma: allowlist secret
    )

    profile = resolve_profile(profile_name="legacy")

    assert profile.password.get_secret_value() == "a-literal-password"


def test_an_existing_profile_is_not_silently_overwritten(config_file):
    _add()
    before = config_file.read_text()

    result = _add()

    assert result.exit_code == 2
    assert "already exists" in result.stderr
    assert config_file.read_text() == before


def test_rest_port_is_omitted_unless_given(config_file):
    """No default: an invented rest_port would count the wrong corpus."""
    _add()
    assert "rest_port" not in config_file.read_text()


def test_rest_port_is_written_when_given(config_file):
    _add("--rest-port", "8030")
    assert "rest_port = 8030" in config_file.read_text()


def test_a_new_config_file_is_not_world_readable(config_file):
    _add()
    mode = config_file.stat().st_mode

    assert not mode & 0o004, "a fresh config must not be world-readable"


def test_adding_to_an_existing_file_keeps_earlier_profiles(config_file):
    config_file.write_text(
        'default_profile = "first"\n\n'
        "[profiles.first]\n"
        'host = "one.example.com"\n'
        'username = "admin"\n'
        'password = "env:FIRST_SECRET"\n'  # pragma: allowlist secret
    )

    result = _add()

    assert result.exit_code == 0
    text = config_file.read_text()
    assert "[profiles.first]" in text
    assert "[profiles.scratch]" in text


def test_quotes_in_a_value_cannot_break_out_of_the_toml_string(config_file):
    """The block is rendered by hand, so escaping is asserted, not assumed."""
    result = runner.invoke(
        app,
        [
            "config",
            "add",
            "--profile",
            "odd",
            "--host",
            'we"ird',
            "--username",
            "admin",
            "--password-ref",
            "env:SCRATCH_SECRET",
        ],
    )

    assert result.exit_code == 0
    with patch.dict(os.environ, {"SCRATCH_SECRET": SECRET_VALUE}):
        profile = resolve_profile(profile_name="odd")
    assert profile.host == 'we"ird'


HOSTILE = [
    ("newline", "ad\nmin"),
    ("carriage-return", "ad\rmin"),
    ("tab", "ad\tmin"),
    ("control-01", "ad\x01min"),
    ("del-7f", "ad\x7fmin"),
    ("quote", 'ad"min'),
    ("backslash", "ad\\min"),
    ("both", 'ad"\\\nmin'),
]


@pytest.mark.parametrize("label,value", HOSTILE, ids=[h[0] for h in HOSTILE])
def test_hostile_values_round_trip_through_write_then_read(
    config_file, monkeypatch, label, value
):
    """A value that writes but does not read back.

    A raw newline inside a TOML basic string made tomllib reject the file. Every command
    then died at exit 1, and recovery meant editing config.toml by hand.
    """
    result = runner.invoke(
        app,
        [
            "config",
            "add",
            "--profile",
            "scratch",
            "--host",
            "ml.example.com",
            "--username",
            value,
            "--password-ref",
            "env:SCRATCH_SECRET",
        ],
    )

    assert result.exit_code == 0, f"{label}: {result.stderr}"

    # It must parse back, and the config must remain usable.
    monkeypatch.setenv("SCRATCH_SECRET", SECRET_VALUE)
    profile = resolve_profile(profile_name="scratch")
    assert profile.username == value, f"{label} did not survive the round trip"

    listed = runner.invoke(app, ["config", "list"])
    assert listed.exit_code == 0, f"{label} left the config unreadable"


def test_control_characters_are_escaped_not_written_raw():
    from marklogic_tool.commands.config_cmd import _toml_str

    rendered = _toml_str("a\nb\tc\x01d")

    assert "\n" not in rendered
    assert "\t" not in rendered
    assert "\x01" not in rendered
    assert "\\n" in rendered
    assert "\\u0001" in rendered


def test_the_round_trip_guard_actually_refuses(config_file, monkeypatch):
    """Prove the guard fires. It is defence in depth.

    Correct escaping makes every value round-trip, so ordinary use never reaches this
    refusal. The test breaks the renderer back to the naive version instead.
    """
    import marklogic_tool.commands.config_cmd as mod

    def naive(value: object) -> str:
        text = str(value)
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    monkeypatch.setattr(mod, "_toml_str", naive)

    result = runner.invoke(
        app,
        [
            "config",
            "add",
            "--profile",
            "scratch",
            "--host",
            "ml.example.com",
            "--username",
            "ad\nmin",
            "--password-ref",
            "env:SCRATCH_SECRET",
        ],
    )

    assert result.exit_code == 2
    assert "refusing to write" in result.stderr
    assert not config_file.exists(), "a refusal must leave nothing on disk"
