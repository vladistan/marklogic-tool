"""Tests for eval command — input modes, language selection, output formatting."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marklogic_tool.cli import app

runner = CliRunner()

MOCK_MULTIPART_SINGLE = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: string\r\n"
    b"\r\n"
    b"Documents\r\n"
    b"--ML_BOUNDARY--\r\n"
)

MOCK_MULTIPART_MULTIPLE = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: integer\r\n"
    b"\r\n"
    b"1\r\n"
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: integer\r\n"
    b"\r\n"
    b"2\r\n"
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: integer\r\n"
    b"\r\n"
    b"3\r\n"
    b"--ML_BOUNDARY--\r\n"
)


def _mock_response(content=MOCK_MULTIPART_SINGLE, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode() if isinstance(content, bytes) else content
    resp.headers = {"content-type": "multipart/mixed; boundary=ML_BOUNDARY"}
    resp.is_success = status_code < 400
    resp.request = MagicMock()
    resp.request.url = MagicMock()
    resp.request.url.path = "/v1/eval"
    return resp


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_xquery_positional_argument(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(
        app, ["-o", "table", "eval", "xdmp:database-name(xdmp:database())"]
    )

    assert result.exit_code == 0
    assert "Documents" in result.stdout
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["data"]["xquery"] == "xdmp:database-name(xdmp:database())"


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_javascript_flag(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["eval", "-j", "xdmp.databaseName(xdmp.database())"])

    assert result.exit_code == 0
    call_kwargs = mock_client.post.call_args
    assert "javascript" in call_kwargs[1]["data"]
    assert "xquery" not in call_kwargs[1]["data"]


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_from_file(mock_client_cls, mock_resolve, tmp_path):
    query_file = tmp_path / "test.xqy"
    query_file.write_text("xdmp:host-name()")

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["eval", "-f", str(query_file)])

    assert result.exit_code == 0
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["data"]["xquery"] == "xdmp:host-name()"


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_from_stdin(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["eval"], input="xdmp:host-name()")

    assert result.exit_code == 0
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["data"]["xquery"] == "xdmp:host-name()"


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_with_database_flag(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["eval", "-d", "Documents", "1+1"])

    assert result.exit_code == 0
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["data"]["database"] == "Documents"


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_with_variables(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(
        app, ["eval", "--vars", '{"x": 1}', "declare variable $x external; $x"]
    )

    assert result.exit_code == 0
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["data"]["vars"] == '{"x": 1}'


def test_eval_invalid_vars_json():
    result = runner.invoke(app, ["eval", "--vars", "not json", "1+1"])

    assert result.exit_code == 2
    assert "Invalid JSON" in result.stdout or "Invalid JSON" in (result.stderr or "")


def test_eval_no_code_provided():
    result = runner.invoke(app, ["eval"])

    assert result.exit_code == 2


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_multiple_results_text_mode(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response(content=MOCK_MULTIPART_MULTIPLE)
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["-o", "table", "eval", "(1, 2, 3)"])

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert lines == ["1", "2", "3"]


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_multiple_results_json_mode(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = _mock_response(content=MOCK_MULTIPART_MULTIPLE)
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["-o", "json", "eval", "(1, 2, 3)"])

    assert result.exit_code == 0
    import json

    data = json.loads(result.stdout)
    assert len(data) == 3
    assert data[0]["value"] == "1"
    assert data[0]["type"] == "integer"


@patch("marklogic_tool.commands.eval.resolve_profile")
@patch("marklogic_tool.commands.eval.MarkLogicClient")
def test_eval_empty_sequence(mock_client_cls, mock_resolve):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    empty_response = _mock_response(content=b"")
    empty_response.headers = {"content-type": "text/plain"}
    empty_response.content = b""
    mock_client.post.return_value = empty_response
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["eval", "()"])

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_eval_file_not_found():
    result = runner.invoke(app, ["eval", "-f", "/nonexistent/query.xqy"])

    assert result.exit_code == 3
