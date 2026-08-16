"""Tests for edge cases — error handling, empty results, and boundary conditions."""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    NetworkError,
    TimeoutError,
)

runner = CliRunner()

MOCK_EMPTY_MULTIPART = b""


MOCK_EMPTY_SEARCH_RESPONSE = json.dumps(
    {
        "total": 0,
        "start": 1,
        "page-length": 10,
        "results": [],
    }
)

MOCK_UNICODE_SEARCH_RESPONSE = json.dumps(
    {
        "total": 1,
        "start": 1,
        "page-length": 10,
        "results": [
            {
                "index": 1,
                "uri": "/doc/données/rapport-été.xml",
                "score": 8192,
                "matches": [
                    {
                        "path": "/content",
                        "match-text": [
                            "Üniversité de ",
                            {"highlight": "données"},
                            " été",
                        ],
                    }
                ],
            }
        ],
    }
)


def _mock_client_context(mock_client_cls):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client_cls.return_value = mock_client
    return mock_client


def _mock_response(content="", status_code=200, content_type="application/json"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = content
    resp.content = content.encode() if isinstance(content, str) else content
    resp.headers = {"content-type": content_type}
    resp.is_success = status_code < 400
    resp.request = MagicMock()
    resp.request.url = MagicMock()
    resp.request.url.path = "/v1/eval"
    return resp


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_empty_result_exits_zero(mock_client_cls, mock_resolve):
    mock_client = _mock_client_context(mock_client_cls)
    mock_client.post.return_value = _mock_response(
        content="", status_code=200, content_type="text/plain"
    )

    result = runner.invoke(app, ["eval", "()"])

    assert result.exit_code == 0


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_network_timeout_exits_with_code_6(mock_client_cls, mock_resolve):
    mock_client = _mock_client_context(mock_client_cls)
    mock_client.get.side_effect = TimeoutError(
        "Request timed out after 30s to ml-dev.example.com:8000"
    )

    result = runner.invoke(app, ["search", "test"])

    assert result.exit_code == 6
    assert "timed out" in result.output.lower() or "timed out" in (result.stderr or "")


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_connection_refused_exits_with_code_5(mock_client_cls, mock_resolve):
    mock_client = _mock_client_context(mock_client_cls)
    mock_client.get.side_effect = NetworkError(
        "Cannot connect to ml-dev.example.com:8000"
    )

    result = runner.invoke(app, ["search", "test"])

    assert result.exit_code == 5
    assert "connect" in result.output.lower() or "connect" in (result.stderr or "")


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_database_not_found_error(mock_client_cls, mock_resolve):
    mock_client = _mock_client_context(mock_client_cls)
    from marklogic_tool.core.exceptions import NotFoundError

    mock_client.get.side_effect = NotFoundError("Resource not found: /v1/search")

    result = runner.invoke(app, ["search", "-d", "nonexistent_db", "test"])

    assert result.exit_code == 3
    assert "not found" in result.output.lower()


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_empty_results_json_format(mock_client_cls, mock_resolve):
    mock_client = _mock_client_context(mock_client_cls)
    mock_client.get.return_value = _mock_response(
        content=MOCK_EMPTY_SEARCH_RESPONSE, content_type="application/json"
    )

    result = runner.invoke(app, ["-o", "json", "search", "xyzzy_nothing"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert data[0]["total"] == 0


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_unicode_in_search_query(mock_client_cls, mock_resolve):
    mock_client = _mock_client_context(mock_client_cls)
    mock_client.get.return_value = _mock_response(
        content=MOCK_UNICODE_SEARCH_RESPONSE, content_type="application/json"
    )

    result = runner.invoke(app, ["-o", "table", "search", "données"])

    assert result.exit_code == 0
    assert "données" in result.stdout or "rapport" in result.stdout


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_auth_failure_shows_host(mock_client_cls, mock_resolve):
    mock_client = _mock_client_context(mock_client_cls)
    mock_client.get.side_effect = AuthenticationError(
        "HTTP 401: authentication failed at ml-dev.example.com:8000"
    )

    result = runner.invoke(app, ["search", "test"])

    assert result.exit_code == 3
    assert (
        "ml-dev.example.com" in result.output
        or "authentication" in result.output.lower()
    )
