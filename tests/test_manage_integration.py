"""Integration tests for management commands — real MarkLogic server.

Run with: uv run pytest -m integration tests/test_manage_integration.py
"""

import json

import pytest
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import ConfigurationError

runner = CliRunner()


def _clean_output(text):
    return "\n".join(line for line in text.splitlines() if "[warning" not in line)


def _skip_if_no_config():
    try:
        resolve_profile()
    except ConfigurationError:
        pytest.skip("No MarkLogic config available")


@pytest.mark.integration
def test_db_list():
    _skip_if_no_config()
    result = runner.invoke(app, ["db", "list"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "example-content" in output or "Documents" in output


@pytest.mark.integration
def test_db_list_json():
    _skip_if_no_config()
    result = runner.invoke(app, ["-o", "json", "db", "list"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    data = json.loads(output)
    assert len(data) > 0
    assert "name" in data[0]


@pytest.mark.integration
def test_db_show():
    _skip_if_no_config()
    result = runner.invoke(app, ["db", "show", "example-content"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "example-content" in output


@pytest.mark.integration
def test_server_list():
    _skip_if_no_config()
    result = runner.invoke(app, ["server", "list"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "App-Services" in output or "example-app" in output


@pytest.mark.integration
def test_server_show():
    _skip_if_no_config()
    result = runner.invoke(app, ["server", "show", "example-app"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "8030" in output


@pytest.mark.integration
def test_host_list():
    _skip_if_no_config()
    result = runner.invoke(app, ["host", "list"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert output.strip() != ""


@pytest.mark.integration
def test_host_show():
    _skip_if_no_config()
    result = runner.invoke(app, ["host", "show"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "11" in output


@pytest.mark.integration
def test_group_list():
    _skip_if_no_config()
    result = runner.invoke(app, ["group", "list"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "Default" in output


@pytest.mark.integration
def test_group_show():
    _skip_if_no_config()
    result = runner.invoke(app, ["group", "show", "Default"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "Default" in output
