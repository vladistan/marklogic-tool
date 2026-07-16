"""Integration tests for document retrieval — real MarkLogic server.

Run with: uv run pytest -m integration tests/test_doc_integration.py
"""

import json

import pytest
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import ConfigurationError

runner = CliRunner()

DATABASE = "taxtime-nextgen-content"
KNOWN_XML_URI = "/doc/email/2021/176be87d1cdf7f00.xml"
KNOWN_JSON_URI = "/musicians/musician1.json"


def _clean_output(text):
    return "\n".join(line for line in text.splitlines() if "[warning" not in line)


def _skip_if_no_config():
    try:
        resolve_profile()
    except ConfigurationError:
        pytest.skip("No MarkLogic config available")


@pytest.mark.integration
def test_doc_get_xml():
    _skip_if_no_config()
    result = runner.invoke(app, ["doc", "get", "-d", DATABASE, KNOWN_XML_URI])

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert "<" in output


@pytest.mark.integration
def test_doc_get_json():
    _skip_if_no_config()
    result = runner.invoke(
        app, ["doc", "get", "-d", DATABASE, "-f", "json", KNOWN_JSON_URI]
    )

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    parsed = json.loads(output)
    assert isinstance(parsed, dict)


@pytest.mark.integration
def test_doc_get_metadata():
    _skip_if_no_config()
    result = runner.invoke(
        app, ["doc", "get", "-d", DATABASE, "--metadata", KNOWN_XML_URI]
    )

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    assert output.strip() != ""


@pytest.mark.integration
def test_doc_get_not_found():
    _skip_if_no_config()
    result = runner.invoke(
        app, ["doc", "get", "-d", DATABASE, "/does/not/exist/ever.xml"]
    )

    assert result.exit_code == 1


@pytest.mark.integration
def test_doc_get_metadata_json_output():
    _skip_if_no_config()
    result = runner.invoke(
        app, ["-o", "json", "doc", "get", "-d", DATABASE, "--metadata", KNOWN_JSON_URI]
    )

    assert result.exit_code == 0
    output = _clean_output(result.stdout)
    parsed = json.loads(output)
    assert isinstance(parsed, list)
