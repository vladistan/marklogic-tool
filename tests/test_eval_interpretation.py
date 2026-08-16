"""Eval names the interpretation it used.

`_resolve_source` reads a file only when `--file` says so.

The real defect runs the other way: the tool sent a positional path to the server as XQuery.
"""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.commands.eval import (
    FILE,
    INLINE,
    _interpretation_note,
    _resolve_source,
)
from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings

runner = CliRunner()

MULTILINE = 'xquery version "1.0-ml";\nlet $x := 1\nreturn $x'


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
    )


def _run(args, profile, *, status=500, code="XDMP-MISSINGCONTEXT"):
    def handler(request: httpx.Request) -> httpx.Response:
        if status == 200:
            return httpx.Response(
                200,
                content=b"--B\r\nContent-Type: text/plain\r\nX-Primitive: integer\r\n\r\n1\r\n--B--",
                headers={"content-type": "multipart/mixed; boundary=B"},
            )
        return httpx.Response(status, json={"errorResponse": {"messageCode": code}})

    transport = httpx.MockTransport(handler)

    def factory(_profile, **kwargs):
        kwargs.setdefault("transport", transport)
        return MarkLogicClient(profile, **kwargs)

    with (
        patch("marklogic_tool.commands.eval.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.eval.MarkLogicClient", factory),
    ):
        return runner.invoke(app, args)


def test_multiline_inline_query_executes(profile):
    """It always did; this pins it so nobody 'fixes' it into a heuristic."""
    result = _run(["eval", MULTILINE], profile, status=200)

    assert result.exit_code == 0


def test_positional_argument_is_always_code(tmp_path):
    """Explicit, content-blind dispatch — no sniffing for newlines or suffixes."""
    real = tmp_path / "query.xqy"
    real.write_text("1+1")

    source, origin = _resolve_source(str(real), None)

    assert origin is INLINE or origin == INLINE
    assert source == str(real), "the path text itself is the code, not its contents"


def test_file_option_reads_the_file(tmp_path):
    real = tmp_path / "query.xqy"
    real.write_text("1+1")

    source, origin = _resolve_source(None, real)

    assert origin == FILE
    assert source == "1+1"


def test_failure_names_the_inline_interpretation(profile):
    result = _run(["eval", "./missing.xqy"], profile)

    assert result.exit_code != 0
    assert "inline code" in result.stderr
    assert "positional argument is always code" in result.stderr


def test_failure_points_at_file_when_that_file_exists(tmp_path, profile):
    real = tmp_path / "query.xqy"
    real.write_text("1+1")

    result = _run(["eval", str(real)], profile)

    assert result.exit_code != 0
    assert "--file" in result.stderr
    assert str(real) in result.stderr


def test_missing_file_names_the_file_interpretation(profile):
    result = _run(["eval", "--file", "./missing.xqy"], profile)

    assert result.exit_code == 3
    assert "FILE interpretation" in result.stderr
    assert "--file" in result.stderr


def test_note_is_silent_for_non_inline_origins():
    assert _interpretation_note("anything", FILE) == ""


def test_note_does_not_probe_a_multiline_query_as_a_path():
    """A query is not a path; never stat() a whole XQuery program."""
    note = _interpretation_note(MULTILINE, INLINE)

    assert "inline code" in note
    assert "--file" not in note


def test_note_survives_a_pathological_argument():
    """An argument can be anything; the note must not raise on it."""
    for hostile in ("\x00bad", "x" * 9000, "", "   "):
        assert isinstance(_interpretation_note(hostile, INLINE), str)


def test_interpretation_is_reported_but_never_changes_dispatch(tmp_path):
    """The existence check informs the MESSAGE only, never what runs."""
    real = tmp_path / "query.xqy"
    real.write_text("SHOULD NOT BE READ")

    source, origin = _resolve_source(str(real), None)

    assert source == str(real)
    assert "SHOULD NOT BE READ" not in source
    assert Path(source).is_file(), "the file exists, and was still not read"
