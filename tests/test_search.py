"""Tests for search command."""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marklogic_tool.cli import app

runner = CliRunner()

MOCK_SEARCH_RESPONSE = json.dumps(
    {
        "snippet-format": "snippet",
        "total": 3,
        "start": 1,
        "page-length": 10,
        "results": [
            {
                "index": 1,
                "uri": "/doc/email/2021/176be87d1cdf7f00.xml",
                "score": 12288,
                "matches": [
                    {
                        "path": "/envelope/body",
                        "match-text": ["Hello ", {"highlight": "Armstrong"}, " test"],
                    }
                ],
            },
            {
                "index": 2,
                "uri": "/musicians/musician1.json",
                "score": 8192,
                "matches": [
                    {
                        "path": "/musician/lastName",
                        "match-text": [{"highlight": "Armstrong"}],
                    }
                ],
            },
            {
                "index": 3,
                "uri": "/doc/email/2021/176bf277081d4ac3.xml",
                "score": 4096,
                "matches": [],
            },
        ],
    }
)

MOCK_EMPTY_RESPONSE = json.dumps(
    {
        "total": 0,
        "start": 1,
        "page-length": 10,
        "results": [],
    }
)


def _mock_response(content=MOCK_SEARCH_RESPONSE, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = content
    resp.content = content.encode()
    resp.headers = {"content-type": "application/json"}
    resp.is_success = status_code < 400
    resp.request = MagicMock()
    resp.request.url = MagicMock()
    resp.request.url.path = "/v1/search"
    return resp


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_basic_query(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["-o", "table", "search", "Armstrong"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["q"] == "Armstrong"


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_structured_query(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    sq = '{"query": {"word-query": {"text": "test"}}}'
    result = runner.invoke(app, ["search", "--structured", sq])

    assert result.exit_code == 0
    mock_client.post.assert_called_once()


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_with_database(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["search", "-d", "mydb", "test"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["database"] == "mydb"


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_with_collection(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["search", "-c", "my-col", "test"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["collection"] == ["my-col"]


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_with_multiple_collections(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(
        app, ["search", "-c", "col1", "-c", "col2", "-c", "col3", "test"]
    )

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["collection"] == ["col1", "col2", "col3"]


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_with_directory(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["search", "--directory", "/doc/", "test"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["directory"] == "/doc/"


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_page_length(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["search", "-n", "5", "test"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["pageLength"] == 5


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_start_offset(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["search", "--start", "11", "test"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["start"] == 11


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_json_output(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["-o", "json", "search", "Armstrong"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert data[0]["total"] == 3


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_empty_results(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response(content=MOCK_EMPTY_RESPONSE)
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["-o", "table", "search", "xyzzy_nonexistent"])

    assert result.exit_code == 0


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_search_table_shows_uri_and_snippet(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["-o", "table", "search", "Armstrong"])

    assert result.exit_code == 0
    assert "musician1.json" in result.stdout


def test_search_no_query_provided():
    result = runner.invoke(app, ["search"])

    assert result.exit_code == 2


def test_search_invalid_structured_json():
    result = runner.invoke(app, ["search", "--structured", "not json"])

    assert result.exit_code == 2
