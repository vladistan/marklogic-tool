"""Tests for database commands."""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marklogic_tool.cli import app

runner = CliRunner()

MOCK_DB_LIST = {
    "database-default-list": {
        "list-items": {
            "list-item": [
                {"nameref": "Documents", "idref": "123"},
                {"nameref": "Content", "idref": "456"},
            ]
        }
    }
}

MOCK_DB_STATUS = {
    "database-status": {
        "status-properties": {
            "state": {"units": "enum", "value": "available"},
            "data-size": {"units": "MB", "value": 500},
            "forests-count": {"units": "quantity", "value": 2},
            "in-memory-size": {"units": "MB", "value": 50},
            "min-capacity": {"units": "%", "value": 75.0},
            "merge-count": {"units": "quantity", "value": 0},
        }
    }
}

MOCK_MULTIPART_COUNTS = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: string\r\n"
    b"\r\n"
    b"Documents|100\r\n"
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: string\r\n"
    b"\r\n"
    b"Content|5000\r\n"
    b"--ML_BOUNDARY--\r\n"
)

MOCK_MULTIPART_DOC_COUNT = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: integer\r\n"
    b"\r\n"
    b"5000\r\n"
    b"--ML_BOUNDARY--\r\n"
)

MOCK_MULTIPART_COLLECTIONS = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: string\r\n"
    b"\r\n"
    b"coll-a|3000\r\n"
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: string\r\n"
    b"\r\n"
    b"coll-b|2000\r\n"
    b"--ML_BOUNDARY--\r\n"
)


def _mock_eval_response(content):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    resp.headers = {"content-type": "multipart/mixed; boundary=ML_BOUNDARY"}
    resp.is_success = True
    resp.request = MagicMock()
    resp.request.url = MagicMock()
    resp.request.url.path = "/v1/eval"
    return resp


@patch("marklogic_tool.commands.db.ManageClient")
@patch("marklogic_tool.commands.db.MarkLogicClient")
@patch("marklogic_tool.commands.db.resolve_profile")
def test_db_list(mock_resolve, mock_ml_cls, mock_manage_cls):
    mock_manage = MagicMock()
    mock_manage.__enter__ = MagicMock(return_value=mock_manage)
    mock_manage.__exit__ = MagicMock(return_value=False)
    mock_manage.get_json.side_effect = [MOCK_DB_LIST, MOCK_DB_STATUS, MOCK_DB_STATUS]
    mock_manage_cls.return_value = mock_manage

    mock_ml = MagicMock()
    mock_ml.__enter__ = MagicMock(return_value=mock_ml)
    mock_ml.__exit__ = MagicMock(return_value=False)
    mock_ml.post.return_value = _mock_eval_response(MOCK_MULTIPART_COUNTS)
    mock_ml_cls.return_value = mock_ml

    result = runner.invoke(app, ["-o", "table", "db", "list"])

    assert result.exit_code == 0
    assert "Documents" in result.stdout
    assert "Content" in result.stdout


@patch("marklogic_tool.commands.db.ManageClient")
@patch("marklogic_tool.commands.db.MarkLogicClient")
@patch("marklogic_tool.commands.db.resolve_profile")
def test_db_list_json(mock_resolve, mock_ml_cls, mock_manage_cls):
    mock_manage = MagicMock()
    mock_manage.__enter__ = MagicMock(return_value=mock_manage)
    mock_manage.__exit__ = MagicMock(return_value=False)
    mock_manage.get_json.side_effect = [MOCK_DB_LIST, MOCK_DB_STATUS, MOCK_DB_STATUS]
    mock_manage_cls.return_value = mock_manage

    mock_ml = MagicMock()
    mock_ml.__enter__ = MagicMock(return_value=mock_ml)
    mock_ml.__exit__ = MagicMock(return_value=False)
    mock_ml.post.return_value = _mock_eval_response(MOCK_MULTIPART_COUNTS)
    mock_ml_cls.return_value = mock_ml

    result = runner.invoke(app, ["-o", "json", "db", "list"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 2


@patch(
    "marklogic_tool.commands.db.collection_lexicon_enabled",
    new=lambda *args, **kwargs: True,
)
@patch("marklogic_tool.commands.db._fetch_collections")
@patch("marklogic_tool.commands.db._fetch_single_db_doc_count")
@patch("marklogic_tool.commands.db._fetch_database_status")
@patch("marklogic_tool.commands.db.resolve_profile")
def test_db_show(mock_resolve, mock_status, mock_count, mock_colls):
    mock_status.return_value = {
        "name": "Content",
        "state": "available",
        "data_size_mb": 500,
        "in_memory_mb": 50,
        "forests_count": 2,
        "min_capacity_pct": 75.0,
        "merge_count": 0,
    }
    mock_count.return_value = 5000
    mock_colls.return_value = [{"name": "coll-a", "documents": 3000}]

    result = runner.invoke(app, ["-o", "table", "db", "show", "Content"])

    assert result.exit_code == 0
    assert "Content" in result.stdout
    assert "5,000" in result.stdout
    assert "available" in result.stdout


@patch(
    "marklogic_tool.commands.db.collection_lexicon_enabled",
    new=lambda *args, **kwargs: True,
)
@patch("marklogic_tool.commands.db._fetch_collections")
@patch("marklogic_tool.commands.db._fetch_single_db_doc_count")
@patch("marklogic_tool.commands.db._fetch_database_status")
@patch("marklogic_tool.commands.db.resolve_profile")
def test_db_show_json(mock_resolve, mock_status, mock_count, mock_colls):
    mock_status.return_value = {
        "name": "Content",
        "state": "available",
        "data_size_mb": 500,
        "in_memory_mb": 50,
        "forests_count": 2,
        "min_capacity_pct": 75.0,
        "merge_count": 0,
    }
    mock_count.return_value = 5000
    mock_colls.return_value = []

    result = runner.invoke(app, ["-o", "json", "db", "show", "Content"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data[0]["name"] == "Content"
