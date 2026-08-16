"""Tests for document retrieval command."""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marklogic_tool.cli import app

runner = CliRunner()


def _mock_response(content="<doc/>", content_type="application/xml", status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = content
    resp.content = content.encode() if isinstance(content, str) else content
    resp.headers = {"content-type": content_type}
    resp.is_success = status_code < 400
    resp.request = MagicMock()
    resp.request.url = MagicMock()
    resp.request.url.path = "/v1/documents"
    return resp


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_retrieves_document(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response("<root>hello</root>")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "/test/doc.xml"])

    assert result.exit_code == 0
    assert "<root>hello</root>" in result.stdout


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_json_format(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response('{"key": "val"}', "application/json")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "/test/doc.json", "-f", "json"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["headers"]["Accept"] == "application/json"


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_xml_format(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response("<doc/>", "application/xml")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "/test/doc.xml", "-f", "xml"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["headers"]["Accept"] == "application/xml"


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_text_format(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response("plain text", "text/plain")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "/test/doc.txt", "-f", "text"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["headers"]["Accept"] == "text/plain"


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_with_database(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "-d", "mydb", "/test/doc.xml"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["database"] == "mydb"


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_metadata(mock_client_cls, mock_resolve):
    meta = json.dumps(
        {
            "collections": ["coll1", "coll2"],
            "permissions": [{"role-name": "admin", "capabilities": ["read", "update"]}],
            "quality": 0,
        }
    )
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response(meta, "application/json")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "--metadata", "/test/doc.xml"])

    assert result.exit_code == 0
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["category"] == "metadata"


def test_doc_get_metadata_and_format_mutually_exclusive():
    result = runner.invoke(app, ["doc", "get", "--metadata", "-f", "json", "/x.xml"])

    assert result.exit_code == 2


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_binary_piped_outputs_content(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response(b"\x89PNG\r\n", "image/png", 200)
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "/test/img.png"])

    assert result.exit_code == 0


@patch("marklogic_tool.commands.doc.sys")
@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_binary_on_tty_warns(mock_client_cls, mock_resolve, mock_sys):
    mock_sys.stdout.isatty.return_value = True
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response(b"\x89PNG\r\n", "image/png", 200)
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "/test/img.png"])

    assert result.exit_code == 0


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_not_found(mock_client_cls, mock_resolve):
    from marklogic_tool.core.exceptions import NotFoundError

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = NotFoundError("Resource not found: /missing.xml")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["doc", "get", "/missing.xml"])

    assert result.exit_code == 3


@patch("marklogic_tool.commands.doc.resolve_profile")
@patch("marklogic_tool.commands.doc.MarkLogicClient")
def test_doc_get_metadata_json_output(mock_client_cls, mock_resolve):
    meta = json.dumps(
        {
            "collections": ["coll1"],
            "permissions": [],
            "quality": 5,
        }
    )
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _mock_response(meta, "application/json")
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["-o", "json", "doc", "get", "--metadata", "/x.xml"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
