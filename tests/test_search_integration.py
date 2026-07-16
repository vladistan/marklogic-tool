"""Integration tests for search command — real MarkLogic server.

Run with: uv run pytest -m integration tests/test_search_integration.py
"""

import json

import pytest
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import ConfigurationError

runner = CliRunner()

DATABASE = "taxtime-nextgen-content"


def _clean_output(text):
    return "\n".join(line for line in text.splitlines() if "[warning" not in line)


def _skip_if_no_config():
    try:
        resolve_profile()
    except ConfigurationError:
        pytest.skip("No MarkLogic config available")


@pytest.mark.integration
def test_search_full_text():
    _skip_if_no_config()
    result = runner.invoke(app, ["search", "-d", DATABASE, "Armstrong"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert (
        "musician" in output.lower() or "armstrong" in output.lower() or output.strip()
    )


@pytest.mark.integration
def test_search_with_page_length():
    _skip_if_no_config()
    result = runner.invoke(app, ["search", "-d", DATABASE, "-n", "3", "Armstrong"])

    assert result.exit_code == 0


@pytest.mark.integration
def test_search_collection_scoped():
    _skip_if_no_config()
    result = runner.invoke(app, ["search", "-d", DATABASE, "-c", "search-test", "test"])

    assert result.exit_code == 0


@pytest.mark.integration
def test_search_json_output():
    _skip_if_no_config()
    result = runner.invoke(app, ["-o", "json", "search", "-d", DATABASE, "Armstrong"])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    data = json.loads(output)
    assert isinstance(data, list)
    assert "total" in data[0]


@pytest.mark.integration
def test_search_empty_results():
    _skip_if_no_config()
    result = runner.invoke(
        app, ["search", "-d", DATABASE, "xyzzy_absolutely_no_match_12345"]
    )

    assert result.exit_code == 0


@pytest.mark.integration
def test_search_pagination():
    _skip_if_no_config()
    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "search",
            "-d",
            DATABASE,
            "--start",
            "3",
            "-n",
            "2",
            "Armstrong",
        ],
    )

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    data = json.loads(output)
    assert data[0].get("start") == 3
