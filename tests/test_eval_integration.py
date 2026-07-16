"""Integration tests for eval command — real MarkLogic server.

Run with: uv run pytest -m integration tests/test_eval_integration.py
"""

import pytest
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import ConfigurationError

runner = CliRunner()


def _clean_output(text):
    """Strip structlog warning lines from CLI output."""
    return "\n".join(line for line in text.splitlines() if "[warning" not in line)


def _skip_if_no_config():
    try:
        resolve_profile()
    except ConfigurationError:
        pytest.skip("No MarkLogic config available")


@pytest.mark.integration
def test_eval_xquery_database_name():
    _skip_if_no_config()
    result = runner.invoke(app, ["eval", "xdmp:database-name(xdmp:database())"])

    assert result.exit_code == 0
    assert result.stdout.strip() != ""


@pytest.mark.integration
def test_eval_javascript_database_name():
    _skip_if_no_config()
    result = runner.invoke(app, ["eval", "-j", "xdmp.databaseName(xdmp.database())"])

    assert result.exit_code == 0
    assert result.stdout.strip() != ""


@pytest.mark.integration
def test_eval_xquery_and_javascript_return_same():
    _skip_if_no_config()
    xq = runner.invoke(app, ["eval", "xdmp:database-name(xdmp:database())"])
    js = runner.invoke(app, ["eval", "-j", "xdmp.databaseName(xdmp.database())"])

    assert xq.exit_code == 0
    assert js.exit_code == 0
    assert _clean_output(xq.stdout).strip() == _clean_output(js.stdout).strip()


@pytest.mark.integration
def test_eval_syntax_error_returns_error():
    _skip_if_no_config()
    result = runner.invoke(app, ["eval", "this is not valid xquery !!!"])

    assert result.exit_code != 0


@pytest.mark.integration
def test_eval_multiple_results():
    _skip_if_no_config()
    result = runner.invoke(app, ["eval", "(1, 2, 3)"])

    assert result.exit_code == 0
    lines = _clean_output(result.stdout).strip().splitlines()
    assert len(lines) == 3


@pytest.mark.integration
def test_eval_json_output():
    _skip_if_no_config()
    result = runner.invoke(app, ["-o", "json", "eval", "(1, 2, 3)"])

    assert result.exit_code == 0
    import json

    data = json.loads(_clean_output(result.stdout))
    assert len(data) == 3


@pytest.mark.integration
def test_eval_empty_sequence():
    _skip_if_no_config()
    result = runner.invoke(app, ["eval", "()"])

    assert result.exit_code == 0
    assert _clean_output(result.stdout).strip() == ""


@pytest.mark.integration
def test_eval_host_name():
    _skip_if_no_config()
    result = runner.invoke(app, ["eval", "xdmp:host-name()"])

    assert result.exit_code == 0
    assert result.stdout.strip() != ""
