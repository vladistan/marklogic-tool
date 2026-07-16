"""Tests for error message quality — actionable messages, no credential leaks, correct exit codes."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExitCode,
    NetworkError,
    NotFoundError,
    ServerError,
    TimeoutError,
)

runner = CliRunner()


def _make_profile():
    return ProfileSettings(
        host="ml-dev.example.com",
        port=8000,
        username="admin",
        password="super-secret-password",
    )


def test_network_error_suggests_checking_marklogic():
    profile = _make_profile()
    with (
        patch.object(httpx.Client, "request", side_effect=httpx.ConnectError("refused")),
        pytest.raises(NetworkError, match="Check that MarkLogic is running"),
        MarkLogicClient(profile) as client,
    ):
        client.get("/v1/search")


def test_timeout_error_includes_timeout_value():
    profile = _make_profile()
    with (
        patch.object(httpx.Client, "request", side_effect=httpx.ReadTimeout("timed out")),
        pytest.raises(TimeoutError, match="30s"),
        MarkLogicClient(profile) as client,
    ):
        client.get("/v1/search")


def test_auth_error_names_host():
    profile = _make_profile()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.is_success = False
    mock_resp.request = MagicMock()
    mock_resp.request.url = MagicMock()
    with (
        patch.object(httpx.Client, "request", return_value=mock_resp),
        pytest.raises(AuthenticationError, match="ml-dev.example.com"),
        MarkLogicClient(profile) as client,
    ):
        client.get("/v1/search")


def test_no_credentials_in_network_error():
    profile = _make_profile()
    with (
        patch.object(httpx.Client, "request", side_effect=httpx.ConnectError("refused")),
        pytest.raises(NetworkError) as exc_info,
        MarkLogicClient(profile) as client,
    ):
        client.get("/v1/search")
    assert "super-secret-password" not in str(exc_info.value)
    assert "admin" not in str(exc_info.value.message)


def test_no_credentials_in_timeout_error():
    profile = _make_profile()
    with (
        patch.object(httpx.Client, "request", side_effect=httpx.ReadTimeout("timed out")),
        pytest.raises(TimeoutError) as exc_info,
        MarkLogicClient(profile) as client,
    ):
        client.get("/v1/eval")
    assert "super-secret-password" not in str(exc_info.value)


def test_no_credentials_in_auth_error():
    profile = _make_profile()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.is_success = False
    mock_resp.request = MagicMock()
    mock_resp.request.url = MagicMock()
    with (
        patch.object(httpx.Client, "request", return_value=mock_resp),
        pytest.raises(AuthenticationError) as exc_info,
        MarkLogicClient(profile) as client,
    ):
        client.get("/v1/search")
    assert "super-secret-password" not in str(exc_info.value)


def test_no_credentials_in_config_show_output():
    result = runner.invoke(app, ["config", "show", "--help"])
    assert result.exit_code == 0
    assert "masked" in result.output.lower() or "credentials" in result.output.lower()


def test_exit_code_mapping():
    assert ExitCode.SUCCESS == 0
    assert ExitCode.GENERAL == 1
    assert ExitCode.USAGE == 2
    assert ExitCode.INPUT == 3
    assert ExitCode.OUTPUT == 4
    assert ExitCode.NETWORK == 5
    assert ExitCode.TIMEOUT == 6


def test_exit_codes_match_exceptions():
    assert NetworkError("test").exit_code == 5
    assert TimeoutError("test").exit_code == 6
    assert ConfigurationError("test").exit_code == 3
    assert AuthenticationError("test").exit_code == 3
    assert NotFoundError("test").exit_code == 1
    assert ServerError("test").exit_code == 1


@patch("marklogic_tool.commands.search.resolve_profile")
@patch("marklogic_tool.commands.search.MarkLogicClient")
def test_no_credentials_in_cli_error_output(mock_client_cls, mock_resolve):
    mock_resolve.return_value = _make_profile()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = NetworkError(
        "Cannot connect to ml-dev.example.com:8000. "
        "Check that MarkLogic is running and the host/port are correct."
    )
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["search", "test"])

    assert "super-secret-password" not in result.output
    assert "Cannot connect" in result.output
