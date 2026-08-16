"""Tests for the global --timeout option and its messages."""

import re
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import ExitCode, TimeoutError
from marklogic_tool.core.manage_client import ManageClient

runner = CliRunner()

SRC = Path(__file__).resolve().parents[1] / "src" / "marklogic_tool"


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        rest_port=8030,
        username="admin",
        password=SecretStr("secret123"),
        timeout=17,
    )


def _status_with(profile, transport, args):
    def factory(_profile, **kwargs):
        kwargs.setdefault("transport", transport)
        return ManageClient(profile, **kwargs)

    with (
        patch("marklogic_tool.commands.status.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.status.ManageClient", factory),
    ):
        return runner.invoke(app, args)


def test_timeout_is_accepted_globally(profile):
    seen: dict[str, int | None] = {}

    def factory(_profile, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise httpx.ConnectError("stop here")

    with (
        patch("marklogic_tool.commands.status.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.status.ManageClient", factory),
    ):
        runner.invoke(app, ["--timeout", "5", "status"])

    assert seen["timeout"] == 5


def test_timeout_is_accepted_after_the_subcommand(profile):
    """the documented invocations place it after the verb."""
    seen: dict[str, int | None] = {}

    def factory(_profile, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise httpx.ConnectError("stop here")

    with (
        patch("marklogic_tool.commands.status.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.status.ManageClient", factory),
    ):
        result = runner.invoke(app, ["status", "--timeout", "9"])

    assert result.exit_code != 2, "per-command --timeout must parse"
    assert seen["timeout"] == 9


def test_per_command_timeout_overrides_the_global_one(profile):
    seen: dict[str, int | None] = {}

    def factory(_profile, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise httpx.ConnectError("stop here")

    with (
        patch("marklogic_tool.commands.status.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.status.ManageClient", factory),
    ):
        runner.invoke(app, ["--timeout", "5", "status", "--timeout", "9"])

    assert seen["timeout"] == 9


def test_unset_timeout_defaults_to_the_profile_value(profile):
    """No invented figure: the profile governs."""
    client = MarkLogicClient(profile)
    assert client.timeout == 17

    manage = ManageClient(profile)
    assert manage.timeout == 17


def test_client_deadline_breach_exits_6_naming_the_flag(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    result = _status_with(profile, httpx.MockTransport(handler), ["status"])

    assert result.exit_code == ExitCode.TIMEOUT
    assert "--timeout" in result.stderr
    assert "CLIENT" in result.stderr


def test_server_limit_exits_6_and_says_timeout_cannot_raise_it(profile):
    """the operator must know WHICH limit bit them."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"errorResponse": {"messageCode": "XDMP-EXTIME"}}
        )

    result = _status_with(profile, httpx.MockTransport(handler), ["status"])

    assert result.exit_code == ExitCode.TIMEOUT
    assert "cannot raise" in result.stderr
    assert "request timeout" in result.stderr


def test_the_two_timeout_messages_are_textually_distinct(profile):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    def extime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"errorResponse": {"messageCode": "XDMP-EXTIME"}}
        )

    client_side = _status_with(profile, httpx.MockTransport(slow), ["status"])
    server_side = _status_with(profile, httpx.MockTransport(extime), ["status"])

    assert client_side.exit_code == server_side.exit_code == ExitCode.TIMEOUT
    assert client_side.stderr != server_side.stderr


def test_client_timeout_error_is_exit_6():
    assert TimeoutError("x").exit_code == 6


def test_no_invented_default_timeout_figure_on_any_timeout_option():
    """The profile value governs.

    The profile's own `timeout: int = 30` is the profile value. A `--timeout` option carrying
    its own number outranks the profile on every invocation that omits the flag.
    """
    pattern = re.compile(r'typer\.Option\(\s*([^,]+),\s*"--timeout"')
    declarations: list[str] = []

    for path in SRC.rglob("*.py"):
        for match in pattern.finditer(path.read_text()):
            declarations.append(f"{path.name}: default={match.group(1).strip()}")

    assert declarations, "expected --timeout to be declared somewhere"
    for declaration in declarations:
        assert declaration.endswith("default=None"), (
            f"--timeout carries an invented default: {declaration}"
        )


def test_both_clients_fall_back_to_the_profile_timeout(profile):
    """The fallback is what makes 'no invented default' true at runtime."""
    assert MarkLogicClient(profile).timeout == profile.timeout
    assert ManageClient(profile).timeout == profile.timeout
    assert MarkLogicClient(profile, timeout=3).timeout == 3
    assert ManageClient(profile, timeout=3).timeout == 3


def test_both_clients_raise_the_same_client_deadline_text(profile):
    """One wording, both clients — it lived in two places and diverged once."""

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    transport = httpx.MockTransport(slow)

    with (
        MarkLogicClient(profile, transport=transport) as query,
        pytest.raises(TimeoutError) as query_error,
    ):
        query.get("/v1/search")

    with (
        ManageClient(profile, transport=transport) as manage,
        pytest.raises(TimeoutError) as manage_error,
    ):
        manage.get_json("/manage/v2/hosts")

    assert "--timeout" in str(query_error.value)
    assert "--timeout" in str(manage_error.value)
    assert "CLIENT deadline" in str(query_error.value)
    assert "CLIENT deadline" in str(manage_error.value)
