"""`db show` survives a disabled collection lexicon.

The ticket blamed the MarkLogic version. The failing axis is the collection lexicon, and the
same error is reachable on later versions. A version-aware path fixes none of them.
"""

# cspell:ignore COLLXCNNOTFOUND

from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.commands.db import (
    COLLECTION_LEXICON_DISABLED,
    collection_lexicon_enabled,
)
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.manage_client import ManageClient

runner = CliRunner()

DB_STATUS = {
    "database-status": {
        "status-properties": {
            "state": {"value": "available"},
            "data-size": {"value": 3976},
            "in-memory-size": {"value": 1},
            "forests-count": {"value": 1},
            "min-capacity": {"value": 99.9},
            "merge-count": {"value": 0},
        }
    }
}


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml10.example.com",
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
    )


def _manage(profile, *, lexicon):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/properties"):
            payload: dict[str, object] = {"database-name": "db1"}
            if lexicon is not None:
                payload["collection-lexicon"] = lexicon
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=DB_STATUS)

    return httpx.MockTransport(handler)


def test_lexicon_enabled_is_read_from_the_database_properties(profile):
    def factory(_profile, **kwargs):
        kwargs.setdefault("transport", _manage(profile, lexicon=True))
        return ManageClient(profile, **kwargs)

    with patch("marklogic_tool.commands.db.ManageClient", factory):
        assert collection_lexicon_enabled(profile, "db1") is True


def test_lexicon_disabled_is_reported_as_disabled(profile):
    def factory(_profile, **kwargs):
        kwargs.setdefault("transport", _manage(profile, lexicon=False))
        return ManageClient(profile, **kwargs)

    with patch("marklogic_tool.commands.db.ManageClient", factory):
        assert collection_lexicon_enabled(profile, "db1") is False


def test_absent_lexicon_property_fails_closed(profile):
    """Report what is knowable rather than provoking a 500 to find out."""

    def factory(_profile, **kwargs):
        kwargs.setdefault("transport", _manage(profile, lexicon=None))
        return ManageClient(profile, **kwargs)

    with patch("marklogic_tool.commands.db.ManageClient", factory):
        assert collection_lexicon_enabled(profile, "db1") is False


def _run_show(profile, *, lexicon, collections=None):
    def manage_factory(_profile, **kwargs):
        kwargs.setdefault("transport", _manage(profile, lexicon=lexicon))
        return ManageClient(profile, **kwargs)

    with (
        patch("marklogic_tool.commands.db.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.db.ManageClient", manage_factory),
        patch("marklogic_tool.commands.db._fetch_single_db_doc_count", return_value=7),
        patch(
            "marklogic_tool.commands.db._fetch_collections",
            return_value=collections if collections is not None else [],
        ),
    ):
        return runner.invoke(app, ["-o", "json", "db", "show", "db1"])


def test_disabled_lexicon_does_not_fail_the_command(profile):
    """It used to surface as a bare HTTP 500 and blank the whole report."""
    result = _run_show(profile, lexicon=False)

    assert result.exit_code == 0
    assert '"documents": 7' in result.stdout
    assert '"state": "available"' in result.stdout


def test_unavailable_collections_are_null_never_empty(profile):
    """[] asserts 'no collections'; the truth is 'cannot see them'."""
    result = _run_show(profile, lexicon=False)

    assert '"collections": null' in result.stdout
    assert '"collections": []' not in result.stdout


def test_unavailable_collections_name_the_setting(profile):
    result = _run_show(profile, lexicon=False)

    assert COLLECTION_LEXICON_DISABLED in result.stdout
    assert "collections_unavailable_reason" in result.stdout


def test_enabled_lexicon_still_lists_collections(profile):
    result = _run_show(
        profile, lexicon=True, collections=[{"name": "expenses", "documents": 3}]
    )

    assert result.exit_code == 0
    assert "expenses" in result.stdout
    assert "collections_unavailable_reason" not in result.stdout


def test_genuinely_empty_is_distinguishable_from_unavailable(profile):
    """The whole point: 'none' and 'cannot see' must not render alike."""
    empty = _run_show(profile, lexicon=True, collections=[])
    unavailable = _run_show(profile, lexicon=False)

    assert '"collections": []' in empty.stdout
    assert '"collections": null' in unavailable.stdout
    assert "collections_unavailable_reason" not in empty.stdout
    assert "collections_unavailable_reason" in unavailable.stdout


def test_no_collections_call_is_made_when_the_lexicon_is_off(profile):
    """Do not provoke the 500 we already know is coming."""

    def manage_factory(_profile, **kwargs):
        kwargs.setdefault("transport", _manage(profile, lexicon=False))
        return ManageClient(profile, **kwargs)

    with (
        patch("marklogic_tool.commands.db.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.db.ManageClient", manage_factory),
        patch("marklogic_tool.commands.db._fetch_single_db_doc_count", return_value=0),
        patch("marklogic_tool.commands.db._fetch_collections") as fetch,
    ):
        result = runner.invoke(app, ["-o", "json", "db", "show", "db1"])

    assert result.exit_code == 0
    fetch.assert_not_called()
