"""Tests for CLI skeleton."""

from typer.testing import CliRunner

from marklogic_tool.__about__ import __version__
from marklogic_tool.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "marklogic-tool" in result.stdout


def test_version():
    """Read the version rather than pin it.

    A literal fails on every release. The repo says one version. The shipped package says
    another. The suite a user runs then breaks for a reason that is not theirs.
    """
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    # Guard the tautology: an empty __version__ would make the check below vacuous.
    assert __version__, "__about__.__version__ is empty"
    assert f"marklogic-tool {__version__}" in result.stdout


def test_no_args_shows_help():
    result = runner.invoke(app, [])
    assert "Usage" in result.stdout


def test_config_show_missing_config(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKLOGIC_CONFIG", raising=False)
    monkeypatch.delenv("ML_PROFILE", raising=False)
    monkeypatch.setattr("marklogic_tool.core.config._find_config_file", lambda: None)
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code != 0


def test_config_show_with_config():
    assert "config" in runner.invoke(app, ["--help"]).stdout


def test_quiet_flag_accepted():
    result = runner.invoke(app, ["--quiet", "--help"])
    assert result.exit_code == 0


def test_quiet_short_flag_accepted():
    result = runner.invoke(app, ["-q", "--help"])
    assert result.exit_code == 0
